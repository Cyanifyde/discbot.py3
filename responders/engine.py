"""
Auto-responder engine - main entry point and orchestration.

This module ties together matching, config loading, and delivery.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import time
from typing import Any, Optional

import discord

from classes.response_handlers import ResponderInput
from core.permissions import is_module_enabled

from .config_loader import (
    TriggerSpec,
    extract_config,
    load_guild_config,
    merge_settings,
    normalize_trigger_items,
)
from .matching import (
    check_input_limits,
    extract_input_text,
    match_trigger,
    passes_filters,
    strip_bot_mention_prefix,
)
from .delivery import coerce_responses, send_response

logger = logging.getLogger("discbot.responder")

MODULE_NAME = "autoresponder"
_HANDLER_NAMESPACE = "classes"

# Caches
_HANDLER_CACHE: dict[str, Any] = {}
_COOLDOWNS: dict[tuple[int, str, int], float] = {}


class AutoResponderEngine:
    """
    Main engine for processing auto-responses.
    
    Encapsulates state that was previously module-level globals.
    """

    def __init__(self) -> None:
        self.handler_cache: dict[str, Any] = {}
        self.cooldowns: dict[tuple[int, str, int], float] = {}

    def clear_guild_cooldowns(self, guild_id: int) -> None:
        """Clear all cooldowns for a guild."""
        self.cooldowns = {
            k: v for k, v in self.cooldowns.items() if k[0] != guild_id
        }

    def clear_all_cooldowns(self) -> None:
        """Clear all cooldowns."""
        self.cooldowns.clear()

    def clear_handler_cache(self) -> None:
        """Clear handler cache (useful for hot-reloading)."""
        self.handler_cache.clear()


def _cooldown_key(
    message: discord.Message,
    trigger: str,
    settings: dict[str, Any],
) -> tuple[int, str, int]:
    """Generate a cooldown key based on scope."""
    guild_id = message.guild.id if message.guild else 0
    scope = str(settings.get("cooldown_scope", "user")).lower()
    
    if scope == "guild":
        return (guild_id, trigger, 0)
    return (guild_id, trigger, message.author.id)


def _check_cooldown(
    message: discord.Message,
    trigger: str,
    settings: dict[str, Any],
) -> bool:
    """Check if trigger is on cooldown. Returns True if allowed."""
    seconds = float(settings.get("cooldown_seconds", 0) or 0)
    if seconds <= 0:
        return True
    
    key = _cooldown_key(message, trigger, settings)
    now = time.monotonic()
    last = _COOLDOWNS.get(key)
    
    if last is not None and now - last < seconds:
        return False
    
    _COOLDOWNS[key] = now
    
    # Clean up old entries periodically (every 100 checks)
    if len(_COOLDOWNS) > 1000:
        _cleanup_old_cooldowns(now)
    
    return True


def _cleanup_old_cooldowns(now: float, max_age: float = 3600.0) -> None:
    """Remove cooldown entries older than max_age seconds."""
    expired_keys = [key for key, timestamp in _COOLDOWNS.items() if now - timestamp > max_age]
    for key in expired_keys:
        _COOLDOWNS.pop(key, None)



def _normalize_handler_path(path: str) -> Optional[str]:
    """Normalize a handler path to module:attr format."""
    if not path:
        return None
    
    raw = path.strip()
    if not raw:
        return None
    
    if ":" in raw:
        module_name, attr = raw.split(":", 1)
        module_name = module_name.strip()
        attr = attr.strip()
        if not module_name or not attr:
            return None
        if not module_name.startswith(f"{_HANDLER_NAMESPACE}."):
            module_name = f"{_HANDLER_NAMESPACE}.{module_name}"
        return f"{module_name}:{attr}"
    
    if "." in raw:
        module_name, attr = raw.rsplit(".", 1)
        module_name = module_name.strip()
        attr = attr.strip()
        if not module_name or not attr:
            return None
        if not module_name.startswith(f"{_HANDLER_NAMESPACE}."):
            module_name = f"{_HANDLER_NAMESPACE}.{module_name}"
        return f"{module_name}.{attr}"
    
    return None


def _load_handler(path: str) -> Optional[Any]:
    """Load a handler class or function by path."""
    normalized = _normalize_handler_path(path)
    if not normalized:
        return None
    
    # Check cache
    cached = _HANDLER_CACHE.get(normalized)
    if cached is not None:
        return cached
    
    # Parse module and attribute
    if ":" in normalized:
        module_name, attr = normalized.split(":", 1)
    else:
        module_name, attr = normalized.rsplit(".", 1)
    
    if not module_name.startswith(f"{_HANDLER_NAMESPACE}."):
        return None
    
    try:
        module = importlib.import_module(module_name)
        handler = getattr(module, attr, None)
    except (ImportError, AttributeError):
        return None
    
    if handler is None:
        return None
    
    _HANDLER_CACHE[normalized] = handler
    return handler


async def _invoke_handler(handler: Any, payload: ResponderInput) -> Any:
    """Invoke a handler with the payload."""
    result = None
    
    if inspect.isclass(handler):
        instance = handler(payload.settings)
        result = instance.run(payload)
    elif hasattr(handler, "run"):
        result = handler.run(payload)
    elif callable(handler):
        result = handler(payload)
    
    if inspect.isawaitable(result):
        return await result
    return result


def _unwrap_handler_result(result: Any) -> tuple[Any, dict[str, Any]]:
    """
    Unwrap a handler result.
    
    Handlers can return:
    - Simple value (used as response)
    - Dict with response, targets, settings (for more control)
    """
    if isinstance(result, dict) and (
        "response" in result or "targets" in result or "settings" in result
    ):
        response = result.get("response")
        overrides: dict[str, Any] = {}
        
        if isinstance(result.get("settings"), dict):
            overrides = merge_settings(overrides, result.get("settings"))
        
        targets = result.get("targets")
        if isinstance(targets, (list, str)):
            overrides["response_targets"] = targets
        
        return response, overrides
    
    return result, {}


async def handle_auto_responder(message: discord.Message) -> bool:
    """
    Main entry point for auto-responder.
    
    Processes a message and sends responses if triggers match.
    Returns True if any response was handled.
    """
    # Basic checks
    if message.guild is None:
        return False
    if message.author.bot:
        return False
    
    # Check if module is enabled
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False
    
    content = message.content or ""
    if not content.strip():
        return False
    
    # Load config
    data = await load_guild_config(message.guild.id)
    if not data:
        return False
    
    triggers, global_settings = extract_config(data)
    items = normalize_trigger_items(triggers, global_settings)
    if not items:
        return False
    
    # Process triggers
    handled = False
    stripped_content, mention_prefixed = strip_bot_mention_prefix(content, message, global_settings)
    
    for spec in items:
        # Check filters
        if not passes_filters(message, spec.settings):
            continue
        
        # Try to match
        match_span = match_trigger(content, spec.trigger, spec.settings)
        matched_content = content
        
        # Try with stripped content if mention prefixed
        if not match_span and mention_prefixed:
            match_span = match_trigger(stripped_content, spec.trigger, spec.settings)
            if match_span:
                matched_content = stripped_content
        
        if not match_span:
            continue
        
        # Extract input and check limits
        input_text = extract_input_text(matched_content, match_span, spec.settings)
        if not check_input_limits(input_text, spec.settings):
            continue
        
        # Check cooldown
        if not _check_cooldown(message, spec.trigger, spec.settings):
            continue
        
        # Build payload
        payload = ResponderInput(
            message=message,
            command=spec.trigger,
            text=input_text,
            args=input_text.split(),
            raw=content,
            settings=spec.settings,
        )
        
        # Get response
        response = None
        overrides: dict[str, Any] = {}
        
        if spec.handler:
            handler = _load_handler(spec.handler)
            if handler:
                try:
                    result = await _invoke_handler(handler, payload)
                except Exception as e:
                    logger.warning("Handler %s raised: %s", spec.handler, e)
                    result = None
                response, overrides = _unwrap_handler_result(result)
        
        if response is None:
            response = spec.response
        
        if response is None:
            continue
        
        # Send response(s)
        final_settings = merge_settings(spec.settings, overrides)
        
        for item in coerce_responses(response):
            try:
                sent = await send_response(message, item, final_settings)
            except Exception as e:
                logger.warning("Failed to send response: %s", e)
                sent = False
            handled = handled or sent
        
        # Delete trigger message if configured
        if handled and final_settings.get("delete_trigger_message"):
            try:
                await message.delete()
            except Exception:
                pass
        
        if handled:
            return True
    
    return False
