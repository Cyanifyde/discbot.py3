"""
Guild configuration loading and validation.

Handles loading, validating, and normalizing guild-specific configuration files.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import is_int, is_safe_relative_path, is_sha256_hex, is_valid_id

OWNER_ID = 701780009777496094

DEFAULT_CONFIG_PATH = BASE_DIR / "config.default.json"
GUILD_CONFIG_DIR = BASE_DIR / "config.guild"
HASHES_DEFAULT_PATH = "./hashes.txt"

DEFAULT_CONFIG: Dict[str, Any] = {
    "guild_id": 0,
    "unverified_role_id": None,
    "action_log_channel_id": None,
    "excluded_channel_ids": [],
    "ignored_channel_ids": [],
    "exemptions": [],
    "exempt_role_ids": [],
    "max_image_bytes": 5_242_880,
    "first_run_grace_days": 3,
    "inactive_days_threshold": 7,
    "inactivity_message_threshold": 3,
    "snapshot_members_per_run": 200,
    "enforcement_scan_max_users_per_run": 200,
    "queue_max_jobs": 1_000,
    "queue_compact_threshold_bytes": 5_000_000,
    "worker_count": 2,
    "worker_job_timeout_seconds": 15,
    "queue_flush_interval_seconds": 30,
    "queue_state_flush_interval_seconds": 15,
    "hashes_files": [HASHES_DEFAULT_PATH],
    "extra_hashes": [],
    "enable_discord_cdn_url_scan": False,
    "allowed_discord_cdn_domains": ["cdn.discordapp.com", "media.discordapp.net"],
    "enable_discord_message_link_scan": False,
    "token": None,
    "module_data": {},
}

CONFIG_SCHEMA: Dict[str, Tuple[str, bool]] = {
    "guild_id": ("int", True),
    "unverified_role_id": ("int_or_none", False),
    "action_log_channel_id": ("int_or_none", False),
    "excluded_channel_ids": ("list_int", False),
    "ignored_channel_ids": ("list_int", False),
    "exemptions": ("list_int", False),
    "exempt_role_ids": ("list_int", False),
    "max_image_bytes": ("pos_int", True),
    "first_run_grace_days": ("nonneg_int", True),
    "inactive_days_threshold": ("nonneg_int", True),
    "inactivity_message_threshold": ("nonneg_int", True),
    "snapshot_members_per_run": ("pos_int", True),
    "enforcement_scan_max_users_per_run": ("pos_int", True),
    "queue_max_jobs": ("pos_int", True),
    "queue_compact_threshold_bytes": ("pos_int", True),
    "worker_count": ("pos_int", True),
    "worker_job_timeout_seconds": ("pos_int", True),
    "queue_flush_interval_seconds": ("pos_int", True),
    "queue_state_flush_interval_seconds": ("pos_int", True),
    "hashes_files": ("list_str", True),
    "extra_hashes": ("list_sha256", True),
    "enable_discord_cdn_url_scan": ("bool", True),
    "allowed_discord_cdn_domains": ("list_str", True),
    "enable_discord_message_link_scan": ("bool", True),
    "token": ("str_or_none", False),
    "module_data": ("dict", False),
}


class ConfigError(RuntimeError):
    pass


def validate_and_normalize_config(data: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    normalized: Dict[str, Any] = {}

    for key, (type_name, required) in CONFIG_SCHEMA.items():
        if key not in data:
            if required:
                errors.append(f"Missing required config key: {key}")
            else:
                normalized[key] = DEFAULT_CONFIG.get(key)
            continue
        value = data[key]
        if type_name == "int":
            if not is_valid_id(value):
                errors.append(f"{key} must be an integer ID")
                continue
            normalized[key] = int(value)
        elif type_name == "int_or_none":
            if value is None:
                normalized[key] = None
            elif is_valid_id(value):
                normalized[key] = int(value)
            else:
                errors.append(f"{key} must be an integer ID or null")
        elif type_name == "pos_int":
            if not is_int(value) or value <= 0:
                errors.append(f"{key} must be a positive integer")
            else:
                normalized[key] = int(value)
        elif type_name == "nonneg_int":
            if not is_int(value) or value < 0:
                errors.append(f"{key} must be a non-negative integer")
            else:
                normalized[key] = int(value)
        elif type_name == "bool":
            if not isinstance(value, bool):
                errors.append(f"{key} must be a boolean")
            else:
                normalized[key] = value
        elif type_name == "list_int":
            if not isinstance(value, list):
                errors.append(f"{key} must be a list of integer IDs")
                continue
            items: List[int] = []
            for item in value:
                if not is_valid_id(item):
                    errors.append(f"{key} must be a list of integer IDs")
                    items = []
                    break
                items.append(int(item))
            normalized[key] = items
        elif type_name == "list_str":
            if not isinstance(value, list):
                errors.append(f"{key} must be a list of strings")
                continue
            if any(not isinstance(item, str) for item in value):
                errors.append(f"{key} must be a list of strings")
                continue
            if key == "hashes_files":
                safe_items: List[str] = []
                for item in value:
                    if not is_safe_relative_path(item):
                        errors.append("hashes_files must use safe relative paths")
                        safe_items = []
                        break
                    safe_items.append(item)
                normalized[key] = safe_items
            else:
                normalized[key] = list(value)
        elif type_name == "list_sha256":
            if not isinstance(value, list):
                errors.append(f"{key} must be a list of sha256 hex strings")
                continue
            hashes: List[str] = []
            for item in value:
                if not isinstance(item, str) or not is_sha256_hex(item):
                    errors.append(f"{key} must be a list of sha256 hex strings")
                    hashes = []
                    break
                hashes.append(item.lower())
            normalized[key] = hashes
        elif type_name == "str_or_none":
            if value is None:
                normalized[key] = None
            elif isinstance(value, str):
                normalized[key] = value
            else:
                errors.append(f"{key} must be a string or null")
        elif type_name == "dict":
            if not isinstance(value, dict):
                errors.append(f"{key} must be a dict/object")
            else:
                normalized[key] = value
        else:
            errors.append(f"Unknown config type for {key}")

    if errors:
        raise ConfigError("; ".join(errors))

    if normalized.get("guild_id", 0) <= 0:
        raise ConfigError("guild_id must be set to a valid guild ID")

    allowed_domains = normalized.get("allowed_discord_cdn_domains") or []
    normalized["allowed_discord_cdn_domains"] = [d.lower() for d in allowed_domains]

    if OWNER_ID in (normalized.get("exemptions") or []):
        raise ConfigError("OWNER_ID must not appear in config files")

    return normalized


async def load_default_template() -> Dict[str, Any]:
    data = await read_json(DEFAULT_CONFIG_PATH, default=None)
    if data is None:
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        raise ConfigError("config.default.json must be a JSON object")
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


async def load_guild_config(guild_id: int) -> Dict[str, Any]:
    path = GUILD_CONFIG_DIR / f"{guild_id}.json"
    data = await read_json(path, default=None)
    if data is None:
        raise ConfigError(f"Missing guild config: {path}")
    if not isinstance(data, dict):
        raise ConfigError("Guild override config must be a JSON object")
    value = data.get("guild_id")
    if not is_valid_id(value):
        raise ConfigError("guild_id must be set to a valid guild ID")
    if int(value) != int(guild_id):
        raise ConfigError("guild_id in override does not match config file name")
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return validate_and_normalize_config(merged)


async def ensure_guild_config(guild_id: int, template: Dict[str, Any]) -> Dict[str, Any]:
    path = GUILD_CONFIG_DIR / f"{guild_id}.json"
    exists = await asyncio.to_thread(path.exists)
    if exists:
        return await load_guild_config(guild_id)
    seeded = dict(template)
    seeded["guild_id"] = int(guild_id)
    normalized = validate_and_normalize_config(seeded)
    await write_json_atomic(path, normalized)
    return normalized
