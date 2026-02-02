"""
Moderation module - warnings, mutes, bans, kicks, and mod notes.

Provides moderation commands that can be synced across linked servers.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Optional, Tuple

import discord

from core.help_system import help_system
from core.moderation_storage import get_moderation_store
from core.permissions import can_use_command, is_module_enabled
from core.utils import iso_to_dt
from services.sync_service import sync_action_downstream, request_upstream

logger = logging.getLogger("discbot.moderation")

MODULE_NAME = "moderation"

# Duration parsing regex (e.g., "1h", "30m", "7d", "1d12h")
DURATION_PATTERN = re.compile(
    r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$",
    re.IGNORECASE
)


def parse_duration(duration_str: str) -> Optional[timedelta]:
    """
    Parse a duration string like "1h30m" or "7d" into a timedelta.

    Returns None if invalid.
    """
    duration_str = duration_str.strip().lower()

    # Handle simple formats like "1h", "30m", "7d"
    match = DURATION_PATTERN.match(duration_str)
    if not match:
        # Try simple single-unit format
        simple_match = re.match(r"^(\d+)([dhms])$", duration_str)
        if simple_match:
            value = int(simple_match.group(1))
            unit = simple_match.group(2)
            if unit == "d":
                return timedelta(days=value)
            elif unit == "h":
                return timedelta(hours=value)
            elif unit == "m":
                return timedelta(minutes=value)
            elif unit == "s":
                return timedelta(seconds=value)
        return None

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)

    if days == hours == minutes == seconds == 0:
        return None

    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def format_duration(td: timedelta) -> str:
    """Format a timedelta as a human-readable string."""
    total_seconds = int(td.total_seconds())

    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds and not parts:  # Only show seconds if it's the only unit
        parts.append(f"{seconds}s")

    return "".join(parts) if parts else "0s"


def setup_moderation() -> None:
    """Register help information for the moderation module."""
    help_system.register_module(
        name="Moderation",
        description="Moderation tools for warnings, mutes, bans, kicks, and mod notes.",
        help_command="moderation help",
        commands=[
            ("warn <user> [reason]", "Issue a warning to a user"),
            ("warnings <user>", "View a user's warnings"),
            ("clearwarning <user> <id>", "Remove a specific warning"),
            ("clearwarnings <user>", "Remove all warnings for a user"),
            ("mute <user> <duration> [reason]", "Timeout a user (e.g., 1h, 30m, 7d)"),
            ("unmute <user>", "Remove timeout from a user"),
            ("ban <user> [reason]", "Ban a user from the server"),
            ("unban <user_id>", "Unban a user by ID"),
            ("kick <user> [reason]", "Kick a user from the server"),
            ("note <user> <text>", "Add a mod note (not visible to user)"),
            ("notes <user>", "View mod notes for a user"),
            ("clearnote <user> <id>", "Remove a specific note"),
        ]
    )


async def _cmd_help(message: discord.Message) -> None:
    """Show help for moderation commands."""
    embed = help_system.get_module_embed("Moderation")
    if embed is None:
        await message.reply("Help not available.", mention_author=False)
        return
    await message.reply(embed=embed, mention_author=False)


def _parse_user_mention(content: str, guild: discord.Guild) -> Tuple[Optional[discord.Member], str]:
    """
    Parse a user mention or ID from the start of content.

    Returns (member, remaining_content) or (None, content) if not found.
    """
    content = content.strip()

    # Try mention format <@!123> or <@123>
    match = re.match(r"<@!?(\d+)>\s*", content)
    if match:
        user_id = int(match.group(1))
        member = guild.get_member(user_id)
        remaining = content[match.end():]
        return member, remaining

    # Try raw ID
    match = re.match(r"(\d{17,20})\s*", content)
    if match:
        user_id = int(match.group(1))
        member = guild.get_member(user_id)
        remaining = content[match.end():]
        return member, remaining

    return None, content


async def _check_permissions(
    message: discord.Message,
    command: str,
) -> bool:
    """
    Check if the user has permission to use a moderation command.

    Returns True if permitted, False otherwise (and sends error message).
    """
    if not message.guild:
        return False

    if not isinstance(message.author, discord.Member):
        return False

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.reply(
            "Moderation module is disabled in this server.\n"
            "An administrator can enable it with `modules enable moderation`",
            mention_author=False,
        )
        return False

    if not await can_use_command(message.author, command):
        await message.reply(
            "You don't have permission to use this command.",
            mention_author=False,
        )
        return False

    return True


# ─── Warning Commands ─────────────────────────────────────────────────────────


async def handle_warn_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: warn <user> [reason]"""
    content = message.content.strip()
    if not content.lower().startswith("warn "):
        return False

    if not await _check_permissions(message, "warn"):
        return True

    # Parse: warn <user> [reason]
    args = content[5:].strip()  # Remove "warn "

    member, reason = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `warn @user [reason]`\n"
            "Could not find that user.",
            mention_author=False,
        )
        return True

    reason = reason.strip() or "No reason provided"

    # Add warning
    store = await get_moderation_store(message.guild.id)
    warning = await store.add_warning(member.id, message.author.id, reason)
    total = await store.count_warnings(member.id)

    await message.reply(
        f"**Warning #{warning['id']}** issued to {member.mention}\n"
        f"**Reason:** {reason}\n"
        f"**Total warnings:** {total}",
        mention_author=False,
    )

    logger.info(
        "Warning issued to %s (%s) in guild %s by %s: %s",
        member.name, member.id, message.guild.id, message.author.id, reason
    )

    # Sync to linked servers
    synced_to = await sync_action_downstream(
        bot, message.guild, "warning", member.id, reason, message.author.id
    )
    if synced_to:
        await message.channel.send(
            f"📤 Synced warning to {len(synced_to)} linked server(s).",
            delete_after=10,
        )

    # Request upstream approval
    await request_upstream(
        bot, message.guild, "warning", member.id, reason, message.author.id,
        record_action=False,
    )

    return True


