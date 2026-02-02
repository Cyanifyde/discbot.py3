"""
Moderation storage - persistent storage for warnings, notes, and mod actions.

Provides per-guild storage for moderation data with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso

# Storage directory
MODERATION_DIR = BASE_DIR / "data" / "moderation"


class ModerationStore:
    """Per-guild storage for moderation data (warnings, notes)."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = MODERATION_DIR / str(guild_id)
        self.warnings_path = self.root / "warnings.json"
        self.notes_path = self.root / "notes.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Warnings ─────────────────────────────────────────────────────────────

    async def _read_warnings(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read warnings file."""
        data = await read_json(self.warnings_path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_warnings(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Write warnings file."""
        await write_json_atomic(self.warnings_path, data)

    async def add_warning(
        self,
        user_id: int,
        mod_id: int,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Add a warning to a user.

        Returns the created warning record.
        """
        async with self._lock:
            data = await self._read_warnings()
            user_key = str(user_id)

            if user_key not in data:
                data[user_key] = []

            # Generate next ID
            existing_ids = [w.get("id", 0) for w in data[user_key]]
            next_id = max(existing_ids, default=0) + 1

            warning = {
                "id": next_id,
                "reason": reason,
                "mod_id": str(mod_id),
                "timestamp": dt_to_iso(utcnow()),
            }

            data[user_key].append(warning)
            await self._write_warnings(data)

            return warning

    async def get_warnings(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all warnings for a user."""
        async with self._lock:
            data = await self._read_warnings()
            return data.get(str(user_id), [])

    async def remove_warning(self, user_id: int, warning_id: int) -> bool:
        """
        Remove a specific warning by ID.

        Returns True if removed, False if not found.
        """
        async with self._lock:
            data = await self._read_warnings()
            user_key = str(user_id)

            if user_key not in data:
                return False

            original_len = len(data[user_key])
            data[user_key] = [w for w in data[user_key] if w.get("id") != warning_id]

            if len(data[user_key]) == original_len:
                return False

            # Clean up empty lists
            if not data[user_key]:
                del data[user_key]

            await self._write_warnings(data)
            return True

    async def clear_warnings(self, user_id: int) -> int:
        """
        Clear all warnings for a user.

        Returns the number of warnings removed.
        """
        async with self._lock:
            data = await self._read_warnings()
            user_key = str(user_id)

            if user_key not in data:
                return 0

            count = len(data[user_key])
            del data[user_key]

            await self._write_warnings(data)
            return count

    async def count_warnings(self, user_id: int) -> int:
        """Get the number of warnings for a user."""
        warnings = await self.get_warnings(user_id)
        return len(warnings)

    # ─── Notes ────────────────────────────────────────────────────────────────

    async def _read_notes(self) -> Dict[str, List[Dict[str, Any]]]:
        """Read notes file."""
        data = await read_json(self.notes_path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_notes(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        """Write notes file."""
        await write_json_atomic(self.notes_path, data)

    async def add_note(
        self,
        user_id: int,
        mod_id: int,
        text: str,
    ) -> Dict[str, Any]:
        """
        Add a note to a user.

        Returns the created note record.
        """
        async with self._lock:
            data = await self._read_notes()
            user_key = str(user_id)

            if user_key not in data:
                data[user_key] = []

            # Generate next ID
            existing_ids = [n.get("id", 0) for n in data[user_key]]
            next_id = max(existing_ids, default=0) + 1

            note = {
                "id": next_id,
                "text": text,
                "mod_id": str(mod_id),
                "timestamp": dt_to_iso(utcnow()),
            }

            data[user_key].append(note)
            await self._write_notes(data)

            return note

    async def get_notes(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all notes for a user."""
        async with self._lock:
            data = await self._read_notes()
            return data.get(str(user_id), [])

    async def remove_note(self, user_id: int, note_id: int) -> bool:
        """
        Remove a specific note by ID.

        Returns True if removed, False if not found.
        """
        async with self._lock:
            data = await self._read_notes()
            user_key = str(user_id)

            if user_key not in data:
                return False

            original_len = len(data[user_key])
            data[user_key] = [n for n in data[user_key] if n.get("id") != note_id]

            if len(data[user_key]) == original_len:
                return False

            # Clean up empty lists
            if not data[user_key]:
                del data[user_key]

            await self._write_notes(data)
            return True

    async def clear_notes(self, user_id: int) -> int:
        """
        Clear all notes for a user.

        Returns the number of notes removed.
        """
        async with self._lock:
            data = await self._read_notes()
            user_key = str(user_id)

            if user_key not in data:
                return 0

            count = len(data[user_key])
            del data[user_key]

            await self._write_notes(data)
            return count


# Cache of ModerationStore instances per guild
_stores: Dict[int, ModerationStore] = {}
_stores_lock = asyncio.Lock()


async def get_moderation_store(guild_id: int) -> ModerationStore:
    """Get or create a ModerationStore for a guild."""
    async with _stores_lock:
        if guild_id not in _stores:
            store = ModerationStore(guild_id)
            await store.initialize()
            _stores[guild_id] = store
        return _stores[guild_id]
