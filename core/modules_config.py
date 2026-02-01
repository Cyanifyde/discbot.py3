from __future__ import annotations

import asyncio
from typing import Dict, Iterable, Set

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import safe_int

GLOBAL_MODULES_PATH = BASE_DIR / "modules.conf"
GUILD_MODULES_DIR = BASE_DIR / "config.guild"
GUILD_MODULES_SUFFIX = ".modules.conf"
AUTO_RESPONDER_SUFFIX = ".autoresponder.json"
AUTO_RESPONDER_TEMPLATE = GUILD_MODULES_DIR / f"template{AUTO_RESPONDER_SUFFIX}"
AVAILABLE_MODULES: Set[str] = {"autoresponder"}
DEFAULT_GLOBAL_CONF = {
    "modules": sorted(AVAILABLE_MODULES),
    "default_enabled": sorted(AVAILABLE_MODULES),
    "guilds": {},
}


def _normalize_module_list(values: Iterable[object]) -> Set[str]:
    modules: Set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        name = value.strip().lower()
        if name:
            modules.add(name)
    return modules


def _normalize_mode(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() == "replace":
        return "replace"
    return "additive"


def _normalize_override_block(value: object) -> Dict[str, object]:
    if isinstance(value, list):
        return {"mode": "replace", "enabled": value, "disabled": []}
    if isinstance(value, dict):
        return value
    return {}


def _apply_overrides(
    base: Set[str],
    available: Set[str],
    override: Dict[str, object],
) -> Set[str]:
    mode = _normalize_mode(override.get("mode"))
    enabled = _normalize_module_list(override.get("enabled", [])) & available
    disabled = _normalize_module_list(override.get("disabled", [])) & available
    if mode == "replace":
        return set(enabled)
    return (base | enabled) - disabled


def _extract_available_modules(data: Dict[str, object]) -> Set[str]:
    modules = data.get("modules")
    if not isinstance(modules, list):
        return set(AVAILABLE_MODULES)
    normalized = _normalize_module_list(modules)
    return normalized | AVAILABLE_MODULES


def _extract_default_enabled(data: Dict[str, object], available: Set[str]) -> Set[str]:
    default_enabled = data.get("default_enabled")
    if not isinstance(default_enabled, list):
        return set(available)
    return _normalize_module_list(default_enabled) & available


async def load_guild_enabled_modules(guild_id: int) -> Set[str]:
    data = await ensure_global_modules_conf()
    available = _extract_available_modules(data)
    enabled = _extract_default_enabled(data, available)
    guilds = data.get("guilds", {})
    if isinstance(guilds, dict):
        override = _normalize_override_block(guilds.get(str(guild_id), guilds.get(guild_id)))
        enabled = _apply_overrides(enabled, available, override)
    override_path = GUILD_MODULES_DIR / f"{guild_id}{GUILD_MODULES_SUFFIX}"
    local = await read_json(override_path, default=None)
    if isinstance(local, dict):
        enabled = _apply_overrides(enabled, available, local)
    return enabled


async def ensure_global_modules_conf() -> Dict[str, object]:
    data = await read_json(GLOBAL_MODULES_PATH, default=None)
    if not isinstance(data, dict):
        await write_json_atomic(GLOBAL_MODULES_PATH, DEFAULT_GLOBAL_CONF)
        return dict(DEFAULT_GLOBAL_CONF)
    changed = False
    modules = data.get("modules")
    if not isinstance(modules, list):
        data["modules"] = sorted(AVAILABLE_MODULES)
        changed = True
    else:
        normalized = _normalize_module_list(modules)
        merged = normalized | AVAILABLE_MODULES
        if merged != normalized:
            data["modules"] = sorted(merged)
            changed = True
    default_enabled = data.get("default_enabled")
    if not isinstance(default_enabled, list):
        data["default_enabled"] = sorted(_normalize_module_list(data.get("modules", [])) or AVAILABLE_MODULES)
        changed = True
    if not isinstance(data.get("guilds"), dict):
        data["guilds"] = {}
        changed = True
    if changed:
        await write_json_atomic(GLOBAL_MODULES_PATH, data)
    return data


async def ensure_guild_modules_conf(guild_id: int) -> Dict[str, object]:
    path = GUILD_MODULES_DIR / f"{guild_id}{GUILD_MODULES_SUFFIX}"
    data = await read_json(path, default=None)
    if not isinstance(data, dict):
        payload = {"mode": "additive", "enabled": [], "disabled": []}
        await write_json_atomic(path, payload)
        return dict(payload)
    changed = False
    if "mode" in data and _normalize_mode(data.get("mode")) != data.get("mode"):
        data["mode"] = _normalize_mode(data.get("mode"))
        changed = True
    if "mode" not in data:
        data["mode"] = "additive"
        changed = True
    if "enabled" not in data or not isinstance(data.get("enabled"), list):
        data["enabled"] = []
        changed = True
    if "disabled" not in data or not isinstance(data.get("disabled"), list):
        data["disabled"] = []
        changed = True
    if changed:
        await write_json_atomic(path, data)
    return data


async def ensure_guild_autoresponder_conf(guild_id: int) -> None:
    path = GUILD_MODULES_DIR / f"{guild_id}{AUTO_RESPONDER_SUFFIX}"
    exists = await asyncio.to_thread(path.exists)
    if exists:
        return
    template = await read_json(AUTO_RESPONDER_TEMPLATE, default=None)
    if not isinstance(template, dict):
        template = {}
    await write_json_atomic(path, template)


async def module_is_enabled(guild_id: int, module_name: str) -> bool:
    name = module_name.strip().lower()
    if not name:
        return False
    enabled = await load_guild_enabled_modules(guild_id)
    return name in enabled