async def handle_warnings_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: warnings <user>"""
    content = message.content.strip()
    if not content.lower().startswith("warnings "):
        return False

    if not await _check_permissions(message, "warnings"):
        return True

    args = content[9:].strip()  # Remove "warnings "

    member, _ = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `warnings @user`\n"
            "Could not find that user.",
            mention_author=False,
        )
        return True

    store = await get_moderation_store(message.guild.id)
    warnings = await store.get_warnings(member.id)

    if not warnings:
        await message.reply(
            f"{member.mention} has no warnings.",
            mention_author=False,
        )
        return True

    embed = discord.Embed(
        title=f"Warnings for {member.display_name}",
        color=0xFF9900,
    )

    for w in warnings[-10:]:  # Show last 10
        mod = message.guild.get_member(int(w.get("mod_id", 0)))
        mod_name = mod.display_name if mod else f"Unknown ({w.get('mod_id')})"
        timestamp = w.get("timestamp", "Unknown")

        embed.add_field(
            name=f"#{w['id']} - {timestamp[:10]}",
            value=f"**Reason:** {w['reason']}\n**By:** {mod_name}",
            inline=False,
        )

    if len(warnings) > 10:
        embed.set_footer(text=f"Showing 10 of {len(warnings)} warnings")

    await message.reply(embed=embed, mention_author=False)
    return True


async def handle_clearwarning_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: clearwarning <user> <id>"""
    content = message.content.strip()
    if not content.lower().startswith("clearwarning "):
        return False

    if not await _check_permissions(message, "clearwarning"):
        return True

    args = content[13:].strip()  # Remove "clearwarning "

    member, remaining = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `clearwarning @user <warning_id>`",
            mention_author=False,
        )
        return True

    # Parse warning ID
    try:
        warning_id = int(remaining.strip())
    except ValueError:
        await message.reply(
            "**Usage:** `clearwarning @user <warning_id>`\n"
            "Warning ID must be a number.",
            mention_author=False,
        )
        return True

    store = await get_moderation_store(message.guild.id)
    removed = await store.remove_warning(member.id, warning_id)

    if removed:
        await message.reply(
            f"Removed warning #{warning_id} from {member.mention}.",
            mention_author=False,
        )
    else:
        await message.reply(
            f"Warning #{warning_id} not found for {member.mention}.",
            mention_author=False,
        )

    return True


