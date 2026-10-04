import unittest
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from domain.types import GameResult
from network.messages import RequestType
from referee.commands.dispatcher import CommandDispatcher
from referee.commands.sync import SyncCommand
from referee.stats_port import IStatsSource, PairTotals, StatGame, StatsError
from tests.test_page_parser import SETTINGS
from tests.test_session import FakeDriver, FakeSocket

TEXTS = SETTINGS.texts
ZONE = ZoneInfo(SETTINGS.tournament.timezone)
ROUND_START = datetime(2026, 10, 4, 18, 0, tzinfo=ZONE)
NOW = datetime(2026, 10, 4, 20, 0, tzinfo=ZONE)
WIN, LOSS, DRAW = GameResult.WIN, GameResult.LOSS, GameResult.DRAW


def game(index, result, hour=19, minute=0, day=4):
    """A game of alice (the profile owner) against bob; `index` makes the game id."""
    return StatGame(f"gm{index}", datetime(2026, 10, day, hour, minute, tzinfo=ZONE), "alice", "bob", result)


class FakeStats(IStatsSource):
    def __init__(self, games=(), error=None, totals=None):
        self.games, self.error, self.totals, self.asked = list(games), error, totals, []

    def pair_totals(self, player, opponent):
        self.asked.append((player, opponent))
        if self.error:
            raise self.error
        return self.totals

    def pair_games(self, player, opponent):
        self.asked.append((player, opponent))
        if self.error:
            raise self.error
        return self.games


class SyncTestCase(unittest.TestCase):
    def build(self, games=(), error=None, names=("alice", "bob"), round_start=ROUND_START, totals=None):
        tournament = replace(SETTINGS.tournament, round_start=round_start)
        settings = replace(SETTINGS, tournament=tournament)
        self.driver, self.socket = FakeDriver(names), FakeSocket()
        self.stats = FakeStats(games, error, totals)
        self.dispatcher = CommandDispatcher(self.driver, self.socket)
        SyncCommand(settings, self.stats, clock=lambda: NOW).register_on(self.dispatcher)

    def sync(self, text="!sync", sender="gtrate"):
        self.dispatcher.dispatch(sender, text)

    def sent_scores(self):
        [packet] = self.socket.packets
        self.assertEqual(RequestType.SET_SCORE.value, packet["type"])
        return packet["data"]

    def assertRefused(self, reason_part=None):
        self.assertEqual([], self.socket.packets)
        [said] = self.driver.said
        self.assertTrue(said.startswith(TEXTS.sync_failed.split("{")[0]), said)
        if reason_part:
            self.assertIn(reason_part, said)


class SyncScoreTests(SyncTestCase):
    def test_sends_the_score_counted_from_the_stats_page(self):
        self.build([game(1, WIN), game(2, WIN), game(3, LOSS)])
        self.sync()
        self.assertEqual({"sender": "gtrate", "players": ["alice", "bob"], "scores": [2.0, 1.0]},
                         self.sent_scores())
        self.assertEqual([("alice", "bob")], self.stats.asked)

    def test_a_draw_counts_with_the_configured_points(self):
        self.build([game(1, DRAW), game(2, WIN)])
        self.sync()
        points = SETTINGS.tournament.scoring.games
        self.assertEqual([points.win + points.draw, points.draw + points.loss], self.sent_scores()["scores"])

    def test_games_before_the_round_start_are_ignored(self):
        self.build([game(1, WIN, hour=17, minute=59), game(2, LOSS, hour=18, minute=0)])
        self.sync()
        self.assertEqual([0.0, 1.0], self.sent_scores()["scores"])

    def test_seats_are_read_when_the_command_arrives(self):
        self.build([game(1, WIN)], names=("alice", "bob"))
        self.driver.names = ("carol", "dave")
        self.sync()
        self.assertEqual([("carol", "dave")], self.stats.asked)


