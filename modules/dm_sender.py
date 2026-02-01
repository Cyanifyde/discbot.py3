"""
DM sender module.

Allows the bot owner to send messages to channels via DM commands.
"""
from __future__ import annotations

from typing import Optional, Tuple

import discord

from core.config import OWNER_ID
from core.utils import is_valid_id, safe_int, sanitize_text

TRIGGERS = {"send", "say", "post"}
HELP_TEXT = (
    "**DM Send Command**\n"
    "```\n"
    "send <guild_id> <channel_id> <message>\n"
    "```\n"
    "**Example:**\n"
    "`send 123456789123456789 987654321987654321 Hello world`"
)


def _should_attempt_parse(content: str) -> bool:
    parts = content.strip().split()
    if not parts:
        return False
    if parts[0].lower() in TRIGGERS:
        return True
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return True
    return False


def _parse_dm_payload(content: str) -> Optional[Tuple[int, int, str]]:
    parts = content.strip().split()
    if not parts:
        return None
    if parts[0].lower() in TRIGGERS:
        parts = parts[1:]
    if len(parts) < 3:
        return None
    guild_id = safe_int(parts[0])
    channel_id = safe_int(parts[1])
    if guild_id is None or channel_id is None:
        return None
    if not is_valid_id(guild_id) or not is_valid_id(channel_id):
        return None
    text = " ".join(parts[2:]).strip()
    if not text:
        return None
    return guild_id, channel_id, text


async def handle_dm_send(bot: discord.Client, message: discord.Message) -> bool:
    content = message.content or ""
    if not _should_attempt_parse(content):
        return False
    if message.author.id != OWNER_ID:
        return False
    parsed = _parse_dm_payload(content)
    if not parsed:
        await message.channel.send(
            HELP_TEXT,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    guild_id, channel_id, text = parsed
    guild = bot.get_guild(guild_id)
    if guild is None:
        await message.channel.send(
            sanitize_text("Bot is not in that guild or cannot access it."),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None
    if channel is None or not isinstance(channel, discord.abc.Messageable):
        await message.channel.send(
            sanitize_text("Channel not found or not messageable."),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    try:
        await channel.send(
            text,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        await message.channel.send(
            sanitize_text("Missing permission to send in that channel."),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except Exception as exc:
        await message.channel.send(
            sanitize_text(f"Send failed: {exc.__class__.__name__}"),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    await message.channel.send(
        "sent",
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True
