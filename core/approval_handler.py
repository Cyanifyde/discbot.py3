"""
Approval handler - manages pending upstream approval requests.

Tracks pending approvals and handles reaction events for approve/decline.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso, iso_to_dt

if TYPE_CHECKING:
    from services.sync_service import SyncAction

# Storage directory
APPROVALS_DIR = BASE_DIR / "data" / "approvals"

# Approval expiry (24 hours)
APPROVAL_EXPIRY_HOURS = 24


class ApprovalHandler:
    """Handler for pending upstream approval requests."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    def _pending_path(self, guild_id: int) -> Path:
        """Get path to a guild's pending approvals file."""
        return APPROVALS_DIR / f"{guild_id}_pending.json"

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(APPROVALS_DIR.mkdir, parents=True, exist_ok=True)

    async def _read_pending(self, guild_id: int) -> Dict[str, Dict[str, Any]]:
        """Read pending approvals for a guild."""
        path = self._pending_path(guild_id)
        data = await read_json(path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_pending(self, guild_id: int, data: Dict[str, Dict[str, Any]]) -> None:
        """Write pending approvals for a guild."""
        path = self._pending_path(guild_id)
        await write_json_atomic(path, data)

    async def _cleanup_expired(self, data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Remove expired pending approvals."""
        now = utcnow()
        cleaned = {}
        for msg_id, info in data.items():
            expires_at = iso_to_dt(info.get("expires_at"))
            if expires_at and expires_at > now:
                cleaned[msg_id] = info
        return cleaned

    async def add_pending_approval(
        self,
        message_id: int,
        parent_guild_id: int,
        child_guild_id: int,
        action: "SyncAction",
    ) -> None:
        """
        Add a pending approval request.

        Args:
            message_id: The message ID of the approval request
            parent_guild_id: The guild where the approval was posted
            child_guild_id: The guild that requested the approval
            action: The action awaiting approval
        """
        async with self._lock:
            data = await self._read_pending(parent_guild_id)
            data = await self._cleanup_expired(data)

            expires_at = utcnow() + timedelta(hours=APPROVAL_EXPIRY_HOURS)

            data[str(message_id)] = {
                "child_guild_id": str(child_guild_id),
                "action_type": action.action_type,
                "user_id": str(action.user_id),
                "reason": action.reason,
                "mod_id": str(action.mod_id),
                "origin_guild_id": str(action.origin_guild_id),
                "origin_guild_name": action.origin_guild_name,
                "timestamp": action.timestamp,
                "duration": action.duration,
                "expires_at": dt_to_iso(expires_at),
            }

            await self._write_pending(parent_guild_id, data)

    async def get_pending_approval(
        self,
        parent_guild_id: int,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a pending approval by message ID.

        Returns None if not found or expired.
        """
        async with self._lock:
            data = await self._read_pending(parent_guild_id)
            info = data.get(str(message_id))

            if not info:
                return None

            expires_at = iso_to_dt(info.get("expires_at"))
            if not expires_at or expires_at <= utcnow():
                return None

            return info

    async def consume_pending_approval(
        self,
        parent_guild_id: int,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Consume (use and remove) a pending approval.

        Returns the approval info if valid, None otherwise.
        """
        async with self._lock:
            data = await self._read_pending(parent_guild_id)
            info = data.get(str(message_id))

            if not info:
                return None

            expires_at = iso_to_dt(info.get("expires_at"))
            if not expires_at or expires_at <= utcnow():
                # Remove expired
                del data[str(message_id)]
                await self._write_pending(parent_guild_id, data)
                return None

            # Remove the approval (consumed)
            del data[str(message_id)]
            await self._write_pending(parent_guild_id, data)
            return info

    def info_to_sync_action(self, info: Dict[str, Any]) -> "SyncAction":
        """Convert stored approval info back to a SyncAction."""
        from services.sync_service import SyncAction

        return SyncAction(
            action_type=info["action_type"],
            user_id=int(info["user_id"]),
            reason=info["reason"],
            mod_id=int(info["mod_id"]),
            origin_guild_id=int(info["origin_guild_id"]),
            origin_guild_name=info["origin_guild_name"],
            timestamp=info["timestamp"],
            duration=info.get("duration"),
        )


# Global singleton
_handler: Optional[ApprovalHandler] = None
_handler_lock = asyncio.Lock()


async def get_approval_handler() -> ApprovalHandler:
    """Get or create the global ApprovalHandler instance."""
    global _handler
    async with _handler_lock:
        if _handler is None:
            _handler = ApprovalHandler()
            await _handler.initialize()
        return _handler
