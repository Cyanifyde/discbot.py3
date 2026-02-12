"""
Roles module - temporary roles, role requests, bundles, and reaction roles.

Provides role management features including temporary assignments and automated distribution.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

import discord

from core.help_system import help_system
from core.permissions import is_module_enabled
from core.command_registry import command_registry, CommandRoute
from core.roles_storage import RolesStore
from core.utils import dt_to_iso, iso_to_dt, parse_deadline, parse_duration_extended

logger = logging.getLogger("discbot.roles")

MODULE_NAME = "roles"


def setup_roles() -> None:
    """Register help information for the roles module."""
    help_system.register_module(
        name="Roles",
        description="Role management with temporary assignments, requests, and bundles.",
        help_command="roles help",
        commands=[
            ("temprole help", "Temporary role commands (mod only)"),
            ("requestrole help", "Role request commands"),
            ("approverole help", "Approve/deny role requests (mod only)"),
            ("rolebundle help", "Role bundle commands (mod only)"),
            ("reactionrole help", "Reaction role setup commands (mod only)"),
            ("roles help", "Show this help message"),
        ],
    )

    help_system.register_module(
        name="Temp Roles",
        description="Temporary role assignment tools (mod only).",
        help_command="temprole help",
        commands=[
            ("temprole @user @role <duration>", "Give temporary role (mod only)"),
            ("temprole list", "List temporary roles (mod only)"),
            ("temprole remove <id>", "Remove a temporary role (mod only)"),
            ("temprole extend <id> <duration>", "Extend a temporary role expiry (mod only)"),
            ("temprole help", "Show this help message"),
        ],
        group="Roles",
        hidden=True,
    )

    help_system.register_module(
        name="Role Requests",
        description="Request roles and let moderators approve/deny them.",
        help_command="requestrole help",
        commands=[
            ("requestrole @role [reason]", "Request a role"),
            ("requestrole list", "List pending role requests (mod only)"),
            ("requestrole help", "Show this help message"),
        ],
        group="Roles",
        hidden=True,
    )

    help_system.register_module(
        name="Approve Role Requests",
        description="Moderator tools for approving/denying role requests.",
        help_command="approverole help",
        commands=[
            ("approverole <id> approve", "Approve a role request (mod only)"),
            ("approverole <id> deny", "Deny a role request (mod only)"),
            ("approverole help", "Show this help message"),
        ],
        group="Roles",
        hidden=True,
    )

    help_system.register_module(
        name="Role Bundles",
        description="Create bundles of roles and apply them to users (mod only).",
        help_command="rolebundle help",
        commands=[
            ("rolebundle create <name> @role1 @role2...", "Create role bundle (mod only)"),
            ("rolebundle give @user <bundle_name>", "Give role bundle (mod only)"),
            ("rolebundle list", "List role bundles"),
            ("rolebundle remove <bundle_name>", "Remove role bundle (mod only)"),
            ("rolebundle help", "Show this help message"),
        ],
        group="Roles",
        hidden=True,
    )

    help_system.register_module(
        name="Reaction Roles",
        description="Reaction roles: users react to a message to receive roles.",
        help_command="reactionrole help",
        commands=[
            (
                "reactionrole add <channel_id/message_id> <emoji> <role_id|@role> [custom text]",
                "Add a reaction role mapping to an existing message (mod only)",
            ),
            (
                "reactionrole create <channel_id> <emoji> <role_id|@role> [custom text]",
                "Create a reaction role message in a channel (mod only)",
            ),
            ("reactionrole remove <channel_id/message_id> <emoji>", "Remove reaction role mapping (mod only)"),
            ("reactionrole list <channel_id/message_id>", "List reaction roles on a message"),
            ("reactionrole order <channel_id/message_id> <emoji1> <emoji2> ...", "Set display order of reaction roles (mod only)"),
            ("reactionrole purge [channel_id]", "Remove all reaction roles (from server or specific channel) (mod only)"),
            ("reactionrole help", "Show this help message"),
        ],
        group="Roles",
        hidden=True,
    )

    command_registry.register(CommandRoute(
        name="roles",
        roots=["roles", "temprole", "requestrole", "approverole", "rolebundle", "reactionrole"],
        handler=handle_roles_command,
        needs_bot=True,
    ))


async def handle_roles_command(message: discord.Message, bot: discord.Client) -> bool:
    """
    Handle roles-related commands.

    Returns True if command was handled, False otherwise.
    """
    if not message.guild:
        return False

    # Check if module is enabled
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False

    content = (message.content or "").strip()
    if not content:
        return False

    parts = content.split()
    command = parts[0].lower()

    # Umbrella + per-subcommand help
    if command == "roles" and len(parts) >= 2 and parts[1].lower() == "help":
        embed = help_system.get_module_help("Roles")
        if embed:
            await message.reply(embed=embed)
        else:
            await message.reply(" Help information not available.")
        return True

    if len(parts) >= 2 and parts[1].lower() == "help":
        target_map = {
            "temprole": "Temp Roles",
            "requestrole": "Role Requests",
            "approverole": "Approve Role Requests",
            "rolebundle": "Role Bundles",
            "reactionrole": "Reaction Roles",
        }
        if command in target_map:
            embed = help_system.get_module_help(target_map[command])
            if embed:
                await message.reply(embed=embed)
            else:
                await message.reply(" Help information not available.")
            return True

    # Route to handlers
    if command == "temprole":
        await _handle_temprole(message, parts, bot)
        return True
    elif command == "requestrole":
        await _handle_requestrole(message, parts)
        return True
    elif command == "approverole":
        await _handle_approverole(message, parts, bot)
        return True
    elif command == "rolebundle":
        await _handle_rolebundle(message, parts, bot)
        return True
    elif command == "reactionrole":
        await _handle_reactionrole(message, parts, bot)
        return True

    return False


# ─── Temporary Roles ──────────────────────────────────────────────────────────


async def _handle_temprole(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle temporary role commands."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to use this command.")
        return

    if len(parts) < 2:
        await message.reply(" Usage: `temprole @user @role <duration>` | `temprole list` | `temprole remove <id>` | `temprole extend <id> <duration>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "list":
        await _handle_temprole_list(message)
    elif subcommand == "remove":
        await _handle_temprole_remove(message, parts, bot)
    elif subcommand == "extend":
        await _handle_temprole_extend(message, parts)
    else:
        await _handle_temprole_add(message, parts, bot)


async def _handle_temprole_add(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Add a temporary role."""
    if not message.mentions or not message.role_mentions or len(parts) < 4:
        await message.reply(" Usage: `temprole @user @role <duration>`")
        return

    user = message.mentions[0]
    role = message.role_mentions[0]
    duration_str = parts[-1]

    # Parse duration
    expires_at = parse_deadline(duration_str)
    if not expires_at:
        await message.reply(" Invalid duration. Try: `3d`, `2w`, `1mo`")
        return

    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    # Add role to user
    try:
        await user.add_roles(role, reason=f"Temporary role (expires {expires_at})")
    except discord.Forbidden:
        await message.reply(" I don't have permission to add that role")
        return

    # Store temporary role
    temp_role = await store.add_temp_role(
        user.id,
        role.id,
        dt_to_iso(expires_at),
        f"Added by {message.author.display_name}",
    )

    await message.reply(
        f" Gave {user.mention} the {role.mention} role temporarily\n"
        f"**ID:** `{temp_role.get('id', '')[:8]}`\n"
        f"**Expires:** {expires_at.strftime('%Y-%m-%d %H:%M')}"
    )