async def handle_clearwarnings_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: clearwarnings <user>"""
    content = message.content.strip()
    if not content.lower().startswith("clearwarnings "):
        return False

    if not await _check_permissions(message, "clearwarnings"):
        return True

    args = content[14:].strip()  # Remove "clearwarnings "

    member, _ = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `clearwarnings @user`",
            mention_author=False,
        )
        return True

    store = await get_moderation_store(message.guild.id)
    count = await store.clear_warnings(member.id)

    await message.reply(
        f"Cleared {count} warning(s) from {member.mention}.",
        mention_author=False,
    )

    return True


# ─── Mute Commands ────────────────────────────────────────────────────────────


async def handle_mute_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: mute <user> <duration> [reason]"""
    content = message.content.strip()
    if not content.lower().startswith("mute "):
        return False

    if not await _check_permissions(message, "mute"):
        return True

    args = content[5:].strip()  # Remove "mute "

    member, remaining = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `mute @user <duration> [reason]`\n"
            "Duration examples: 1h, 30m, 7d, 1d12h",
            mention_author=False,
        )
        return True

    # Parse duration and reason
    parts = remaining.strip().split(None, 1)
    if not parts:
        await message.reply(
            "**Usage:** `mute @user <duration> [reason]`\n"
            "Duration examples: 1h, 30m, 7d, 1d12h",
            mention_author=False,
        )
        return True

    duration = parse_duration(parts[0])
    if not duration:
        await message.reply(
            f"Invalid duration: `{parts[0]}`\n"
            "Examples: 1h, 30m, 7d, 1d12h",
            mention_author=False,
        )
        return True

    # Discord timeout max is 28 days
    if duration > timedelta(days=28):
        await message.reply(
            "Maximum timeout duration is 28 days.",
            mention_author=False,
        )
        return True

    reason = parts[1].strip() if len(parts) > 1 else "No reason provided"

    # Apply timeout
    try:
        await member.timeout(duration, reason=f"{reason} (by {message.author})")

        await message.reply(
            f"**Muted** {member.mention} for {format_duration(duration)}\n"
            f"**Reason:** {reason}",
            mention_author=False,
        )

        logger.info(
            "Muted %s (%s) in guild %s for %s by %s: %s",
            member.name, member.id, message.guild.id, format_duration(duration),
            message.author.id, reason
        )

        # Sync to linked servers
        duration_seconds = int(duration.total_seconds())
        synced_to = await sync_action_downstream(
            bot, message.guild, "mute", member.id, reason, message.author.id,
            duration=duration_seconds
        )
        if synced_to:
            await message.channel.send(
                f"📤 Synced mute to {len(synced_to)} linked server(s).",
                delete_after=10,
            )

        # Request upstream approval
        await request_upstream(
            bot, message.guild, "mute", member.id, reason, message.author.id,
            duration=duration_seconds,
            record_action=False,
        )

    except discord.Forbidden:
        await message.reply(
            "I don't have permission to timeout that user.",
            mention_author=False,
        )
    except discord.HTTPException as e:
        logger.error("Failed to mute user: %s", e)
        await message.reply(
            "Failed to mute user. Please try again.",
            mention_author=False,
        )

    return True


async def handle_unmute_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: unmute <user>"""
    content = message.content.strip()
    if not content.lower().startswith("unmute "):
        return False

    if not await _check_permissions(message, "unmute"):
        return True

    args = content[7:].strip()  # Remove "unmute "

    member, _ = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `unmute @user`",
            mention_author=False,
        )
        return True

    if not member.is_timed_out():
        await message.reply(
            f"{member.mention} is not muted.",
            mention_author=False,
        )
        return True

    try:
        await member.timeout(None, reason=f"Unmuted by {message.author}")

        await message.reply(
            f"**Unmuted** {member.mention}",
            mention_author=False,
        )

        logger.info(
            "Unmuted %s (%s) in guild %s by %s",
            member.name, member.id, message.guild.id, message.author.id
        )
    except discord.Forbidden:
        await message.reply(
            "I don't have permission to unmute that user.",
            mention_author=False,
        )
    except discord.HTTPException as e:
        logger.error("Failed to unmute user: %s", e)
        await message.reply(
            "Failed to unmute user. Please try again.",
            mention_author=False,
        )

    return True


# ─── Ban/Kick Commands ────────────────────────────────────────────────────────


async def handle_ban_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: ban <user> [reason]"""
    content = message.content.strip()
    if not content.lower().startswith("ban "):
        return False

    if not await _check_permissions(message, "ban"):
        return True

    # Check bot permissions
    if not message.guild.me.guild_permissions.ban_members:
        await message.reply(
            "I don't have permission to ban members.",
            mention_author=False,
        )
        return True

    args = content[4:].strip()  # Remove "ban "

    member, reason = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `ban @user [reason]`",
            mention_author=False,
        )
        return True

    reason = reason.strip() or "No reason provided"

    # Check hierarchy
    if member.top_role >= message.guild.me.top_role:
        await message.reply(
            "I cannot ban that user - their role is too high.",
            mention_author=False,
        )
        return True

    if member.id == message.author.id:
        await message.reply(
            "You cannot ban yourself.",
            mention_author=False,
        )
        return True

    try:
        await message.guild.ban(
            member,
            reason=f"{reason} (by {message.author})",
            delete_message_days=0,
        )

        await message.reply(
            f"**Banned** {member.mention} ({member.id})\n"
            f"**Reason:** {reason}",
            mention_author=False,
        )

        logger.info(
            "Banned %s (%s) from guild %s by %s: %s",
            member.name, member.id, message.guild.id, message.author.id, reason
        )

        # Sync to linked servers
        synced_to = await sync_action_downstream(
            bot, message.guild, "ban", member.id, reason, message.author.id
        )
        if synced_to:
            await message.channel.send(
                f"📤 Synced ban to {len(synced_to)} linked server(s).",
                delete_after=10,
            )

        # Request upstream approval
        await request_upstream(
            bot, message.guild, "ban", member.id, reason, message.author.id,
            record_action=False,
        )

    except discord.Forbidden:
        await message.reply(
            "I don't have permission to ban that user.",
            mention_author=False,
        )
    except discord.HTTPException as e:
        logger.error("Failed to ban user: %s", e)
        await message.reply(
            "Failed to ban user. Please try again.",
            mention_author=False,
        )

    return True


