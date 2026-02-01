"""
Sus Scanner module - controls the suspicious image hash scanning system.

This module allows moderators to enable/disable the hash-matching scanner
that detects suspicious images. The scanner doesn't auto-run by default.

Text commands:
    sus enable   - Enable the scanner (if not already running)
    sus disable  - Disable the scanner (stops processing, but doesn't lose queue)
    sus status   - Check if scanner is enabled and show stats
    sus reload   - Reload the hash list from file
    sus stats    - Show detailed scanning statistics
    sus help     - Show all sus commands

The sus state is persisted in guild config and survives bot restarts.
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

logger = logging.getLogger("discbot.sus_scanner")

# Module name for config storage
MODULE_NAME = "sus_scanner"

# Command patterns
SUS_COMMAND_PATTERN = re.compile(
    r'^sus\s+(\w+)(?:\s+(.*))?$',
    re.IGNORECASE
)

# Available subcommands
SUBCOMMANDS = {"enable", "disable", "status", "reload", "stats", "help"}


def is_mod(member: discord.Member) -> bool:
    """Check if member has mod permissions."""
    perms = member.guild_permissions
    return (
        perms.administrator or
        perms.manage_guild or
        perms.manage_roles or
        perms.manage_messages
    )


async def get_sus_state(guild_id: int) -> Dict[str, Any]:
    """Get the sus scanner state for a guild."""
    data = await get_guild_module_data(guild_id, MODULE_NAME)
    if data is None:
        return {
            "enabled": False,
            "enabled_at": None,
            "enabled_by": None,
            "disabled_at": None,
            "disabled_by": None,
            "total_scans": 0,
            "total_matches": 0,
        }
    return data


async def set_sus_enabled(
    guild_id: int,
    enabled: bool,
    user_id: int,
) -> Dict[str, Any]:
    """Set the sus scanner enabled state."""
    data = await get_sus_state(guild_id)
    
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


async def increment_sus_stats(
    guild_id: int,
    scans: int = 0,
    matches: int = 0,
) -> None:
    """Increment sus scanner statistics."""
    data = await get_sus_state(guild_id)
    data["total_scans"] = data.get("total_scans", 0) + scans
    data["total_matches"] = data.get("total_matches", 0) + matches
    await update_guild_module_data(guild_id, MODULE_NAME, data)


async def is_sus_enabled(guild_id: int) -> bool:
    """Check if sus scanner is enabled for a guild."""
    data = await get_sus_state(guild_id)
    return data.get("enabled", False)


async def handle_sus_command(
    message: discord.Message,
    bot: "DiscBot",
) -> bool:
    """
    Handle sus commands.
    
    Returns True if message was a sus command (handled), False otherwise.
    """
    if not message.guild:
        return False
    
    content = message.content.strip()
    match = SUS_COMMAND_PATTERN.match(content)
    if not match:
        return False
    
    subcommand = match.group(1).lower()
    # args = match.group(2)  # For future use
    
    if subcommand not in SUBCOMMANDS:
        return False
    
    # Check permissions
    member = message.guild.get_member(message.author.id)
    if not member or not is_mod(member):
        await message.reply(
            "❌ You need moderator permissions to use sus commands.",
            mention_author=False,
        )
        return True
    
    guild_id = message.guild.id
    state = bot.guild_states.get(guild_id)
    
    if subcommand == "help":
        await _handle_help(message)
    elif subcommand == "enable":
        await _handle_enable(message, bot, state)
    elif subcommand == "disable":
        await _handle_disable(message, bot, state)
    elif subcommand == "status":
        await _handle_status(message, bot, state)
    elif subcommand == "reload":
        await _handle_reload(message, bot, state)
    elif subcommand == "stats":
        await _handle_stats(message, bot, state)
    
    return True


async def _handle_help(message: discord.Message) -> None:
    """Show help for sus commands."""
    help_text = """**🔍 Sus Scanner Commands**

**`sus enable`** - Enable the suspicious image scanner
**`sus disable`** - Disable the scanner (queue is preserved)
**`sus status`** - Check scanner status and basic info
**`sus stats`** - Show detailed scanning statistics
**`sus reload`** - Reload the hash list from file
**`sus help`** - Show this help message

**How it works:**
The sus scanner checks images against a database of known suspicious image hashes. When a match is found, the configured action is taken (ban/kick/mute/notify).

The scanner does NOT run automatically. A moderator must enable it with `sus enable`.
"""
    await message.reply(help_text, mention_author=False)


async def _handle_enable(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Enable the sus scanner."""
    guild_id = message.guild.id
    
    # Check if already enabled
    current = await get_sus_state(guild_id)
    if current.get("enabled"):
        await message.reply(
            "✅ Sus scanner is already enabled and running!",
            mention_author=False,
        )
        return
    
    if not state:
        await message.reply(
            "❌ Guild state not initialized. Please try again later.",
            mention_author=False,
        )
        return
    
    # Enable in config
    await set_sus_enabled(guild_id, True, message.author.id)
    
    # Start the queue processor if not running
    if state.queue_processor.stop_event.is_set() or state.queue_processor.reader_task is None:
        await state.queue_processor.start()
        logger.info("Started sus scanner for guild %s by user %s", guild_id, message.author.id)
    
    await message.reply(
        "✅ **Sus scanner enabled!**\n"
        "The scanner will now check images against the hash database.\n"
        f"Loaded **{len(state.hashes):,}** hashes.",
        mention_author=False,
    )


