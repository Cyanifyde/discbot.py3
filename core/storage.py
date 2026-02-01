from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from .paths import BASE_DIR
from .io_utils import read_json, write_json_atomic
from .utils import dt_to_iso, iso_to_dt, safe_int, utcnow


class SuspicionStore:
    def __init__(self, guild_id: int, cache_size: int = 3) -> None:
        self.guild_id = guild_id
        self.root = BASE_DIR / ".suspicion" / str(guild_id)
        self.lock_path = self.root / "lock.json"
        self.state_path = self.root / "state.json"
        self.queue_path = self.root / "queue.jsonl"
        self.queue_state_path = self.root / "queue.state.json"
        self.cache_size = max(2, min(5, cache_size))
        self.cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.cache_meta: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = asyncio.Lock()
        self.shard_locks: Dict[str, asyncio.Lock] = {}
        self.locked_shards: set[str] = set()
        self.state_lock = asyncio.Lock()
        self.state_data: Dict[str, Any] = {}
        self.lock_data: Dict[str, Any] = {}

    @staticmethod
    def shard_for(user_id: str) -> str:
        shard = user_id[:2] if len(user_id) >= 2 else user_id.zfill(2)
        return shard.zfill(2)

    def shard_path(self, shard: str) -> Path:
        return self.root / f"{shard}.json"

    def _get_shard_lock(self, shard: str) -> asyncio.Lock:
        if shard not in self.shard_locks:
            self.shard_locks[shard] = asyncio.Lock()
        return self.shard_locks[shard]

    async def initialize(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        await self._load_lock()
        await self._load_state()

    async def _load_lock(self) -> None:
        data = await read_json(self.lock_path, default=None)
        if data is None:
            now_iso = dt_to_iso(utcnow())
            data = {
                "guild_id": str(self.guild_id),
                "initialized_at": now_iso,
                "snapshot_complete": False,
            }
            await write_json_atomic(self.lock_path, data)
        else:
            if str(data.get("guild_id")) != str(self.guild_id):
                raise ConfigError("lock.json guild_id mismatch")
        self.lock_data = data

    async def _load_state(self) -> None:
        data = await read_json(self.state_path, default=None)
        if data is None:
            data = {
                "snapshot_after": None,
                "snapshot_complete": False,
                "queue_dropped": 0,
                "enforcement_cursor": {"shard": "00", "after": None},
            }
            await write_json_atomic(self.state_path, data)
        self.state_data = data

    async def update_state(self, updater: Callable[[Dict[str, Any]], None]) -> None:
        async with self.state_lock:
            updater(self.state_data)
            await write_json_atomic(self.state_path, self.state_data)

    async def increment_queue_dropped(self) -> None:
        async def _inc(state: Dict[str, Any]) -> None:
            state["queue_dropped"] = int(state.get("queue_dropped", 0)) + 1

        await self.update_state(_inc)

    async def update_lock(self, updater: Callable[[Dict[str, Any]], None]) -> None:
        async with self.state_lock:
            updater(self.lock_data)
            await write_json_atomic(self.lock_path, self.lock_data)

    async def _read_shard_file(self, path: Path) -> Dict[str, Any]:
        data = await read_json(path, default={})
        if not isinstance(data, dict):
            return {}
        return data

    async def _write_shard_file(self, shard: str, data: Dict[str, Any]) -> None:
        await write_json_atomic(self.shard_path(shard), data)

    async def _evict_if_needed(self) -> None:
        if len(self.cache) <= self.cache_size:
            return
        for shard, data in list(self.cache.items()):
            if shard in self.locked_shards:
                continue
            meta = self.cache_meta.get(shard, {})
            if meta.get("dirty"):
                await self._write_shard_file(shard, data)
            self.cache.pop(shard, None)
            self.cache_meta.pop(shard, None)
            break

    async def _get_shard_data(self, shard: str) -> Dict[str, Any]:
        async with self.cache_lock:
            if shard in self.cache:
                self.cache.move_to_end(shard)
                return self.cache[shard]
        data = await self._read_shard_file(self.shard_path(shard))
        async with self.cache_lock:
            self.cache[shard] = data
            self.cache_meta.setdefault(shard, {})
            self.cache.move_to_end(shard)
            await self._evict_if_needed()
        return data

    async def _mark_dirty(self, shard: str) -> None:
        async with self.cache_lock:
            meta = self.cache_meta.setdefault(shard, {})
            meta["dirty"] = True

    async def flush_dirty_shards(self) -> None:
        async with self.cache_lock:
            shards = list(self.cache.items())
        for shard, data in shards:
            meta = self.cache_meta.get(shard, {})
            if meta.get("dirty"):
                await self._write_shard_file(shard, data)
                meta["dirty"] = False

    async def flush_all(self) -> None:
        await self.flush_dirty_shards()
        async with self.state_lock:
            await write_json_atomic(self.state_path, self.state_data)
        async with self.state_lock:
            await write_json_atomic(self.lock_path, self.lock_data)

    def default_record(self) -> Dict[str, Any]:
        return {
            "joined_at": None,
            "last_message_at": None,
            "nonexcluded_messages": 0,
            "cleared": False,
            "enforced": False,
            "grace_until": None,
        }

    async def read_record(self, user_id: int) -> Optional[Dict[str, Any]]:
        user_str = str(user_id)
        shard = self.shard_for(user_str)
        lock = self._get_shard_lock(shard)
        async with lock:
            async with self.cache_lock:
                self.locked_shards.add(shard)
            try:
                data = await self._get_shard_data(shard)
                record = data.get(user_str)
                return dict(record) if isinstance(record, dict) else None
            finally:
                async with self.cache_lock:
                    self.locked_shards.discard(shard)

    async def update_record(self, user_id: int, updater: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        user_str = str(user_id)
        shard = self.shard_for(user_str)
        lock = self._get_shard_lock(shard)
        async with lock:
            async with self.cache_lock:
                self.locked_shards.add(shard)
            try:
                data = await self._get_shard_data(shard)
                record = data.get(user_str)
                if not isinstance(record, dict):
                    record = self.default_record()
                    data[user_str] = record
                updater(record)
                await self._mark_dirty(shard)
                return dict(record)
            finally:
                async with self.cache_lock:
                    self.locked_shards.discard(shard)

    async def delete_record(self, user_id: int) -> None:
        user_str = str(user_id)
        shard = self.shard_for(user_str)
        lock = self._get_shard_lock(shard)
        async with lock:
            async with self.cache_lock:
                self.locked_shards.add(shard)
            try:
                data = await self._get_shard_data(shard)
                if user_str in data:
                    del data[user_str]
                    await self._mark_dirty(shard)
            finally:
                async with self.cache_lock:
                    self.locked_shards.discard(shard)

    async def record_message(self, user_id: int, when: dt.datetime) -> None:
        when_iso = dt_to_iso(when)

        def _update(record: Dict[str, Any]) -> None:
            record["last_message_at"] = when_iso
            record["nonexcluded_messages"] = int(record.get("nonexcluded_messages", 0)) + 1
            if record["nonexcluded_messages"] >= 1:
                record["cleared"] = True

        await self.update_record(user_id, _update)

    async def ensure_joined_at(self, user_id: int, joined_at: Optional[dt.datetime]) -> None:
        joined_iso = dt_to_iso(joined_at)

        def _update(record: Dict[str, Any]) -> None:
            if record.get("joined_at") is None:
                record["joined_at"] = joined_iso

        await self.update_record(user_id, _update)

    async def set_grace_until(self, user_id: int, grace_until: Optional[dt.datetime]) -> None:
        grace_iso = dt_to_iso(grace_until)

        def _update(record: Dict[str, Any]) -> None:
            if record.get("grace_until") is None:
                record["grace_until"] = grace_iso

        await self.update_record(user_id, _update)

    async def mark_enforced(self, user_id: int) -> None:
        def _update(record: Dict[str, Any]) -> None:
            record["enforced"] = True

        await self.update_record(user_id, _update)

    async def mark_cleared(self, user_id: int, cleared: bool = True) -> None:
        def _update(record: Dict[str, Any]) -> None:
            record["cleared"] = cleared

        await self.update_record(user_id, _update)

    async def reset_record(self, user_id: int) -> None:
        def _update(record: Dict[str, Any]) -> None:
            record["last_message_at"] = None
            record["nonexcluded_messages"] = 0
            record["cleared"] = False
            record["enforced"] = False

        await self.update_record(user_id, _update)

    async def list_records(
        self,
        filter_func: Callable[[Dict[str, Any]], bool],
        limit: int,
        cursor: Optional[str],
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], Optional[str]]:
        results: List[Tuple[str, Dict[str, Any]]] = []
        next_cursor: Optional[str] = None
        shards = [f"{i:02d}" for i in range(100)]
        start_shard = "00"
        start_after = cursor

        if cursor:
            start_shard = self.shard_for(cursor)

        shards = shards[shards.index(start_shard) :] + shards[: shards.index(start_shard)]
        start_after_int = safe_int(start_after) if start_after else None

        for shard in shards:
            data = await self._read_shard_file(self.shard_path(shard))
            parsed_ids: List[Tuple[int, str]] = []
            for user_id in data.keys():
                user_int = safe_int(user_id)
                if user_int is None:
                    continue
                parsed_ids.append((user_int, user_id))
            parsed_ids.sort(key=lambda item: item[0])
            for user_id_int, user_id in parsed_ids:
                if start_after_int is not None and shard == start_shard and user_id_int <= start_after_int:
                    continue
                record = data.get(user_id)
                if not isinstance(record, dict):
                    continue
                if filter_func(record):
                    results.append((user_id, record))
                    if len(results) >= limit:
                        next_cursor = user_id
                        return results, next_cursor
            start_after = None
            start_after_int = None
        return results, next_cursor

    async def summary_counts(self) -> Dict[str, int]:
        counts = {"total": 0, "cleared": 0, "enforced": 0, "uncleared": 0}
        for shard in [f"{i:02d}" for i in range(100)]:
            data = await self._read_shard_file(self.shard_path(shard))
            for record in data.values():
                if not isinstance(record, dict):
                    continue
                counts["total"] += 1
                if record.get("cleared"):
                    counts["cleared"] += 1
                else:
                    counts["uncleared"] += 1
                if record.get("enforced"):
                    counts["enforced"] += 1
        return counts
