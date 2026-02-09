import unittest


class TestReactionRoleParsing(unittest.TestCase):
    def test_extract_role_id_token(self) -> None:
        from modules.roles import _extract_role_id_token

        self.assertEqual(_extract_role_id_token("123"), 123)
        self.assertEqual(_extract_role_id_token("<@&123>"), 123)
        self.assertIsNone(_extract_role_id_token(""))
        self.assertIsNone(_extract_role_id_token("abc"))
        self.assertIsNone(_extract_role_id_token("<@&abc>"))
        self.assertIsNone(_extract_role_id_token("<@&>"))
        self.assertIsNone(_extract_role_id_token("<@123>"))

    def test_parse_pairs(self) -> None:
        from modules.roles import _parse_reactionrole_pairs

        self.assertEqual(_parse_reactionrole_pairs(["✅", "123"]), [("✅", 123)])
        self.assertEqual(
            _parse_reactionrole_pairs(["✅", "<@&123>", "❌", "456"]),
            [("✅", 123), ("❌", 456)],
        )
        self.assertIsNone(_parse_reactionrole_pairs([]))
        self.assertIsNone(_parse_reactionrole_pairs(["✅"]))
        self.assertIsNone(_parse_reactionrole_pairs(["✅", "abc"]))
        self.assertIsNone(_parse_reactionrole_pairs(["✅", "123", "❌"]))


if __name__ == "__main__":
    unittest.main()

