import unittest

from referee.lobby       import LobbyWatcher
from referee.page_parser import LobbyTable, Seat

ROSTER = {"wbca1": "Alpha", "wbcb1": "Beta", "wbca2": "Alpha"}


def table(number, *names):
    return LobbyTable(number, "1m+1s", tuple(Seat(n, 1200) for n in names), joinable=False)


class LobbyWatcherTests(unittest.TestCase):
    def setUp(self):
        self.watcher = LobbyWatcher()
        self.watcher.set_roster(ROSTER)

    def test_picks_table_with_two_roster_players_from_different_teams(self):
        tables = [table(1, "wbca1", "wbca2"), table(2, "stranger", "wbcb1"), table(3, "wbca1", "wbcb1")]
        self.assertEqual(3, self.watcher.pick(tables).number)

    def test_nothing_to_pick(self):
        self.assertIsNone(self.watcher.pick([table(1, "wbca1")]))

    def test_no_roster_yet(self):
        self.assertFalse(LobbyWatcher().has_roster)
        self.assertIsNone(LobbyWatcher().pick([table(3, "wbca1", "wbcb1")]))

    def test_skipped_table_is_ignored_until_it_leaves_the_lobby(self):
        tables = [table(3, "wbca1", "wbcb1")]
        self.watcher.skip(3)
        self.assertIsNone(self.watcher.pick(tables))
        self.assertIsNone(self.watcher.pick([]))                 # table 3 gone: skip is forgotten
        self.assertEqual(3, self.watcher.pick(tables).number)

    def test_choice_among_eligible_tables_is_delegated_to_the_chooser(self):
        watcher = LobbyWatcher(choose=lambda options: options[-1])
        watcher.set_roster(ROSTER)
        tables = [table(1, "wbca1", "wbcb1"), table(2, "wbca2", "wbcb1"), table(3, "stranger", "wbcb1")]
        self.assertEqual(2, watcher.pick(tables).number)

    def test_default_choice_is_one_of_the_eligible_tables(self):
        tables = [table(1, "wbca1", "wbcb1"), table(2, "wbca2", "wbcb1"), table(3, "stranger", "wbcb1")]
        self.assertIn(self.watcher.pick(tables).number, (1, 2))

    def test_the_chooser_only_sees_tables_that_are_not_skipped(self):
        seen = []
        watcher = LobbyWatcher(choose=lambda options: seen.append([t.number for t in options]) or options[0])
        watcher.set_roster(ROSTER)
        watcher.skip(1)
        watcher.pick([table(1, "wbca1", "wbcb1"), table(2, "wbca2", "wbcb1")])
        self.assertEqual([[2]], seen)


if __name__ == "__main__":
    unittest.main()
