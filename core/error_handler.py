"""
Centralized error handler.

Generates unique error codes, writes detailed JSON logs, and sends
user-facing error embeds to the channel where the error occurred.
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import discord

from core.io_utils import write_json_atomic
from core.paths import resolve_repo_path
from core.utils import utcnow

logger = logging.getLogger("discbot.error_handler")

LOGS_DIR: Path = resolve_repo_path("logs")


def _generate_code() -> str:
    """Return a unique error code like ``ERR-A1B2C3``."""
    return f"ERR-{uuid4().hex[:6].upper()}"


def _build_log_entry(
    error: BaseException,
    *,
    code: str,
    context: str,
    guild_id: Optional[int] = None,
    guild_name: Optional[str] = None,
    channel_id: Optional[int] = None,
    channel_name: Optional[str] = None,
    user_id: Optional[int] = None,
    user_name: Optional[str] = None,
    command_text: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "error_code": code,
        "context": context,
        "error_type": type(error).__qualname__,
        "error_message": str(error),
        "traceback": traceback.format_exception(type(error), error, error.__traceback__),
        "timestamp": utcnow().isoformat(),
    }
    if guild_id is not None:
        entry["guild_id"] = guild_id
    if guild_name is not None:
        entry["guild_name"] = guild_name
    if channel_id is not None:
        entry["channel_id"] = channel_id
    if channel_name is not None:
        entry["channel_name"] = channel_name
    if user_id is not None:
        entry["user_id"] = user_id
    if user_name is not None:
        entry["user_name"] = user_name
    if command_text is not None:
        entry["command_text"] = command_text
    if extra:
        entry["extra"] = extra
    return entry


async def _write_log(code: str, entry: Dict[str, Any]) -> None:
    path = LOGS_DIR / f"{code}.json"
    try:
        await write_json_atomic(path, entry)
    except Exception as exc:
        logger.error("Failed to write error log %s: %s", code, exc)


def _error_embed(code: str) -> discord.Embed:
    return discord.Embed(
        title="Something went wrong",
        description=f"An error occurred while processing your request.\n\nError code: **{code}**",
        color=discord.Color.red(),
    )


# ── Public API ────────────────────────────────────────────────────────────────


async def handle_command_error(error: BaseException, message: discord.Message) -> None:
    """Handle an error from a prefix command handler.  Sends a red embed to the channel."""
    code = _generate_code()
    guild = message.guild
    entry = _build_log_entry(
        error,
        code=code,
        context="command",
        guild_id=guild.id if guild else None,
        guild_name=guild.name if guild else None,
        channel_id=message.channel.id,
        channel_name=getattr(message.channel, "name", None),
        user_id=message.author.id,
        user_name=str(message.author),
        command_text=message.content,
    )
    logger.error("Error %s in command: %s", code, error, exc_info=error)
    await _write_log(code, entry)
    try:
        await message.channel.send(embed=_error_embed(code))
    except Exception:
        pass


async def handle_interaction_error(
    error: BaseException,
    interaction: discord.Interaction,
) -> None:
    """Handle an error from a component/button interaction."""
    code = _generate_code()
    guild = interaction.guild
    entry = _build_log_entry(
        error,
        code=code,
        context="interaction",
        guild_id=guild.id if guild else None,
        guild_name=guild.name if guild else None,
        channel_id=interaction.channel_id,
        channel_name=getattr(interaction.channel, "name", None),
        user_id=interaction.user.id,
        user_name=str(interaction.user),
        extra={"interaction_data": str(interaction.data)},
    )
    logger.error("Error %s in interaction: %s", code, error, exc_info=error)
    await _write_log(code, entry)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=_error_embed(code), ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=_error_embed(code), ephemeral=True,
            )
    except Exception:
        pass


async def handle_slash_command_error(
    error: BaseException,
    interaction: discord.Interaction,
    command_name: str,
) -> None:
    """Handle an error from a slash command."""
    code = _generate_code()
    guild = interaction.guild
    entry = _build_log_entry(
        error,
        code=code,
        context=f"slash_command:{command_name}",
        guild_id=guild.id if guild else None,
        guild_name=guild.name if guild else None,
        channel_id=interaction.channel_id,
        channel_name=getattr(interaction.channel, "name", None),
        user_id=interaction.user.id,
        user_name=str(interaction.user),
    )
    logger.error("Error %s in /%s: %s", code, command_name, error, exc_info=error)
    await _write_log(code, entry)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=_error_embed(code), ephemeral=True,
            )
        else:
            await interaction.followup.send(
                embed=_error_embed(code), ephemeral=True,
            )
    except Exception:
        pass


async def handle_event_error(
    error: BaseException,
    *,
    context: str,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Handle an error from a non-command event (reactions, lifecycle, etc.).

    Logs to file only -- no user-facing embed is sent.
    """
    code = _generate_code()
    entry = _build_log_entry(
        error,
        code=code,
        context=context,
        guild_id=guild_id,
        channel_id=channel_id,
        extra=extra,
    )
    logger.error("Error %s in %s: %s", code, context, error, exc_info=error)
    await _write_log(code, entry)
