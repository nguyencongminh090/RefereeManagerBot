import io
import unittest
import urllib.error
from datetime import datetime, timezone

from config.settings import StatsConfig
from referee.stats_port import StatsError
from referee.stats_source import HttpStatsSource
from tests.test_stats_parser import PAGE, TOTALS_PAGE

CONFIG = StatsConfig(url="https://stats.example/en/stat.phtml", game_code="gm", timezone="UTC",
                     timeout_seconds=4.0)


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *exc): return False


class HttpStatsSourceTests(unittest.TestCase):
    def build(self, reply):
        self.requests = []

        def opener(request, timeout):
            self.requests.append((request.full_url, timeout))
            if isinstance(reply, Exception):
                raise reply
            return FakeResponse(reply.encode("utf-8"))

        return HttpStatsSource(CONFIG, opener=opener)

    def test_asks_for_the_games_of_the_pair_with_the_configured_timeout(self):
        source = self.build(PAGE)
        games = source.pair_games("alice", "bob")
        self.assertEqual(3, len(games))
        self.assertEqual([("https://stats.example/en/stat.phtml?u=alice&g=gm&sk=2&oid=bob", 4.0)], self.requests)
        self.assertEqual(datetime(2026, 10, 4, 18, 48, tzinfo=timezone.utc), games[0].played_at)

    def test_names_are_url_encoded(self):
        source = self.build(PAGE)
        source.pair_games("a b", "c&d")
        self.assertIn("u=a+b", self.requests[0][0])
        self.assertIn("oid=c%26d", self.requests[0][0])

    def test_totals_are_searched_by_opponent_on_the_opponents_tab(self):
        totals = self.build(TOTALS_PAGE).pair_totals("alice", "bob")
        self.assertEqual((17, 9, 2), (totals.wins, totals.losses, totals.draws))
        self.assertEqual("https://stats.example/en/stat.phtml?u=alice&g=gm&sk=3&sid=bob", self.requests[0][0])

    def test_totals_of_an_opponent_never_met_are_none(self):
        self.assertIsNone(self.build(TOTALS_PAGE).pair_totals("alice", "dave"))

    def test_a_network_failure_becomes_a_stats_error(self):
        for failure in (urllib.error.URLError("down"), TimeoutError("slow")):
            with self.assertRaises(StatsError):
                self.build(failure).pair_games("alice", "bob")
            with self.assertRaises(StatsError):
                self.build(failure).pair_totals("alice", "bob")

    def test_a_bad_page_becomes_a_stats_error(self):
        with self.assertRaises(StatsError):
            self.build(PAGE.replace("<b>draw</b>", "<b>odd</b>")).pair_games("alice", "bob")


if __name__ == "__main__":
    unittest.main()
