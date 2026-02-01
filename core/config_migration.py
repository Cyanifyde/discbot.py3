"""
Config migration system - auto-updates configs when templates change.

This module provides utilities to merge new template defaults into existing
guild configs without overwriting user data.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR

logger = logging.getLogger("discbot.config_migration")

GUILD_CONFIG_DIR = BASE_DIR / "config.guild"


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any], _path: str = "") -> Dict[str, Any]:
    """
    Deep merge overlay into base, returning a new dict.
    
    - New keys from base are added to overlay
    - Existing keys in overlay are preserved
    - Nested dicts are recursively merged
    - Lists and other values in overlay take precedence
    """
    result = dict(overlay)
    
    for key, base_value in base.items():
        if key not in result:
            # Key doesn't exist in overlay, add it from base
            result[key] = base_value
            logger.debug("Added new key %s%s", _path, key)
        elif isinstance(base_value, dict) and isinstance(result[key], dict):
            # Both are dicts, merge recursively
            result[key] = deep_merge(base_value, result[key], f"{_path}{key}.")
        # Otherwise, keep the overlay value (user's data)
    
    return result


async def migrate_json_config(
    template_path: Path,
    config_path: Path,
    preserve_keys: Optional[Set[str]] = None,
) -> bool:
    """
    Migrate a JSON config file to match a template.
    
    Args:
        template_path: Path to the template file with default structure
        config_path: Path to the existing config to migrate
        preserve_keys: Keys that should never be overwritten (even if missing from template)
    
    Returns:
        True if migration occurred, False if no changes needed
    """
    template = await read_json(template_path, default=None)
    if template is None:
        logger.warning("Template not found: %s", template_path)
        return False
    
    if not isinstance(template, dict):
        logger.warning("Template is not a dict: %s", template_path)
        return False
    
    existing = await read_json(config_path, default=None)
    if existing is None:
        # Config doesn't exist, just copy template
        await write_json_atomic(config_path, template)
        logger.info("Created new config from template: %s", config_path)
        return True
    
    if not isinstance(existing, dict):
        logger.warning("Existing config is not a dict: %s", config_path)
        return False
    
    # Merge template into existing (existing takes precedence)
    merged = deep_merge(template, existing)
    
    # Preserve special keys that shouldn't be removed
    if preserve_keys:
        for key in preserve_keys:
            if key in existing and key not in merged:
                merged[key] = existing[key]
    
    # Check if anything changed
    if merged == existing:
        return False
    
    await write_json_atomic(config_path, merged)
    logger.info("Migrated config: %s", config_path)
    return True


async def migrate_guild_autoresponder(guild_id: int) -> bool:
    """Migrate a guild's autoresponder config to match the template."""
    template_path = GUILD_CONFIG_DIR / "template.autoresponder.json"
    config_path = GUILD_CONFIG_DIR / f"{guild_id}.autoresponder.json"
    
    return await migrate_json_config(
        template_path,
        config_path,
        preserve_keys={"triggers"},  # Never lose user's triggers
    )


async def migrate_guild_modules(guild_id: int) -> bool:
    """Migrate a guild's modules config to match the template."""
    template_path = GUILD_CONFIG_DIR / "template.modules.conf"
    config_path = GUILD_CONFIG_DIR / f"{guild_id}.modules.conf"
    
    return await migrate_json_config(template_path, config_path)


async def migrate_all_guild_configs() -> Dict[str, int]:
    """
    Migrate all guild configs to match current templates.
    
    Returns:
        Dict with counts: {"autoresponder": N, "modules": M}
    """
    results = {"autoresponder": 0, "modules": 0}
    
    # Find all guild config files
    if not GUILD_CONFIG_DIR.exists():
        return results
    
    guild_ids: Set[int] = set()
    
    for path in GUILD_CONFIG_DIR.iterdir():
        if path.suffix == ".json" and path.stem.isdigit():
            guild_ids.add(int(path.stem))
        elif path.name.endswith(".autoresponder.json"):
            try:
                guild_id = int(path.name.replace(".autoresponder.json", ""))
                guild_ids.add(guild_id)
            except ValueError:
                pass
    
    for guild_id in guild_ids:
        if await migrate_guild_autoresponder(guild_id):
            results["autoresponder"] += 1
        if await migrate_guild_modules(guild_id):
            results["modules"] += 1
    
    if results["autoresponder"] or results["modules"]:
        logger.info(
            "Config migration complete: %d autoresponder, %d modules configs updated",
            results["autoresponder"],
            results["modules"],
        )
    
    return results


async def ensure_guild_module_data(
    guild_id: int,
    module_name: str,
    default_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Ensure a module's data section exists in the guild config.
    
    This is used by modules to store persistent data (like verification buttons).
    
    Args:
        guild_id: The guild ID
        module_name: Name of the module (e.g., "verification")
        default_data: Default data structure if none exists
    
    Returns:
        The module's data (existing or newly created)
    """
    config_path = GUILD_CONFIG_DIR / f"{guild_id}.json"
    
    config = await read_json(config_path, default=None)
    if config is None or not isinstance(config, dict):
        return default_data
    
    # Module data is stored under "module_data" key
    module_data = config.get("module_data", {})
    if not isinstance(module_data, dict):
        module_data = {}
    
    if module_name not in module_data:
        module_data[module_name] = default_data
        config["module_data"] = module_data
        await write_json_atomic(config_path, config)
        return default_data
    
    return module_data[module_name]


async def update_guild_module_data(
    guild_id: int,
    module_name: str,
    data: Dict[str, Any],
) -> None:
    """
    Update a module's data section in the guild config.
    
    Args:
        guild_id: The guild ID
        module_name: Name of the module
        data: The data to store
    """
    config_path = GUILD_CONFIG_DIR / f"{guild_id}.json"
    
    config = await read_json(config_path, default=None)
    if config is None or not isinstance(config, dict):
        config = {"guild_id": guild_id}
    
    module_data = config.get("module_data", {})
    if not isinstance(module_data, dict):
        module_data = {}
    
    module_data[module_name] = data
    config["module_data"] = module_data
    
    await write_json_atomic(config_path, config)


async def get_guild_module_data(
    guild_id: int,
    module_name: str,
) -> Optional[Dict[str, Any]]:
    """
    Get a module's data from the guild config.
    
    Returns None if not found.
    """
    config_path = GUILD_CONFIG_DIR / f"{guild_id}.json"
    
    config = await read_json(config_path, default=None)
    if config is None or not isinstance(config, dict):
        return None
    
    module_data = config.get("module_data", {})
    if not isinstance(module_data, dict):
        return None
    
    return module_data.get(module_name)