async def _handle_disable(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Disable the sus scanner."""
    guild_id = message.guild.id
    
    # Check if already disabled
    current = await get_sus_state(guild_id)
    if not current.get("enabled"):
        await message.reply(
            "ℹ️ Sus scanner is already disabled.",
            mention_author=False,
        )
        return
    
    # Disable in config
    await set_sus_enabled(guild_id, False, message.author.id)
    
    # Note: We don't stop the queue processor here to avoid losing queued jobs
    # The processor will just not process new jobs
    logger.info("Disabled sus scanner for guild %s by user %s", guild_id, message.author.id)
    
    await message.reply(
        "⏹️ **Sus scanner disabled.**\n"
        "New images will not be scanned.\n"
        "Use `sus enable` to re-enable the scanner.",
        mention_author=False,
    )


async def _handle_status(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Show sus scanner status."""
    guild_id = message.guild.id
    data = await get_sus_state(guild_id)
    
    enabled = data.get("enabled", False)
    status_emoji = "✅" if enabled else "❌"
    status_text = "Enabled" if enabled else "Disabled"
    
    # Build status message
    lines = [
        f"**🔍 Sus Scanner Status: {status_emoji} {status_text}**",
        "",
    ]
    
    if state:
        lines.append(f"**Loaded Hashes:** {len(state.hashes):,}")
        lines.append(f"**Queue Size:** {state.queue_processor.queued_jobs:,}")
        
        # Check if processor is actually running
        processor_running = (
            state.queue_processor.reader_task is not None and
            not state.queue_processor.stop_event.is_set()
        )
        proc_status = "🟢 Running" if processor_running else "🔴 Stopped"
        lines.append(f"**Processor:** {proc_status}")
    else:
        lines.append("⚠️ Guild state not initialized")
    
    if data.get("enabled_by"):
        lines.append(f"\n**Last enabled by:** <@{data['enabled_by']}>")
        if data.get("enabled_at"):
            lines.append(f"**Enabled at:** {data['enabled_at']}")
    
    if data.get("disabled_by"):
        lines.append(f"\n**Last disabled by:** <@{data['disabled_by']}>")
        if data.get("disabled_at"):
            lines.append(f"**Disabled at:** {data['disabled_at']}")
    
    await message.reply(
        "\n".join(lines),
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _handle_stats(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Show detailed sus scanner stats."""
    guild_id = message.guild.id
    data = await get_sus_state(guild_id)
    
    lines = [
        "**📊 Sus Scanner Statistics**",
        "",
        f"**Total Scans:** {data.get('total_scans', 0):,}",
        f"**Total Matches:** {data.get('total_matches', 0):,}",
    ]
    
    total_scans = data.get('total_scans', 0)
    total_matches = data.get('total_matches', 0)
    
    if total_scans > 0:
        match_rate = (total_matches / total_scans) * 100
        lines.append(f"**Match Rate:** {match_rate:.2f}%")
    
    if state:
        lines.append("")
        lines.append("**Current Session:**")
        lines.append(f"• Actions taken: {state.action_count:,}")
        lines.append(f"• Hashes loaded: {len(state.hashes):,}")
        lines.append(f"• Queue depth: {state.queue_processor.queued_jobs:,}")
        
        # Queue store stats
        compactions = state.queue_store.state.get("compactions", 0)
        if compactions:
            lines.append(f"• Queue compactions: {compactions:,}")
    
    await message.reply("\n".join(lines), mention_author=False)


async def _handle_reload(
    message: discord.Message,
    bot: "DiscBot",
    state: Optional["GuildState"],
) -> None:
    """Reload the hash list."""
    if not state:
        await message.reply(
            "❌ Guild state not initialized.",
            mention_author=False,
        )
        return
    
    old_count = len(state.hashes)
    
    try:
        state.hashes = await load_hashes(state.config)
        new_count = len(state.hashes)
        
        diff = new_count - old_count
        diff_text = f"(+{diff})" if diff > 0 else f"({diff})" if diff < 0 else "(no change)"
        
        logger.info(
            "Reloaded hashes for guild %s: %d -> %d",
            message.guild.id,
            old_count,
            new_count,
        )
        
        await message.reply(
            f"✅ **Hash list reloaded!**\n"
            f"**Before:** {old_count:,} hashes\n"
            f"**After:** {new_count:,} hashes {diff_text}",
            mention_author=False,
        )
    except Exception as e:
        logger.error("Failed to reload hashes: %s", e)
        await message.reply(
            f"❌ Failed to reload hashes: {e}",
            mention_author=False,
        )


async def restore_sus_state(bot: "DiscBot") -> None:
    """
    Restore sus scanner state for all guilds on bot startup.
    
    This should be called after guild states are initialized.
    Only starts the scanner for guilds where it was previously enabled.
    """
    for guild_id, state in bot.guild_states.items():
        try:
            data = await get_sus_state(guild_id)
            if data.get("enabled"):
                # Start the queue processor
                if state.queue_processor.stop_event.is_set() or state.queue_processor.reader_task is None:
                    await state.queue_processor.start()
                    logger.info("Restored sus scanner for guild %s (was enabled)", guild_id)
        except Exception as e:
            logger.error("Failed to restore sus state for guild %s: %s", guild_id, e)