class SyncRoundStartTests(SyncTestCase):
    def test_a_time_argument_overrides_the_configured_round_start_for_today(self):
        self.build([game(1, WIN, hour=18, minute=30), game(2, WIN, hour=19, minute=30)])
        self.sync("!sync 19:00")
        self.assertEqual([1.0, 0.0], self.sent_scores()["scores"])

    def test_a_date_and_time_argument_is_accepted(self):
        self.build([game(1, WIN, hour=19, minute=30)])
        self.sync("!sync 2026-10-04 19:00")
        self.assertEqual([1.0, 0.0], self.sent_scores()["scores"])

    def test_an_unreadable_time_is_answered_with_the_usage_text(self):
        self.build([game(1, WIN)])
        for text in ("!sync noon", "!sync 25:00", "!sync 19:00 20:00 21:00"):
            self.sync(text)
        self.assertEqual([], self.socket.packets)
        self.assertEqual([TEXTS.sync_usage] * 3, self.driver.said)


class SyncTodayTests(SyncTestCase):
    """Without a round start and without an argument the games of today are counted."""

    def test_counts_the_games_of_today_only(self):
        games = [game(1, WIN, hour=0, minute=5), game(2, WIN, hour=19), game(3, LOSS, hour=23, minute=59, day=3)]
        self.build(games, round_start=None)
        self.sync()
        self.assertEqual([2.0, 0.0], self.sent_scores()["scores"])

    def test_refuses_when_the_pair_has_no_game_today(self):
        self.build([game(1, WIN, day=3)], round_start=None)
        self.sync()
        self.assertRefused()

    def test_more_games_today_than_a_match_changes_nothing(self):
        total = SETTINGS.tournament.total_matches
        self.build([game(i, WIN, hour=10, minute=i) for i in range(total + 1)], round_start=None)
        self.sync()
        self.assertRefused(str(total + 1))

    def test_the_configured_round_start_wins_over_today(self):
        games = [game(1, WIN, hour=19, minute=50), game(2, LOSS, hour=19, minute=0)]
        self.build(games, round_start=datetime(2026, 10, 4, 19, 30, tzinfo=ZONE))
        self.sync()
        self.assertEqual([1.0, 0.0], self.sent_scores()["scores"])


class SyncTotalTests(SyncTestCase):
    def test_total_sets_the_score_from_the_all_time_record_without_a_round_start(self):
        self.build(totals=PairTotals(wins=3, losses=1, draws=1), round_start=None)
        self.sync("!sync total")
        points = SETTINGS.tournament.scoring.games
        self.assertEqual([points.points(3, 1, 1), points.points(1, 1, 3)], self.sent_scores()["scores"])
        self.assertEqual([("alice", "bob")], self.stats.asked)

    def test_total_refuses_a_record_longer_than_a_match(self):
        self.build(totals=PairTotals(wins=17, losses=9, draws=0))
        self.sync("!sync total")
        self.assertRefused("26")

    def test_total_refuses_when_the_pair_never_played(self):
        for totals in (None, PairTotals(0, 0, 0)):
            self.build(totals=totals)
            self.sync("!sync total")
            self.assertRefused()

    def test_total_reports_a_stats_failure(self):
        self.build(error=StatsError("timed out"))
        self.sync("!sync TOTAL")
        self.assertRefused("timed out")


class SyncRefusalTests(SyncTestCase):
    def test_no_games_found_changes_nothing(self):
        self.build([game(1, WIN, hour=10)])
        self.sync()
        self.assertRefused()

    def test_more_games_than_a_match_has_changes_nothing(self):
        self.build([game(i, WIN) for i in range(SETTINGS.tournament.total_matches + 1)])
        self.sync()
        self.assertRefused(str(SETTINGS.tournament.total_matches))

    def test_a_stats_failure_is_reported_with_its_reason(self):
        self.build(error=StatsError("timed out"))
        self.sync()
        self.assertRefused("timed out")

    def test_unreadable_seats_are_reported_and_the_site_is_not_asked(self):
        self.build(names=("alice", ""))
        self.sync()
        self.assertEqual([], self.stats.asked)
        self.assertEqual([TEXTS.seats_unreadable], self.driver.said)


if __name__ == "__main__":
    unittest.main()
