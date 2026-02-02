"""
Server Link module - cross-server linking for syncing moderation actions.

Allows servers to link together in a parent/child hierarchy:
- Parent actions flow downstream to children automatically
- Child actions can flow upstream with parent approval (if enabled)
- Two types of keys: admin (trusted) and public (readonly)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord

from core.help_system import help_system
from core.link_storage import get_link_storage, TrustLevel
from core.permissions import can_use_command, is_module_enabled
from core.sync_protection import (
    DEFAULT_MAX_ACTIONS,
    DEFAULT_WINDOW_SECONDS,
    get_sync_protection,
)

logger = logging.getLogger("discbot.server_link")

MODULE_NAME = "serverlink"


def setup_server_link() -> None:
    """Register help information for the server link module."""
    help_system.register_module(
        name="Server Link",
        description="Cross-server linking for syncing moderation actions. "
                    "Link servers in a parent/child hierarchy to share bans, kicks, and more.",
        help_command="serverlink help",
        commands=[
            ("linkserver", "Generate a link key (admin=trusted, non-admin=readonly)"),
            ("addlink <key>", "Subscribe as child to a parent server"),
            ("links", "Show all parent/child relationships"),
            ("unlink <server_id>", "Remove a link to a server"),
            ("linksettings <server_id> <setting> <value>", "Configure sync settings"),
            ("linkprotection [window|max|reset] [value]", "Configure sync protection thresholds"),
        ]
    )


async def _cmd_help(message: discord.Message) -> None:
    """Show help for server link commands."""
    embed = help_system.get_module_embed("Server Link")
    if embed is None:
        await message.reply("Help not available.", mention_author=False)
        return

    # Add extra info about key types
    embed.add_field(
        name="Key Types",
        value=(
            "**Admin key** (): Created by admins. Children get full trust - "
            "can request upstream approval.\n"
            "**Public key** (): Created by non-admins. Children are read-only - "
            "can only receive, never send upstream."
        ),
        inline=False,
    )

    await message.reply(embed=embed, mention_author=False)


async def _check_module_enabled(message: discord.Message) -> bool:
    """Check if the module is enabled."""
    if not message.guild:
        return False

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.reply(
            "Server Link module is disabled in this server.\n"
            "An administrator can enable it with `modules enable serverlink`",
            mention_author=False,
        )
        return False

    return True


# ─── linkserver Command ───────────────────────────────────────────────────────


async def handle_linkserver_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle: linkserver

    Generates a link key. If run by admin, creates trusted key.
    If run by non-admin, creates readonly key.
    """
    content = message.content.strip().lower()
    if content != "linkserver":
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    # Check if user is admin
    is_admin = False
    if isinstance(message.author, discord.Member):
        is_admin = message.author.guild_permissions.administrator

    # Generate key
    storage = await get_link_storage()
    key = await storage.create_pending_link(
        parent_guild_id=message.guild.id,
        parent_guild_name=message.guild.name,
        created_by_user_id=message.author.id,
        is_admin_key=is_admin,
    )

    key_type = " **Admin key** (trusted)" if is_admin else " **Public key** (read-only)"

    # DM the key to the user
    try:
        dm_embed = discord.Embed(
            title="Server Link Key Generated",
            description=f"Your link key for **{message.guild.name}**:",
            color=0x5865F2,
        )
        dm_embed.add_field(
            name="Key",
            value=f"```{key}```",
            inline=False,
        )
        dm_embed.add_field(
            name="Type",
            value=key_type,
            inline=False,
        )
        dm_embed.add_field(
            name="Usage",
            value="Share this key with another server admin.\n"
                  "They run `addlink <key>` to subscribe to your server.",
            inline=False,
        )
        dm_embed.add_field(
            name="Expires",
            value="5 minutes",
            inline=True,
        )

        if is_admin:
            dm_embed.add_field(
                name="Trust Level",
                value="Children using this key can request upstream approval.",
                inline=False,
            )
        else:
            dm_embed.add_field(
                name="Trust Level",
                value="Children using this key are read-only. They cannot send actions upstream.",
                inline=False,
            )

        await message.author.send(embed=dm_embed)

        await message.reply(
            f"Link key sent to your DMs! {key_type}\n"
            f"Valid for 5 minutes.",
            mention_author=False,
        )

    except discord.Forbidden:
        await message.reply(
            "I couldn't DM you the link key. Please enable DMs from server members.",
            mention_author=False,
        )

    logger.info(
        "Link key generated for guild %s by %s (admin=%s)",
        message.guild.id, message.author.id, is_admin
    )

    return True