async def _handle_temprole_list(message: discord.Message) -> None:
    """List temporary roles."""
    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    temp_roles = await store.get_temp_roles()

    if not temp_roles:
        await message.reply(" No temporary roles")
        return

    embed = discord.Embed(
        title="Temporary Roles",
        description=f"Total: {len(temp_roles)}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow(),
    )

    for tr in temp_roles[:10]:
        tr_id = (tr.get("id") or "")[:8]
        value = (
            f"**ID:** `{tr_id}`\n"
            f"**User:** <@{tr['user_id']}>\n"
            f"**Role:** <@&{tr['role_id']}>\n"
            f"**Expires:** {tr['expires_at'][:16]}"
        )

        embed.add_field(
            name="Temporary Role",
            value=value,
            inline=False,
        )

    await message.reply(embed=embed)

async def _handle_temprole_remove(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Remove a temporary role by ID."""
    if len(parts) < 3:
        await message.reply(" Usage: `temprole remove <id>`")
        return

    temp_id = parts[2].strip()
    store = RolesStore(message.guild.id)
    await store.initialize()
    removed = await store.remove_temp_role_by_id(temp_id)
    if not removed:
        await message.reply(f" No temporary role found with ID starting with `{temp_id}`")
        return

    user_id = int(removed.get("user_id", 0) or 0)
    role_id = int(removed.get("role_id", 0) or 0)
    member = message.guild.get_member(user_id)
    if member is None:
        try:
            member = await message.guild.fetch_member(user_id)
        except Exception:
            member = None

    role = message.guild.get_role(role_id)
    if member and role:
        try:
            await member.remove_roles(role, reason=f"Temp role removed by {message.author.id}")
        except discord.Forbidden:
            await message.reply(" Removed from storage, but I can't remove the role due to permissions.")
            return
        except Exception:
            pass

    await message.reply(f" Temporary role removed. (`{(removed.get('id') or '')[:8]}`)")


async def _handle_temprole_extend(message: discord.Message, parts: list[str]) -> None:
    """Extend a temporary role expiry by duration."""
    if len(parts) < 4:
        await message.reply(" Usage: `temprole extend <id> <duration>`")
        return

    temp_id = parts[2].strip()
    duration_str = parts[3].strip()
    delta = parse_duration_extended(duration_str)
    if not delta:
        await message.reply(" Invalid duration. Try: `3d`, `2w`, `1mo`")
        return

    store = RolesStore(message.guild.id)
    await store.initialize()
    tr = await store.get_temp_role(temp_id)
    if not tr:
        await message.reply(f" No temporary role found with ID starting with `{temp_id}`")
        return

    current = iso_to_dt(tr.get("expires_at"))
    if current is None:
        await message.reply(" This temporary role has an invalid expiration timestamp.")
        return

    new_expires = current + delta
    updated = await store.extend_temp_role(temp_id, dt_to_iso(new_expires) or tr.get("expires_at", ""))
    if not updated:
        await message.reply(" Failed to update expiration.")
        return

    await message.reply(
        f" Updated expiry for `{(updated.get('id') or '')[:8]}`\n"
        f"**New Expires:** {new_expires.strftime('%Y-%m-%d %H:%M')}"
    )


# ─── Role Requests ────────────────────────────────────────────────────────────


async def _handle_requestrole(message: discord.Message, parts: list[str]) -> None:
    """Handle role request."""
    if len(parts) >= 2 and parts[1].lower() == "list":
        await _handle_requestrole_list(message)
        return

    if not message.role_mentions:
        await message.reply(" Usage: `requestrole @role [reason]`")
        return

    role = message.role_mentions[0]
    reason = " ".join(parts[2:]).strip() if len(parts) > 2 else ""

    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    request_id = str(uuid.uuid4())
    request = await store.add_role_request(
        request_id,
        message.author.id,
        role.id,
        reason,
    )

    await message.reply(
        f" Role request submitted! ID: `{request_id[:8]}`\n"
        f"**Role:** {role.mention}\n"
        f"Moderators will review your request."
    )

async def _handle_requestrole_list(message: discord.Message) -> None:
    """List pending role requests (mod only)."""
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to list role requests.")
        return

    store = RolesStore(message.guild.id)
    await store.initialize()
    pending = await store.get_pending_requests()
    if not pending:
        await message.reply(" No pending role requests.")
        return

    embed = discord.Embed(
        title="Pending Role Requests",
        description=f"Total: {len(pending)}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    for req in pending[:10]:
        rid = (req.get("id") or "")[:8]
        role_id = req.get("role_id")
        user_id = req.get("user_id")
        reason = (req.get("reason") or "").strip()
        value = f"**User:** <@{user_id}>\n**Role:** <@&{role_id}>"
        if reason:
            value += f"\n**Reason:** {reason[:200]}"
        embed.add_field(name=f"`{rid}`", value=value, inline=False)

    await message.reply(embed=embed)


async def _handle_approverole(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle role request approval."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to approve role requests.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `approverole <id> approve|deny`")
        return

    request_id = parts[1]
    action = parts[2].lower()

    if action not in ["approve", "deny"]:
        await message.reply(" Action must be 'approve' or 'deny'")
        return

    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    # Update request status
    updated = await store.update_request_status(
        request_id,
        "approved" if action == "approve" else "denied",
        message.author.id,
    )

    if not updated:
        await message.reply(f" No request found with ID starting with `{request_id}`")
        return

    if action == "approve":
        user_id = int(updated.get("user_id", 0) or 0)
        role_id = int(updated.get("role_id", 0) or 0)
        member = message.guild.get_member(user_id)
        if member is None:
            try:
                member = await message.guild.fetch_member(user_id)
            except Exception:
                member = None
        role = message.guild.get_role(role_id)
        if not member or not role:
            await message.reply(" Approved, but I couldn't find the user or role to assign.")
            return
        try:
            await member.add_roles(role, reason=f"Role request approved by {message.author.id}")
        except discord.Forbidden:
            await message.reply(" Approved, but I don't have permission to assign that role.")
            return

    await message.reply(f" Role request {action}d (`{(updated.get('id') or '')[:8]}`)")


# ─── Role Bundles ─────────────────────────────────────────────────────────────


async def _handle_rolebundle(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle role bundle commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `rolebundle <create|give|list|remove>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "create":
        await _handle_rolebundle_create(message, parts)
    elif subcommand == "give":
        await _handle_rolebundle_give(message, parts, bot)
    elif subcommand == "list":
        await _handle_rolebundle_list(message)
    elif subcommand == "remove":
        await _handle_rolebundle_remove(message, parts)
    else:
        await message.reply(" Usage: `rolebundle <create|give|list|remove>`")


async def _handle_rolebundle_create(message: discord.Message, parts: list[str]) -> None:
    """Create a role bundle."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to create role bundles.")
        return

    if len(parts) < 3 or not message.role_mentions:
        await message.reply(" Usage: `rolebundle create <name> @role1 @role2...`")
        return

    bundle_name = parts[2]
    role_ids = [r.id for r in message.role_mentions]

    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    bundle_id = str(uuid.uuid4())
    bundle = await store.add_bundle(bundle_id, bundle_name, role_ids)

    roles_str = ", ".join(f"<@&{rid}>" for rid in role_ids)
    await message.reply(
        f" Role bundle created! ID: `{bundle_id[:8]}`\n"
        f"**Name:** {bundle_name}\n"
        f"**Roles:** {roles_str}"
    )


