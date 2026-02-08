"""
High-traffic utility helpers (AFK) with caching.

This avoids per-message directory creation and per-message AFK disk reads by:
- Reusing UtilityStore instances via a small LRU
- Caching AFK state in-memory with a short TTL
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Optional

from core.utility_storage import UtilityStore


class UserUtilityService:
    __slots__ = ("_stores", "_max_stores", "_afk_cache", "_afk_ttl")

    def __init__(self, *, max_stores: int = 5000, afk_cache_ttl_seconds: float = 5.0) -> None:
        self._stores: OrderedDict[int, UtilityStore] = OrderedDict()
        self._max_stores = max(1, int(max_stores))
        self._afk_cache: dict[int, tuple[float, bool, Optional[str]]] = {}
        self._afk_ttl = float(afk_cache_ttl_seconds)

    async def is_afk(self, user_id: int) -> tuple[bool, Optional[str]]:
        uid = int(user_id)
        now = time.monotonic()
        cached = self._afk_cache.get(uid)
        if cached:
            ts, active, msg = cached
            if now - ts < self._afk_ttl:
                return active, msg

        store = self._get_store(uid)
        await store.initialize()
        active, msg = await store.is_afk()
        self._afk_cache[uid] = (time.monotonic(), bool(active), msg)
        return bool(active), msg

    async def set_afk(self, user_id: int, message: Optional[str] = None) -> None:
        uid = int(user_id)
        store = self._get_store(uid)
        await store.initialize()
        await store.set_afk(message)
        self._afk_cache[uid] = (time.monotonic(), True, message)

    async def clear_afk(self, user_id: int) -> dict[str, Any]:
        uid = int(user_id)
        store = self._get_store(uid)
        await store.initialize()
        result = await store.clear_afk()
        self._afk_cache[uid] = (time.monotonic(), False, None)
        return result

    async def add_mention(self, user_id: int, mention_dict: dict[str, Any]) -> None:
        uid = int(user_id)
        store = self._get_store(uid)
        await store.initialize()
        await store.add_mention(mention_dict)
        # Ensure cache doesn't lie about active state; keep message if present.
        cached = self._afk_cache.get(uid)
        if cached:
            _ts, _active, msg = cached
            self._afk_cache[uid] = (time.monotonic(), True, msg)

    def _get_store(self, user_id: int) -> UtilityStore:
        uid = int(user_id)
        store = self._stores.get(uid)
        if store is not None:
            self._stores.move_to_end(uid)
            return store

        store = UtilityStore(uid)
        self._stores[uid] = store
        if len(self._stores) > self._max_stores:
            self._stores.popitem(last=False)
        return store

