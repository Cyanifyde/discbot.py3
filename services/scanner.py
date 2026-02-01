"""
Scanner service - controls the suspicious image hash scanning system.

This service allows moderators to enable/disable the hash-matching scanner
that detects suspicious images. The scanner doesn't auto-run by default.

Text commands:
    scanner enable   - Enable the scanner (if not already running)
    scanner disable  - Disable the scanner (stops processing, preserves queue)
    scanner status   - Check if scanner is enabled and show stats
    scanner reload   - Reload the hash list from file
    scanner stats    - Show detailed scanning statistics
    scanner help     - Show all scanner commands

The scanner state is persisted in guild config and survives bot restarts.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord

from core.config_migration import get_guild_module_data, update_guild_module_data
from core.hashes import load_hashes
from core.utils import utcnow

if TYPE_CHECKING:
    from bot.client import DiscBot
    from bot.guild_state import GuildState

logger = logging.getLogger("discbot.scanner")

MODULE_NAME = "scanner"

COMMAND_PATTERN = re.compile(r"^scanner\s+(\w+)(?:\s+(.*))?$", re.IGNORECASE)

SUBCOMMANDS = {
    "enable", "disable", "status", "reload", "stats", "help",
    "setup", "addhash", "removehash", "listhashes", "clearhashes",
    "removerole", "addrole", "clearroles", "config",
}

# Default state structure
DEFAULT_STATE: Dict[str, Any] = {
    "enabled": False,
    "enabled_at": None,
    "enabled_by": None,
    "disabled_at": None,
    "disabled_by": None,
    "total_scans": 0,
    "total_matches": 0,
    "guild_hashes": [],       # Per-guild hash list
    "roles_to_remove": [],    # Role IDs to remove on match (empty = all roles)
    "roles_to_add": [],       # Role IDs to add on match
}


def _is_mod(member: discord.Member) -> bool:
    """Check if member has mod permissions."""
    perms = member.guild_permissions
    return (
        perms.administrator
        or perms.manage_guild
        or perms.manage_roles
        or perms.manage_messages
    )


async def get_state(guild_id: int) -> Dict[str, Any]:
    """Get the scanner state for a guild."""
    data = await get_guild_module_data(guild_id, MODULE_NAME)
    if data is None:
        return dict(DEFAULT_STATE)
    # Ensure all keys exist
    result = dict(DEFAULT_STATE)
    result.update(data)
    return result


async def set_enabled(guild_id: int, enabled: bool, user_id: int) -> Dict[str, Any]:
    """Set the scanner enabled state."""
    data = await get_state(guild_id)

    if enabled:
        data["enabled"] = True
        data["enabled_at"] = utcnow().isoformat()
        data["enabled_by"] = user_id
    else:
        data["enabled"] = False
        data["disabled_at"] = utcnow().isoformat()
        data["disabled_by"] = user_id

    await update_guild_module_data(guild_id, MODULE_NAME, data)
    return data


async def increment_stats(guild_id: int, scans: int = 0, matches: int = 0) -> None:
    """Increment scanner statistics."""
    data = await get_state(guild_id)
    data["total_scans"] = data.get("total_scans", 0) + scans
    data["total_matches"] = data.get("total_matches", 0) + matches
    await update_guild_module_data(guild_id, MODULE_NAME, data)


async def is_enabled(guild_id: int) -> bool:
    """Check if scanner is enabled for a guild."""
    data = await get_state(guild_id)
    return data.get("enabled", False)


async def handle_command(message: discord.Message, bot: "DiscBot") -> bool:
    """
    Handle scanner commands.

    Returns True if message was a scanner command (handled), False otherwise.
    """
    if not message.guild:
        return False

    content = message.content.strip()
    match = COMMAND_PATTERN.match(content)
    if not match:
        return False

    subcommand = match.group(1).lower()
    if subcommand not in SUBCOMMANDS:
        return False

    member = message.guild.get_member(message.author.id)
    if not member or not _is_mod(member):
        await message.reply(
            "You need moderator permissions to use scanner commands.",
            mention_author=False,
        )
        return True

    guild_id = message.guild.id
    state = bot.guild_states.get(guild_id)

    if subcommand == "help":
        await _cmd_help(message)
    elif subcommand == "enable":
        await _cmd_enable(message, bot, state)
    elif subcommand == "disable":
        await _cmd_disable(message, bot, state)
    elif subcommand == "status":
        await _cmd_status(message, bot, state)
    elif subcommand == "reload":
        await _cmd_reload(message, bot, state)
    elif subcommand == "stats":
        await _cmd_stats(message, bot, state)
    elif subcommand == "setup":
        await _cmd_setup(message)
    elif subcommand == "addhash":
        await _cmd_addhash(message, match.group(2), state)
    elif subcommand == "removehash":
        await _cmd_removehash(message, match.group(2), state)
    elif subcommand == "listhashes":
        await _cmd_listhashes(message)
    elif subcommand == "clearhashes":
        await _cmd_clearhashes(message, state)
    elif subcommand == "removerole":
        await _cmd_removerole(message, match.group(2))
    elif subcommand == "addrole":
        await _cmd_addrole(message, match.group(2))
    elif subcommand == "clearroles":
        await _cmd_clearroles(message)
    elif subcommand == "config":
        await _cmd_config(message)

    return True


async def _cmd_help(message: discord.Message) -> None:
    """Show help for scanner commands."""
    help_text = """**Image Scanner Commands**

