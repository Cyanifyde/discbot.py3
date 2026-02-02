"""
Roles storage - persistent storage for role management features.

Provides storage for temporary roles, role requests, bundles, and reaction roles.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso

# Storage directory
ROLES_DIR = BASE_DIR / "data" / "roles"


class RolesStore:
    """Storage for role management features."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = ROLES_DIR / str(guild_id)
        self.temp_roles_path = self.root / "temp_roles.json"
        self.requests_path = self.root / "requests.json"
        self.bundles_path = self.root / "bundles.json"
        self.reaction_roles_path = self.root / "reaction_roles.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Temporary Roles ──────────────────────────────────────────────────────

    async def _read_temp_roles(self) -> Dict[str, Any]:
        """Read temporary roles file."""
        default = {"temp_roles": []}
        data = await read_json(self.temp_roles_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_temp_roles(self, data: Dict[str, Any]) -> None:
        """Write temporary roles file."""
        await write_json_atomic(self.temp_roles_path, data)

    async def add_temp_role(
        self,
        user_id: int,
        role_id: int,
        expires_at: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Add a temporary role assignment."""
        async with self._lock:
            data = await self._read_temp_roles()

            temp_role = {
                "user_id": user_id,
                "role_id": role_id,
                "added_at": dt_to_iso(utcnow()),
                "expires_at": expires_at,
                "reason": reason,
            }

            data["temp_roles"].append(temp_role)
            await self._write_temp_roles(data)
            return temp_role

    async def get_expired_temp_roles(self) -> List[Dict[str, Any]]:
        """Get all expired temporary roles."""
        from datetime import datetime

        async with self._lock:
            data = await self._read_temp_roles()
            now = utcnow()

            expired = []
            for temp_role in data["temp_roles"]:
                expires_at = datetime.fromisoformat(temp_role["expires_at"].replace("Z", "+00:00"))
                if expires_at <= now:
                    expired.append(temp_role)

            return expired

    async def remove_temp_role(self, user_id: int, role_id: int) -> bool:
        """Remove a temporary role entry."""
        async with self._lock:
            data = await self._read_temp_roles()
            original_len = len(data["temp_roles"])

            data["temp_roles"] = [
                tr for tr in data["temp_roles"]
                if not (tr["user_id"] == user_id and tr["role_id"] == role_id)
            ]

            if len(data["temp_roles"]) < original_len:
                await self._write_temp_roles(data)
                return True

            return False

    # ─── Role Requests ────────────────────────────────────────────────────────

    async def _read_requests(self) -> Dict[str, Any]:
        """Read role requests file."""
        default = {"requests": [], "config": {"requestable_roles": []}}
        data = await read_json(self.requests_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_requests(self, data: Dict[str, Any]) -> None:
        """Write role requests file."""
        await write_json_atomic(self.requests_path, data)

    async def add_role_request(
        self,
        request_id: str,
        user_id: int,
        role_id: int,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Add a role request."""
        async with self._lock:
            data = await self._read_requests()

            request = {
                "id": request_id,
                "user_id": user_id,
                "role_id": role_id,
                "reason": reason,
                "status": "pending",
                "created_at": dt_to_iso(utcnow()),
                "reviewed_by": None,
            }

            data["requests"].append(request)
            await self._write_requests(data)
            return request

    async def update_request_status(
        self,
        request_id: str,
        status: str,
        reviewer_id: int,
    ) -> bool:
        """Update role request status."""
        async with self._lock:
            data = await self._read_requests()

            for request in data["requests"]:
                if request["id"].startswith(request_id):
                    request["status"] = status
                    request["reviewed_by"] = reviewer_id
                    await self._write_requests(data)
                    return True

            return False

    async def get_pending_requests(self) -> List[Dict[str, Any]]:
        """Get all pending role requests."""
        async with self._lock:
            data = await self._read_requests()
            return [r for r in data["requests"] if r["status"] == "pending"]

    # ─── Role Bundles ─────────────────────────────────────────────────────────

    async def _read_bundles(self) -> Dict[str, Any]:
        """Read role bundles file."""
        default = {"bundles": []}
        data = await read_json(self.bundles_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_bundles(self, data: Dict[str, Any]) -> None:
        """Write role bundles file."""
        await write_json_atomic(self.bundles_path, data)

    async def add_bundle(
        self,
        bundle_id: str,
        name: str,
        role_ids: List[int],
    ) -> Dict[str, Any]:
        """Add a role bundle."""
        async with self._lock:
            data = await self._read_bundles()

            bundle = {
                "id": bundle_id,
                "name": name,
                "role_ids": role_ids,
                "created_at": dt_to_iso(utcnow()),
            }

            data["bundles"].append(bundle)
            await self._write_bundles(data)
            return bundle

    async def get_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Get a role bundle."""
        async with self._lock:
            data = await self._read_bundles()
            for bundle in data["bundles"]:
                if bundle["id"].startswith(bundle_id) or bundle["name"].lower() == bundle_id.lower():
                    return bundle
            return None

    async def get_all_bundles(self) -> List[Dict[str, Any]]:
        """Get all role bundles."""
        async with self._lock:
            data = await self._read_bundles()
            return data["bundles"]

    # ─── Reaction Roles ───────────────────────────────────────────────────────

    async def _read_reaction_roles(self) -> Dict[str, Any]:
        """Read reaction roles file."""
        default = {"reaction_roles": {}}
        data = await read_json(self.reaction_roles_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_reaction_roles(self, data: Dict[str, Any]) -> None:
        """Write reaction roles file."""
        await write_json_atomic(self.reaction_roles_path, data)

    async def add_reaction_role(
        self,
        message_id: int,
        emoji: str,
        role_id: int,
    ) -> bool:
        """Add a reaction role mapping."""
        async with self._lock:
            data = await self._read_reaction_roles()

            msg_key = str(message_id)
            if msg_key not in data["reaction_roles"]:
                data["reaction_roles"][msg_key] = {}

            data["reaction_roles"][msg_key][emoji] = role_id
            await self._write_reaction_roles(data)
            return True

    async def get_reaction_role(
        self,
        message_id: int,
        emoji: str,
    ) -> Optional[int]:
        """Get role ID for a reaction."""
        async with self._lock:
            data = await self._read_reaction_roles()
            msg_key = str(message_id)
            return data["reaction_roles"].get(msg_key, {}).get(emoji)

    async def get_all_reaction_roles(
        self,
        message_id: int,
    ) -> Dict[str, int]:
        """Get all reaction roles for a message."""
        async with self._lock:
            data = await self._read_reaction_roles()
            msg_key = str(message_id)
            return data["reaction_roles"].get(msg_key, {})
