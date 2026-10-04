import unittest

from referee.info_text import BREAK_HINT, FINAL, LAST_GAME, info_key


class InfoKeyTests(unittest.TestCase):
    def test_last_game_and_final_follow_the_configured_total(self):
        self.assertIsNone(info_key(games=5, total=12, break_after=0))
        self.assertEqual(LAST_GAME, info_key(games=11, total=12, break_after=0))
        self.assertEqual(FINAL, info_key(games=12, total=12, break_after=0))
        self.assertEqual(FINAL, info_key(games=13, total=12, break_after=0))

    def test_break_hint_comes_one_game_before_each_break(self):
        self.assertEqual(BREAK_HINT, info_key(games=9, total=15, break_after=10))
        self.assertIsNone(info_key(games=10, total=15, break_after=10))
        self.assertEqual(LAST_GAME, info_key(games=14, total=15, break_after=10))
        self.assertEqual(FINAL, info_key(games=15, total=15, break_after=10))

    def test_no_break_hint_without_a_break_rule(self):
        self.assertIsNone(info_key(games=9, total=15, break_after=0))

    def test_the_last_game_wins_over_a_break_hint(self):
        self.assertEqual(LAST_GAME, info_key(games=9, total=10, break_after=5))
        self.assertEqual(BREAK_HINT, info_key(games=4, total=10, break_after=5))


if __name__ == "__main__":
    unittest.main()