async def handle_unban_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: unban <user_id>"""
    content = message.content.strip()
    if not content.lower().startswith("unban "):
        return False

    if not await _check_permissions(message, "unban"):
        return True

    if not message.guild.me.guild_permissions.ban_members:
        await message.reply(
            "I don't have permission to unban members.",
            mention_author=False,
        )
        return True

    args = content[6:].strip()  # Remove "unban "

    # Parse user ID
    try:
        user_id = int(args.split()[0])
    except (ValueError, IndexError):
        await message.reply(
            "**Usage:** `unban <user_id>`",
            mention_author=False,
        )
        return True

    try:
        await message.guild.unban(discord.Object(id=user_id), reason=f"Unbanned by {message.author}")

        await message.reply(
            f"**Unbanned** user ID `{user_id}`",
            mention_author=False,
        )

        logger.info(
            "Unbanned user %s from guild %s by %s",
            user_id, message.guild.id, message.author.id
        )
    except discord.NotFound:
        await message.reply(
            f"User `{user_id}` is not banned.",
            mention_author=False,
        )
    except discord.Forbidden:
        await message.reply(
            "I don't have permission to unban users.",
            mention_author=False,
        )
    except discord.HTTPException as e:
        logger.error("Failed to unban user: %s", e)
        await message.reply(
            "Failed to unban user. Please try again.",
            mention_author=False,
        )

    return True


async def handle_kick_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: kick <user> [reason]"""
    content = message.content.strip()
    if not content.lower().startswith("kick "):
        return False

    if not await _check_permissions(message, "kick"):
        return True

    if not message.guild.me.guild_permissions.kick_members:
        await message.reply(
            "I don't have permission to kick members.",
            mention_author=False,
        )
        return True

    args = content[5:].strip()  # Remove "kick "

    member, reason = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `kick @user [reason]`",
            mention_author=False,
        )
        return True

    reason = reason.strip() or "No reason provided"

    # Check hierarchy
    if member.top_role >= message.guild.me.top_role:
        await message.reply(
            "I cannot kick that user - their role is too high.",
            mention_author=False,
        )
        return True

    if member.id == message.author.id:
        await message.reply(
            "You cannot kick yourself.",
            mention_author=False,
        )
        return True

    try:
        await member.kick(reason=f"{reason} (by {message.author})")

        await message.reply(
            f"**Kicked** {member.mention} ({member.id})\n"
            f"**Reason:** {reason}",
            mention_author=False,
        )

        logger.info(
            "Kicked %s (%s) from guild %s by %s: %s",
            member.name, member.id, message.guild.id, message.author.id, reason
        )

        # Sync to linked servers
        synced_to = await sync_action_downstream(
            bot, message.guild, "kick", member.id, reason, message.author.id
        )
        if synced_to:
            await message.channel.send(
                f"📤 Synced kick to {len(synced_to)} linked server(s).",
                delete_after=10,
            )

        # Request upstream approval
        await request_upstream(
            bot, message.guild, "kick", member.id, reason, message.author.id,
            record_action=False,
        )

    except discord.Forbidden:
        await message.reply(
            "I don't have permission to kick that user.",
            mention_author=False,
        )
    except discord.HTTPException as e:
        logger.error("Failed to kick user: %s", e)
        await message.reply(
            "Failed to kick user. Please try again.",
            mention_author=False,
        )

    return True


