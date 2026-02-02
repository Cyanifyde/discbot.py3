"""
Art search storage - per-guild settings for channel-restricted image search.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR

ART_SEARCH_DIR = BASE_DIR / "data" / "art_search"


class ArtSearchStore:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.root = ART_SEARCH_DIR / str(guild_id)
        self.data_path = self.root / "settings.json"
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    async def _read(self) -> Dict[str, Any]:
        data = await read_json(self.data_path, default={"channels": []})
        if not isinstance(data, dict):
            return {"channels": []}
        channels = data.get("channels")
        if not isinstance(channels, list):
            channels = []
        data["channels"] = [int(c) for c in channels if isinstance(c, int) or (isinstance(c, str) and c.isdigit())]
        return data

    async def _write(self, data: Dict[str, Any]) -> None:
        await write_json_atomic(self.data_path, data)

    async def list_channels(self) -> List[int]:
        async with self._lock:
            data = await self._read()
            return list(dict.fromkeys(data.get("channels", [])))

    async def add_channel(self, channel_id: int) -> None:
        async with self._lock:
            data = await self._read()
            channels = data.get("channels", [])
            if channel_id not in channels:
                channels.append(channel_id)
            data["channels"] = channels
            await self._write(data)

    async def remove_channel(self, channel_id: int) -> bool:
        async with self._lock:
            data = await self._read()
            channels = data.get("channels", [])
            if channel_id not in channels:
                return False
            channels.remove(channel_id)
            data["channels"] = channels
            await self._write(data)
            return True

