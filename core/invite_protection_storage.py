"""
Invite protection storage - allowlist and approval workflow for Discord invite links.

Per-guild, JSON-backed storage with async-safe operations.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import dt_to_iso, utcnow

INVITE_PROTECTION_DIR = BASE_DIR / "data" / "invite_protection"


class InviteProtectionStore:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = INVITE_PROTECTION_DIR / str(guild_id)
        self.data_path = self.root / "invites.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    async def _read(self) -> Dict[str, Any]:
        data = await read_json(self.data_path, default={"allowlist": {}, "pending": {}})
        if not isinstance(data, dict):
            return {"allowlist": {}, "pending": {}}
        if "allowlist" not in data or not isinstance(data.get("allowlist"), dict):
            data["allowlist"] = {}
        if "pending" not in data or not isinstance(data.get("pending"), dict):
            data["pending"] = {}
        return data

    async def _write(self, data: Dict[str, Any]) -> None:
        await write_json_atomic(self.data_path, data)

    async def is_allowlisted(self, code: str) -> bool:
        async with self._lock:
            data = await self._read()
            return code in data["allowlist"]

    async def list_allowlist(self) -> List[Tuple[str, Dict[str, Any]]]:
        async with self._lock:
            data = await self._read()
            items = list(data["allowlist"].items())
            items.sort(key=lambda kv: kv[0])
            return [(k, v if isinstance(v, dict) else {}) for k, v in items]

    async def add_allowlist(self, code: str, actor_id: int) -> None:
        async with self._lock:
            data = await self._read()
            data["allowlist"][code] = {
                "added_by": actor_id,
                "added_at": dt_to_iso(utcnow()),
            }
            await self._write(data)

    async def remove_allowlist(self, code: str) -> bool:
        async with self._lock:
            data = await self._read()
            if code not in data["allowlist"]:
                return False
            del data["allowlist"][code]
            await self._write(data)
            return True

    async def add_pending(
        self,
        code: str,
        invite_url: str,
        posted_by: int,
        channel_id: int,
        message_id: int,
    ) -> str:
        async with self._lock:
            data = await self._read()

            for pid, entry in data["pending"].items():
                if isinstance(entry, dict) and entry.get("code") == code:
                    return pid

            pending_id = str(uuid.uuid4())
            data["pending"][pending_id] = {
                "code": code,
                "invite_url": invite_url,
                "posted_by": posted_by,
                "posted_at": dt_to_iso(utcnow()),
                "channel_id": channel_id,
                "message_id": message_id,
            }
            await self._write(data)
            return pending_id

    async def list_pending(self) -> List[Tuple[str, Dict[str, Any]]]:
        async with self._lock:
            data = await self._read()
            items = [
                (pid, entry if isinstance(entry, dict) else {})
                for pid, entry in data["pending"].items()
            ]
            items.sort(key=lambda kv: kv[1].get("posted_at", ""), reverse=True)
            return items

    async def remove_pending(self, pending_id: str) -> bool:
        async with self._lock:
            data = await self._read()
            if pending_id not in data["pending"]:
                return False
            del data["pending"][pending_id]
            await self._write(data)
            return True

    async def approve(self, token: str, actor_id: int) -> Optional[str]:
        """
        Approve a pending invite by ID (full UUID or unique prefix), or approve a raw code.

        Returns the allowlisted code if successful, otherwise None.
        """
        token = token.strip()
        if not token:
            return None

        async with self._lock:
            data = await self._read()

            # Raw code approval (already allowlisted -> treat as success)
            if token in data["allowlist"]:
                return token

            # Pending by exact id
            entry = data["pending"].get(token)
            if isinstance(entry, dict):
                code = str(entry.get("code") or "").strip()
                if not code:
                    return None
                data["allowlist"][code] = {"added_by": actor_id, "added_at": dt_to_iso(utcnow())}
                del data["pending"][token]
                await self._write(data)
                return code

            # Pending by unique prefix
            matches = [pid for pid in data["pending"].keys() if pid.startswith(token)]
            if len(matches) == 1:
                pid = matches[0]
                entry2 = data["pending"].get(pid)
                if not isinstance(entry2, dict):
                    return None
                code = str(entry2.get("code") or "").strip()
                if not code:
                    return None
                data["allowlist"][code] = {"added_by": actor_id, "added_at": dt_to_iso(utcnow())}
                del data["pending"][pid]
                await self._write(data)
                return code

            # Approve as raw code (not pending)
            code = token
            data["allowlist"][code] = {"added_by": actor_id, "added_at": dt_to_iso(utcnow())}
            await self._write(data)
            return code

    async def deny(self, token: str) -> Optional[str]:
        """
        Deny (remove) a pending invite by ID (full UUID or unique prefix).
        Returns the removed pending_id if successful, otherwise None.
        """
        token = token.strip()
        if not token:
            return None

        async with self._lock:
            data = await self._read()

            if token in data["pending"]:
                del data["pending"][token]
                await self._write(data)
                return token

            matches = [pid for pid in data["pending"].keys() if pid.startswith(token)]
            if len(matches) == 1:
                pid = matches[0]
                del data["pending"][pid]
                await self._write(data)
                return pid

            return None

