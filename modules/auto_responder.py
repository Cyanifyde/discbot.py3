"""
Auto-responder module.

Handles automatic responses to configured message triggers with support for
custom handlers, filtering, cooldowns, and multiple delivery modes.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import discord

from core.config import GUILD_CONFIG_DIR
from core.io_utils import read_json, write_json_atomic
from core.paths import resolve_repo_path
from core.utils import is_safe_relative_path, sanitize_text
from core.help_system import help_system
from core.permissions import can_use_command, is_module_enabled, can_use_module
from classes.response_handlers import ResponderInput

MODULE_NAME = "autoresponder"
CONFIG_SUFFIX = ".autoresponder.json"

# Register help immediately on module import
help_system.register_module(
    name="Auto-Responder",
    description="Automatic responses to configured triggers with custom handlers, filters, and delivery modes.",
    help_command="autoresponder help",
    commands=[
        ("autoresponder help", "Show this help message"),
        ("listresponses", "List all server-added responses"),
        ("addresponse \"trigger\" \"response\"", "Add a simple text response"),
        ("addresponse \"trigger\" \"\" --embed title=\"T\" desc=\"D\"", "Add an embed response"),
        ("addresponse ... --allow-roles id1,id2", "Restrict to specific roles"),
        ("addresponse ... --block-roles id1,id2", "Block specific roles"),
        ("removeresponse \"trigger\"", "Remove a server-added response"),
    ]
)

# Command patterns
ADD_RESPONSE_PATTERN = re.compile(
    r'^addresponse\s+"([^"]+)"\s+"([^"]+)"(?:\s+(.*))?$',
    re.IGNORECASE | re.DOTALL
)
REMOVE_RESPONSE_PATTERN = re.compile(
    r'^removeresponse\s+"([^"]+)"$',
    re.IGNORECASE
)

_CACHE: Dict[int, Tuple[Optional[float], Dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 30.0  # Cache config for 30 seconds
_HANDLER_CACHE: Dict[str, Any] = {}
_HANDLER_NAMESPACE = "classes"
_COOLDOWNS: Dict[Tuple[int, str, int], float] = {}
_LAST_COOLDOWN_CLEANUP = 0.0
_COOLDOWN_CLEANUP_INTERVAL = 300.0  # Cleanup every 5 minutes
_HELP_REGISTERED = False  # Track if help has been registered

DEFAULT_SETTINGS: Dict[str, Any] = {
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
    trigger: str
    handler: Optional[str]
    response: Any
    settings: Dict[str, Any]


async def _stat_mtime(path: Path) -> Optional[float]:
    def _read() -> Optional[float]:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return None

    return await asyncio.to_thread(_read)


def _merge_settings(*sources: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            merged[key] = value
    return merged


def _normalize_id_list(value: Any) -> List[int]:
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, int):
                items.append(item)
            elif isinstance(item, str) and item.strip().isdigit():
                items.append(int(item.strip()))
        return items
    return []


async def _load_guild_config(guild_id: int) -> Dict[str, Any]:
    path = GUILD_CONFIG_DIR / f"{guild_id}{CONFIG_SUFFIX}"
    now = time.monotonic()
    cached = _CACHE.get(guild_id)
    if cached:
        cache_time, data = cached
        if cache_time is not None and now - cache_time < _CACHE_TTL_SECONDS:
            return data
    data = await read_json(path, default=None)
    if not isinstance(data, dict):
        data = {}
    _CACHE[guild_id] = (now, data)
    return data


def _extract_config(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
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


def _build_trigger_spec(
    trigger: str,
    value: Any,
    global_settings: Dict[str, Any],
) -> Optional[TriggerSpec]:
    settings = _merge_settings(DEFAULT_SETTINGS, global_settings)
    handler: Optional[str] = None
    response: Any = None
    if isinstance(value, dict):
        handler_value = value.get("handler") or value.get("class")
        if isinstance(handler_value, str) and handler_value.strip():
            handler = handler_value.strip()
        settings_val = value.get("settings")
        if isinstance(settings_val, dict):
            settings = _merge_settings(settings, settings_val)
        match_val = value.get("match")
        if isinstance(match_val, dict):
            settings = _merge_settings(settings, match_val)
        if "enabled" in value:
            settings["enabled"] = bool(value.get("enabled"))
        if "response" in value:
            response = value.get("response")
        elif handler is None:
            response = value
    else:
        response = value
    if not settings.get("enabled", True):
        return None
    if handler is None and response is None:
        return None
    return TriggerSpec(trigger=trigger, handler=handler, response=response, settings=settings)


def _normalize_trigger_items(data: Dict[str, Any], global_settings: Dict[str, Any]) -> List[TriggerSpec]:
    items: List[TriggerSpec] = []
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        trigger = key.strip()
        if not trigger:
            continue
        spec = _build_trigger_spec(trigger, value, global_settings)
        if spec:
            items.append(spec)
    # Sort by trigger length (longest first) for better matching
    # This sorting happens once per config load, cached by TTL
    items.sort(key=lambda item: len(item.trigger), reverse=True)
    return items


def _match_trigger(content: str, trigger: str, settings: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    mode = settings.get("match_mode") or settings.get("match") or "startswith"
    case_sensitive = bool(settings.get("case_sensitive", False))
    haystack = content
    needle = trigger
    if not case_sensitive:
        haystack = content.lower()
        needle = trigger.lower()
    if mode == "equals":
        if haystack == needle:
            return (0, len(content))
        return None
    if mode == "contains":
        idx = haystack.find(needle)
        if idx == -1:
            return None
        return (idx, idx + len(trigger))
    if mode == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            match = re.search(trigger, content, flags=flags)
        except re.error:
            return None
        if not match:
            return None
        return match.span()
    if haystack.startswith(needle):
        return (0, len(trigger))
    return None


def _extract_input_text(
    content: str,
    match_span: Optional[Tuple[int, int]],
    settings: Dict[str, Any],
) -> str:
    if not match_span or not settings.get("strip_trigger", True):
        return content.strip()
    start, end = match_span
    if start == 0:
        return content[end:].strip()
    return content[end:].strip()


def _passes_filters(message: discord.Message, settings: Dict[str, Any]) -> bool:
    author = message.author
    guild = message.guild
    if settings.get("require_mention"):
        if guild is None or guild.me is None:
            return False
        bot_id = guild.me.id
        if not any(mention.id == bot_id for mention in message.mentions):
            return False
    allowed_users = _normalize_id_list(settings.get("allowed_user_ids"))
    if allowed_users and author.id not in allowed_users:
        return False
    blocked_users = _normalize_id_list(settings.get("blocked_user_ids"))
    if blocked_users and author.id in blocked_users:
        return False
    if guild is not None and isinstance(author, discord.Member):
        allowed_roles = set(_normalize_id_list(settings.get("allowed_role_ids")))
        if allowed_roles and not any(role.id in allowed_roles for role in author.roles):
            return False
        blocked_roles = set(_normalize_id_list(settings.get("blocked_role_ids")))
        if blocked_roles and any(role.id in blocked_roles for role in author.roles):
            return False
        channel_id = message.channel.id
        allowed_channels = _normalize_id_list(settings.get("allowed_channel_ids"))
        if allowed_channels and channel_id not in allowed_channels:
            return False
        blocked_channels = _normalize_id_list(settings.get("blocked_channel_ids"))
        if blocked_channels and channel_id in blocked_channels:
            return False
        category_id = getattr(message.channel, "category_id", None)
        allowed_categories = _normalize_id_list(settings.get("allowed_category_ids"))
        if allowed_categories and category_id not in allowed_categories:
            return False
        blocked_categories = _normalize_id_list(settings.get("blocked_category_ids"))
        if blocked_categories and category_id in blocked_categories:
            return False
    return True


def _strip_bot_mention_prefix(
    content: str,
    message: discord.Message,
    settings: Dict[str, Any],
) -> Tuple[str, bool]:
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
            return stripped[len(token) :].lstrip(), True
    return content, False


def _check_input_limits(text: str, settings: Dict[str, Any]) -> bool:
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


def _cooldown_key(message: discord.Message, trigger: str, settings: Dict[str, Any]) -> Tuple[int, str, int]:
    guild_id = message.guild.id if message.guild else 0
    scope = str(settings.get("cooldown_scope", "user")).lower()
    if scope == "guild":
        return (guild_id, trigger, 0)
    return (guild_id, trigger, message.author.id)


def _cleanup_expired_cooldowns(now: float) -> None:
    """Remove cooldowns older than 1 hour to prevent memory leak."""
    max_age = 3600.0  # 1 hour
    expired = [key for key, timestamp in _COOLDOWNS.items() if now - timestamp > max_age]
    for key in expired:
        _COOLDOWNS.pop(key, None)


def _check_cooldown(message: discord.Message, trigger: str, settings: Dict[str, Any]) -> bool:
    seconds = float(settings.get("cooldown_seconds", 0) or 0)
    if seconds <= 0:
        return True
    key = _cooldown_key(message, trigger, settings)
    now = time.monotonic()
    
    # Periodic cleanup to prevent memory leak
    global _LAST_COOLDOWN_CLEANUP
    if now - _LAST_COOLDOWN_CLEANUP > _COOLDOWN_CLEANUP_INTERVAL:
        _cleanup_expired_cooldowns(now)
        _LAST_COOLDOWN_CLEANUP = now
    
    last = _COOLDOWNS.get(key)
    if last is not None and now - last < seconds:
        return False
    _COOLDOWNS[key] = now
    return True


def _normalize_handler_path(path: str) -> Optional[str]:
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
    normalized = _normalize_handler_path(path)
    if not normalized:
        return None
    cached = _HANDLER_CACHE.get(normalized)
    if cached is not None:
        return cached
    if ":" in normalized:
        module_name, attr = normalized.split(":", 1)
    else:
        module_name, attr = normalized.rsplit(".", 1)
    if not module_name.startswith(f"{_HANDLER_NAMESPACE}."):
        return None
    module = importlib.import_module(module_name)
    handler = getattr(module, attr, None)
    if handler is None:
        return None
    _HANDLER_CACHE[normalized] = handler
    return handler


async def _invoke_handler(handler: Any, payload: ResponderInput) -> Any:
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


def _coerce_responses(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    return [value]


def _build_embeds(value: Any) -> List[discord.Embed]:
    if isinstance(value, dict):
        return [discord.Embed.from_dict(value)]
    if isinstance(value, list):
        embeds: List[discord.Embed] = []
        for item in value:
            if isinstance(item, dict):
                embeds.append(discord.Embed.from_dict(item))
        return embeds
    return []


async def _build_files(value: Any) -> List[discord.File]:
    if not isinstance(value, list):
        return []
    files: List[discord.File] = []
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


def _unwrap_handler_result(result: Any) -> Tuple[Any, Dict[str, Any]]:
    if isinstance(result, dict) and (
        "response" in result or "targets" in result or "settings" in result
    ):
        response = result.get("response")
        overrides: Dict[str, Any] = {}
        settings_val = result.get("settings")
        if isinstance(settings_val, dict):
            overrides = _merge_settings(overrides, settings_val)
        targets = result.get("targets")
        if isinstance(targets, list) or isinstance(targets, str):
            overrides["response_targets"] = targets
        return response, overrides
    return result, {}


def _resolve_targets(settings: Dict[str, Any]) -> List[str]:
    targets = settings.get("response_targets")
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list) or not targets:
        targets = [settings.get("response_mode", "channel")]
    normalized: List[str] = []
    for item in targets:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if key == "ephemeral":
            key = "dm"
        if key in {"channel", "reply", "dm"}:
            normalized.append(key)
    return normalized or ["channel"]


def _build_allowed_mentions(
    message: discord.Message,
    settings: Dict[str, Any],
) -> Tuple[str, discord.AllowedMentions]:
    mention_parts: List[str] = []
    allow_users = False
    allow_roles = False
    if settings.get("mention_user"):
        mention_parts.append(message.author.mention)
        allow_users = True
    role_mentions: List[discord.Role] = []
    if message.guild is not None:
        for role_id in _normalize_id_list(settings.get("mention_roles")):
            role = message.guild.get_role(role_id)
            if role:
                role_mentions.append(role)
    if role_mentions:
        mention_parts.extend(role.mention for role in role_mentions)
        allow_roles = True
    allowed_mentions = discord.AllowedMentions(
        users=allow_users,
        roles=role_mentions if allow_roles else False,
        replied_user=bool(settings.get("reply_ping_author", False)),
    )
    mention_text = " ".join(mention_parts).strip()
    return mention_text, allowed_mentions


def _apply_text_wrappers(text: Optional[str], settings: Dict[str, Any]) -> Optional[str]:
    if text is None:
        return None
    prefix = settings.get("response_prefix") or ""
    suffix = settings.get("response_suffix") or ""
    return f"{prefix}{text}{suffix}"


async def _send_response(
    message: discord.Message,
    response: Any,
    settings: Dict[str, Any],
) -> bool:
    if response is None:
        return False
    content: Optional[str] = None
    embeds: List[discord.Embed] = []
    files: List[discord.File] = []
    if isinstance(response, str):
        content = sanitize_text(response)
    elif isinstance(response, dict):
        raw_content = response.get("content")
        if isinstance(raw_content, str):
            content = sanitize_text(raw_content)
        if "embed" in response:
            embeds = _build_embeds(response.get("embed"))
        elif "embeds" in response:
            embeds = _build_embeds(response.get("embeds"))
        files = await _build_files(response.get("files"))
    else:
        return False

    mention_text, allowed_mentions = _build_allowed_mentions(message, settings)
    content = _apply_text_wrappers(content, settings)
    if mention_text:
        content = f"{mention_text} {content or ''}".strip()
    if not content and not embeds and not files:
        return False

    targets = _resolve_targets(settings)
    handled = False
    delay = float(settings.get("delay_seconds", 0) or 0)
    if delay > 0:
        await asyncio.sleep(delay)

    for target in targets:
        try:
            if target == "dm":
                try:
                    await message.author.send(
                        content=content,
                        embeds=embeds if embeds else None,
                        files=files if files else None,
                        allowed_mentions=allowed_mentions,
                    )
                    handled = True
                except Exception:
                    if settings.get("dm_fallback_to_channel", True):
                        await message.channel.send(
                            content=content,
                            embeds=embeds if embeds else None,
                            files=files if files else None,
                            allowed_mentions=allowed_mentions,
                        )
                        handled = True
            elif target == "reply":
                if settings.get("typing"):
                    async with message.channel.typing():
                        await message.reply(
                            content=content,
                            embeds=embeds if embeds else None,
                            files=files if files else None,
                            allowed_mentions=allowed_mentions,
                            mention_author=bool(settings.get("reply_ping_author", False)),
                        )
                else:
                    await message.reply(
                        content=content,
                        embeds=embeds if embeds else None,
                        files=files if files else None,
                        allowed_mentions=allowed_mentions,
                        mention_author=bool(settings.get("reply_ping_author", False)),
                    )
                handled = True
            else:
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
                handled = True
        except Exception:
            continue
    return handled


async def handle_auto_responder(message: discord.Message) -> bool:
    if message.guild is None:
        return False
    if message.author.bot:
        return False
    
    # Handle autoresponder help command
    content_lower = (message.content or "").strip().lower()
    if content_lower == "autoresponder help":
        if not await is_module_enabled(message.guild.id, MODULE_NAME):
            await message.reply(
                "Auto Responder module is disabled in this server.\n"
                "An administrator can enable it with `modules enable autoresponder`",
                mention_author=False,
            )
            return True
        await _cmd_help(message)
        return True
    
    if not await is_module_enabled(message.guild.id, MODULE_NAME):
        return False
    content = message.content or ""
    if not content.strip():
        return False
    data = await _load_guild_config(message.guild.id)
    if not data:
        return False
    triggers, global_settings = _extract_config(data)
    items = _normalize_trigger_items(triggers, global_settings)
    if not items:
        return False
    handled = False
    stripped_content, mention_prefixed = _strip_bot_mention_prefix(content, message, global_settings)
    for spec in items:
        if not _passes_filters(message, spec.settings):
            continue
        match_span = _match_trigger(content, spec.trigger, spec.settings)
        matched_content = content
        if not match_span and mention_prefixed:
            match_span = _match_trigger(stripped_content, spec.trigger, spec.settings)
            if match_span:
                matched_content = stripped_content
        if not match_span:
            continue
        input_text = _extract_input_text(matched_content, match_span, spec.settings)
        if not _check_input_limits(input_text, spec.settings):
            continue
        if not _check_cooldown(message, spec.trigger, spec.settings):
            continue
        payload = ResponderInput(
            message=message,
            command=spec.trigger,
            text=input_text,
            args=input_text.split(),
            raw=content,
            settings=spec.settings,
        )
        response = None
        overrides: Dict[str, Any] = {}
        if spec.handler:
            handler = _load_handler(spec.handler)
            if handler:
                try:
                    result = await _invoke_handler(handler, payload)
                except Exception as e:
                    import logging
                    logging.getLogger("discbot.autoresponder").error(
                        "Handler '%s' failed: %s", spec.handler, e
                    )
                    result = None
                response, overrides = _unwrap_handler_result(result)
        if response is None:
            response = spec.response
        if response is None:
            continue
        final_settings = _merge_settings(spec.settings, overrides)
        for item in _coerce_responses(response):
            try:
                sent = await _send_response(message, item, final_settings)
            except Exception as e:
                import logging
                logging.getLogger("discbot.autoresponder").error(
                    "Failed to send response for trigger '%s': %s", spec.trigger, e
                )
                sent = False
            handled = handled or sent
        if handled and final_settings.get("delete_trigger_message"):
            try:
                await message.delete()
            except discord.HTTPException as e:
                logging.getLogger("discbot.autoresponder").debug(
                    "Failed to delete trigger message: %s", e
                )
        if handled:
            return True
    return False


async def _save_guild_config(guild_id: int, data: Dict[str, Any]) -> None:
    """Save guild config and invalidate cache."""
    path = GUILD_CONFIG_DIR / f"{guild_id}{CONFIG_SUFFIX}"
    await write_json_atomic(path, data)
    # Invalidate cache
    _CACHE.pop(guild_id, None)


def _is_mod(member: discord.Member) -> bool:
    """Check if member has mod permissions."""
    perms = member.guild_permissions
    return (
        perms.administrator
        or perms.manage_guild
        or perms.manage_messages
    )


async def _cmd_help(message: discord.Message) -> None:
    """Show help for auto-responder commands using the help system."""
    embed = help_system.get_module_embed("Auto-Responder")
    if embed is None:
        await message.reply("Help not available.", mention_author=False)
        return
    await message.reply(embed=embed, mention_author=False)


async def handle_list_responses_command(message: discord.Message) -> bool:
    """
    Handle the listresponses command.
    
    Lists all user-added auto-responses for this server.
    
    Returns True if the command was handled.
    """
    content = message.content.strip().lower()
    
    if content != "listresponses":
        return False
    
    if not message.guild:
        return False
    
    # Check if module is enabled
    if not await is_module_enabled(message.guild.id, "autoresponder"):
        await message.reply(
            "Auto Responder module is disabled in this server.\n"
            "An administrator can enable it with `modules enable autoresponder`",
            mention_author=False,
        )
        return True
    
    if not await can_use_command(message.author, "listresponses"):
        await message.reply(
            "You don't have permission to use this command in this server.\n"
            "An administrator can grant access with `modules allow listresponses @YourRole`",
            mention_author=False,
        )
        return True
    
    # Load config
    data = await _load_guild_config(message.guild.id)
    if not isinstance(data, dict):
        data = {}
    
    user_added = data.get("user_added", [])
    if not isinstance(user_added, list):
        user_added = []
    
    triggers = data.get("triggers", {})
    if not isinstance(triggers, dict):
        triggers = {}
    
    if not user_added:
        await message.reply(
            "No server-added responses found.\n"
            'Use `addresponse "trigger" "response"` to add one.',
            mention_author=False,
        )
        return True
    
    # Build response list
    lines = []
    for trigger in user_added:
        if trigger in triggers:
            trig_data = triggers[trigger]
            line_parts = []
            role_info = ""
            
            if isinstance(trig_data, dict):
                # Check for role restrictions
                settings = trig_data.get("settings", {})
                allowed = settings.get("allowed_role_ids", [])
                blocked = settings.get("blocked_role_ids", [])
                if allowed:
                    role_info = f" [Allowed: {len(allowed)} role(s)]"
                elif blocked:
                    role_info = f" [Blocked: {len(blocked)} role(s)]"
                
                if "embeds" in trig_data:
                    line_parts.append(f"**{trigger}** - [Embed]{role_info}")
                elif "response" in trig_data:
                    resp = trig_data["response"]
                    preview = resp[:35] + "..." if len(resp) > 35 else resp
                    line_parts.append(f"**{trigger}** - {preview}{role_info}")
                else:
                    line_parts.append(f"**{trigger}** - [Custom]{role_info}")
            else:
                preview = str(trig_data)[:35]
                line_parts.append(f"**{trigger}** - {preview}")
            
            lines.extend(line_parts)
    
    embed = discord.Embed(
        title="Server-Added Auto-Responses",
        description="\n".join(lines) if lines else "No responses found.",
        color=discord.Color.blue(),
    )
    embed.set_footer(text=f"{len(lines)} response(s)")
    
    await message.reply(embed=embed, mention_author=False)
    return True


async def handle_add_response_command(message: discord.Message) -> bool:
    """
    Handle the addresponse text command.
    
    Format: addresponse "trigger" "response" [--embed title="Title" description="Desc" color=#hex]
    
    Returns True if the command was handled.
    """
    content = message.content.strip()
    
    # Check if it's the addresponse command
    if not content.lower().startswith("addresponse"):
        return False
    
    # Must be in a guild
    if not message.guild:
        return False
    
    # Must be a mod
    if not isinstance(message.author, discord.Member):
        return False
    
    # Check module and command permissions (guild-specific)
    if not await is_module_enabled(message.guild.id, "autoresponder"):
        await message.reply(
            "Auto Responder module is disabled in this server.\n"
            "An administrator can enable it with `modules enable autoresponder`",
            mention_author=False,
        )
        return True
    
    if not await can_use_command(message.author, "addresponse"):
        await message.reply(
            "You don't have permission to use this command in this server.\n"
            "An administrator can grant access with `modules allow addresponse @YourRole`",
            mention_author=False,
        )
        return True
    
    if not _is_mod(message.author):
        await message.reply(
            "You need Administrator, Manage Server, or Manage Messages permission to use this command.",
            mention_author=False,
        )
        return True
    
    # Parse the command - support both simple and embed formats
    # Simple: addresponse "trigger" "response"
    # Embed: addresponse "trigger" "" --embed title="Title" description="Desc" color=#5865F2
    
    match = ADD_RESPONSE_PATTERN.match(content)
    if not match:
        await message.reply(
            "**Invalid format.**\\n"
            "**Simple response:**\\n"
            '```\\naddresponse "trigger" "response"\\n```\\n'
            "**Embed response:**\\n"
            '```\\naddresponse "trigger" "" --embed title=\"Title\" description=\"Description\" color=#5865F2\\n```\\n'
            "**Available embed fields:** title, description, color, url, footer",
            mention_author=False,
        )
        return True
    
    trigger = match.group(1).strip()
    response = match.group(2).strip()
    extra = match.group(3) or ""
    
    if not trigger:
        await message.reply(
            "Trigger cannot be empty.",
            mention_author=False,
        )
        return True
    
    # Parse role permissions (--allow-roles role_id1,role_id2 or --block-roles role_id1,role_id2)
    allowed_role_ids = []
    blocked_role_ids = []
    
    allow_match = re.search(r'--allow-roles?\s+(\d+(?:,\s*\d+)*)', extra, re.IGNORECASE)
    if allow_match:
        allowed_role_ids = [int(r.strip()) for r in allow_match.group(1).split(',')]
    
    block_match = re.search(r'--block-roles?\s+(\d+(?:,\s*\d+)*)', extra, re.IGNORECASE)
    if block_match:
        blocked_role_ids = [int(r.strip()) for r in block_match.group(1).split(',')]
    
    # Check if embed format
    embed_data = None
    if "--embed" in extra.lower():
        # Parse embed parameters
        embed_data = _parse_embed_params(extra)
        if not embed_data:
            await message.reply(
                "Failed to parse embed parameters. Use format:\\n"
                '`--embed title="Title" description="Desc" color=#hex`',
                mention_author=False,
            )
            return True
    elif not response:
        await message.reply(
            "Response cannot be empty unless using --embed.",
            mention_author=False,
        )
        return True
    
    # Load current config
    data = await _load_guild_config(message.guild.id)
    if not isinstance(data, dict):
        data = {"settings": {}, "triggers": {}}
    
    triggers = data.get("triggers", {})
    if not isinstance(triggers, dict):
        triggers = {}
    
    # Track user-added triggers
    user_added = data.get("user_added", [])
    if not isinstance(user_added, list):
        user_added = []
    
    # Build trigger settings
    trigger_settings = {
        "match_mode": "equals",  # Default to exact match for user-added
        "case_sensitive": False,
    }
    if allowed_role_ids:
        trigger_settings["allowed_role_ids"] = allowed_role_ids
    if blocked_role_ids:
        trigger_settings["blocked_role_ids"] = blocked_role_ids
    
    # Add the trigger
    if embed_data:
        # Add role restrictions to embed data
        if "settings" not in embed_data:
            embed_data["settings"] = trigger_settings
        else:
            embed_data["settings"].update(trigger_settings)
        triggers[trigger] = embed_data
    else:
        triggers[trigger] = {
            "response": response,
            "settings": trigger_settings,
        }
    
    # Mark as user-added
    if trigger not in user_added:
        user_added.append(trigger)
    
    data["triggers"] = triggers
    data["user_added"] = user_added  # type: ignore[assignment]
    
    # Save config
    await _save_guild_config(message.guild.id, data)
    
    # Build confirmation message
    role_info = ""
    if allowed_role_ids:
        role_info += f"\nAllowed roles: {', '.join(str(r) for r in allowed_role_ids)}"
    if blocked_role_ids:
        role_info += f"\nBlocked roles: {', '.join(str(r) for r in blocked_role_ids)}"
    
    if embed_data:
        await message.reply(
            f'Added embed response for trigger: `{trigger}`{role_info}',
            mention_author=False,
        )
    else:
        await message.reply(
            f'Added response for trigger: `{trigger}` → `{response[:50]}{"..." if len(response) > 50 else ""}`{role_info}',
            mention_author=False,
        )
    
    return True


def _parse_embed_params(extra: str) -> Optional[Dict[str, Any]]:
    """Parse embed parameters from command text."""
    # Extract parameters after --embed
    embed_part = extra[extra.lower().find("--embed") + 7:].strip()
    
    params = {}
    # Parse key="value" pairs
    pattern = re.compile(r'(\w+)="([^"]*)"')
    for match in pattern.finditer(embed_part):
        key = match.group(1).lower()
        value = match.group(2)
        params[key] = value
    
    if not params:
        return None
    
    # Build embed structure
    embed_data: Dict[str, Any] = {
        "content": "",
        "embeds": [{}]
    }
    
    embed = embed_data["embeds"][0]
    
    if "title" in params:
        embed["title"] = params["title"]
    if "description" in params:
        embed["description"] = params["description"]
    if "url" in params:
        embed["url"] = params["url"]
    if "color" in params:
        # Parse color (hex)
        color_str = params["color"].lstrip("#")
        try:
            embed["color"] = int(color_str, 16)
        except ValueError:
            embed["color"] = 0x5865F2  # Default Discord blurple
    if "footer" in params:
        embed["footer"] = {"text": params["footer"]}
    
    if not embed:
        return None
    
    return embed_data


async def handle_remove_response_command(message: discord.Message) -> bool:
    """
    Handle the removeresponse text command.
    
    Only allows removing user-added responses, not built-in ones.
    
    Returns True if the command was handled.
    """
    content = message.content.strip()
    
    # Check if it's the removeresponse command
    if not content.lower().startswith("removeresponse"):
        return False
    
    # Must be in a guild
    if not message.guild:
        return False
    
    # Must be a mod
    if not isinstance(message.author, discord.Member):
        return False
    
    # Check module and command permissions (guild-specific)
    if not await is_module_enabled(message.guild.id, "autoresponder"):
        await message.reply(
            "Auto Responder module is disabled in this server.\n"
            "An administrator can enable it with `modules enable autoresponder`",
            mention_author=False,
        )
        return True
    
    if not await can_use_command(message.author, "removeresponse"):
        await message.reply(
            "You don't have permission to use this command in this server.\n"
            "An administrator can grant access with `modules allow removeresponse @YourRole`",
            mention_author=False,
        )
        return True
    
    if not _is_mod(message.author):
        await message.reply(
            "You need Administrator, Manage Server, or Manage Messages permission to use this command.",
            mention_author=False,
        )
        return True
    
    # Parse the command
    match = REMOVE_RESPONSE_PATTERN.match(content)
    if not match:
        await message.reply(
            "**Invalid format.**\\n"
            '```\\nremoveresponse "trigger"\\n```',
            mention_author=False,
        )
        return True
    
    trigger = match.group(1).strip()
    
    if not trigger:
        await message.reply(
            "Trigger cannot be empty.",
            mention_author=False,
        )
        return True
    
    # Load current config
    data = await _load_guild_config(message.guild.id)
    if not isinstance(data, dict):
        await message.reply(
            f"No triggers found.",
            mention_author=False,
        )
        return True
    
    triggers = data.get("triggers", {})
    user_added = data.get("user_added", [])
    
    if not isinstance(triggers, dict):
        triggers = {}
    if not isinstance(user_added, list):
        user_added = []
    
    # Check if trigger exists
    if trigger not in triggers:
        await message.reply(
            f'Trigger `{trigger}` not found.',
            mention_author=False,
        )
        return True
    
    # Check if it's user-added
    if trigger not in user_added:
        await message.reply(
            f"Cannot remove `{trigger}` - this is a built-in trigger.\\n"
            "You can only remove triggers you added yourself.",
            mention_author=False,
        )
        return True
    
    # Remove the trigger
    del triggers[trigger]
    user_added.remove(trigger)
    
    data["triggers"] = triggers
    data["user_added"] = user_added
    
    # Save config
    await _save_guild_config(message.guild.id, data)
    
    await message.reply(
        f'Removed trigger: `{trigger}`',
        mention_author=False,
    )
    
    return True