**Basic:**
**`scanner enable`** - Enable the suspicious image scanner
**`scanner disable`** - Disable the scanner (queue is preserved)
**`scanner status`** - Check scanner status and basic info
**`scanner stats`** - Show detailed scanning statistics
**`scanner reload`** - Reload the global hash list from file

**Hash Management (per-guild):**
**`scanner setup`** - Show setup instructions
**`scanner addhash <hash>`** - Add a hash to this guild's list
**`scanner removehash <hash>`** - Remove a hash from this guild's list
**`scanner listhashes`** - List all guild-specific hashes
**`scanner clearhashes`** - Clear all guild-specific hashes

**Role Configuration:**
**`scanner removerole <role_id|all>`** - Add role to remove on match
**`scanner addrole <role_id>`** - Add role to give on match
**`scanner clearroles`** - Clear all configured roles
**`scanner config`** - Show current configuration

**`scanner help`** - Show this help message

**How it works:**
The scanner checks images against a database of known suspicious image hashes.
When a match is found, the configured action is taken (role removal, etc).

The scanner does **not** run automatically.
A moderator must enable it with `scanner enable`.
"""
    await message.reply(help_text, mention_author=False)


async def _cmd_enable(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Enable the scanner."""
    guild_id = message.guild.id

    current = await get_state(guild_id)
    if current.get("enabled"):
        await message.reply(
            "Scanner is already enabled and running!",
            mention_author=False,
        )
        return

    if not state:
        await message.reply(
            "Guild state not initialized. Please try again later.",
            mention_author=False,
        )
        return

    await set_enabled(guild_id, True, message.author.id)

    if state.queue_processor.stop_event.is_set() or state.queue_processor.reader_task is None:
        await state.queue_processor.start()
        logger.info(
            "Started scanner for guild %s by user %s",
            guild_id,
            message.author.id,
        )

    await message.reply(
        "**Image scanner enabled!**\n"
        "The scanner will now check images against the hash database.\n"
        f"Loaded **{len(state.hashes):,}** hashes.",
        mention_author=False,
    )