# ─── addlink Command ──────────────────────────────────────────────────────────


async def handle_addlink_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle: addlink <key>

    Subscribe as child to a parent server. Requires admin permission.
    """
    content = message.content.strip()
    if not content.lower().startswith("addlink "):
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    # Must be admin
    if not isinstance(message.author, discord.Member):
        return False

    if not message.author.guild_permissions.administrator:
        await message.reply(
            "Only administrators can add server links.",
            mention_author=False,
        )
        return True

    # Parse key
    key = content[8:].strip().upper()
    if not key or len(key) != 6:
        await message.reply(
            "**Usage:** `addlink <key>`\n"
            "Key should be 6 characters.",
            mention_author=False,
        )
        return True

    storage = await get_link_storage()

    # Consume the pending link
    link_info = await storage.consume_pending_link(key)
    if not link_info:
        await message.reply(
            "Invalid or expired link key.",
            mention_author=False,
        )
        return True

    parent_guild_id = int(link_info["parent_guild_id"])
    parent_guild_name = link_info["parent_guild_name"]
    is_admin_key = link_info.get("is_admin_key", False)
    trust_level: TrustLevel = "trusted" if is_admin_key else "readonly"

    # Can't link to self
    if parent_guild_id == message.guild.id:
        await message.reply(
            "You can't link a server to itself.",
            mention_author=False,
        )
        return True

    # Check if already linked
    existing = await storage.get_parent(message.guild.id, parent_guild_id)
    if existing:
        await message.reply(
            f"Already linked to **{parent_guild_name}**.",
            mention_author=False,
        )
        return True

    # Add the link (both directions)
    await storage.add_parent_link(
        child_guild_id=message.guild.id,
        parent_guild_id=parent_guild_id,
        parent_guild_name=parent_guild_name,
        trust_level=trust_level,
    )

    await storage.add_child_link(
        parent_guild_id=parent_guild_id,
        child_guild_id=message.guild.id,
        child_guild_name=message.guild.name,
        trust_level=trust_level,
    )

    trust_icon = "" if trust_level == "trusted" else ""
    trust_desc = "trusted" if trust_level == "trusted" else "read-only"

    await message.reply(
        f"**Linked!** {trust_icon}\n"
        f"This server is now a **child** of **{parent_guild_name}**.\n"
        f"Trust level: **{trust_desc}**\n\n"
        f"You will receive moderation actions from the parent server.",
        mention_author=False,
    )

    logger.info(
        "Server %s linked as child to %s (trust=%s)",
        message.guild.id, parent_guild_id, trust_level
    )

    return True


# ─── links Command ────────────────────────────────────────────────────────────


async def handle_links_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle: links

    Show all parent and child links.
    """
    content = message.content.strip().lower()
    if content != "links":
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    storage = await get_link_storage()

    parents = await storage.get_parents(message.guild.id)
    children = await storage.get_children(message.guild.id)

    if not parents and not children:
        await message.reply(
            "This server has no links.\n"
            "Use `linkserver` to generate a link key, or `addlink <key>` to subscribe to a parent.",
            mention_author=False,
        )
        return True

    embed = discord.Embed(
        title=f"Server Links for {message.guild.name}",
        color=0x5865F2,
    )

    # Parents (servers this guild subscribes to)
    if parents:
        parent_lines = []
        for p in parents:
            trust_icon = "" if p.get("trust_level") == "trusted" else ""
            name = p.get("guild_name", "Unknown")
            guild_id = p.get("guild_id", "?")
            parent_lines.append(f"{trust_icon} **{name}** (`{guild_id}`)")

        embed.add_field(
            name=" Parents (receiving from)",
            value="\n".join(parent_lines) or "None",
            inline=False,
        )

    # Children (servers subscribing to this guild)
    if children:
        child_lines = []
        for c in children:
            trust_icon = "" if c.get("trust_level") == "trusted" else ""
            name = c.get("guild_name", "Unknown")
            guild_id = c.get("guild_id", "?")
            upstream = "" if c.get("accept_upstream") else ""
            child_lines.append(f"{trust_icon} **{name}** (`{guild_id}`) {upstream}")

        embed.add_field(
            name=" Children (sending to)",
            value="\n".join(child_lines) or "None",
            inline=False,
        )

    embed.add_field(
        name="Legend",
        value=" = trusted (can send upstream)   = read-only\n = upstream enabled",
        inline=False,
    )

    await message.reply(embed=embed, mention_author=False)
    return True


