import io
import unittest

from domain.types     import GameResult, Scoring
from storage          import Database, TournamentStore
from storage.models   import GameRecord, TournamentSpec
from webui.actions    import ActionError, ActionOptions, DashboardActions

ROSTER = ("team,country,full_name,nickname,role,captain,contact\n"
          "Alpha,Demoland,A One,wbca1,main,yes,\nAlpha,Demoland,A Two,wbca2,main,,\n"
          "Beta,Demoland,B One,wbcb1,main,yes,\nBeta,Demoland,B Two,wbcb2,main,,\n")
WIN, LOSS = GameResult.WIN, GameResult.LOSS


class DashboardActionsTests(unittest.TestCase):
    def setUp(self):
        self.store = TournamentStore(Database(":memory:"))
        self.tid = self.store.create_tournament(
            TournamentSpec("Cup", "team", team_size=2, max_substitutes=0, games_per_pair=4))
        self.store.import_roster_csv(self.tid, io.StringIO(ROSTER))
        self.changes = 0
        self.actions = DashboardActions(
            self.store, self.tid, ActionOptions(Scoring(1, 0.5, 0), self.count_change))
        self.alpha, self.beta = [e["id"] for e in self.store.list_entrants(self.tid)][:2]

    def count_change(self):
        self.changes += 1

    def play(self, uid="g1"):
        return self.store.record_game(self.tid, GameRecord("wbca1", "wbcb1", WIN, LOSS,
                                                           game_uid=uid))

    def voided(self, game_id):
        return {g["id"] for g in self.store.list_games(self.tid, True) if g["voided"]} \
            & {game_id}

    def test_void_and_restore_a_game(self):
        game = self.play()
        self.actions.perform({"action": "void_game", "game_id": game, "voided": True})
        self.assertEqual({game}, self.voided(game))
        self.actions.perform({"action": "void_game", "game_id": game, "voided": False})
        self.assertEqual(set(), self.voided(game))

    def test_changes_are_audited_under_the_dashboard_actor(self):
        game = self.play()
        self.actions.perform({"action": "void_game", "game_id": game, "voided": True})
        latest = self.store.audit_log(1)[0]
        self.assertEqual(("dashboard", "void", "game"),
                         (latest["actor"], latest["action"], latest["entity"]))

    def test_the_changed_callback_runs_after_a_success_only(self):
        game = self.play()
        with self.assertRaises(ActionError):
            self.actions.perform({"action": "void_game", "game_id": 999, "voided": True})
        self.assertEqual(0, self.changes)
        self.actions.perform({"action": "void_game", "game_id": game, "voided": True})
        self.assertEqual(1, self.changes)

    def test_game_of_another_tournament_is_refused(self):
        other = self.store.create_tournament(TournamentSpec("Other", "team", team_size=2,
                                                            max_substitutes=0))
        self.store.import_roster_csv(other, io.StringIO(ROSTER.replace("wbc", "xyz")))
        foreign = self.store.record_game(other, GameRecord("xyza1", "xyzb1", WIN, LOSS,
                                                           game_uid="f1"))
        with self.assertRaises(ActionError):
            self.actions.perform({"action": "void_game", "game_id": foreign, "voided": True})
        self.assertEqual(set(), {g["id"] for g in self.store.list_games(other, True)
                                 if g["voided"]})

    def test_sudden_death_can_be_recorded_and_removed(self):
        self.actions.perform({"action": "record_sudden_death",
                              "winner_id": self.alpha, "loser_id": self.beta})
        self.assertEqual(1, len(self.store.list_sudden_death(self.tid)))
        self.actions.perform({"action": "delete_sudden_death",
                              "winner_id": self.alpha, "loser_id": self.beta})
        self.assertEqual([], self.store.list_sudden_death(self.tid))

    def test_storage_rejection_becomes_an_action_error_with_its_message(self):
        with self.assertRaises(ActionError) as ctx:
            self.actions.perform({"action": "record_sudden_death",
                                  "winner_id": self.alpha, "loser_id": self.alpha})
        self.assertIn("two different", str(ctx.exception))

    def test_set_pair_score_records_corrective_games(self):
        self.actions.perform({"action": "set_pair_score", "p1": "wbca1", "p2": "wbcb1",
                              "points1": 2, "points2": 1})
        rows = self.store.standings(self.tid, Scoring(1, 0.5, 0))
        self.assertEqual({"Alpha": 2.0, "Beta": 1.0}, {r.name: r.points for r in rows})

    def test_bad_requests_are_refused_without_touching_the_store(self):
        game = self.play()
        bad = [{}, {"action": "nope"}, {"action": "void_game"},
               {"action": "void_game", "game_id": "1", "voided": True},
               {"action": "void_game", "game_id": True, "voided": True},
               {"action": "void_game", "game_id": game, "voided": "yes"},
               {"action": "set_pair_score", "p1": "wbca1", "p2": "wbcb1",
                "points1": -1, "points2": 1},
               {"action": "set_pair_score", "p1": "", "p2": "wbcb1", "points1": 1, "points2": 1},
               {"action": "set_pair_score", "p1": "wbca1", "p2": "wbcb1",
                "points1": float("nan"), "points2": 1},
               {"action": "record_sudden_death", "winner_id": "a", "loser_id": 2}]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ActionError):
                self.actions.perform(request)
        self.assertEqual(set(), self.voided(game))
        self.assertEqual(0, self.changes)

    def test_non_object_request_is_refused(self):
        with self.assertRaises(ActionError):
            self.actions.perform(["void_game"])


if __name__ == "__main__":
    unittest.main()
