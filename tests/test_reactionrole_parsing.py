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

    def test_extract_channel_id_token(self) -> None:
        from modules.roles import _extract_channel_id_token

        self.assertEqual(_extract_channel_id_token("123"), 123)
        self.assertEqual(_extract_channel_id_token("<#123>"), 123)
        self.assertIsNone(_extract_channel_id_token(""))
        self.assertIsNone(_extract_channel_id_token("abc"))
        self.assertIsNone(_extract_channel_id_token("<#abc>"))
        self.assertIsNone(_extract_channel_id_token("<#>"))


if __name__ == "__main__":
    unittest.main()

