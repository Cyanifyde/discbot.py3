"""
Trigger matching logic for auto-responder.

Handles different match modes and filter checks.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import discord

from core.constants import MatchMode


def normalize_id_list(value: Any) -> list[int]:
    """Convert various input formats to a list of integer IDs."""
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, int):
                items.append(item)
            elif isinstance(item, str) and item.strip().isdigit():
                items.append(int(item.strip()))
        return items
    return []


def match_trigger(
    content: str,
    trigger: str,
    settings: dict[str, Any],
) -> Optional[tuple[int, int]]:
    """
    Match a trigger against content.
    
    Returns (start, end) span if matched, None otherwise.
    
    Supports modes:
    - startswith: Content starts with trigger
    - equals: Content exactly equals trigger
    - contains: Content contains trigger anywhere
    - regex: Trigger is a regex pattern
    """
    mode = settings.get("match_mode") or settings.get("match") or MatchMode.STARTSWITH
    case_sensitive = bool(settings.get("case_sensitive", False))
    
    haystack = content
    needle = trigger
    
    if not case_sensitive:
        haystack = content.lower()
        needle = trigger.lower()
    
    if mode == MatchMode.EQUALS:
        if haystack == needle:
            return (0, len(content))
        return None
    
    if mode == MatchMode.CONTAINS:
        idx = haystack.find(needle)
        if idx == -1:
            return None
        return (idx, idx + len(trigger))
    
    if mode == MatchMode.REGEX:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            match = re.search(trigger, content, flags=flags)
        except re.error:
            return None
        if not match:
            return None
        return match.span()
    
    # Default: startswith
    if haystack.startswith(needle):
        return (0, len(trigger))
    return None


def passes_filters(message: discord.Message, settings: dict[str, Any]) -> bool:
    """
    Check if a message passes all configured filters.
    
    Filters include:
    - require_mention: Bot must be mentioned
    - allowed/blocked user IDs
    - allowed/blocked role IDs  
    - allowed/blocked channel IDs
    - allowed/blocked category IDs
    """
    author = message.author
    guild = message.guild
    
    # Check mention requirement
    if settings.get("require_mention"):
        if guild is None or guild.me is None:
            return False
        bot_id = guild.me.id
        if not any(mention.id == bot_id for mention in message.mentions):
            return False
    
    # User filters
    allowed_users = normalize_id_list(settings.get("allowed_user_ids"))
    if allowed_users and author.id not in allowed_users:
        return False
    
    blocked_users = normalize_id_list(settings.get("blocked_user_ids"))
    if blocked_users and author.id in blocked_users:
        return False
    
    # Guild-specific filters
    if guild is not None:
        # Role filters
        allowed_roles = set(normalize_id_list(settings.get("allowed_role_ids")))
        if allowed_roles and not any(role.id in allowed_roles for role in author.roles):
            return False
        
        blocked_roles = set(normalize_id_list(settings.get("blocked_role_ids")))
        if blocked_roles and any(role.id in blocked_roles for role in author.roles):
            return False
        
        # Channel filters
        channel_id = message.channel.id
        allowed_channels = normalize_id_list(settings.get("allowed_channel_ids"))
        if allowed_channels and channel_id not in allowed_channels:
            return False
        
        blocked_channels = normalize_id_list(settings.get("blocked_channel_ids"))
        if blocked_channels and channel_id in blocked_channels:
            return False
        
        # Category filters
        category_id = getattr(message.channel, "category_id", None)
        allowed_categories = normalize_id_list(settings.get("allowed_category_ids"))
        if allowed_categories and category_id not in allowed_categories:
            return False
        
        blocked_categories = normalize_id_list(settings.get("blocked_category_ids"))
        if blocked_categories and category_id in blocked_categories:
            return False
    
    return True


def extract_input_text(
    content: str,
    match_span: Optional[tuple[int, int]],
    settings: dict[str, Any],
) -> str:
    """Extract the input text after the trigger."""
    if not match_span or not settings.get("strip_trigger", True):
        return content.strip()
    start, end = match_span
    if start == 0:
        return content[end:].strip()
    return content[start:end].strip()


def strip_bot_mention_prefix(
    content: str,
    message: discord.Message,
    settings: dict[str, Any],
) -> tuple[str, bool]:
    """
    Strip bot mention from the beginning of content if allowed.
    
    Returns (stripped_content, was_stripped).
    """
    if not settings.get("allow_mention_prefix", True):
        return content, False
    
    guild = message.guild
    if guild is None or guild.me is None:
        return content, False
    
    bot_id = guild.me.id
    if not any(mention.id == bot_id for mention in message.mentions):
        return content, False
    
    stripped = content.lstrip()
    for token in (f"<@{bot_id}>", f"<@!{bot_id}>"):
        if stripped.startswith(token):
            return stripped[len(token):].lstrip(), True
    
    return content, False


def check_input_limits(text: str, settings: dict[str, Any]) -> bool:
    """Check if input text meets word/character limits."""
    words = text.split()
    
    min_words = int(settings.get("input_min_words", 0) or 0)
    max_words = int(settings.get("input_max_words", 0) or 0)
    if min_words and len(words) < min_words:
        return False
    if max_words and len(words) > max_words:
        return False
    
    min_chars = int(settings.get("input_min_chars", 0) or 0)
    max_chars = int(settings.get("input_max_chars", 0) or 0)
    if min_chars and len(text) < min_chars:
        return False
    if max_chars and len(text) > max_chars:
        return False
    
    return True
