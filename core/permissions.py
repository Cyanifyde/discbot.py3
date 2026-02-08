"""
Guild-specific permission system for modules and commands.

Each guild has its own permission configuration with no data leaking.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

import discord

from core.config_migration import get_guild_module_data, update_guild_module_data

if TYPE_CHECKING:
    pass

# Module name for storing permissions
PERMISSIONS_MODULE = "permissions"

# Available modules
AVAILABLE_MODULES = {
    "scanner": "Image hash scanning for suspicious content",
    "inactivity": "Enforce actions against inactive users",
    "verification": "Role-based verification via buttons",
    "autoresponder": "Automatic responses to triggers",
    "moderation": "Warnings, mutes, bans, kicks, and mod notes",
    "serverstats": "Display server statistics and information",
    "serverlink": "Cross-server linking for syncing moderation actions",
    "inviteprotection": "Detect and gate Discord invite links with allowlist/approval workflow",
    "artsearch": "Search images posted in approved channels",
    "analytics": "View statistics and trends",
    "ptc": "Pass-the-Canvas turn management for collaborative art threads",
}

# Default enable/disable state for modules when first added to a guild.
# Most modules default enabled; potentially disruptive modules default disabled.
DEFAULT_MODULE_ENABLED = {
    "inviteprotection": False,
    "artsearch": True,
    "ptc": False,
}

# Available commands that can have role restrictions
AVAILABLE_COMMANDS = {
    "scanner": ["scanner enable", "scanner disable", "scanner status", "scanner help"],
    "inactivity": ["inactivity enable", "inactivity disable", "inactivity status", "inactivity help"],
    "verification": ["addverification", "removeverification"],
    "autoresponder": ["addresponse", "listresponses", "removeresponse"],
    "moderation": [
        "warn", "warnings", "clearwarning", "clearwarnings",
        "mute", "unmute", "ban", "unban", "kick",
        "note", "notes", "clearnote",
    ],
    "serverstats": ["serverstats", "serverstats help", "botstatus", "botstatus help"],
    "serverlink": ["linkserver", "addlink", "links", "unlink", "linksettings", "linkprotection"],
    "inviteprotection": [
        "invite allowlist",
        "invite approve",
        "invite deny",
        "invite modlog",
        "invite pending",
        "invite status",
    ],
    "artsearch": ["art search", "art channels", "art help"],
    "ptc": [
        "ptc setforum", "ptc bind", "ptc unbind", "ptc start", "ptc status",
        "ptc clear", "ptc force_clear", "ptc debug", "ptc help",
    ],
}


async def get_guild_permissions(guild_id: int) -> Dict[str, Any]:
    """
    Get permission configuration for a guild.
    
    Returns guild-specific permissions with no cross-guild data.
    """
    data = await _get_guild_permissions_cached(guild_id)
    if not isinstance(data, dict):
        # Default: all modules enabled, no role restrictions (admin-only)
        data = {
            "modules": {
                module: {"enabled": DEFAULT_MODULE_ENABLED.get(module, True), "allowed_roles": []}
                for module in AVAILABLE_MODULES
            },
            "commands": {}
        }
    
    # Ensure structure
    if "modules" not in data:
        data["modules"] = {}
    if "commands" not in data:
        data["commands"] = {}
    
    # Add any missing modules with defaults
    for module in AVAILABLE_MODULES:
        if module not in data["modules"]:
            data["modules"][module] = {
                "enabled": DEFAULT_MODULE_ENABLED.get(module, True),
                "allowed_roles": [],
            }

    # Cache the normalized dict to avoid re-normalizing on every call.
    _PERMS_CACHE[int(guild_id)] = (time.monotonic(), data)
    
    return data


async def save_guild_permissions(guild_id: int, data: Dict[str, Any]) -> None:
    """Save guild-specific permission configuration."""
    await update_guild_module_data(guild_id, PERMISSIONS_MODULE, data)
    invalidate_guild_permissions_cache(guild_id)


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

# guild_id -> (monotonic_ts, permissions_data)
_PERMS_CACHE: Dict[int, tuple[float, Dict[str, Any]]] = {}
_PERMS_LOCKS: Dict[int, asyncio.Lock] = {}
CACHE_TTL_SECONDS = 5.0


def invalidate_guild_permissions_cache(guild_id: int) -> None:
    """Invalidate cached permissions for a guild."""
    _PERMS_CACHE.pop(int(guild_id), None)


async def _get_guild_permissions_cached(guild_id: int) -> Any:
    gid = int(guild_id)
    now = time.monotonic()
    cached = _PERMS_CACHE.get(gid)
    if cached:
        ts, data = cached
        if now - ts < CACHE_TTL_SECONDS:
            return data

    lock = _PERMS_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _PERMS_LOCKS[gid] = lock

    async with lock:
        now2 = time.monotonic()
        cached2 = _PERMS_CACHE.get(gid)
        if cached2:
            ts2, data2 = cached2
            if now2 - ts2 < CACHE_TTL_SECONDS:
                return data2

        data = await get_guild_module_data(gid, PERMISSIONS_MODULE)
        if isinstance(data, dict):
            _PERMS_CACHE[gid] = (time.monotonic(), data)
        else:
            # Cache non-dict values as-is for TTL to avoid repeated disk hits on corrupt data.
            _PERMS_CACHE[gid] = (time.monotonic(), data)  # type: ignore[assignment]
        return data


async def is_module_enabled(guild_id: int, module: str) -> bool:
    """Check if a module is enabled for a specific guild."""
    perms = await get_guild_permissions(guild_id)
    module_data = perms.get("modules", {}).get(module, {})
    return module_data.get("enabled", True)


async def set_module_enabled(guild_id: int, module: str, enabled: bool) -> bool:
    """
    Enable or disable a module for a specific guild.
    
    Returns True if successful, False if module not found.
    """
    if module not in AVAILABLE_MODULES:
        return False
    
    perms = await get_guild_permissions(guild_id)
    if module not in perms["modules"]:
        perms["modules"][module] = {"enabled": True, "allowed_roles": []}
    
    perms["modules"][module]["enabled"] = enabled
    await save_guild_permissions(guild_id, perms)
    return True


async def can_use_module(member: discord.Member, module: str) -> bool:
    """
    Check if a member can use a specific module.
    
    Returns True if:
    - Module is enabled for the guild
    - Member is admin, OR
    - Member has a role in the allowed_roles list, OR
    - No roles are specified (defaults to admin-only)
    """
    guild_id = member.guild.id
    
    # Check if module is enabled
    if not await is_module_enabled(guild_id, module):
        return False
    
    # Admins always have access
    if member.guild_permissions.administrator:
        return True
    
    perms = await get_guild_permissions(guild_id)
    module_data = perms.get("modules", {}).get(module, {})
    allowed_roles = module_data.get("allowed_roles", [])
    
    # If no roles specified, only admins can use (already checked above)
    if not allowed_roles:
        return False
    
    # Check if member has any of the allowed roles
    member_role_ids = {role.id for role in member.roles}
    return bool(member_role_ids & set(allowed_roles))


async def can_use_command(member: discord.Member, command: str) -> bool:
    """
    Check if a member can use a specific command.
    
    Returns True if:
    - Member is admin, OR
    - Member has a role in the command's allowed_roles list, OR
    - No roles are specified for the command (defaults to admin-only)
    """
    guild_id = member.guild.id
    
    # Admins always have access
    if member.guild_permissions.administrator:
        return True
    
    perms = await get_guild_permissions(guild_id)
    command_data = perms.get("commands", {}).get(command, {})
    allowed_roles = command_data.get("allowed_roles", [])
    
    # If no roles specified, only admins can use (already checked above)
    if not allowed_roles:
        return False
    
    # Check if member has any of the allowed roles
    member_role_ids = {role.id for role in member.roles}
    return bool(member_role_ids & set(allowed_roles))


async def add_role_to_module(guild_id: int, module: str, role_id: int) -> bool:
    """
    Add a role to a module's allowed roles list.
    
    Returns True if successful, False if module not found.
    """
    if module not in AVAILABLE_MODULES:
        return False
    
    perms = await get_guild_permissions(guild_id)
    if module not in perms["modules"]:
        perms["modules"][module] = {"enabled": True, "allowed_roles": []}
    
    allowed_roles = perms["modules"][module].get("allowed_roles", [])
    if not isinstance(allowed_roles, list):
        allowed_roles = []
    
    if role_id not in allowed_roles:
        allowed_roles.append(role_id)
    
    perms["modules"][module]["allowed_roles"] = allowed_roles
    await save_guild_permissions(guild_id, perms)
    return True


async def remove_role_from_module(guild_id: int, module: str, role_id: int) -> bool:
    """
    Remove a role from a module's allowed roles list.
    
    Returns True if successful, False if module not found or role not in list.
    """
    if module not in AVAILABLE_MODULES:
        return False
    
    perms = await get_guild_permissions(guild_id)
    module_data = perms.get("modules", {}).get(module, {})
    allowed_roles = module_data.get("allowed_roles", [])
    
    if not isinstance(allowed_roles, list) or role_id not in allowed_roles:
        return False
    
    allowed_roles.remove(role_id)
    perms["modules"][module]["allowed_roles"] = allowed_roles
    await save_guild_permissions(guild_id, perms)
    return True


async def add_role_to_command(guild_id: int, command: str, role_id: int) -> bool:
    """
    Add a role to a command's allowed roles list.
    
    Returns True if successful.
    """
    perms = await get_guild_permissions(guild_id)
    
    if command not in perms["commands"]:
        perms["commands"][command] = {"allowed_roles": []}
    
    allowed_roles = perms["commands"][command].get("allowed_roles", [])
    if not isinstance(allowed_roles, list):
        allowed_roles = []
    
    if role_id not in allowed_roles:
        allowed_roles.append(role_id)
    
    perms["commands"][command]["allowed_roles"] = allowed_roles
    await save_guild_permissions(guild_id, perms)
    return True


async def remove_role_from_command(guild_id: int, command: str, role_id: int) -> bool:
    """
    Remove a role from a command's allowed roles list.
    
    Returns True if successful, False if role not in list.
    """
    perms = await get_guild_permissions(guild_id)
    command_data = perms.get("commands", {}).get(command, {})
    allowed_roles = command_data.get("allowed_roles", [])
    
    if not isinstance(allowed_roles, list) or role_id not in allowed_roles:
        return False
    
    allowed_roles.remove(role_id)
    perms["commands"][command]["allowed_roles"] = allowed_roles
    await save_guild_permissions(guild_id, perms)
    return True


async def get_module_roles(guild_id: int, module: str) -> List[int]:
    """Get the list of role IDs allowed to use a module."""
    perms = await get_guild_permissions(guild_id)
    module_data = perms.get("modules", {}).get(module, {})
    allowed_roles = module_data.get("allowed_roles", [])
    return list(allowed_roles) if isinstance(allowed_roles, list) else []


async def get_command_roles(guild_id: int, command: str) -> List[int]:
    """Get the list of role IDs allowed to use a command."""
    perms = await get_guild_permissions(guild_id)
    command_data = perms.get("commands", {}).get(command, {})
    allowed_roles = command_data.get("allowed_roles", [])
    return list(allowed_roles) if isinstance(allowed_roles, list) else []
