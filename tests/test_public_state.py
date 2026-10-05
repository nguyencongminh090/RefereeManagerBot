import io
import unittest

from domain.ports   import BotStatus
from domain.types   import GameResult, Scoring
from storage        import Database, TournamentStore
from storage.models import GameRecord, TournamentSpec
from tests.test_dashboard_state import ROSTER, FakeBots
from webui.public_state import PublicOptions, PublicState

WIN, LOSS, DRAW = GameResult.WIN, GameResult.LOSS, GameResult.DRAW
HIDDEN_KEYS = {"bots", "audit", "problems", "can_edit", "entrants", "sudden_death",
               "refresh_seconds"}


class PublicStateTests(unittest.TestCase):
    def setUp(self):
        self.store = TournamentStore(Database(":memory:"))
        self.tid = self.store.create_tournament(
            TournamentSpec("Test Cup", "team", team_size=2, max_substitutes=0, games_per_pair=4))
        self.store.import_roster_csv(self.tid, io.StringIO(ROSTER))
        self.bots = FakeBots()

    def state(self, recent=20):
        return PublicState(self.store, self.tid, self.bots,
                           PublicOptions(Scoring(1, 0.5, 0), None, recent))

    def play(self, uid, table=1, r1=WIN, r2=LOSS):
        self.store.record_game(self.tid, GameRecord(
            "wbca1", "wbcb1", r1, r2, game_uid=uid, bot_name="secret-bot", table_no=table))

    def test_carries_the_tournament_standings_and_results(self):
        self.play("g1")
        snapshot = self.state().snapshot()
        self.assertEqual("Test Cup", snapshot["tournament"]["name"])
        self.assertEqual(["Alpha", "Beta"], [r["name"] for r in snapshot["standings"]])
        self.assertEqual(("wbca1", "wbcb1", "p1 wins", 1),
                         tuple(snapshot["recent_games"][0][k]
                               for k in ("p1", "p2", "result", "table_no")))

    def test_leaves_out_everything_organizer_only(self):
        self.play("g1")
        self.bots = FakeBots([BotStatus("secret-bot", "10.0.0.5:4000", (1,), 2.0)])
        snapshot = self.state().snapshot()
        self.assertFalse(HIDDEN_KEYS & set(snapshot))
        text = repr(snapshot)
        for secret in ("secret-bot", "10.0.0.5"):
            self.assertNotIn(secret, text)
        self.assertEqual({"at", "table_no", "p1", "p2", "result"},
                         set(snapshot["recent_games"][0]))

    def test_voided_games_are_not_shown(self):
        self.play("g1")
        game_id = self.store.list_games(self.tid)[0]["id"]
        self.store.void_game(game_id, True)
        self.assertEqual([], self.state().snapshot()["recent_games"])

    def test_recent_games_are_newest_first_and_capped(self):
        for i in range(3):
            self.play(f"g{i}", table=i + 1)
        games = self.state(recent=2).snapshot()["recent_games"]
        self.assertEqual([3, 2], [g["table_no"] for g in games])

    def test_live_tables_are_the_claimed_tables_without_bot_names(self):
        self.bots = FakeBots([BotStatus("a", "x", (4, 2), 1.0), BotStatus("b", "y", (2, 7), 1.0)])
        self.assertEqual([2, 4, 7], self.state().snapshot()["live_tables"])
