import unittest


class _DummyGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class _DummyPerms:
    manage_roles = True


class _DummyMember:
    guild_permissions = _DummyPerms()


class _DummyMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.guild = _DummyGuild(1)
        self.author = _DummyMember()

    async def reply(self, *args, **kwargs):
        return None


class _DummyBot:
    pass


class TestAnalyticsModuleAwaits(unittest.IsolatedAsyncioTestCase):
    async def test_bot_stats_is_awaited(self) -> None:
        import modules.analytics as analytics_mod

        async def _enabled(_gid: int, _name: str) -> bool:
            return True

        async def _can_use(_member, _name: str) -> bool:
            return True

        analytics_mod.is_module_enabled = _enabled  # type: ignore[assignment]
        analytics_mod.can_use_module = _can_use  # type: ignore[assignment]

        msg = _DummyMessage("stats bot")
        ok = await analytics_mod.handle_analytics_command(msg, _DummyBot())
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

