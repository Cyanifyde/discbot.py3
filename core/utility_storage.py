"""
Utility storage - persistent storage for bookmarks, AFK, notes, and aliases.

Provides per-user and per-guild storage for utility features with async-safe operations.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR
from .utils import utcnow, dt_to_iso
from .types import Bookmark

# Storage directories
UTILITY_DIR = BASE_DIR / "data" / "utility"
GUILD_UTILITY_DIR = BASE_DIR / "data" / "guilds"


class UtilityStore:
    """Per-user storage for utility features (bookmarks, notes, AFK)."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.root = UTILITY_DIR / str(user_id)
        self.bookmarks_path = self.root / "bookmarks.json"
        self.notes_path = self.root / "notes.json"
        self.afk_path = self.root / "afk.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Bookmarks ────────────────────────────────────────────────────────────

    async def _read_bookmarks(self) -> Dict[str, Any]:
        """Read bookmarks file."""
        default = {"bookmarks": []}
        data = await read_json(self.bookmarks_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_bookmarks(self, data: Dict[str, Any]) -> None:
        """Write bookmarks file."""
        await write_json_atomic(self.bookmarks_path, data)

    async def add_bookmark(self, bookmark: Bookmark) -> None:
        """Add a bookmark."""
        async with self._lock:
            data = await self._read_bookmarks()
            data["bookmarks"].append(bookmark.to_dict())
            await self._write_bookmarks(data)

    async def get_bookmarks(self) -> List[Bookmark]:
        """Get all bookmarks."""
        async with self._lock:
            data = await self._read_bookmarks()
            return [Bookmark.from_dict(b) for b in data["bookmarks"]]

    async def remove_bookmark(self, bookmark_id: str) -> bool:
        """Remove a bookmark."""
        async with self._lock:
            data = await self._read_bookmarks()
            original_len = len(data["bookmarks"])

            data["bookmarks"] = [
                b for b in data["bookmarks"]
                if b.get("id") != bookmark_id
            ]

            if len(data["bookmarks"]) < original_len:
                await self._write_bookmarks(data)
                return True
            return False

    async def get_pending_deliveries(self) -> List[Bookmark]:
        """Get bookmarks scheduled for delayed delivery."""
        from .utils import iso_to_dt

        async with self._lock:
            data = await self._read_bookmarks()
            now = utcnow()
            pending = []

            for bookmark_data in data["bookmarks"]:
                if bookmark_data.get("delivered"):
                    continue

                deliver_at = bookmark_data.get("deliver_at")
                if not deliver_at:
                    continue

                deliver_dt = iso_to_dt(deliver_at)
                if deliver_dt and deliver_dt <= now:
                    pending.append(Bookmark.from_dict(bookmark_data))

            return pending

    async def mark_delivered(self, bookmark_id: str) -> bool:
        """Mark bookmark as delivered."""
        async with self._lock:
            data = await self._read_bookmarks()

            for bookmark in data["bookmarks"]:
                if bookmark.get("id") == bookmark_id:
                    bookmark["delivered"] = True
                    await self._write_bookmarks(data)
                    return True

            return False

    # ─── Personal Notes ───────────────────────────────────────────────────────

    async def _read_notes(self) -> Dict[str, Any]:
        """Read notes file."""
        default = {"notes": []}
        data = await read_json(self.notes_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_notes(self, data: Dict[str, Any]) -> None:
        """Write notes file."""
        await write_json_atomic(self.notes_path, data)

    async def add_note(self, content: str) -> Dict[str, Any]:
        """Add a personal note."""
        import uuid

        async with self._lock:
            data = await self._read_notes()

            note = {
                "id": str(uuid.uuid4()),
                "content": content,
                "created_at": dt_to_iso(utcnow()),
            }

            data["notes"].append(note)
            await self._write_notes(data)
            return note

    async def get_notes(self) -> List[Dict[str, Any]]:
        """Get all notes."""
        async with self._lock:
            data = await self._read_notes()
            return data["notes"]

    async def remove_note(self, note_id: str) -> bool:
        """Remove a note."""
        async with self._lock:
            data = await self._read_notes()
            original_len = len(data["notes"])

            data["notes"] = [n for n in data["notes"] if n.get("id") != note_id]

            if len(data["notes"]) < original_len:
                await self._write_notes(data)
                return True
            return False

    # ─── AFK System ───────────────────────────────────────────────────────────

    async def _read_afk(self) -> Dict[str, Any]:
        """Read AFK data."""
        default = {
            "active": False,
            "message": None,
            "set_at": None,
            "mentions": [],
        }
        data = await read_json(self.afk_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_afk(self, data: Dict[str, Any]) -> None:
        """Write AFK data."""
        await write_json_atomic(self.afk_path, data)

    async def set_afk(self, message: Optional[str] = None) -> None:
        """Set AFK status."""
        async with self._lock:
            data = {
                "active": True,
                "message": message,
                "set_at": dt_to_iso(utcnow()),
                "mentions": [],
            }
            await self._write_afk(data)

    async def clear_afk(self) -> Dict[str, Any]:
        """Clear AFK status and return collected mentions."""
        async with self._lock:
            data = await self._read_afk()
            mentions = data.get("mentions", [])

            # Clear AFK
            await self._write_afk({
                "active": False,
                "message": None,
                "set_at": None,
                "mentions": [],
            })

            return {"mentions": mentions, "was_afk": data.get("active", False)}

    async def is_afk(self) -> tuple[bool, Optional[str]]:
        """Check if user is AFK."""
        async with self._lock:
            data = await self._read_afk()
            return data.get("active", False), data.get("message")

    async def add_mention(self, mention_data: Dict[str, Any]) -> None:
        """Add a mention to AFK collection."""
        async with self._lock:
            data = await self._read_afk()
            if data.get("active"):
                data["mentions"].append(mention_data)
                await self._write_afk(data)


class GuildUtilityStore:
    """Per-guild storage for utility features (aliases)."""

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = GUILD_UTILITY_DIR / str(guild_id)
        self.aliases_path = self.root / "aliases.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Ensure storage directory exists."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # ─── Command Aliases ──────────────────────────────────────────────────────

    async def _read_aliases(self) -> Dict[str, Any]:
        """Read aliases file."""
        default = {"aliases": {}}
        data = await read_json(self.aliases_path, default=default)
        if not isinstance(data, dict):
            return default
        return data

    async def _write_aliases(self, data: Dict[str, Any]) -> None:
        """Write aliases file."""
        await write_json_atomic(self.aliases_path, data)

    async def add_alias(self, shortcut: str, full_command: str) -> None:
        """Add a command alias."""
        async with self._lock:
            data = await self._read_aliases()
            data["aliases"][shortcut] = full_command
            await self._write_aliases(data)

    async def remove_alias(self, shortcut: str) -> bool:
        """Remove an alias."""
        async with self._lock:
            data = await self._read_aliases()

            if shortcut in data["aliases"]:
                del data["aliases"][shortcut]
                await self._write_aliases(data)
                return True
            return False

    async def get_alias(self, shortcut: str) -> Optional[str]:
        """Get alias expansion."""
        async with self._lock:
            data = await self._read_aliases()
            return data["aliases"].get(shortcut)

    async def get_all_aliases(self) -> Dict[str, str]:
        """Get all aliases."""
        async with self._lock:
            data = await self._read_aliases()
            return data["aliases"]