async def _cmd_disable(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Disable the scanner."""
    guild_id = message.guild.id

    current = await get_state(guild_id)
    if not current.get("enabled"):
        await message.reply(
            "Scanner is already disabled.",
            mention_author=False,
        )
        return

    await set_enabled(guild_id, False, message.author.id)
    logger.info(
        "Disabled scanner for guild %s by user %s",
        guild_id,
        message.author.id,
    )

    await message.reply(
        "**Image scanner disabled.**\n"
        "New images will not be scanned.\n"
        "Use `scanner enable` to re-enable.",
        mention_author=False,
    )


async def _cmd_status(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Show scanner status."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    enabled = data.get("enabled", False)
    status_text = "Enabled" if enabled else "Disabled"

    lines = [
        f"**Image Scanner Status: {status_text}**",
        "",
    ]

    if state:
        lines.append(f"**Loaded Hashes:** {len(state.hashes):,}")
        lines.append(f"**Queue Size:** {state.queue_processor.queued_jobs:,}")

        processor_running = (
            state.queue_processor.reader_task is not None
            and not state.queue_processor.stop_event.is_set()
        )
        proc_status = "Running" if processor_running else "Stopped"
        lines.append(f"**Processor:** {proc_status}")
    else:
        lines.append("Guild state not initialized")

    if data.get("enabled_by"):
        lines.append(f"\n**Last enabled by:** User ID {data['enabled_by']}")
        if data.get("enabled_at"):
            lines.append(f"**Enabled at:** {data['enabled_at']}")

    if data.get("disabled_by"):
        lines.append(f"\n**Last disabled by:** User ID {data['disabled_by']}")
        if data.get("disabled_at"):
            lines.append(f"**Disabled at:** {data['disabled_at']}")

    await message.reply(
        "\n".join(lines),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _cmd_stats(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Show detailed scanner stats."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    lines = [
        "**Image Scanner Statistics**",
        "",
        f"**Total Scans:** {data.get('total_scans', 0):,}",
        f"**Total Matches:** {data.get('total_matches', 0):,}",
    ]

    total_scans = data.get("total_scans", 0)
    total_matches = data.get("total_matches", 0)

    if total_scans > 0:
        match_rate = (total_matches / total_scans) * 100
        lines.append(f"**Match Rate:** {match_rate:.2f}%")

    if state:
        lines.append("")
        lines.append("**Current Session:**")
        lines.append(f"• Actions taken: {state.action_count:,}")
        lines.append(f"• Hashes loaded: {len(state.hashes):,}")
        lines.append(f"• Queue depth: {state.queue_processor.queued_jobs:,}")

        compactions = state.queue_store.state.get("compactions", 0)
        if compactions:
            lines.append(f"• Queue compactions: {compactions:,}")

    await message.reply("\n".join(lines), mention_author=False)


async def _cmd_reload(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Reload the hash list."""
    if not state:
        await message.reply(
            "Guild state not initialized.",
            mention_author=False,
        )
        return

    old_count = len(state.hashes)

    try:
        state.hashes = await load_hashes(state.config)
        new_count = len(state.hashes)

        diff = new_count - old_count
        if diff > 0:
            diff_text = f"(+{diff})"
        elif diff < 0:
            diff_text = f"({diff})"
        else:
            diff_text = "(no change)"

        logger.info(
            "Reloaded hashes for guild %s: %d -> %d",
            message.guild.id,
            old_count,
            new_count,
        )

        await message.reply(
            f"**Hash list reloaded!**\n"
            f"**Before:** {old_count:,} hashes\n"
            f"**After:** {new_count:,} hashes {diff_text}",
            mention_author=False,
        )
    except Exception as e:
        logger.error("Failed to reload hashes: %s", e)
        await message.reply(
            f"Failed to reload hashes: {e}",
            mention_author=False,
        )


async def _cmd_setup(message: discord.Message) -> None:
    """Show setup instructions."""
    help_text = """**Scanner Setup Instructions**

**1. Add image hashes to scan for:**
```
scanner addhash <sha256_hash>
```
Add SHA256 hashes of images you want to detect.
You can get a hash by scanning an image file.

**2. Configure roles to remove on match:**
```
scanner removerole <role_id>
```
Add a role ID that will be removed when a match is found.
Use `scanner removerole all` to remove ALL roles (except @everyone).

**3. Configure roles to add on match:**
```
scanner addrole <role_id>
```
Add a role ID that will be given to users when matched.

**4. View current configuration:**
```
scanner config
```

**Example Setup:**
```
scanner addhash abc123def456...
scanner removerole 123456789012345678
scanner addrole 987654321098765432
scanner enable
```
"""
    await message.reply(help_text, mention_author=False)


async def _cmd_addhash(
    message: discord.Message,
    args: Optional[str],
    state: Optional["GuildState"],
) -> None:
    """Add a hash to the guild's hash list."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `scanner addhash <sha256_hash>`\n"
            "Provide a 64-character SHA256 hash.",
            mention_author=False,
        )
        return

    hash_value = args.strip().lower()

    # Validate hash format (SHA256 = 64 hex chars)
    if len(hash_value) != 64 or not all(c in "0123456789abcdef" for c in hash_value):
        await message.reply(
            "Invalid hash format. Must be a 64-character SHA256 hash (hex).",
            mention_author=False,
        )
        return

    guild_hashes = list(data.get("guild_hashes", []))

    if hash_value in guild_hashes:
        await message.reply(
            f"Hash `{hash_value[:16]}...` is already in the list.",
            mention_author=False,
        )
        return

    guild_hashes.append(hash_value)
    data["guild_hashes"] = guild_hashes
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    # Also add to runtime state if available
    if state:
        state.hashes.add(hash_value)

    await message.reply(
        f"Hash added: `{hash_value[:16]}...`\n"
        f"**Total guild hashes:** {len(guild_hashes)}",
        mention_author=False,
    )


async def _cmd_removehash(
    message: discord.Message,
    args: Optional[str],
    state: Optional["GuildState"],
) -> None:
    """Remove a hash from the guild's hash list."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `scanner removehash <sha256_hash>`",
            mention_author=False,
        )
        return

    hash_value = args.strip().lower()
    guild_hashes = list(data.get("guild_hashes", []))

    if hash_value not in guild_hashes:
        await message.reply(
            f"Hash `{hash_value[:16]}...` not found in guild's hash list.",
            mention_author=False,
        )
        return

    guild_hashes.remove(hash_value)
    data["guild_hashes"] = guild_hashes
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    # Also remove from runtime state if available
    if state:
        state.hashes.discard(hash_value)

    await message.reply(
        f"Hash removed: `{hash_value[:16]}...`\n"
        f"**Remaining guild hashes:** {len(guild_hashes)}",
        mention_author=False,
    )


