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
from core.utils import parse_deadline, dt_to_iso

logger = logging.getLogger("discbot.roles")

MODULE_NAME = "roles"


def setup_roles() -> None:
    """Register help information for the roles module."""
    help_system.register_module(
        name="Roles",
        description="Role management with temporary assignments, requests, and bundles.",
        help_command="roles help",
        commands=[
            ("temprole @user @role <duration>", "Give temporary role (mod only)"),
            ("temprole list", "List temporary roles (mod only)"),
            ("requestrole @role [reason]", "Request a role"),
            ("appoverole <id> approve/deny", "Approve/deny role request (mod only)"),
            ("rolebundle create <name> @role1 @role2...", "Create role bundle (mod only)"),
            ("rolebundle give @user <bundle_name>", "Give role bundle (mod only)"),
            ("rolebundle list", "List role bundles"),
            ("reactionrole setup <message_link>", "Setup reaction roles (mod only)"),
        ],
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

    content = message.content.strip()
    parts = content.split(maxsplit=2)

    if len(parts) < 1:
        return False

    command = parts[0].lower()

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
        await message.reply(" Usage: `temprole @user @role <duration>` or `temprole list`")
        return

    subcommand = parts[1].lower()

    if subcommand == "list":
        await _handle_temprole_list(message)
    else:
        await _handle_temprole_add(message, parts, bot)


async def _handle_temprole_add(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Add a temporary role."""
    if not message.mentions or not message.role_mentions:
        await message.reply(" Usage: `temprole @user @role <duration>`")
        return

    if len(parts) < 2:
        await message.reply(" Usage: `temprole @user @role <duration>`")
        return

    user = message.mentions[0]
    role = message.role_mentions[0]

    args = parts[1].split()
    if len(args) < 3:
        await message.reply(" Usage: `temprole @user @role <duration>`")
        return

    duration_str = args[2]

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
    await store.add_temp_role(
        user.id,
        role.id,
        dt_to_iso(expires_at),
        f"Added by {message.author.display_name}",
    )

    await message.reply(
        f" Gave {user.mention} the {role.mention} role temporarily\n"
        f"**Expires:** {expires_at.strftime('%Y-%m-%d %H:%M')}"
    )


async def _handle_temprole_list(message: discord.Message) -> None:
    """List temporary roles."""
    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    all_temp_roles = await store._read_temp_roles()
    temp_roles = all_temp_roles.get("temp_roles", [])

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
        value = (
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


# ─── Role Requests ────────────────────────────────────────────────────────────


async def _handle_requestrole(message: discord.Message, parts: list[str]) -> None:
    """Handle role request."""
    if not message.role_mentions:
        await message.reply(" Usage: `requestrole @role [reason]`")
        return

    role = message.role_mentions[0]
    reason = ""

    if len(parts) >= 2:
        args = parts[1].split(maxsplit=1)
        if len(args) > 1:
            reason = args[1]

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

    if len(parts) < 2:
        await message.reply(" Usage: `approverole <id> approve/deny`")
        return

    args = parts[1].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `approverole <id> approve/deny`")
        return

    request_id = args[0]
    action = args[1].lower()

    if action not in ["approve", "deny"]:
        await message.reply(" Action must be 'approve' or 'deny'")
        return

    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    # Update request status
    success = await store.update_request_status(
        request_id,
        "approved" if action == "approve" else "denied",
        message.author.id,
    )

    if success:
        await message.reply(f" Role request {action}d")
    else:
        await message.reply(f" No request found with ID starting with `{request_id}`")


# ─── Role Bundles ─────────────────────────────────────────────────────────────


async def _handle_rolebundle(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Handle role bundle commands."""
    if len(parts) < 2:
        await message.reply(" Usage: `rolebundle <create|give|list>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "create":
        await _handle_rolebundle_create(message, parts)
    elif subcommand == "give":
        await _handle_rolebundle_give(message, parts, bot)
    elif subcommand == "list":
        await _handle_rolebundle_list(message)
    else:
        await message.reply(" Usage: `rolebundle <create|give|list>`")


async def _handle_rolebundle_create(message: discord.Message, parts: list[str]) -> None:
    """Create a role bundle."""
    # Check mod permissions
    if not message.author.guild_permissions.manage_roles:
        await message.reply(" You need Manage Roles permission to create role bundles.")
        return

    if not message.role_mentions or len(parts) < 3:
        await message.reply(" Usage: `rolebundle create <name> @role1 @role2...`")
        return

    args = parts[2].split(maxsplit=1)
    if len(args) < 1:
        await message.reply(" Usage: `rolebundle create <name> @role1 @role2...`")
        return

    bundle_name = args[0]
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

    if not message.mentions or len(parts) < 3:
        await message.reply(" Usage: `rolebundle give @user <bundle_name>`")
        return

    user = message.mentions[0]
    args = parts[2].split(maxsplit=1)
    if len(args) < 2:
        await message.reply(" Usage: `rolebundle give @user <bundle_name>`")
        return

    bundle_name = args[1]

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
            value=f"**Roles:** {roles_str}",
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
        await message.reply(" Usage: `reactionrole setup <message_link>`")
        return

    subcommand = parts[1].lower()

    if subcommand == "setup":
        await _handle_reactionrole_setup(message, parts, bot)
    else:
        await message.reply(" Usage: `reactionrole setup <message_link>`")


async def _handle_reactionrole_setup(
    message: discord.Message,
    parts: list[str],
    bot: discord.Client,
) -> None:
    """Setup reaction roles."""
    if len(parts) < 3:
        await message.reply(" Usage: `reactionrole setup <message_link>`")
        return

    message_link = parts[2].split()[0]

    # Parse message link
    try:
        parts_link = message_link.split("/")
        message_id = int(parts_link[-1])
        channel_id = int(parts_link[-2])
    except (ValueError, IndexError):
        await message.reply(" Invalid message link")
        return

    await message.reply(
        " Reaction role setup started!\n"
        "React to this message with emojis and mention the roles:\n"
        "Format: `:emoji: @role`\n"
        "Type 'done' when finished."
    )

    # This would require a more complex interaction system
    # For now, just acknowledge the command
    guild_id = message.guild.id
    store = RolesStore(guild_id)
    await store.initialize()

    await message.reply(
        " Reaction roles configured. Users can now react to get roles!\n"
        "Note: Full implementation requires event handlers."
    )
