"""
Commission storage - persistent storage for commissions, waitlists, and artist settings.

Provides per-artist, per-guild storage for commission data with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso
from .types import Commission, WaitlistEntry

# Storage directory
COMMISSION_DIR = BASE_DIR / "data" / "commissions"


class CommissionStore:
    """Per-artist, per-guild storage for commission data."""

    def __init__(self, guild_id: int, artist_id: int) -> None:
        self.guild_id = guild_id
        self.artist_id = artist_id
        self.root = COMMISSION_DIR / str(guild_id) / str(artist_id)
        self.queue_path = self.root / "queue.json"
        self.history_path = self.root / "history.json"
        self.waitlist_path = self.root / "waitlist.json"
        self.stages_path = self.root / "stages.json"
        self.blacklist_path = self.root / "blacklist.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Queue (Active Commissions) ───────────────────────────────────────────

    async def _read_queue(self) -> Dict[str, Any]:
        """Read queue file."""
        default = {
            "slots_total": 5,
            "slots_available": 5,
            "auto_close": True,
            "custom_stages": [
                "Inquiry",
                "Accepted",
                "Queued",
                "In Progress",
                "WIP Shared",
                "Revision",
                "Final Delivered",
                "Completed",
                "Archived",
            ],
            "default_revisions_limit": 3,
            "tos_url": None,
            "commissions": {},
        }
        data = await read_json(self.queue_path, default=default)
        if not isinstance(data, dict):
            return default
        # Ensure all keys exist
        for key in default:
            if key not in data:
                data[key] = default[key]
        return data

    async def _write_queue(self, data: Dict[str, Any]) -> None:
        """Write queue file."""
        await write_json_atomic(self.queue_path, data)

    async def get_commission(self, commission_id: str) -> Optional[Commission]:
        """Get a specific commission by ID."""
        async with self._lock:
            data = await self._read_queue()
            commission_data = data["commissions"].get(commission_id)
            if not commission_data:
                # Check history
                return await self._get_commission_from_history(commission_id)
            return Commission.from_dict(commission_data)

    async def get_active_commissions(self) -> List[Commission]:
        """Get all active commissions for this artist."""
        async with self._lock:
            data = await self._read_queue()
            return [
                Commission.from_dict(c) for c in data["commissions"].values()
            ]

    async def add_commission(self, commission: Commission) -> None:
        """Add a new commission to the queue."""
        async with self._lock:
            data = await self._read_queue()
            data["commissions"][commission.id] = commission.to_dict()

            # Update available slots
            active_count = len(data["commissions"])
            data["slots_available"] = max(0, data["slots_total"] - active_count)

            await self._write_queue(data)

    async def update_commission(self, commission_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update a commission.

        Returns True if updated, False if not found.
        """
        async with self._lock:
            data = await self._read_queue()
            if commission_id not in data["commissions"]:
                return False

            data["commissions"][commission_id].update(updates)
            data["commissions"][commission_id]["updated_at"] = dt_to_iso(utcnow())
            await self._write_queue(data)
            return True

    async def remove_commission(self, commission_id: str, archive: bool = True) -> Optional[Commission]:
        """
        Remove a commission from active queue.

        If archive is True, moves to history. Returns the removed commission.
        """
        async with self._lock:
            data = await self._read_queue()
            if commission_id not in data["commissions"]:
                return None

            commission_data = data["commissions"].pop(commission_id)
            commission = Commission.from_dict(commission_data)

            # Update available slots
            active_count = len(data["commissions"])
            data["slots_available"] = max(0, data["slots_total"] - active_count)

            await self._write_queue(data)

            # Archive if requested
            if archive:
                await self._archive_commission(commission)

            return commission

    async def get_slots_config(self) -> Dict[str, Any]:
        """Get slot configuration."""
        async with self._lock:
            data = await self._read_queue()
            return {
                "slots_total": data["slots_total"],
                "slots_available": data["slots_available"],
                "auto_close": data["auto_close"],
            }

    async def update_slots(self, slots_total: int) -> None:
        """Update total slots count."""
        async with self._lock:
            data = await self._read_queue()
            data["slots_total"] = slots_total
            active_count = len(data["commissions"])
            data["slots_available"] = max(0, slots_total - active_count)
            await self._write_queue(data)

    async def set_auto_close(self, enabled: bool) -> None:
        """Enable/disable auto-close when slots full."""
        async with self._lock:
            data = await self._read_queue()
            data["auto_close"] = enabled
            await self._write_queue(data)

    async def get_custom_stages(self) -> List[str]:
        """Get custom stage names."""
        async with self._lock:
            data = await self._read_queue()
            return data["custom_stages"]

    async def set_custom_stages(self, stages: List[str]) -> None:
        """Set custom stage names."""
        async with self._lock:
            data = await self._read_queue()
            data["custom_stages"] = stages
            await self._write_queue(data)

    async def get_tos_url(self) -> Optional[str]:
        """Get Terms of Service URL."""
        async with self._lock:
            data = await self._read_queue()
            return data.get("tos_url")

    async def set_tos_url(self, url: Optional[str]) -> None:
        """Set Terms of Service URL."""
        async with self._lock:
            data = await self._read_queue()
            data["tos_url"] = url
            await self._write_queue(data)

    # ─── History (Archived Commissions) ───────────────────────────────────────

    async def _read_history(self) -> Dict[str, Any]:
        """Read history file."""
        data = await read_json(self.history_path, default={"commissions": []})
        if not isinstance(data, dict):
            return {"commissions": []}
        if "commissions" not in data:
            data["commissions"] = []
        return data

    async def _write_history(self, data: Dict[str, Any]) -> None:
        """Write history file."""
        await write_json_atomic(self.history_path, data)

    async def _archive_commission(self, commission: Commission) -> None:
        """Add commission to history."""
        async with self._lock:
            data = await self._read_history()
            data["commissions"].append(commission.to_dict())
            await self._write_history(data)

    async def _get_commission_from_history(self, commission_id: str) -> Optional[Commission]:
        """Get commission from history."""
        async with self._lock:
            data = await self._read_history()
            for commission_data in data["commissions"]:
                if commission_data.get("id") == commission_id:
                    return Commission.from_dict(commission_data)
            return None

    async def get_history(self, limit: Optional[int] = None) -> List[Commission]:
        """Get commission history."""
        async with self._lock:
            data = await self._read_history()
            commissions = [Commission.from_dict(c) for c in data["commissions"]]
            # Most recent first
            commissions.reverse()
            if limit:
                commissions = commissions[:limit]
            return commissions

    async def get_completed_count(self) -> int:
        """Get count of completed commissions."""
        async with self._lock:
            data = await self._read_history()
            return len(data["commissions"])

    # ─── Waitlist ─────────────────────────────────────────────────────────────

    async def _read_waitlist(self) -> Dict[str, Any]:
        """Read waitlist file."""
        data = await read_json(self.waitlist_path, default={"entries": []})
        if not isinstance(data, dict):
            return {"entries": []}
        if "entries" not in data:
            data["entries"] = []
        return data

    async def _write_waitlist(self, data: Dict[str, Any]) -> None:
        """Write waitlist file."""
        await write_json_atomic(self.waitlist_path, data)

    async def add_to_waitlist(self, entry: WaitlistEntry) -> None:
        """Add entry to waitlist."""
        async with self._lock:
            data = await self._read_waitlist()
            data["entries"].append(entry.to_dict())
            # Update positions
            for i, e in enumerate(data["entries"]):
                e["position"] = i + 1
            await self._write_waitlist(data)

    async def get_waitlist(self) -> List[WaitlistEntry]:
        """Get all waitlist entries."""
        async with self._lock:
            data = await self._read_waitlist()
            return [WaitlistEntry.from_dict(e) for e in data["entries"]]

    async def remove_from_waitlist(self, entry_id: str) -> Optional[WaitlistEntry]:
        """Remove entry from waitlist."""
        async with self._lock:
            data = await self._read_waitlist()
            for i, entry_data in enumerate(data["entries"]):
                if entry_data.get("id") == entry_id:
                    removed = data["entries"].pop(i)
                    # Update positions
                    for j, e in enumerate(data["entries"]):
                        e["position"] = j + 1
                    await self._write_waitlist(data)
                    return WaitlistEntry.from_dict(removed)
            return None

    async def get_next_waitlist_entry(self) -> Optional[WaitlistEntry]:
        """Get next entry in waitlist (position 1)."""
        async with self._lock:
            data = await self._read_waitlist()
            if not data["entries"]:
                return None
            return WaitlistEntry.from_dict(data["entries"][0])

    async def update_waitlist_entry(self, entry_id: str, updates: Dict[str, Any]) -> bool:
        """Update a waitlist entry."""
        async with self._lock:
            data = await self._read_waitlist()
            for entry_data in data["entries"]:
                if entry_data.get("id") == entry_id:
                    entry_data.update(updates)
                    await self._write_waitlist(data)
                    return True
            return False

    # ─── Blacklist ────────────────────────────────────────────────────────────

    async def _read_blacklist(self) -> Dict[str, Any]:
        """Read blacklist file."""
        data = await read_json(self.blacklist_path, default={"users": []})
        if not isinstance(data, dict):
            return {"users": []}
        if "users" not in data:
            data["users"] = []
        return data

    async def _write_blacklist(self, data: Dict[str, Any]) -> None:
        """Write blacklist file."""
        await write_json_atomic(self.blacklist_path, data)

    async def add_to_blacklist(self, user_id: int, reason: str) -> None:
        """Add user to blacklist."""
        async with self._lock:
            data = await self._read_blacklist()
            # Check if already blacklisted
            for entry in data["users"]:
                if entry.get("user_id") == user_id:
                    return
            data["users"].append({
                "user_id": user_id,
                "reason": reason,
                "added_at": dt_to_iso(utcnow()),
            })
            await self._write_blacklist(data)

    async def remove_from_blacklist(self, user_id: int) -> bool:
        """Remove user from blacklist."""
        async with self._lock:
            data = await self._read_blacklist()
            original_len = len(data["users"])
            data["users"] = [e for e in data["users"] if e.get("user_id") != user_id]
            if len(data["users"]) < original_len:
                await self._write_blacklist(data)
                return True
            return False

    async def is_blacklisted(self, user_id: int) -> bool:
        """Check if user is blacklisted."""
        async with self._lock:
            data = await self._read_blacklist()
            return any(e.get("user_id") == user_id for e in data["users"])

    async def get_blacklist(self) -> List[Dict[str, Any]]:
        """Get all blacklisted users."""
        async with self._lock:
            data = await self._read_blacklist()
            return data["users"]
