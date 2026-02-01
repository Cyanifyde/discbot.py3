"""
Configuration loading for auto-responder.

Handles loading and caching guild-specific responder configs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from core.config import GUILD_CONFIG_DIR
from core.io_utils import read_json

CONFIG_SUFFIX = ".autoresponder.json"

# Cache: guild_id -> (mtime, config_data)
_CONFIG_CACHE: dict[int, tuple[Optional[float], dict[str, Any]]] = {}

# Default settings for triggers
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "match_mode": "startswith",  # startswith|equals|contains|regex
    "case_sensitive": False,
    "strip_trigger": True,
    "allow_mention_prefix": True,
    "require_mention": False,
    "allowed_user_ids": [],
    "blocked_user_ids": [],
    "allowed_role_ids": [],
    "blocked_role_ids": [],
    "allowed_channel_ids": [],
    "blocked_channel_ids": [],
    "allowed_category_ids": [],
    "blocked_category_ids": [],
    "cooldown_seconds": 0.0,
    "cooldown_scope": "user",  # user|guild
    "delete_trigger_message": False,
    "delay_seconds": 0.0,
    "typing": False,
    "response_mode": "channel",  # channel|reply|dm|ephemeral
    "response_targets": [],  # overrides response_mode when set
    "response_prefix": "",
    "response_suffix": "",
    "mention_user": False,
    "mention_roles": [],
    "reply_ping_author": False,
    "dm_fallback_to_channel": True,
    "input_min_words": 0,
    "input_max_words": 0,
    "input_min_chars": 0,
    "input_max_chars": 0,
}


@dataclass
class TriggerSpec:
    """Specification for a single trigger."""
    trigger: str
    handler: Optional[str]
    response: Any
    settings: dict[str, Any]


async def _stat_mtime(path: Path) -> Optional[float]:
    """Get file modification time."""
    def _read() -> Optional[float]:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return None
    return await asyncio.to_thread(_read)


def merge_settings(*sources: Any) -> dict[str, Any]:
    """Merge multiple settings dicts, later ones override earlier."""
    merged: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source)
    return merged


async def load_guild_config(guild_id: int) -> dict[str, Any]:
    """
    Load auto-responder config for a guild.
    
    Uses caching based on file modification time.
    """
    path = GUILD_CONFIG_DIR / f"{guild_id}{CONFIG_SUFFIX}"
    mtime = await _stat_mtime(path)
    
    # Check cache
    cached = _CONFIG_CACHE.get(guild_id)
    if cached and cached[0] == mtime:
        return cached[1]
    
    # Load from file
    data = await read_json(path, default=None)
    if not isinstance(data, dict):
        data = {}
    
    _CONFIG_CACHE[guild_id] = (mtime, data)
    return data


def clear_guild_cache(guild_id: int) -> None:
    """Clear cached config for a guild."""
    _CONFIG_CACHE.pop(guild_id, None)


def extract_config(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Extract triggers and global settings from config data.
    
    Supports two formats:
    1. {"triggers": {...}, "settings": {...}}
    2. Direct trigger dict (legacy)
    """
    if "triggers" in data or "settings" in data:
        triggers = data.get("triggers")
        settings = data.get("settings")
    else:
        triggers = data
        settings = {}
    
    if not isinstance(triggers, dict):
        triggers = {}
    if not isinstance(settings, dict):
        settings = {}
    
    return triggers, settings


def build_trigger_spec(
    trigger: str,
    value: Any,
    global_settings: dict[str, Any],
) -> Optional[TriggerSpec]:
    """
    Build a TriggerSpec from config data.
    
    Returns None if the trigger is disabled or invalid.
    """
    settings = merge_settings(DEFAULT_SETTINGS, global_settings)
    handler: Optional[str] = None
    response: Any = None
    
    if isinstance(value, dict):
        # Check for handler/class
        handler_value = value.get("handler") or value.get("class")
        if isinstance(handler_value, str) and handler_value.strip():
            handler = handler_value.strip()
        
        # Merge settings
        if isinstance(value.get("settings"), dict):
            settings = merge_settings(settings, value.get("settings"))
        if isinstance(value.get("match"), dict):
            settings = merge_settings(settings, value.get("match"))
        
        # Check enabled flag
        if "enabled" in value:
            settings["enabled"] = bool(value.get("enabled"))
        
        # Get response
        if "response" in value:
            response = value.get("response")
        elif handler is None:
            response = value
    else:
        response = value
    
    # Skip disabled triggers
    if not settings.get("enabled", True):
        return None
    
    # Skip if no handler and no response
    if handler is None and response is None:
        return None
    
    return TriggerSpec(
        trigger=trigger,
        handler=handler,
        response=response,
        settings=settings,
    )


def normalize_trigger_items(
    data: dict[str, Any],
    global_settings: dict[str, Any],
) -> list[TriggerSpec]:
    """
    Convert raw config data to a sorted list of TriggerSpecs.
    
    Sorted by trigger length (longest first) for proper matching.
    """
    items: list[TriggerSpec] = []
    
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        trigger = key.strip()
        if not trigger:
            continue
        
        spec = build_trigger_spec(trigger, value, global_settings)
        if spec:
            items.append(spec)
    
    # Sort by length (longest first) for proper matching priority
    items.sort(key=lambda item: len(item.trigger), reverse=True)
    return items
