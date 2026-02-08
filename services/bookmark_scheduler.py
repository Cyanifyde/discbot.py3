"""
Delayed bookmark scheduler backed by a single task and a persisted index.

Replaces per-bookmark asyncio.create_task() fan-out with:
- A heap-based scheduler loop (O(1) tasks)
- A global refs-only index at data/utility/_delayed_index.json
"""
from __future__ import annotations

import asyncio
import heapq
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from core import io_utils, paths
from core.utils import iso_to_dt, utcnow
from core.utility_storage import UtilityStore

if TYPE_CHECKING:
    from bot.client import DiscBot
    from core.types import Bookmark

logger = logging.getLogger("discbot.bookmark_scheduler")


@dataclass(frozen=True)
class DelayedRef:
    id: str
    user_id: int
    deliver_at: str


DeliverFunc = Callable[["DiscBot", "Bookmark"], "asyncio.Future[tuple[bool, bool]]"]


class BookmarkScheduler:
    __slots__ = (
        "_index_path",
        "_index_lock",
        "_heap",
        "_wake",
        "_task",
        "_stop",
        "_cancelled",
        "_retry_counts",
        "_deliver_func",
        "_legacy_rebuild_task",
    )

    def __init__(
        self,
        *,
        index_path: Optional[Path] = None,
        deliver_func: Optional[Callable[["DiscBot", "Bookmark"], asyncio.Future]] = None,
    ) -> None:
        base = paths.BASE_DIR / "data" / "utility"
        self._index_path = index_path or (base / "_delayed_index.json")
        self._index_lock = asyncio.Lock()
        self._heap: list[tuple[float, int, str]] = []  # (due_ts, user_id, bookmark_id)
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._cancelled: set[tuple[int, str]] = set()
        self._retry_counts: dict[tuple[int, str], int] = {}
        self._deliver_func = deliver_func
        self._legacy_rebuild_task: Optional[asyncio.Task] = None

    async def start(self, bot: "DiscBot") -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(bot), name="bookmark-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._legacy_rebuild_task:
            self._legacy_rebuild_task.cancel()
            await asyncio.gather(self._legacy_rebuild_task, return_exceptions=True)
            self._legacy_rebuild_task = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def restore_from_index(self, bot: "DiscBot") -> int:
        """
        Load delayed refs from the persisted index into the in-memory heap.

        Returns number of refs loaded. If index does not exist or is invalid,
        schedules a one-time legacy rebuild task and returns 0.
        """
        data = await io_utils.read_json(self._index_path, default=None)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
            if data is None:
                # First run after upgrade: rebuild once in the background.
                self._legacy_rebuild_task = asyncio.create_task(
                    self._legacy_rebuild(bot),
                    name="bookmark-scheduler-legacy-rebuild",
                )
            else:
                logger.warning("Delayed bookmark index invalid; rebuilding from filesystem")
                self._legacy_rebuild_task = asyncio.create_task(
                    self._legacy_rebuild(bot),
                    name="bookmark-scheduler-legacy-rebuild",
                )
            return 0

        loaded = 0
        now = time.time()
        for item in data["items"]:
            if not isinstance(item, dict):
                continue
            bid = item.get("id")
            uid = item.get("user_id")
            deliver_at = item.get("deliver_at")
            if not isinstance(bid, str) or not isinstance(uid, int) or not isinstance(deliver_at, str):
                continue
            dt = iso_to_dt(deliver_at)
            if dt is None:
                continue
            due_ts = dt.timestamp()
            # If overdue, schedule immediate processing.
            heapq.heappush(self._heap, (min(due_ts, now), uid, bid))
            loaded += 1

        if loaded:
            self._wake.set()
        return loaded

    async def register_delayed(self, bookmark: "Bookmark") -> None:
        if not bookmark.deliver_at:
            return
        dt = iso_to_dt(bookmark.deliver_at)
        if dt is None:
            return
        due_ts = dt.timestamp()
        uid = int(bookmark.user_id)
        bid = str(bookmark.id)
        key = (uid, bid)
        self._cancelled.discard(key)
        self._retry_counts.pop(key, None)

        async with self._index_lock:
            data = await io_utils.read_json(self._index_path, default={"version": 1, "items": []})
            if not isinstance(data, dict):
                data = {"version": 1, "items": []}
            if data.get("version") != 1 or not isinstance(data.get("items"), list):
                data = {"version": 1, "items": []}
            items = [i for i in data["items"] if isinstance(i, dict) and i.get("id") != bid]
            items.append({"id": bid, "user_id": uid, "deliver_at": bookmark.deliver_at})
            data["items"] = items
            await io_utils.write_json_atomic(self._index_path, data)

        heapq.heappush(self._heap, (due_ts, uid, bid))
        self._wake.set()

    async def unregister_delayed(self, user_id: int, bookmark_id: str) -> None:
        uid = int(user_id)
        bid = str(bookmark_id)
        key = (uid, bid)
        self._cancelled.add(key)
        self._retry_counts.pop(key, None)

        async with self._index_lock:
            data = await io_utils.read_json(self._index_path, default={"version": 1, "items": []})
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
                return
            before = len(data["items"])
            data["items"] = [
                i for i in data["items"]
                if not (isinstance(i, dict) and i.get("id") == bid and int(i.get("user_id", -1)) == uid)
            ]
            if len(data["items"]) != before:
                await io_utils.write_json_atomic(self._index_path, data)

        self._wake.set()

    async def _run(self, bot: "DiscBot") -> None:
        while not self._stop.is_set():
            try:
                if not self._heap:
                    self._wake.clear()
                    await self._wake.wait()
                    continue

                due_ts, uid, bid = self._heap[0]
                now = time.time()
                delay = due_ts - now
                if delay > 0:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=delay)
                        continue
                    except asyncio.TimeoutError:
                        pass

                # Due now.
                heapq.heappop(self._heap)
                key = (uid, bid)
                if key in self._cancelled:
                    self._cancelled.discard(key)
                    continue

                await self._deliver_one(bot, uid, bid)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Bookmark scheduler loop error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _deliver_one(self, bot: "DiscBot", user_id: int, bookmark_id: str) -> None:
        store = UtilityStore(int(user_id))
        await store.initialize()
        bookmark = await store.get_bookmark_by_id(bookmark_id)
        if bookmark is None:
            await self.unregister_delayed(user_id, bookmark_id)
            return

        deliver = self._deliver_func
        if deliver is None:
            # Late import to avoid import-time cycles.
            from modules.utility import _deliver_bookmark_now as deliver  # type: ignore[assignment]

        delivered, permanent_fail = await deliver(bot, bookmark)
        if delivered or permanent_fail:
            await store.remove_bookmark(bookmark.id)
            await self.unregister_delayed(user_id, bookmark.id)
            return

        # Transient failure: retry with bounded backoff.
        key = (int(user_id), str(bookmark_id))
        n = self._retry_counts.get(key, 0) + 1
        self._retry_counts[key] = n
        retry_delay = min(300.0, 10.0 * (2 ** min(n, 5)))
        heapq.heappush(self._heap, (time.time() + retry_delay, int(user_id), str(bookmark_id)))
        self._wake.set()

    async def _legacy_rebuild(self, bot: "DiscBot") -> None:
        """
        One-time migration path: scan per-user bookmarks.json files and build index.

        Runs as a background task to avoid blocking on_ready.
        """
        base = paths.BASE_DIR / "data" / "utility"
        if not base.exists():
            return

        # Build a single index in memory, then write once.
        items: list[dict[str, object]] = []
        overdue_delivered = 0

        try:
            for i, user_dir in enumerate(base.iterdir()):
                if self._stop.is_set():
                    return
                if not user_dir.is_dir():
                    continue
                try:
                    user_id = int(user_dir.name)
                except ValueError:
                    continue

                store = UtilityStore(user_id)
                await store.initialize()
                bookmarks = await store.get_bookmarks()
                for bm in bookmarks:
                    if bm.delivered or not bm.deliver_at:
                        continue
                    dt = iso_to_dt(bm.deliver_at)
                    if dt is None:
                        continue
                    if dt <= utcnow():
                        delivered, permanent_fail = await self._deliver_func_or_default(bot, bm)
                        if delivered or permanent_fail:
                            await store.remove_bookmark(bm.id)
                            if delivered:
                                overdue_delivered += 1
                        continue
                    items.append({"id": bm.id, "user_id": int(bm.user_id), "deliver_at": bm.deliver_at})
                    heapq.heappush(self._heap, (dt.timestamp(), int(bm.user_id), str(bm.id)))

                # Yield occasionally for responsiveness.
                if i % 50 == 0:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Legacy delayed bookmark rebuild failed: %s", e, exc_info=True)
            return

        async with self._index_lock:
            await io_utils.write_json_atomic(self._index_path, {"version": 1, "items": items})

        if items:
            self._wake.set()
        logger.info("Delayed bookmark index rebuilt: items=%d overdue_delivered=%d", len(items), overdue_delivered)

    async def _deliver_func_or_default(self, bot: "DiscBot", bookmark: "Bookmark") -> tuple[bool, bool]:
        deliver = self._deliver_func
        if deliver is None:
            from modules.utility import _deliver_bookmark_now as deliver  # type: ignore[assignment]
        return await deliver(bot, bookmark)