# ─── unlink Command ───────────────────────────────────────────────────────────


async def handle_unlink_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle: unlink <server_id>

    Remove a link to a server (either parent or child).
    """
    content = message.content.strip()
    if not content.lower().startswith("unlink "):
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    # Must be admin
    if not isinstance(message.author, discord.Member):
        return False

    if not message.author.guild_permissions.administrator:
        await message.reply(
            "Only administrators can remove server links.",
            mention_author=False,
        )
        return True

    # Parse server ID
    args = content[7:].strip()
    try:
        target_guild_id = int(args)
    except ValueError:
        await message.reply(
            "**Usage:** `unlink <server_id>`\n"
            "Use `links` to see server IDs.",
            mention_author=False,
        )
        return True

    storage = await get_link_storage()

    # Try to remove as parent first
    removed_parent = await storage.remove_parent_link(message.guild.id, target_guild_id)
    if removed_parent:
        # Also remove from the other side
        await storage.remove_child_link(target_guild_id, message.guild.id)

        await message.reply(
            f"Unlinked from parent server `{target_guild_id}`.",
            mention_author=False,
        )
        logger.info(
            "Server %s unlinked from parent %s",
            message.guild.id, target_guild_id
        )
        return True

    # Try to remove as child
    removed_child = await storage.remove_child_link(message.guild.id, target_guild_id)
    if removed_child:
        # Also remove from the other side
        await storage.remove_parent_link(target_guild_id, message.guild.id)

        await message.reply(
            f"Unlinked child server `{target_guild_id}`.",
            mention_author=False,
        )
        logger.info(
            "Server %s unlinked child %s",
            message.guild.id, target_guild_id
        )
        return True

    await message.reply(
        f"No link found to server `{target_guild_id}`.",
        mention_author=False,
    )
    return True


# ─── linksettings Command ─────────────────────────────────────────────────────


async def handle_linksettings_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle: linksettings <server_id> [setting] [value]

    Configure sync settings for a link.
    """
    content = message.content.strip()
    if not content.lower().startswith("linksettings"):
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    # Must be admin
    if not isinstance(message.author, discord.Member):
        return False

    if not message.author.guild_permissions.administrator:
        await message.reply(
            "Only administrators can change link settings.",
            mention_author=False,
        )
        return True

    # Parse arguments
    args = content[12:].strip().split()

    if len(args) == 0:
        await message.reply(
            "**Usage:** `linksettings <server_id> [setting] [value]`\n\n"
            "**Settings for links where you are the CHILD:**\n"
            "• `sync-bans yes/no` - Receive bans\n"
            "• `sync-kicks yes/no` - Receive kicks\n"
            "• `sync-mutes yes/no` - Receive mutes\n"
            "• `sync-warnings yes/no` - Receive warnings\n"
            "• `sync-autoresponder yes/no` - Receive autoresponder configs\n"
            "• `sync-hashes yes/no` - Receive scanner hashes\n\n"
            "**Settings for links where you are the PARENT:**\n"
            "• `accept-upstream yes/no` - Receive child actions for approval (trusted only)\n"
            "• `approval-channel #channel` - Where to post approval requests\n"
            "• `auto-cascade yes/no` - Auto-propagate approved actions to other children",
            mention_author=False,
        )
        return True

    try:
        target_guild_id = int(args[0])
    except ValueError:
        await message.reply(
            "Invalid server ID. Use `links` to see server IDs.",
            mention_author=False,
        )
        return True

    storage = await get_link_storage()

    # Check if it's a parent or child link
    parent_link = await storage.get_parent(message.guild.id, target_guild_id)
    child_link = await storage.get_child(message.guild.id, target_guild_id)

    if not parent_link and not child_link:
        await message.reply(
            f"No link found to server `{target_guild_id}`.",
            mention_author=False,
        )
        return True

    # If no setting specified, show current settings
    if len(args) == 1:
        link = parent_link or child_link
        is_parent = parent_link is not None

        embed = discord.Embed(
            title=f"Link Settings: {link.get('guild_name', 'Unknown')}",
            description=f"You are the **{'child' if is_parent else 'parent'}** in this link.",
            color=0x5865F2,
        )

        trust_icon = "" if link.get("trust_level") == "trusted" else ""
        embed.add_field(
            name="Trust Level",
            value=f"{trust_icon} {link.get('trust_level', 'unknown')}",
            inline=True,
        )

        sync_settings = []
        for key in ["sync_bans", "sync_kicks", "sync_mutes", "sync_warnings", "sync_autoresponder", "sync_hashes"]:
            val = "" if link.get(key, False) else ""
            name = key.replace("sync_", "").replace("_", " ").title()
            sync_settings.append(f"{val} {name}")

        embed.add_field(
            name="Sync Settings",
            value="\n".join(sync_settings),
            inline=False,
        )

        if not is_parent:  # We are parent, they are child
            embed.add_field(
                name="Upstream Settings",
                value=(
                    f"Accept upstream: {'' if link.get('accept_upstream') else ''}\n"
                    f"Approval channel: {link.get('approval_channel_id') or 'Not set'}\n"
                    f"Auto cascade: {'' if link.get('auto_cascade', True) else ''}"
                ),
                inline=False,
            )

        await message.reply(embed=embed, mention_author=False)
        return True

    # Parse setting and value
    if len(args) < 3:
        await message.reply(
            "**Usage:** `linksettings <server_id> <setting> <value>`",
            mention_author=False,
        )
        return True

    setting = args[1].lower().replace("_", "-")
    value = args[2].lower()

    # Map settings to keys
    bool_settings = {
        "sync-bans": "sync_bans",
        "sync-kicks": "sync_kicks",
        "sync-mutes": "sync_mutes",
        "sync-warnings": "sync_warnings",
        "sync-autoresponder": "sync_autoresponder",
        "sync-hashes": "sync_hashes",
        "accept-upstream": "accept_upstream",
        "auto-cascade": "auto_cascade",
    }

    if setting in bool_settings:
        if value not in ("yes", "no", "true", "false", "on", "off"):
            await message.reply(
                f"Value for `{setting}` must be `yes` or `no`.",
                mention_author=False,
            )
            return True

        bool_value = value in ("yes", "true", "on")
        key = bool_settings[setting]

        # Check if setting is valid for this link type
        parent_only_settings = {"accept_upstream", "approval_channel_id", "auto_cascade"}

        if key in parent_only_settings:
            if not child_link:
                await message.reply(
                    f"`{setting}` can only be changed for servers where you are the parent.",
                    mention_author=False,
                )
                return True

            # Check trust level for accept-upstream
            if key == "accept_upstream" and child_link.get("trust_level") != "trusted":
                await message.reply(
                    "Cannot enable upstream for read-only (public key) children.\n"
                    "Only trusted (admin key) children can send upstream.",
                    mention_author=False,
                )
                return True

            success = await storage.update_child_settings(
                message.guild.id, target_guild_id, **{key: bool_value}
            )
        else:
            if parent_link:
                success = await storage.update_parent_settings(
                    message.guild.id, target_guild_id, **{key: bool_value}
                )
            else:
                success = await storage.update_child_settings(
                    message.guild.id, target_guild_id, **{key: bool_value}
                )

        if success:
            await message.reply(
                f"Set `{setting}` to `{value}` for server `{target_guild_id}`.",
                mention_author=False,
            )
        else:
            await message.reply(
                "Failed to update setting.",
                mention_author=False,
            )
        return True

    elif setting == "approval-channel":
        if not child_link:
            await message.reply(
                "`approval-channel` can only be set for servers where you are the parent.",
                mention_author=False,
            )
            return True

        # Parse channel mention or ID
        channel_match = re.match(r"<#(\d+)>", value)
        if channel_match:
            channel_id = int(channel_match.group(1))
        else:
            try:
                channel_id = int(value)
            except ValueError:
                await message.reply(
                    "Please mention a channel or provide a channel ID.",
                    mention_author=False,
                )
                return True

        # Verify channel exists
        channel = message.guild.get_channel(channel_id)
        if not channel:
            await message.reply(
                "Channel not found.",
                mention_author=False,
            )
            return True

        success = await storage.update_child_settings(
            message.guild.id, target_guild_id, approval_channel_id=str(channel_id)
        )

        if success:
            await message.reply(
                f"Set approval channel to {channel.mention} for server `{target_guild_id}`.",
                mention_author=False,
            )
        else:
            await message.reply(
                "Failed to update setting.",
                mention_author=False,
            )
        return True

    else:
        await message.reply(
            f"Unknown setting: `{setting}`\n"
            "Use `linksettings` without arguments to see available settings.",
            mention_author=False,
        )
        return True


