"""
Roles module - temporary roles, role requests, bundles, and reaction roles.

Provides role management features including temporary assignments and automated distribution.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

import discord

from core.help_system import help_system
from core.permissions import is_module_enabled
from core.roles_storage import RolesStore
from core.utils import dt_to_iso, extract_first_message_link, iso_to_dt, parse_deadline, parse_duration_extended

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
            ("reactionrole add <message_link> <emoji> @role", "Add reaction role mapping (mod only)"),
            ("reactionrole remove <message_link> <emoji>", "Remove reaction role mapping (mod only)"),
            ("reactionrole list <message_link>", "List reaction roles on a message"),
            ("reactionrole help", "Show this help message"),
        ],
        group="Roles",
        hidden=True,
    )


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
        await _handle_reactionrole_add(message, parts)
    elif subcommand == "remove":
        await _handle_reactionrole_remove(message, parts)
    elif subcommand == "list":
        await _handle_reactionrole_list(message, parts)
    else:
        await message.reply(" Usage: `reactionrole <add|remove|list> ...`")


def _parse_message_id_arg(message: discord.Message, arg: str) -> Optional[int]:
    arg = (arg or "").strip()
    if not arg:
        return None
    if arg.isdigit():
        try:
            return int(arg)
        except Exception:
            return None
    trip = extract_first_message_link(arg, message.guild.id)
    if not trip:
        return None
    _gid, _cid, mid = trip
    try:
        return int(mid)
    except Exception:
        return None


async def _handle_reactionrole_add(message: discord.Message, parts: list[str]) -> None:
    """Add reaction role mapping."""
    if len(parts) < 5 or not message.role_mentions:
        await message.reply(" Usage: `reactionrole add <message_link> <emoji> @role`")
        return

    message_id = _parse_message_id_arg(message, parts[2])
    if not message_id:
        await message.reply(" Invalid message link or message ID.")
        return

    emoji = parts[3]
    role = message.role_mentions[0]

    store = RolesStore(message.guild.id)
    await store.initialize()
    await store.add_reaction_role(message_id, emoji, role.id)
    await message.reply(f" Reaction role added: {emoji} → {role.mention} (message `{message_id}`)")


async def _handle_reactionrole_remove(message: discord.Message, parts: list[str]) -> None:
    """Remove reaction role mapping."""
    if len(parts) < 4:
        await message.reply(" Usage: `reactionrole remove <message_link> <emoji>`")
        return

    message_id = _parse_message_id_arg(message, parts[2])
    if not message_id:
        await message.reply(" Invalid message link or message ID.")
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
        await message.reply(" Usage: `reactionrole list <message_link>`")
        return

    message_id = _parse_message_id_arg(message, parts[2])
    if not message_id:
        await message.reply(" Invalid message link or message ID.")
        return

    store = RolesStore(message.guild.id)
    await store.initialize()
    mappings = await store.get_all_reaction_roles(message_id)
    if not mappings:
        await message.reply(" No reaction roles configured for that message.")
        return

    lines = [f"**Reaction Roles for `{message_id}`**"]
    for emoji, rid in list(mappings.items())[:25]:
        lines.append(f"- {emoji} → <@&{rid}>")
    await message.reply("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())


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
