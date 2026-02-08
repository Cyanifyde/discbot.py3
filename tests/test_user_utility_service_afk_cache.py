import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TestUserUtilityServiceAfkCache(unittest.IsolatedAsyncioTestCase):
    async def test_afk_cache_avoids_repeated_reads(self) -> None:
        import core.paths as paths
        import core.io_utils as io_utils

        from services.user_utility_service import UserUtilityService

        with TemporaryDirectory() as td:
            old_base = paths.BASE_DIR
            paths.BASE_DIR = Path(td)

            counts = {"read": 0, "write": 0}
            orig_read = io_utils.read_json
            orig_write = io_utils.write_json_atomic

            async def read_count(path, default=None):
                counts["read"] += 1
                return await orig_read(path, default=default)

            async def write_count(path, data):
                counts["write"] += 1
                return await orig_write(path, data)

            io_utils.read_json = read_count  # type: ignore[assignment]
            io_utils.write_json_atomic = write_count  # type: ignore[assignment]

            try:
                svc = UserUtilityService(max_stores=10, afk_cache_ttl_seconds=5.0)

                await svc.set_afk(111, "hi")
                reads_before = counts["read"]

                a1 = await svc.is_afk(111)
                a2 = await svc.is_afk(111)
                self.assertEqual(a1[0], True)
                self.assertEqual(a2[0], True)
                self.assertEqual(counts["read"], reads_before, "Expected is_afk to hit cache after set_afk")

                await svc.clear_afk(111)
                reads_before2 = counts["read"]
                b1 = await svc.is_afk(111)
                b2 = await svc.is_afk(111)
                self.assertEqual(b1[0], False)
                self.assertEqual(b2[0], False)
                self.assertEqual(counts["read"], reads_before2, "Expected is_afk to hit cache after clear_afk")
            finally:
                io_utils.read_json = orig_read  # type: ignore[assignment]
                io_utils.write_json_atomic = orig_write  # type: ignore[assignment]
                paths.BASE_DIR = old_base