# ─── linkprotection Command ───────────────────────────────────────────────────


async def handle_linkprotection_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Handle: linkprotection [window|max|reset] [value]

    Configure sync protection thresholds for this guild.
    """
    content = message.content.strip()
    if not content.lower().startswith("linkprotection"):
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    # Must be admin
    if not isinstance(message.author, discord.Member):
        return False

    if not message.author.guild_permissions.administrator:
        await message.reply(
            "Only administrators can change link protection settings.",
            mention_author=False,
        )
        return True

    args = content.split()
    storage = await get_link_storage()
    protection = await get_sync_protection()

    if len(args) == 1:
        window_seconds, max_actions = await protection.get_guild_thresholds(message.guild.id)
        custom = await storage.get_protection_settings(message.guild.id)

        window_label = f"{window_seconds}s" + (" (custom)" if "window_seconds" in custom else "")
        max_label = f"{max_actions}" + (" (custom)" if "max_actions" in custom else "")

        embed = discord.Embed(
            title="Sync Protection Settings",
            color=0xE67E22,
        )
        embed.add_field(name="Window", value=window_label, inline=True)
        embed.add_field(name="Max Actions", value=max_label, inline=True)
        embed.add_field(
            name="Defaults",
            value=f"{DEFAULT_MAX_ACTIONS} actions in {DEFAULT_WINDOW_SECONDS}s",
            inline=False,
        )
        embed.add_field(
            name="Usage",
            value=(
                "`linkprotection window <seconds>`\n"
                "`linkprotection max <count>`\n"
                "`linkprotection reset`"
            ),
            inline=False,
        )
        await message.reply(embed=embed, mention_author=False)
        return True

    sub = args[1].lower()
    if sub == "reset":
        await storage.update_protection_settings(
            message.guild.id,
            window_seconds=None,
            max_actions=None,
        )
        await message.reply(
            "Sync protection thresholds reset to defaults.",
            mention_author=False,
        )
        return True

    if sub in ("window", "max"):
        if len(args) < 3:
            await message.reply(
                "**Usage:** `linkprotection window <seconds>` or `linkprotection max <count>`",
                mention_author=False,
            )
            return True

        try:
            value = int(args[2])
        except ValueError:
            await message.reply(
                "Value must be a whole number.",
                mention_author=False,
            )
            return True

        if value <= 0:
            await message.reply(
                "Value must be greater than 0.",
                mention_author=False,
            )
            return True

        if sub == "window":
            await storage.update_protection_settings(message.guild.id, window_seconds=value)
            await message.reply(
                f"Set protection window to {value} seconds.",
                mention_author=False,
            )
        else:
            await storage.update_protection_settings(message.guild.id, max_actions=value)
            await message.reply(
                f"Set max actions to {value}.",
                mention_author=False,
            )
        return True

    await message.reply(
        "Unknown subcommand. Use `linkprotection` to view usage.",
        mention_author=False,
    )
    return True


# ─── Help Command ─────────────────────────────────────────────────────────────


async def handle_serverlink_help(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: serverlink help"""
    content = message.content.strip().lower()
    if content != "serverlink help":
        return False

    if not message.guild:
        return False

    if not await _check_module_enabled(message):
        return True

    await _cmd_help(message)
    return True


# ─── Main Handler ─────────────────────────────────────────────────────────────


async def handle_server_link_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Main handler for all server link commands.

    Returns True if the command was handled.
    """
    if not message.guild:
        return False

    handlers = [
        handle_serverlink_help,
        handle_linkserver_command,
        handle_addlink_command,
        handle_links_command,
        handle_unlink_command,
        handle_linksettings_command,
        handle_linkprotection_command,
    ]

    for handler in handlers:
        if await handler(message, bot):
            return True

    return False
