"""
Response delivery for auto-responder.

Handles sending responses via different modes (channel, reply, DM).
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord

from core.paths import resolve_repo_path
from core.utils import is_safe_relative_path, sanitize_text
from .matching import normalize_id_list


def resolve_targets(settings: dict[str, Any]) -> list[str]:
    """
    Resolve response targets from settings.
    
    Returns list of target modes: channel, reply, dm
    """
    targets = settings.get("response_targets")
    
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not targets:
        targets = [settings.get("response_mode", "channel")]
    
    normalized: list[str] = []
    for item in targets:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if key == "ephemeral":
            key = "dm"
        if key in {"channel", "reply", "dm"}:
            normalized.append(key)
    
    return normalized or ["channel"]


def build_allowed_mentions(
    message: discord.Message,
    settings: dict[str, Any],
) -> tuple[str, discord.AllowedMentions]:
    """
    Build mention text and AllowedMentions from settings.
    
    Returns (mention_text, allowed_mentions).
    """
    mention_parts: list[str] = []
    allow_users = False
    allow_roles = False
    
    # User mention
    if settings.get("mention_user"):
        mention_parts.append(message.author.mention)
        allow_users = True
    
    # Role mentions
    role_mentions: list[discord.Role] = []
    if message.guild is not None:
        for role_id in normalize_id_list(settings.get("mention_roles")):
            role = message.guild.get_role(role_id)
            if role:
                role_mentions.append(role)
    
    if role_mentions:
        mention_parts.extend(role.mention for role in role_mentions)
        allow_roles = True
    
    allowed_mentions = discord.AllowedMentions(
        users=role_mentions if allow_users else False,
        roles=role_mentions if allow_roles else False,
        replied_user=bool(settings.get("reply_ping_author", False)),
    )
    
    mention_text = " ".join(mention_parts).strip()
    return mention_text, allowed_mentions


def apply_text_wrappers(text: Optional[str], settings: dict[str, Any]) -> Optional[str]:
    """Apply prefix and suffix wrappers to text."""
    if text is None:
        return None
    prefix = settings.get("response_prefix") or ""
    suffix = settings.get("response_suffix") or ""
    return f"{prefix}{text}{suffix}"


def build_embeds(value: Any) -> list[discord.Embed]:
    """Build embed objects from dict or list of dicts."""
    if isinstance(value, dict):
        return [discord.Embed.from_dict(value)]
    if isinstance(value, list):
        embeds: list[discord.Embed] = []
        for item in value:
            if isinstance(item, dict):
                embeds.append(discord.Embed.from_dict(item))
        return embeds
    return []


async def build_files(value: Any) -> list[discord.File]:
    """
    Build file objects from config.
    
    Supports:
    - String path
    - Dict with path, filename, spoiler
    """
    if not isinstance(value, list):
        return []
    
    files: list[discord.File] = []
    
    for item in value:
        path_str: Optional[str] = None
        filename: Optional[str] = None
        spoiler = False
        
        if isinstance(item, str):
            path_str = item
        elif isinstance(item, dict):
            raw_path = item.get("path")
            if isinstance(raw_path, str):
                path_str = raw_path
            raw_filename = item.get("filename")
            if isinstance(raw_filename, str) and raw_filename.strip():
                filename = raw_filename.strip()
            spoiler = bool(item.get("spoiler", False))
        
        if not path_str:
            continue
        if not is_safe_relative_path(path_str):
            continue
        
        path = resolve_repo_path(path_str)
        exists = await asyncio.to_thread(path.exists)
        if not exists:
            continue
        
        files.append(discord.File(path, filename=filename, spoiler=spoiler))
    
    return files


def coerce_responses(value: Any) -> list[Any]:
    """Ensure value is a list of responses."""
    if isinstance(value, list):
        return value
    return [value]


async def send_response(
    message: discord.Message,
    response: Any,
    settings: dict[str, Any],
) -> bool:
    """
    Send a response to a message.
    
    Handles different response types and delivery modes.
    Returns True if any response was sent successfully.
    """
    if response is None:
        return False
    
    # Parse response
    content: Optional[str] = None
    embeds: list[discord.Embed] = []
    files: list[discord.File] = []
    
    if isinstance(response, str):
        content = sanitize_text(response)
    elif isinstance(response, dict):
        raw_content = response.get("content")
        if isinstance(raw_content, str):
            content = sanitize_text(raw_content)
        if "embed" in response:
            embeds = build_embeds(response.get("embed"))
        elif "embeds" in response:
            embeds = build_embeds(response.get("embeds"))
        files = await build_files(response.get("files"))
    else:
        return False
    
    # Build mentions
    mention_text, allowed_mentions = build_allowed_mentions(message, settings)
    content = apply_text_wrappers(content, settings)
    
    if mention_text:
        content = f"{mention_text} {content or ''}".strip()
    
    if not content and not embeds and not files:
        return False
    
    # Get targets and apply delay
    targets = resolve_targets(settings)
    handled = False
    
    delay = float(settings.get("delay_seconds", 0) or 0)
    if delay > 0:
        await asyncio.sleep(delay)
    
    # Send to each target
    for target in targets:
        try:
            if target == "dm":
                handled = await _send_dm(message, content, embeds, files, allowed_mentions, settings)
            elif target == "reply":
                handled = await _send_reply(message, content, embeds, files, allowed_mentions, settings)
            else:
                handled = await _send_channel(message, content, embeds, files, allowed_mentions, settings)
        except Exception:
            continue
    
    return handled


async def _send_dm(
    message: discord.Message,
    content: Optional[str],
    embeds: list[discord.Embed],
    files: list[discord.File],
    allowed_mentions: discord.AllowedMentions,
    settings: dict[str, Any],
) -> bool:
    """Send response via DM."""
    try:
        await message.author.send(
            content=content,
            embeds=embeds if embeds else None,
            files=files if files else None,
            allowed_mentions=allowed_mentions,
        )
        return True
    except Exception:
        # Fallback to channel if enabled
        if settings.get("dm_fallback_to_channel", True):
            await message.channel.send(
                content=content,
                embeds=embeds if embeds else None,
                files=files if files else None,
                allowed_mentions=allowed_mentions,
            )
            return True
        return False


async def _send_reply(
    message: discord.Message,
    content: Optional[str],
    embeds: list[discord.Embed],
    files: list[discord.File],
    allowed_mentions: discord.AllowedMentions,
    settings: dict[str, Any],
) -> bool:
    """Send response as a reply."""
    ping_author = bool(settings.get("reply_ping_author", False))
    
    if settings.get("typing"):
        async with message.channel.typing():
            await message.reply(
                content=content,
                embeds=embeds if embeds else None,
                files=files if files else None,
                allowed_mentions=allowed_mentions,
                mention_author=ping_author,
            )
    else:
        await message.reply(
            content=content,
            embeds=embeds if embeds else None,
            files=files if files else None,
            allowed_mentions=allowed_mentions,
            mention_author=ping_author,
        )
    return True


async def _send_channel(
    message: discord.Message,
    content: Optional[str],
    embeds: list[discord.Embed],
    files: list[discord.File],
    allowed_mentions: discord.AllowedMentions,
    settings: dict[str, Any],
) -> bool:
    """Send response to channel."""
    if settings.get("typing"):
        async with message.channel.typing():
            await message.channel.send(
                content=content,
                embeds=embeds if embeds else None,
                files=files if files else None,
                allowed_mentions=allowed_mentions,
            )
    else:
        await message.channel.send(
            content=content,
            embeds=embeds if embeds else None,
            files=files if files else None,
            allowed_mentions=allowed_mentions,
        )
    return True
