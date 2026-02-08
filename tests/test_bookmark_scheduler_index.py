import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestBookmarkSchedulerIndex(unittest.IsolatedAsyncioTestCase):
    async def test_register_restore_and_deliver_removes_index_and_bookmark(self) -> None:
        import core.paths as paths
        from core.types import Bookmark
        from core.utils import dt_to_iso, utcnow
        from core.utility_storage import UtilityStore
        from services.bookmark_scheduler import BookmarkScheduler

        with TemporaryDirectory() as td:
            old_base = paths.BASE_DIR
            paths.BASE_DIR = Path(td)
            try:
                index_path = paths.BASE_DIR / "data" / "utility" / "_delayed_index.json"

                async def deliver_ok(_bot, _bookmark):
                    return True, False

                sched = BookmarkScheduler(index_path=index_path, deliver_func=deliver_ok)  # type: ignore[arg-type]

                deliver_at = dt_to_iso(utcnow() + dt.timedelta(seconds=60))
                bm = Bookmark(
                    id="b1",
                    user_id=999,
                    guild_id=1,
                    channel_id=2,
                    message_id=3,
                    message_link="https://discord.com/channels/1/2/3",
                    created_at=dt_to_iso(utcnow()) or "",
                    deliver_at=deliver_at,
                    delivery_method="dm",
                )

                # Persist the bookmark in the user's store.
                store = UtilityStore(bm.user_id)
                await store.initialize()
                await store.add_bookmark(bm)

                # Register in index and ensure it can be restored.
                await sched.register_delayed(bm)
                sched2 = BookmarkScheduler(index_path=index_path, deliver_func=deliver_ok)  # type: ignore[arg-type]
                loaded = await sched2.restore_from_index(object())  # bot unused on valid index
                self.assertEqual(loaded, 1)

                # Deliver directly via helper and ensure it cleans up bookmark + index.
                await sched2._deliver_one(object(), bm.user_id, bm.id)  # type: ignore[attr-defined]

                self.assertIsNone(await store.get_bookmark_by_id(bm.id))
                index_data = await __import__("core.io_utils", fromlist=["read_json"]).read_json(index_path, default={})
                items = index_data.get("items", [])
                self.assertTrue(isinstance(items, list))
                self.assertEqual([i for i in items if isinstance(i, dict) and i.get("id") == bm.id], [])
            finally:
                paths.BASE_DIR = old_base