async def _cmd_listhashes(message: discord.Message) -> None:
    """List all guild-specific hashes."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    guild_hashes = data.get("guild_hashes", [])

    if not guild_hashes:
        await message.reply(
            "No guild-specific hashes configured.\n"
            "Use `scanner addhash <hash>` to add hashes.",
            mention_author=False,
        )
        return

    lines = [f"**Guild Hashes ({len(guild_hashes)} total):**", ""]

    # Show up to 20 hashes
    for i, h in enumerate(guild_hashes[:20]):
        lines.append(f"`{i + 1}.` `{h[:32]}...`")

    if len(guild_hashes) > 20:
        lines.append(f"\n*... and {len(guild_hashes) - 20} more*")

    await message.reply("\n".join(lines), mention_author=False)


async def _cmd_clearhashes(
    message: discord.Message,
    state: Optional["GuildState"],
) -> None:
    """Clear all guild-specific hashes."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    old_count = len(data.get("guild_hashes", []))
    data["guild_hashes"] = []
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    # Note: We don't remove from runtime state since global hashes should remain
    # Reload would be needed to refresh the full hash set

    await message.reply(
        f"**Cleared {old_count} guild-specific hashes.**\n"
        "Use `scanner reload` to refresh the hash list.",
        mention_author=False,
    )


