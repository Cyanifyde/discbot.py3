"""
Link storage - persistent storage for cross-server linking.

Handles pending link keys and parent/child relationships between servers.
"""
from __future__ import annotations

import asyncio
import secrets
import string
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso, iso_to_dt

# Storage directory
LINKS_DIR = BASE_DIR / "data" / "links"

# Pending links file (shared across all guilds)
PENDING_LINKS_PATH = LINKS_DIR / "pending_links.json"

# Link key settings
KEY_LENGTH = 6
KEY_EXPIRY_MINUTES = 5

# Trust levels
TrustLevel = Literal["trusted", "readonly"]


def generate_link_key() -> str:
    """Generate a random 6-character alphanumeric key."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(KEY_LENGTH))


class LinkStorage:
    """Storage for cross-server links."""

    def __init__(self) -> None:
        self._pending_lock = asyncio.Lock()
        self._guild_locks: Dict[int, asyncio.Lock] = {}

    def _get_guild_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create a lock for a specific guild."""
        if guild_id not in self._guild_locks:
            self._guild_locks[guild_id] = asyncio.Lock()
        return self._guild_locks[guild_id]

    def _guild_links_path(self, guild_id: int) -> Path:
        """Get the path to a guild's links file."""
        return LINKS_DIR / f"{guild_id}_links.json"

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(LINKS_DIR.mkdir, parents=True, exist_ok=True)

    # ─── Pending Links ────────────────────────────────────────────────────────

    async def _read_pending(self) -> Dict[str, Dict[str, Any]]:
        """Read pending links file."""
        data = await read_json(PENDING_LINKS_PATH, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_pending(self, data: Dict[str, Dict[str, Any]]) -> None:
        """Write pending links file."""
        await write_json_atomic(PENDING_LINKS_PATH, data)

    async def _cleanup_expired_pending(self, data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Remove expired pending links."""
        now = utcnow()
        cleaned = {}
        for key, info in data.items():
            expires_at = iso_to_dt(info.get("expires_at"))
            if expires_at and expires_at > now:
                cleaned[key] = info
        return cleaned

    async def create_pending_link(
        self,
        parent_guild_id: int,
        parent_guild_name: str,
        created_by_user_id: int,
        is_admin_key: bool,
    ) -> str:
        """
        Create a pending link key.

        Args:
            parent_guild_id: The guild that will be the parent
            parent_guild_name: Name of the parent guild
            created_by_user_id: User who created the key
            is_admin_key: True if created by admin (trusted), False for public (readonly)

        Returns:
            The generated link key
        """
        async with self._pending_lock:
            data = await self._read_pending()
            data = await self._cleanup_expired_pending(data)

            # Generate unique key
            key = generate_link_key()
            attempts = 0
            while key in data and attempts < 10:
                key = generate_link_key()
                attempts += 1

            expires_at = utcnow() + timedelta(minutes=KEY_EXPIRY_MINUTES)

            data[key] = {
                "parent_guild_id": str(parent_guild_id),
                "parent_guild_name": parent_guild_name,
                "created_by_user_id": str(created_by_user_id),
                "is_admin_key": is_admin_key,
                "expires_at": dt_to_iso(expires_at),
            }

            await self._write_pending(data)
            return key

    async def get_pending_link(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get a pending link by key.

        Returns None if not found or expired.
        """
        async with self._pending_lock:
            data = await self._read_pending()
            info = data.get(key.upper())

            if not info:
                return None

            expires_at = iso_to_dt(info.get("expires_at"))
            if not expires_at or expires_at <= utcnow():
                return None

            return info

    async def consume_pending_link(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Consume (use and remove) a pending link.

        Returns the link info if valid, None otherwise.
        """
        async with self._pending_lock:
            data = await self._read_pending()
            info = data.get(key.upper())

            if not info:
                return None

            expires_at = iso_to_dt(info.get("expires_at"))
            if not expires_at or expires_at <= utcnow():
                # Remove expired key
                del data[key.upper()]
                await self._write_pending(data)
                return None

            # Remove the key (consumed)
            del data[key.upper()]
            await self._write_pending(data)
            return info

    # ─── Guild Links ──────────────────────────────────────────────────────────

    async def _read_guild_links(self, guild_id: int) -> Dict[str, Any]:
        """Read a guild's links file."""
        path = self._guild_links_path(guild_id)
        data = await read_json(path, default=None)
        if not isinstance(data, dict):
            return {"parents": [], "children": [], "protection": {}}
        if "parents" not in data:
            data["parents"] = []
        if "children" not in data:
            data["children"] = []
        if "protection" not in data or not isinstance(data["protection"], dict):
            data["protection"] = {}
        return data

    async def _write_guild_links(self, guild_id: int, data: Dict[str, Any]) -> None:
        """Write a guild's links file."""
        path = self._guild_links_path(guild_id)
        await write_json_atomic(path, data)

    async def add_parent_link(
        self,
        child_guild_id: int,
        parent_guild_id: int,
        parent_guild_name: str,
        trust_level: TrustLevel,
    ) -> bool:
        """
        Add a parent link to a guild (guild becomes child of parent).

        Returns True if added, False if already exists.
        """
        lock = self._get_guild_lock(child_guild_id)
        async with lock:
            data = await self._read_guild_links(child_guild_id)

            # Check if already linked
            for parent in data["parents"]:
                if str(parent.get("guild_id")) == str(parent_guild_id):
                    return False

            data["parents"].append({
                "guild_id": str(parent_guild_id),
                "guild_name": parent_guild_name,
                "trust_level": trust_level,
                "sync_bans": True,
                "sync_kicks": True,
                "sync_mutes": True,
                "sync_warnings": True,
                "sync_autoresponder": False,
                "sync_hashes": False,
                "created_at": dt_to_iso(utcnow()),
            })

            await self._write_guild_links(child_guild_id, data)
            return True

    async def add_child_link(
        self,
        parent_guild_id: int,
        child_guild_id: int,
        child_guild_name: str,
        trust_level: TrustLevel,
    ) -> bool:
        """
        Add a child link to a guild (guild becomes parent of child).

        Returns True if added, False if already exists.
        """
        lock = self._get_guild_lock(parent_guild_id)
        async with lock:
            data = await self._read_guild_links(parent_guild_id)

            # Check if already linked
            for child in data["children"]:
                if str(child.get("guild_id")) == str(child_guild_id):
                    return False

            data["children"].append({
                "guild_id": str(child_guild_id),
                "guild_name": child_guild_name,
                "trust_level": trust_level,
                "accept_upstream": False,  # Parent must opt-in
                "approval_channel_id": None,
                "auto_cascade": True,
                "sync_bans": True,
                "sync_kicks": True,
                "sync_mutes": True,
                "sync_warnings": True,
                "sync_autoresponder": False,
                "sync_hashes": False,
                "created_at": dt_to_iso(utcnow()),
            })

            await self._write_guild_links(parent_guild_id, data)
            return True

    async def get_parents(self, guild_id: int) -> List[Dict[str, Any]]:
        """Get all parent links for a guild."""
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)
            return list(data.get("parents", []))

    async def get_children(self, guild_id: int) -> List[Dict[str, Any]]:
        """Get all child links for a guild."""
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)
            return list(data.get("children", []))

    async def get_parent(self, guild_id: int, parent_guild_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific parent link."""
        parents = await self.get_parents(guild_id)
        for parent in parents:
            if str(parent.get("guild_id")) == str(parent_guild_id):
                return parent
        return None

    async def get_child(self, guild_id: int, child_guild_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific child link."""
        children = await self.get_children(guild_id)
        for child in children:
            if str(child.get("guild_id")) == str(child_guild_id):
                return child
        return None

    async def get_protection_settings(self, guild_id: int) -> Dict[str, Any]:
        """Get sync protection settings for a guild."""
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)
            settings = data.get("protection", {})
            return dict(settings) if isinstance(settings, dict) else {}

    async def update_protection_settings(
        self,
        guild_id: int,
        **settings: Any,
    ) -> None:
        """Update sync protection settings for a guild."""
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)
            protection = data.get("protection")
            if not isinstance(protection, dict):
                protection = {}
            for key, value in settings.items():
                if value is None:
                    protection.pop(key, None)
                else:
                    protection[key] = value
            data["protection"] = protection
            await self._write_guild_links(guild_id, data)

    async def remove_parent_link(self, guild_id: int, parent_guild_id: int) -> bool:
        """
        Remove a parent link.

        Returns True if removed, False if not found.
        """
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)

            original_len = len(data["parents"])
            data["parents"] = [
                p for p in data["parents"]
                if str(p.get("guild_id")) != str(parent_guild_id)
            ]

            if len(data["parents"]) == original_len:
                return False

            await self._write_guild_links(guild_id, data)
            return True

    async def remove_child_link(self, guild_id: int, child_guild_id: int) -> bool:
        """
        Remove a child link.

        Returns True if removed, False if not found.
        """
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)

            original_len = len(data["children"])
            data["children"] = [
                c for c in data["children"]
                if str(c.get("guild_id")) != str(child_guild_id)
            ]

            if len(data["children"]) == original_len:
                return False

            await self._write_guild_links(guild_id, data)
            return True

    async def update_parent_settings(
        self,
        guild_id: int,
        parent_guild_id: int,
        **settings: Any,
    ) -> bool:
        """
        Update settings for a parent link.

        Valid settings: sync_bans, sync_kicks, sync_mutes, sync_warnings,
                       sync_autoresponder, sync_hashes

        Returns True if updated, False if link not found.
        """
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)

            for parent in data["parents"]:
                if str(parent.get("guild_id")) == str(parent_guild_id):
                    for key, value in settings.items():
                        if key in parent:
                            parent[key] = value
                    await self._write_guild_links(guild_id, data)
                    return True

            return False

    async def update_child_settings(
        self,
        guild_id: int,
        child_guild_id: int,
        **settings: Any,
    ) -> bool:
        """
        Update settings for a child link.

        Valid settings: accept_upstream, approval_channel_id, auto_cascade,
                       sync_bans, sync_kicks, sync_mutes, sync_warnings,
                       sync_autoresponder, sync_hashes

        Returns True if updated, False if link not found.
        """
        lock = self._get_guild_lock(guild_id)
        async with lock:
            data = await self._read_guild_links(guild_id)

            for child in data["children"]:
                if str(child.get("guild_id")) == str(child_guild_id):
                    for key, value in settings.items():
                        if key in child or key in ["accept_upstream", "approval_channel_id", "auto_cascade"]:
                            child[key] = value
                    await self._write_guild_links(guild_id, data)
                    return True

            return False


# Global singleton instance
_storage: Optional[LinkStorage] = None
_storage_lock = asyncio.Lock()


async def get_link_storage() -> LinkStorage:
    """Get or create the global LinkStorage instance."""
    global _storage
    async with _storage_lock:
        if _storage is None:
            _storage = LinkStorage()
            await _storage.initialize()
        return _storage
