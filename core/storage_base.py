"""
Base storage class for all persistent storage implementations.

Provides common functionality for locking, optional caching, and async I/O operations.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .io_utils import read_json, write_json_atomic
from .paths import BASE_DIR


class StorageBase:
    """
    Base class for all storage implementations with locking, optional caching, and async I/O.

    This class eliminates duplicate patterns across storage classes by providing:
    - Automatic directory management
    - asyncio.Lock for thread-safe operations
    - Optional TTL-based caching to reduce disk I/O
    - Async file I/O using existing io_utils

    Usage:
        class MyStore(StorageBase):
            def __init__(self, guild_id: int) -> None:
                super().__init__(guild_id, "my_feature", cache_ttl=30.0)

            async def get_data(self) -> Dict[str, Any]:
                data = await self._read_file("data.json", default={"items": []})
                return data

            async def save_data(self, data: Dict[str, Any]) -> None:
                await self._write_file("data.json", data)
    """

    __slots__ = ("guild_id", "root", "_lock", "_cache", "_cache_ttl")

    def __init__(self, guild_id: int, storage_dir: str, cache_ttl: float = 0.0) -> None:
        """
        Initialize storage for a specific guild.

        Args:
            guild_id: Discord guild ID
            storage_dir: Subdirectory name under data/ (e.g., "art_search", "automation")
            cache_ttl: Cache time-to-live in seconds (0 = caching disabled)
        """
        self.guild_id = guild_id
        self.root = BASE_DIR / "data" / storage_dir / str(guild_id)
        self._lock = asyncio.Lock()
        self._cache: Dict[str, Tuple[float, Any]] = {}  # filename -> (timestamp, data)
        self._cache_ttl = cache_ttl

    async def initialize(self) -> None:
        """
        Ensure the storage directory exists.

        This is idempotent and safe to call multiple times.
        """
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)

    async def _read_file(self, filename: str, default: Any = None) -> Any:
        """
        Read and optionally cache a JSON file.

        If caching is enabled (cache_ttl > 0), this will return cached data
        if it's still fresh, otherwise it reads from disk and updates the cache.

        Args:
            filename: Name of the file to read (relative to self.root)
            default: Default value to return if file doesn't exist

        Returns:
            The parsed JSON data or default value
        """
        # Check cache if TTL > 0
        if self._cache_ttl > 0:
            cache_key = self._get_cache_key(filename)
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if time.monotonic() - timestamp < self._cache_ttl:
                    return data

        # Read from disk using existing io_utils
        path = self.root / filename
        data = await read_json(path, default=default)

        # Update cache
        if self._cache_ttl > 0:
            cache_key = self._get_cache_key(filename)
            self._cache[cache_key] = (time.monotonic(), data)

        return data

    async def _write_file(self, filename: str, data: Any) -> None:
        """
        Write JSON data atomically and invalidate cache.

        Uses write_json_atomic to ensure atomic writes (prevents corruption).
        Automatically invalidates the cache entry for this file.

        Args:
            filename: Name of the file to write (relative to self.root)
            data: Data to serialize as JSON
        """
        path = self.root / filename
        await write_json_atomic(path, data)

        # Invalidate cache entry
        if self._cache_ttl > 0:
            cache_key = self._get_cache_key(filename)
            self._cache.pop(cache_key, None)

    def _get_cache_key(self, filename: str) -> str:
        """
        Generate a cache key for a filename.

        Args:
            filename: Name of the file

        Returns:
            Cache key string
        """
        return filename

    def _invalidate_cache(self, filename: Optional[str] = None) -> None:
        """
        Invalidate cache entries.

        Args:
            filename: If provided, invalidate only this file's cache.
                     If None, invalidate entire cache.
        """
        if filename is None:
            self._cache.clear()
        else:
            cache_key = self._get_cache_key(filename)
            self._cache.pop(cache_key, None)
