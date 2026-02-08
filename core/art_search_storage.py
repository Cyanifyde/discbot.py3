"""
Art search storage -- per-guild channel allowlist for image search.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR

ART_SEARCH_DIR = BASE_DIR / "data" / "art_search"

# Matches <#123456789012345678> channel mentions.
_CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")


def parse_channel_id(raw: str) -> Optional[int]:
    """Parse a channel ID from a raw string, ``<#id>`` mention, or bare integer."""
    raw = raw.strip()
    m = _CHANNEL_MENTION_RE.fullmatch(raw)
    if m:
        return int(m.group(1))
    if raw.isdigit() and len(raw) >= 15:
        return int(raw)
    return None


class ArtSearchStore:
    """Stores the per-guild list of channels that Art Search is allowed to scan."""

    __slots__ = ("guild_id", "root", "data_path", "_lock")

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = ART_SEARCH_DIR / str(guild_id)
        self.data_path = self.root / "settings.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    # -- internal I/O --------------------------------------------------------

    async def _read(self) -> dict[str, Any]:
        data = await read_json(self.data_path, default={"channels": []})
        if not isinstance(data, dict):
            return {"channels": []}
        raw = data.get("channels")
        if not isinstance(raw, list):
            raw = []
        data["channels"] = [
            int(c) for c in raw
            if isinstance(c, int) or (isinstance(c, str) and c.isdigit())
        ]
        return data

    async def _write(self, data: dict[str, Any]) -> None:
        await write_json_atomic(self.data_path, data)

    # -- public API ----------------------------------------------------------

    async def list_channels(self) -> list[int]:
        async with self._lock:
            data = await self._read()
            return list(dict.fromkeys(data.get("channels", [])))

    async def add_channel(self, channel_id: int) -> bool:
        """Add *channel_id*. Returns ``True`` if it was newly added."""
        async with self._lock:
            data = await self._read()
            channels: list[int] = data.get("channels", [])
            if channel_id in channels:
                return False
            channels.append(channel_id)
            data["channels"] = channels
            await self._write(data)
            return True

    async def remove_channel(self, channel_id: int) -> bool:
        """Remove *channel_id*. Returns ``True`` if it existed."""
        async with self._lock:
            data = await self._read()
            channels: list[int] = data.get("channels", [])
            if channel_id not in channels:
                return False
            channels.remove(channel_id)
            data["channels"] = channels
            await self._write(data)
            return True

