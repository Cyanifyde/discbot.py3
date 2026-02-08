import unittest


class TestPermissionsCache(unittest.IsolatedAsyncioTestCase):
    async def test_permissions_cached_and_invalidated(self) -> None:
        import core.permissions as perms

        calls = {"get": 0, "update": 0}

        async def fake_get_guild_module_data(guild_id: int, module: str):
            calls["get"] += 1
            # Minimal valid structure for normalization.
            return {"modules": {}, "commands": {}}

        async def fake_update_guild_module_data(guild_id: int, module: str, data):
            calls["update"] += 1
            return None

        orig_get = perms.get_guild_module_data
        orig_update = perms.update_guild_module_data
        try:
            perms.get_guild_module_data = fake_get_guild_module_data  # type: ignore[assignment]
            perms.update_guild_module_data = fake_update_guild_module_data  # type: ignore[assignment]

            perms.invalidate_guild_permissions_cache(123)

            a = await perms.get_guild_permissions(123)
            b = await perms.get_guild_permissions(123)
            self.assertIsInstance(a, dict)
            self.assertIsInstance(b, dict)
            self.assertEqual(calls["get"], 1, "Expected permissions to be loaded once within TTL")

            await perms.save_guild_permissions(123, {"modules": {}, "commands": {}})
            _ = await perms.get_guild_permissions(123)
            self.assertEqual(calls["get"], 2, "Expected cache invalidation after save")
            self.assertEqual(calls["update"], 1)
        finally:
            perms.get_guild_module_data = orig_get  # type: ignore[assignment]
            perms.update_guild_module_data = orig_update  # type: ignore[assignment]
            perms.invalidate_guild_permissions_cache(123)

