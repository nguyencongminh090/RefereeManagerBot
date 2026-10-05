import io
import json
import unittest

from domain.ports   import BotStatus, IBotStatusSource
from domain.types   import GameResult, Scoring
from storage        import Database, TournamentStore
from storage.models import GameRecord, TournamentSpec
from webui.state    import DashboardOptions, DashboardState

ROSTER = ("team,country,full_name,nickname,role,captain,contact\n"
          "Alpha,Demoland,A One,wbca1,main,yes,\nAlpha,Demoland,A Two,wbca2,main,,\n"
          "Beta,Demoland,B One,wbcb1,main,yes,\nBeta,Demoland,B Two,wbcb2,main,,\n")
WIN, LOSS, DRAW = GameResult.WIN, GameResult.LOSS, GameResult.DRAW


class FakeBots(IBotStatusSource):
    def __init__(self, bots=()):
        self._bots = list(bots)

    def bots(self):
        return list(self._bots)


class DashboardStateTests(unittest.TestCase):
    def setUp(self):
        self.store = TournamentStore(Database(":memory:"))
        self.tid = self.store.create_tournament(
            TournamentSpec("Test Cup", "team", team_size=2, max_substitutes=0, games_per_pair=4))
        self.store.import_roster_csv(self.tid, io.StringIO(ROSTER))
        self.bots = FakeBots()

    def state(self, recent=20, audit=20, can_edit=False):
        options = DashboardOptions(Scoring(1, 0.5, 0), None, recent, audit, can_edit)
        return DashboardState(self.store, self.tid, self.bots, options)

    def play(self, uid, p1="wbca1", p2="wbcb1", r1=WIN, r2=LOSS, table=1):
        self.store.record_game(self.tid, GameRecord(
            p1, p2, r1, r2, game_uid=uid, bot_name="bot-a", table_no=table))

    def test_header_names_the_tournament(self):
        snapshot = self.state().snapshot()
        self.assertEqual("Test Cup", snapshot["tournament"]["name"])
        self.assertEqual("team", snapshot["tournament"]["format"])
        self.assertTrue(snapshot["generated_at"].endswith("Z"))

    def test_standings_rank_the_winner_first(self):
        self.play("g1")
        rows = self.state().snapshot()["standings"]
        self.assertEqual(["Alpha", "Beta"], [r["name"] for r in rows])
        self.assertEqual((1, 1, 0, 1.0), (rows[0]["rank"], rows[0]["wins"], rows[0]["losses"],
                                         rows[0]["points"]))

    def test_recent_games_are_newest_first_and_capped(self):
        for i in range(3):
            self.play(f"g{i}", table=i + 1)
        games = self.state(recent=2).snapshot()["recent_games"]
        self.assertEqual([3, 2], [g["table_no"] for g in games])

    def test_game_row_shows_players_result_and_bot(self):
        self.play("g1", r1=DRAW, r2=DRAW)
        game = self.state().snapshot()["recent_games"][0]
        self.assertEqual(("wbca1", "wbcb1", "draw", "bot-a"),
                         (game["p1"], game["p2"], game["result"], game["bot"]))

    def test_win_is_reported_from_the_first_players_view(self):
        self.play("g1", r1=LOSS, r2=WIN)
        self.assertEqual("p2 wins", self.state().snapshot()["recent_games"][0]["result"])

    def test_bots_come_from_the_status_source(self):
        self.bots = FakeBots([BotStatus("bot-a", "1.1.1.1:5", (2, 4), 1.5)])
        self.assertEqual([{"name": "bot-a", "address": "1.1.1.1:5", "tables": [2, 4],
                           "idle_seconds": 1.5}], self.state().snapshot()["bots"])

    def test_problems_come_from_validate(self):
        self.store.add_team(self.tid, "Empty")
        problems = self.state().snapshot()["problems"]
        self.assertTrue(problems)
        self.assertEqual(self.store.validate(self.tid), problems)

    def test_audit_lists_latest_changes_capped(self):
        entries = self.state(audit=3).snapshot()["audit"]
        self.assertEqual(3, len(entries))
        self.assertEqual({"at", "actor", "action", "entity", "entity_id"}, set(entries[0]))

    def test_voided_games_stay_listed_and_flagged_so_they_can_be_restored(self):
        game = self.store.record_game(self.tid, GameRecord("wbca1", "wbcb1", WIN, LOSS,
                                                           game_uid="g1"))
        self.store.void_game(game)
        snapshot = self.state().snapshot()
        self.assertEqual([True], [g["voided"] for g in snapshot["recent_games"]])
        self.assertEqual(0, snapshot["standings"][0]["games"])

    def test_can_edit_flag_is_reported(self):
        self.assertFalse(self.state().snapshot()["can_edit"])
        self.assertTrue(self.state(can_edit=True).snapshot()["can_edit"])

    def test_entrants_and_sudden_death_are_listed_for_the_forms(self):
        alpha, beta = [e["id"] for e in self.store.list_entrants(self.tid)][:2]
        self.store.record_sudden_death(self.tid, alpha, beta)
        snapshot = self.state().snapshot()
        self.assertEqual(["Alpha", "Beta"], sorted(e["name"] for e in snapshot["entrants"]))
        self.assertEqual({"id", "name"}, set(snapshot["entrants"][0]))
        decider = snapshot["sudden_death"][0]
        self.assertEqual(("Alpha", "Beta", alpha, beta),
                         (decider["winner"], decider["loser"],
                          decider["winner_id"], decider["loser_id"]))

    def test_snapshot_is_json_serialisable(self):
        self.play("g1")
        json.dumps(self.state().snapshot())

    def test_empty_tournament_gives_empty_lists(self):
        store = TournamentStore(Database(":memory:"))
        tid = store.create_tournament(TournamentSpec("Empty", "individual"))
        options = DashboardOptions(Scoring(1, 0.5, 0), None, 5, 5, False)
        snapshot = DashboardState(store, tid, FakeBots(), options).snapshot()
        self.assertEqual(([], [], []), (snapshot["standings"], snapshot["recent_games"],
                                        snapshot["bots"]))


if __name__ == "__main__":
    unittest.main()