# ─── Note Commands ────────────────────────────────────────────────────────────


async def handle_note_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: note <user> <text>"""
    content = message.content.strip()
    if not content.lower().startswith("note "):
        return False

    if not await _check_permissions(message, "note"):
        return True

    args = content[5:].strip()  # Remove "note "

    member, text = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `note @user <text>`",
            mention_author=False,
        )
        return True

    text = text.strip()
    if not text:
        await message.reply(
            "**Usage:** `note @user <text>`\n"
            "Please provide note text.",
            mention_author=False,
        )
        return True

    store = await get_moderation_store(message.guild.id)
    note = await store.add_note(member.id, message.author.id, text)

    await message.reply(
        f"**Note #{note['id']}** added for {member.mention}",
        mention_author=False,
    )

    logger.info(
        "Note added for %s (%s) in guild %s by %s",
        member.name, member.id, message.guild.id, message.author.id
    )

    return True


async def handle_notes_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: notes <user>"""
    content = message.content.strip()
    if not content.lower().startswith("notes "):
        return False

    if not await _check_permissions(message, "notes"):
        return True

    args = content[6:].strip()  # Remove "notes "

    member, _ = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `notes @user`",
            mention_author=False,
        )
        return True

    store = await get_moderation_store(message.guild.id)
    notes = await store.get_notes(member.id)

    if not notes:
        await message.reply(
            f"No notes for {member.mention}.",
            mention_author=False,
        )
        return True

    embed = discord.Embed(
        title=f"Notes for {member.display_name}",
        color=0x5865F2,
    )

    for n in notes[-10:]:  # Show last 10
        mod = message.guild.get_member(int(n.get("mod_id", 0)))
        mod_name = mod.display_name if mod else f"Unknown ({n.get('mod_id')})"
        timestamp = n.get("timestamp", "Unknown")

        embed.add_field(
            name=f"#{n['id']} - {timestamp[:10]} by {mod_name}",
            value=n["text"][:1024],  # Discord field value limit
            inline=False,
        )

    if len(notes) > 10:
        embed.set_footer(text=f"Showing 10 of {len(notes)} notes")

    await message.reply(embed=embed, mention_author=False)
    return True


async def handle_clearnote_command(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: clearnote <user> <id>"""
    content = message.content.strip()
    if not content.lower().startswith("clearnote "):
        return False

    if not await _check_permissions(message, "clearnote"):
        return True

    args = content[10:].strip()  # Remove "clearnote "

    member, remaining = _parse_user_mention(args, message.guild)
    if not member:
        await message.reply(
            "**Usage:** `clearnote @user <note_id>`",
            mention_author=False,
        )
        return True

    try:
        note_id = int(remaining.strip())
    except ValueError:
        await message.reply(
            "**Usage:** `clearnote @user <note_id>`\n"
            "Note ID must be a number.",
            mention_author=False,
        )
        return True

    store = await get_moderation_store(message.guild.id)
    removed = await store.remove_note(member.id, note_id)

    if removed:
        await message.reply(
            f"Removed note #{note_id} from {member.mention}.",
            mention_author=False,
        )
    else:
        await message.reply(
            f"Note #{note_id} not found for {member.mention}.",
            mention_author=False,
        )

    return True


# ─── Help Command ─────────────────────────────────────────────────────────────


async def handle_moderation_help(message: discord.Message, bot: discord.Client) -> bool:
    """Handle: moderation help"""
    content = message.content.strip().lower()
    if content != "moderation help":
        return False

    if not message.guild:
        return False

    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        await message.reply(
            "Moderation module is disabled in this server.\n"
            "An administrator can enable it with `modules enable moderation`",
            mention_author=False,
        )
        return True

    await _cmd_help(message)
    return True


# ─── Main Handler ─────────────────────────────────────────────────────────────


async def handle_moderation_command(
    message: discord.Message,
    bot: discord.Client,
) -> bool:
    """
    Main handler for all moderation commands.

    Returns True if the command was handled.
    """
    if not message.guild:
        return False

    content = message.content.strip().lower()

    # Check each command handler
    handlers = [
        handle_moderation_help,
        handle_warn_command,
        handle_warnings_command,
        handle_clearwarning_command,
        handle_clearwarnings_command,
        handle_mute_command,
        handle_unmute_command,
        handle_ban_command,
        handle_unban_command,
        handle_kick_command,
        handle_note_command,
        handle_notes_command,
        handle_clearnote_command,
    ]

    for handler in handlers:
        if await handler(message, bot):
            return True

    return False