async def _handle_rolebundle_give(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Give a role bundle to a user."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to give role bundles.")
        return

    if not message.mentions or len(parts) < 4:
        await message.reply(" Usage: `rolebundle give @user <bundle_name>`")
        return

    user = message.mentions[0]
    bundle_name = parts[3]

    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    bundle = await store.get_bundle(bundle_name)
    if not bundle:
        await message.reply(f" No bundle found with name `{bundle_name}`")
        return

    # Add all roles in bundle
    roles = [message.guild.get_role(rid) for rid in bundle["role_ids"]]
    roles = [r for r in roles if r is not None]

    try:
        await user.add_roles(*roles, reason=f"Role bundle: {bundle['name']}")
        roles_str = ", ".join(r.mention for r in roles)
        await message.reply(f" Gave {user.mention} the **{bundle['name']}** bundle\n**Roles:** {roles_str}")
    except discord.Forbidden:
        await message.reply(" I don't have permission to add those roles")

async def _handle_rolebundle_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove a role bundle."""
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to remove role bundles.")
        return

    if len(parts) < 3:
        await message.reply(" Usage: `rolebundle remove <bundle_name>`")
        return

    target = parts[2]
    store = RolesStore(message.guild.id)
    await store.initialize()
    removed = await store.remove_bundle(target)
    if not removed:
        await message.reply(f" No bundle found with name/ID `{target}`")
        return
    await message.reply(f" Role bundle removed: **{removed.get('name', 'Unknown')}** (`{(removed.get('id') or '')[:8]}`)")


async def _handle_rolebundle_list(message: discord.Message) -> None:
    """List role bundles."""
    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    bundles = await store.get_all_bundles()

    if not bundles:
        await message.reply(" No role bundles configured")
        return

    embed = discord.Embed(
        title="Role Bundles",
        description=f"Total: {len(bundles)}",
        color=discord.Color.purple(),
        timestamp=discord.utils.utcnow(),
    )

    for bundle in bundles[:10]:
        roles_str = ", ".join(f"<@&{rid}>" for rid in bundle["role_ids"])
        embed.add_field(
            name=bundle["name"],
            value=f"**ID:** `{bundle.get('id','')[:8]}`\n**Roles:** {roles_str}",
            inline=False,
        )

    await message.reply(embed=embed)


# ─── Reaction Roles ───────────────────────────────────────────────────────────


async def _handle_reactionrole(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle reaction role setup."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to setup reaction roles.")
        return

    if len(parts) < 2:
        await message.reply(" Usage: `reactionrole <add|remove|list> ...`")
        return

    subcommand = parts[1].lower()

    if subcommand == "add":
        await _handle_reactionrole_add(message, parts, bot)
    elif subcommand == "create":
        await _handle_reactionrole_create(message, parts, bot)
    elif subcommand == "remove":
        await _handle_reactionrole_remove(message, parts)
    elif subcommand == "list":
        await _handle_reactionrole_list(message, parts)
    elif subcommand == "order":
        await _handle_reactionrole_order(message, parts, bot)
    elif subcommand == "purge":
        await _handle_reactionrole_purge(message, parts, bot)
    else:
        await message.reply(" Usage: `reactionrole <add|create|remove|list|order|purge> ...`")


_CHANNEL_MSG_REF_RE = re.compile(
    r"(?:<#)?(\d+)>?\s*/\s*(\d+)"
)


def _parse_channel_message_ref(arg: str) -> tuple[Optional[int], Optional[int]]:
    """Parse a channel_id/message_id reference.

    Accepted formats:
      - 123456/789012
      - <#123456>/789012
    Returns (channel_id, message_id) or (None, None).
    """
    arg = (arg or "").strip()
    if not arg:
        return None, None
    m = _CHANNEL_MSG_REF_RE.search(arg)
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except Exception:
        return None, None


_ROLE_MENTION_RE = re.compile(r"^<@&(\d+)>$")
_CHANNEL_MENTION_RE = re.compile(r"^<#(\d+)>$")


def _extract_role_id_token(token: str) -> Optional[int]:
    """Extract a role ID from either a raw ID or a role mention token (<@&id>)."""
    token = (token or "").strip()
    if not token:
        return None

    if token.isdigit():
        try:
            return int(token)
        except Exception:
            return None
    m = _ROLE_MENTION_RE.match(token)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _extract_channel_id_token(token: str) -> Optional[int]:
    """Extract a channel ID from either a raw ID or a channel mention token (<#id>)."""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        try:
            return int(token)
        except Exception:
            return None
    m = _CHANNEL_MENTION_RE.match(token)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


async def _create_reaction_role_embed(guild: discord.Guild, mappings: dict[str, Any]) -> discord.Embed:
    """Create a reaction role embed from emoji->role_id/role_data mappings."""
    embed = discord.Embed(
        title="Reaction Roles",
        description="React to this message to get or remove roles!\nClick an emoji below to toggle the corresponding role.",
        color=discord.Color.blurple(),
    )

    if not mappings:
        embed.add_field(
            name="No roles configured",
            value="Contact a moderator to set up reaction roles.",
            inline=False,
        )
        return embed

    # Build role list with emoji
    role_lines: list[str] = []
    for emoji, value in list(mappings.items())[:25]:
        # Handle both old format (int) and new format (dict with role_id and text)
        if isinstance(value, dict):
            role_id = value.get("role_id")
            custom_text = value.get("text")
        else:
            role_id = value
            custom_text = None

        if custom_text:
            # Use custom text instead of role mention
            role_lines.append(f"{emoji} • {custom_text}")
        else:
            # Use role mention (old behavior)
            role = guild.get_role(int(role_id)) if role_id else None
            if role:
                role_lines.append(f"{emoji} • {role.mention}")
            else:
                role_lines.append(f"{emoji} • <@&{role_id}> *(role not found)*")

    embed.add_field(
        name="Available Roles",
        value="\n".join(role_lines) if role_lines else "No roles available",
        inline=False,
    )

    embed.set_footer(text="React below to receive your roles!")
    return embed


async def _maybe_add_reaction_to_message(
    bot: discord.Client,
    guild_id: int,
    channel_id: Optional[int],
    message_id: int,
    emoji: str,
) -> None:
    """Best-effort: add a reaction to an existing message (only possible with a message link)."""
    if not channel_id:
        return
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception:
            return
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        target = await channel.fetch_message(int(message_id))
    except Exception:
        return
    try:
        await target.add_reaction(emoji)
        return
    except Exception:
        pass
    try:
        await target.add_reaction(discord.PartialEmoji.from_str(emoji))
    except Exception:
        return


async def _handle_reactionrole_add(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Add a single reaction role mapping to an existing message."""
    if len(parts) < 5:
        await message.reply(
            " Usage: `reactionrole add <channel_id/message_id> <emoji> <role_id|@role> [custom text]`"
        )
        return

    if not message.guild:
        await message.reply(" This command must be used in a server.")
        return

    # Parse channel_id/message_id from the argument
    channel_id, message_id = _parse_channel_message_ref(parts[2])
    if not channel_id or not message_id:
        await message.reply(
            " Invalid channel/message reference.\n"
            "**Expected format:** `channel_id/message_id` or `<#channel>/message_id`"
        )
        return

    emoji = parts[3]
    role_token = parts[4]

    # Custom text is everything after the role_id
    custom_text = " ".join(parts[5:]).strip() if len(parts) > 5 else None

    # Extract role ID from token (handles both raw ID and @role mention)
    role_id = _extract_role_id_token(role_token)
    if not role_id:
        await message.reply(" Invalid role ID or mention.")
        return

    role = message.guild.get_role(role_id)
    if not role:
        await message.reply(f" Role <@&{role_id}> not found in this server.")
        return

    # Fetch the target channel and message
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None
    if not isinstance(channel, discord.TextChannel):
        await message.reply(" Channel not found or not a text channel.")
        return

    # Verify channel belongs to this guild
    if channel.guild.id != message.guild.id:
        await message.reply(" That channel is not in this server.")
        return

    target_message: Optional[discord.Message] = None
    try:
        target_message = await channel.fetch_message(message_id)
    except discord.NotFound:
        await message.reply(f" Message `{message_id}` not found in <#{channel_id}>.")
        return
    except Exception:
        await message.reply(f" Failed to fetch message `{message_id}` from <#{channel_id}>.")
        return

    store = RolesStore(message.guild.id)
    await store.initialize()

    # Store mapping and add reaction
    await store.add_reaction_role(message_id, emoji, role.id, custom_text)
    await _maybe_add_reaction_to_message(bot, message.guild.id, channel_id, message_id, emoji)

    # If target message is from the bot, update its embed
    if bot.user and target_message.author.id == bot.user.id and target_message.embeds:
        try:
            all_mappings = await store.get_all_reaction_roles(message_id)
            updated_embed = await _create_reaction_role_embed(message.guild, all_mappings)
            await target_message.edit(embed=updated_embed)
        except Exception:
            pass  # If update fails, continue anyway

    # Send confirmation
    embed = discord.Embed(
        title="Reaction Role Added",
        description=f"Added reaction role to message `{message_id}`",
        color=discord.Color.green(),
    )

    display_text = custom_text if custom_text else role.mention
    embed.add_field(
        name="Mapping",
        value=f"{emoji} -> {display_text}",
        inline=False,
    )

    embed.add_field(
        name="Message Link",
        value=f"[Jump to message](https://discord.com/channels/{message.guild.id}/{channel_id}/{message_id})",
        inline=False,
    )

    await message.reply(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def _handle_reactionrole_create(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Create an embed message in a channel and attach a single reaction role to it."""
    if len(parts) < 5:
        await message.reply(
            " Usage: `reactionrole create <channel_id> <emoji> <role_id|@role> [custom text]`"
        )
        return

    if not message.guild:
        await message.reply(" This command must be used in a server.")
        return

    channel_id = _extract_channel_id_token(parts[2])
    if not channel_id:
        await message.reply(" Invalid channel ID.")
        return

    emoji = parts[3]
    role_token = parts[4]

    # Custom text is everything after the role_id
    custom_text = " ".join(parts[5:]).strip() if len(parts) > 5 else None

    # Extract role ID from token (handles both raw ID and @role mention)
    role_id = _extract_role_id_token(role_token)
    if not role_id:
        await message.reply(" Invalid role ID or mention.")
        return

    role = message.guild.get_role(role_id)
    if not role:
        await message.reply(f" Role <@&{role_id}> not found in this server.")
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None
    if channel is None:
        await message.reply(" I couldn't access that channel ID.")
        return

    # Must be able to send messages to the target.
    if not hasattr(channel, "send"):
        await message.reply(f" That channel type can't receive messages (`{type(channel).__name__}`).")
        return

    # Prevent cross-guild surprises if someone pastes an ID from another server.
    ch_guild = getattr(channel, "guild", None)
    if ch_guild is not None and ch_guild.id != message.guild.id:
        await message.reply(" That channel is not in this server.")
        return

    store = RolesStore(message.guild.id)
    await store.initialize()

    # Build the mappings dict for the embed (single entry)
    mappings: dict[str, Any] = {}
    if custom_text:
        mappings[emoji] = {"role_id": role_id, "text": custom_text}
    else:
        mappings[emoji] = role_id

    # Create the reaction role embed
    rr_embed = await _create_reaction_role_embed(message.guild, mappings)

    try:
        rr_message = await channel.send(embed=rr_embed, allowed_mentions=discord.AllowedMentions.none())  # type: ignore[no-any-return]
    except discord.Forbidden:
        await message.reply(" I don't have permission to send embeds in that channel (need Send Messages + Embed Links).")
        return
    except Exception:
        await message.reply(" Failed to post the reaction-role embed in that channel.")
        return

    # Store mapping and add reaction
    await store.add_reaction_role(rr_message.id, emoji, role.id, custom_text)

    reaction_success = False
    try:
        await rr_message.add_reaction(emoji)
        reaction_success = True
    except discord.Forbidden:
        pass
    except Exception:
        try:
            await rr_message.add_reaction(discord.PartialEmoji.from_str(emoji))
            reaction_success = True
        except Exception:
            pass

    # Send confirmation embed
    confirm_embed = discord.Embed(
        title="✅ Reaction Role Message Created",
        description=f"Created reaction role message in <#{channel_id}>",
        color=discord.Color.green(),
    )

    display_text = custom_text if custom_text else role.mention
    status = "" if reaction_success else " *(failed to add reaction)*"
    confirm_embed.add_field(
        name="Reaction Role",
        value=f"{emoji} → {display_text}{status}",
        inline=False,
    )

    confirm_embed.add_field(
        name="Message Link",
        value=f"[Jump to message](https://discord.com/channels/{message.guild.id}/{channel_id}/{rr_message.id})",
        inline=False,
    )

    if not reaction_success:
        confirm_embed.add_field(
            name="⚠️ Note",
            value="Failed to add the reaction emoji. You may need to add it manually or check if the emoji is valid.",
            inline=False,
        )

    await message.reply(embed=confirm_embed, allowed_mentions=discord.AllowedMentions.none())


async def _handle_reactionrole_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove reaction role mapping."""
    if len(parts) < 4:
        await message.reply(" Usage: `reactionrole remove <channel_id/message_id> <emoji>`")
        return

    channel_id, message_id = _parse_channel_message_ref(parts[2])
    if not channel_id or not message_id:
        await message.reply(
            " Invalid channel/message reference.\n"
            "**Expected format:** `channel_id/message_id` or `<#channel>/message_id`"
        )
        return

    emoji = parts[3]
    store = RolesStore(message.guild.id)
    await store.initialize()
    ok = await store.remove_reaction_role(message_id, emoji)
    if ok:
        await message.reply(f" Reaction role removed: {emoji} (message `{message_id}`)")
    else:
        await message.reply(" No matching reaction role mapping found.")


async def _handle_reactionrole_list(message: discord.Message, parts: list[str]) -> None:
    """List reaction roles for a message."""
    if len(parts) < 3:
        await message.reply(" Usage: `reactionrole list <channel_id/message_id>`")
        return

    channel_id, message_id = _parse_channel_message_ref(parts[2])
    if not channel_id or not message_id:
        await message.reply(
            " Invalid channel/message reference.\n"
            "**Expected format:** `channel_id/message_id` or `<#channel>/message_id`"
        )
        return

    store = RolesStore(message.guild.id)
    await store.initialize()
    mappings = await store.get_all_reaction_roles(message_id)
    if not mappings:
        await message.reply(" No reaction roles configured for that message.")
        return

    lines = [f"**Reaction Roles for `{message_id}`**"]
    for emoji, value in list(mappings.items())[:25]:
        if isinstance(value, dict):
            role_id = value.get("role_id")
            custom_text = value.get("text")
        else:
            role_id = value
            custom_text = None

        if custom_text:
            lines.append(f"- {emoji} → {custom_text} (<@&{role_id}>)")
        else:
            lines.append(f"- {emoji} → <@&{role_id}>")
    await message.reply("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())


async def _handle_reactionrole_order(
    message: discord.Message, parts: list[str], bot: discord.Client
) -> None:
    """Reorder reaction roles on a message."""
    if len(parts) < 4:
        await message.reply(
            " Usage: `reactionrole order <channel_id/message_id> <emoji1> <emoji2> ...`\n"
            "List the emojis in the order you want them displayed."
        )
        return

    if not message.guild:
        await message.reply(" This command must be used in a server.")
        return

    channel_id, message_id = _parse_channel_message_ref(parts[2])
    if not channel_id or not message_id:
        await message.reply(
            " Invalid channel/message reference.\n"
            "**Expected format:** `channel_id/message_id` or `<#channel>/message_id`"
        )
        return

    emoji_order = parts[3:]
    if not emoji_order:
        await message.reply(" Provide the emojis in the order you want.")
        return

    store = RolesStore(message.guild.id)
    await store.initialize()

    # Validate emojis exist
    current = await store.get_all_reaction_roles(message_id)
    if not current:
        await message.reply(" No reaction roles configured for that message.")
        return

    current_emojis = set(current.keys())
    given_emojis = set(emoji_order)

    missing = current_emojis - given_emojis
    extra = given_emojis - current_emojis
    if missing or extra or len(emoji_order) != len(current):
        lines = [" Emoji list doesn't match the existing reaction roles."]
        if missing:
            lines.append(f"**Missing:** {' '.join(missing)}")
        if extra:
            lines.append(f"**Unknown:** {' '.join(extra)}")
        lines.append(f"\n**Current emojis:** {' '.join(current.keys())}")
        await message.reply("\n".join(lines))
        return

    ok = await store.reorder_reaction_roles(message_id, emoji_order)
    if not ok:
        await message.reply(" Failed to reorder reaction roles.")
        return

    # Fetch the target message to reorder reactions and update embed
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None

    target: Optional[discord.Message] = None
    if isinstance(channel, discord.TextChannel):
        try:
            target = await channel.fetch_message(message_id)
        except Exception:
            pass

    if target:
        # Update the embed if the message is ours
        if bot.user and target.author.id == bot.user.id and target.embeds:
            try:
                new_mappings = await store.get_all_reaction_roles(message_id)
                updated_embed = await _create_reaction_role_embed(message.guild, new_mappings)
                await target.edit(embed=updated_embed)
            except Exception:
                pass

        # Remove all reactions then re-add in the new order
        try:
            await target.clear_reactions()
        except (discord.Forbidden, discord.HTTPException):
            pass

        for emoji in emoji_order:
            try:
                await target.add_reaction(emoji)
            except Exception:
                try:
                    await target.add_reaction(discord.PartialEmoji.from_str(emoji))
                except Exception:
                    pass

    # Show new order
    new_mappings = await store.get_all_reaction_roles(message_id)
    order_display = " ".join(new_mappings.keys())
    await message.reply(f" Reaction roles reordered: {order_display}")


async def _handle_reactionrole_purge(message: discord.Message, parts: list[str], bot: discord.Client) -> None:
    """Purge all reaction roles from the server or a specific channel."""
    if not message.guild:
        return

    store = RolesStore(message.guild.id)
    await store.initialize()

    # Check if a channel ID was provided
    channel_id: Optional[int] = None
    if len(parts) >= 3:
        channel_id = _extract_channel_id_token(parts[2])
        if not channel_id:
            await message.reply(" Invalid channel ID.")
            return

    # Get all reaction roles before purging
    all_data = await store.get_all_reaction_roles_data()

    if channel_id:
        # Filter to only messages in the specified channel
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception:
                pass

        if not isinstance(channel, discord.TextChannel):
            await message.reply(" Channel not found or not a text channel.")
            return

        # Check if channel is in the same guild
        if channel.guild.id != message.guild.id:
            await message.reply(" That channel is not in this server.")
            return

        # Count messages to purge in this channel
        messages_to_purge = []
        for msg_id_str in all_data.keys():
            try:
                msg_id = int(msg_id_str)
                # Try to fetch the message to check its channel
                try:
                    msg = await channel.fetch_message(msg_id)
                    if msg:
                        messages_to_purge.append(msg_id_str)
                except discord.NotFound:
                    # Message doesn't exist in this channel, skip
                    pass
                except Exception:
                    pass
            except ValueError:
                pass

        if not messages_to_purge:
            await message.reply(f" No reaction roles found in <#{channel_id}>.")
            return

        # Purge only messages from this channel
        removed_count = await store.purge_reaction_roles_by_messages(messages_to_purge)

        embed = discord.Embed(
            title="🗑️ Reaction Roles Purged",
            description=f"Removed all reaction roles from <#{channel_id}>",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Messages Cleared", value=str(removed_count), inline=False)
    else:
        # Purge all reaction roles from the entire server
        if not all_data:
            await message.reply(" No reaction roles configured in this server.")
            return

        total_messages = len(all_data)
        total_mappings = sum(len(mappings) if isinstance(mappings, dict) else 0 for mappings in all_data.values())

        # Clear all reaction roles
        await store.purge_all_reaction_roles()

        embed = discord.Embed(
            title="🗑️ All Reaction Roles Purged",
            description="Removed all reaction roles from this server",
            color=discord.Color.red(),
        )
        embed.add_field(name="Messages Cleared", value=str(total_messages), inline=True)
        embed.add_field(name="Total Mappings Removed", value=str(total_mappings), inline=True)

    await message.reply(embed=embed)


async def handle_reaction_role_event(
    payload: discord.RawReactionActionEvent,
    bot: discord.Client,
    *,
    added: bool,
) -> None:
    """Apply/remove reaction roles when users react/unreact."""
    if not payload.guild_id or not payload.user_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    # Ignore bot reactions (including ourselves)
    if bot.user and payload.user_id == bot.user.id:
        return

    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
    if member.bot:
        return

    if not await is_module_enabled(guild.id, MODULE_NAME):
        return

    emoji_key = str(payload.emoji)

    store = RolesStore(guild.id)
    await store.initialize()
    role_id = await store.get_reaction_role(payload.message_id, emoji_key)
    if not role_id:
        return

    role = guild.get_role(int(role_id))
    if role is None:
        return

    try:
        if added:
            await member.add_roles(role, reason="Reaction role")
        else:
            await member.remove_roles(role, reason="Reaction role removed")
    except discord.Forbidden:
        return
    except Exception:
        return


async def restore_reaction_roles(bot: discord.Client) -> None:
    """Restore reaction emojis to reaction role messages after bot restart."""
    logger.info("Restoring reaction roles...")
    restored_count = 0
    error_count = 0

    for guild in bot.guilds:
        if not await is_module_enabled(guild.id, MODULE_NAME):
            continue

        try:
            store = RolesStore(guild.id)
            await store.initialize()
            all_data = await store.get_all_reaction_roles_data()

            if not all_data:
                continue

            for msg_id_str, mappings in all_data.items():
                if not isinstance(mappings, dict):
                    continue

                try:
                    message_id = int(msg_id_str)
                except (ValueError, TypeError):
                    continue

                # Try to find the message in guild channels
                message: Optional[discord.Message] = None
                for channel in guild.text_channels:
                    try:
                        message = await channel.fetch_message(message_id)
                        break
                    except discord.NotFound:
                        continue
                    except (discord.Forbidden, discord.HTTPException):
                        continue
                    except Exception:
                        continue

                if not message:
                    continue

                # Add missing reactions
                for emoji in mappings.keys():
                    try:
                        # Check if reaction already exists
                        has_reaction = any(
                            str(reaction.emoji) == emoji for reaction in message.reactions
                        )
                        if not has_reaction:
                            try:
                                await message.add_reaction(emoji)
                                restored_count += 1
                            except Exception:
                                # Try as PartialEmoji
                                try:
                                    await message.add_reaction(discord.PartialEmoji.from_str(emoji))
                                    restored_count += 1
                                except Exception:
                                    error_count += 1
                    except Exception:
                        error_count += 1

        except Exception as e:
            logger.error("Error restoring reaction roles for guild %s: %s", guild.id, e)
            error_count += 1

    logger.info(
        "Reaction role restore complete (restored=%d, failed=%d)",
        restored_count,
        error_count,
    )
    if error_count > 0:
        logger.warning("Failed to restore %d reaction role emojis", error_count)