async def _cmd_removerole(message: discord.Message, args: Optional[str]) -> None:
    """Add a role to the removal list."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `scanner removerole <role_id|all>`",
            mention_author=False,
        )
        return

    args = args.strip().lower()
    roles_to_remove = list(data.get("roles_to_remove", []))

    if args == "all":
        if "all" not in roles_to_remove:
            roles_to_remove.append("all")
        data["roles_to_remove"] = roles_to_remove
        await update_guild_module_data(guild_id, MODULE_NAME, data)
        await message.reply(
            "**Configured to remove ALL roles** on hash match.",
            mention_author=False,
        )
        return

    # Parse role ID
    role_id_str = args.strip("<@&>")
    if not role_id_str.isdigit():
        await message.reply(
            "Invalid role ID. Provide a numeric role ID or 'all'.",
            mention_author=False,
        )
        return

    role_id = int(role_id_str)

    # Verify role exists
    role = message.guild.get_role(role_id)
    if not role:
        await message.reply(
            f"Role with ID `{role_id}` not found in this server.",
            mention_author=False,
        )
        return

    if role_id in roles_to_remove:
        await message.reply(
            f"Role **{role.name}** is already in the removal list.",
            mention_author=False,
        )
        return

    roles_to_remove.append(role_id)
    data["roles_to_remove"] = roles_to_remove
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        f"Role **{role.name}** (`{role_id}`) will be removed on hash match.",
        mention_author=False,
    )


async def _cmd_addrole(message: discord.Message, args: Optional[str]) -> None:
    """Add a role to the add list."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    if not args:
        await message.reply(
            "Usage: `scanner addrole <role_id>`",
            mention_author=False,
        )
        return

    # Parse role ID
    role_id_str = args.strip().strip("<@&>")
    if not role_id_str.isdigit():
        await message.reply(
            "Invalid role ID. Provide a numeric role ID.",
            mention_author=False,
        )
        return

    role_id = int(role_id_str)

    # Verify role exists
    role = message.guild.get_role(role_id)
    if not role:
        await message.reply(
            f"Role with ID `{role_id}` not found in this server.",
            mention_author=False,
        )
        return

    roles_to_add = list(data.get("roles_to_add", []))
    if role_id in roles_to_add:
        await message.reply(
            f"Role **{role.name}** is already in the add list.",
            mention_author=False,
        )
        return

    roles_to_add.append(role_id)
    data["roles_to_add"] = roles_to_add
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        f"Role **{role.name}** (`{role_id}`) will be added on hash match.",
        mention_author=False,
    )


async def _cmd_clearroles(message: discord.Message) -> None:
    """Clear all configured roles."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    data["roles_to_remove"] = []
    data["roles_to_add"] = []
    await update_guild_module_data(guild_id, MODULE_NAME, data)

    await message.reply(
        "**All role configurations cleared.**\n"
        "No roles will be removed or added on hash match.",
        mention_author=False,
    )


async def _cmd_config(message: discord.Message) -> None:
    """Show current configuration."""
    guild_id = message.guild.id
    data = await get_state(guild_id)

    lines = ["**Scanner Configuration**", ""]

    # Guild hashes
    guild_hashes = data.get("guild_hashes", [])
    lines.append(f"**Guild Hashes:** {len(guild_hashes)}")

    # Roles to remove
    roles_to_remove = data.get("roles_to_remove", [])
    if not roles_to_remove:
        lines.append("**Roles to Remove:** None configured")
    elif "all" in roles_to_remove:
        lines.append("**Roles to Remove:** ALL roles")
    else:
        lines.append("**Roles to Remove:**")
        for role_id in roles_to_remove:
            if isinstance(role_id, int):
                role = message.guild.get_role(role_id)
                name = role.name if role else "Unknown"
                lines.append(f"• {name} (`{role_id}`)")

    # Roles to add
    roles_to_add = data.get("roles_to_add", [])
    if not roles_to_add:
        lines.append("\n**Roles to Add:** None configured")
    else:
        lines.append("\n**Roles to Add:**")
        for role_id in roles_to_add:
            if isinstance(role_id, int):
                role = message.guild.get_role(role_id)
                name = role.name if role else "Unknown"
                lines.append(f"• {name} (`{role_id}`)")

    await message.reply("\n".join(lines), mention_author=False)


async def restore_state(bot: "DiscBot") -> None:
    """
    Restore scanner state for all guilds on bot startup.

    Only starts the scanner for guilds where it was previously enabled.
    Also loads guild-specific hashes into runtime state.
    """
    for guild_id, state in bot.guild_states.items():
        try:
            data = await get_state(guild_id)

            # Load guild-specific hashes into runtime state
            guild_hashes = data.get("guild_hashes", [])
            if guild_hashes:
                for h in guild_hashes:
                    if isinstance(h, str):
                        state.hashes.add(h)
                logger.info(
                    "Loaded %d guild hashes for guild %s",
                    len(guild_hashes),
                    guild_id,
                )

            if data.get("enabled"):
                if (
                    state.queue_processor.stop_event.is_set()
                    or state.queue_processor.reader_task is None
                ):
                    await state.queue_processor.start()
                    logger.info(
                        "Restored scanner for guild %s (was enabled)",
                        guild_id,
                    )
        except Exception as e:
            logger.error("Failed to restore scanner state for guild %s: %s", guild_id, e)
