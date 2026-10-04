import unittest

from domain.types import GameResult
from domain.types import Scoring
from storage import Database, TournamentStore
from storage.errors import DuplicateError, NotFoundError, ValidationError
from storage.models import GameRecord, NewIndividual, NewPlayer, RankingRules, TournamentSpec
from storage.ranking import Encounter, GameLine, RankInput, encounters_from_games, rank_entrants
from tests.test_storage import SCORING, DRAW, LOSS, WIN, open_store

DEFAULT_ORDER = ("score_difference", "head_to_head", "sudden_death")


def row(entrant_id, points, difference=0.0):
    return RankInput(entrant_id, f"E{entrant_id}", points, difference)


def ranks(placements):
    return [(p.entrant_id, p.rank, p.tied) for p in placements]


class RankEntrantsTests(unittest.TestCase):
    def test_points_decide_first(self):
        result = rank_entrants([row(1, 3), row(2, 5)], [], [], DEFAULT_ORDER)
        self.assertEqual([(2, 1, False), (1, 2, False)], ranks(result))

    def test_tie_on_points_broken_by_difference(self):
        result = rank_entrants([row(1, 4, 1.0), row(2, 4, 3.0)], [], [], DEFAULT_ORDER)
        self.assertEqual([2, 1], [p.entrant_id for p in result])
        self.assertFalse(any(p.tied for p in result))

    def test_tie_on_both_broken_by_head_to_head(self):
        encounters = [Encounter(1, 2, 7.0, 5.0)]
        result = rank_entrants([row(1, 4, 2.0), row(2, 4, 2.0)], encounters, [], DEFAULT_ORDER)
        self.assertEqual([(1, 1, False), (2, 2, False)], ranks(result))

    def test_three_way_tie_uses_mini_table_of_tied_only(self):
        # 4 is not tied and beat everybody: its games must not count in the mini-table.
        rows = [row(1, 6), row(2, 6), row(3, 6), row(4, 9)]
        encounters = [Encounter(1, 2, 1.0, 0.0), Encounter(2, 3, 1.0, 0.0), Encounter(3, 1, 0.5, 0.5),
                      Encounter(1, 4, 0.0, 1.0), Encounter(2, 4, 0.0, 1.0), Encounter(3, 4, 0.0, 1.0)]
        result = rank_entrants(rows, encounters, [], DEFAULT_ORDER)
        # mini-table: 1 = 1.5, 2 = 1.0, 3 = 0.5
        self.assertEqual([4, 1, 2, 3], [p.entrant_id for p in result])
        self.assertFalse(any(p.tied for p in result))

    def test_sudden_death_is_last_criterion(self):
        rows = [row(1, 4), row(2, 4)]
        self.assertEqual([(1, 1, True), (2, 1, True)], ranks(rank_entrants(rows, [], [], DEFAULT_ORDER)))
        result = rank_entrants(rows, [], [(2, 1)], DEFAULT_ORDER)
        self.assertEqual([(2, 1, False), (1, 2, False)], ranks(result))

    def test_still_tied_share_rank_and_keep_name_order(self):
        rows = [row(1, 4), row(2, 4), row(3, 4), row(4, 1)]
        result = rank_entrants(rows, [], [], DEFAULT_ORDER)
        self.assertEqual([(1, 1, True), (2, 1, True), (3, 1, True), (4, 4, False)], ranks(result))

    def test_partial_break_leaves_remaining_tied(self):
        rows = [row(1, 4, 3.0), row(2, 4, 1.0), row(3, 4, 1.0)]
        result = rank_entrants(rows, [], [], DEFAULT_ORDER)
        self.assertEqual([(1, 1, False), (2, 2, True), (3, 2, True)], ranks(result))

    def test_configured_order_is_respected(self):
        rows       = [row(1, 4, 1.0), row(2, 4, 5.0)]
        encounters = [Encounter(1, 2, 1.0, 0.0)]
        by_diff = rank_entrants(rows, encounters, [], ("score_difference", "head_to_head"))
        by_h2h  = rank_entrants(rows, encounters, [], ("head_to_head", "score_difference"))
        self.assertEqual([2, 1], [p.entrant_id for p in by_diff])
        self.assertEqual([1, 2], [p.entrant_id for p in by_h2h])

    def test_criterion_missing_from_config_is_not_used(self):
        rows = [row(1, 4, 1.0), row(2, 4, 5.0)]
        result = rank_entrants(rows, [], [], ("head_to_head",))
        self.assertEqual([(1, 1, True), (2, 1, True)], ranks(result))

    def test_unknown_criterion_rejected(self):
        with self.assertRaises(ValueError):
            rank_entrants([row(1, 1), row(2, 1)], [], [], ("coin_toss",))


class EncountersFromGamesTests(unittest.TestCase):
    def test_game_points_per_encounter(self):
        games = [GameLine(1, 2, None, 1), GameLine(2, 1, None, 3), GameLine(1, 2, None, 2)]
        [enc] = encounters_from_games(games, SCORING, None)
        self.assertEqual((1, 2, 1.5, 1.5), (enc.entrant_a, enc.entrant_b, enc.points_a, enc.points_b))

    def test_match_points_when_configured(self):
        match = Scoring(win=1.0, draw=0.5, loss=0.0)
        games = [GameLine(1, 2, None, 1), GameLine(1, 2, None, 1), GameLine(2, 1, 7, 3)]
        encounters = {(e.entrant_a, e.entrant_b, e.points_a): e for e in encounters_from_games(games, SCORING, match)}
        self.assertEqual(2, len(encounters))                       # two fixtures = two team matches
        self.assertIn((1, 2, 1.0), encounters)                      # 2-0 win
        self.assertIn((1, 2, 0.5), encounters)                      # draw


class StandingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store(self)
        self.tid = self.store.create_tournament(TournamentSpec("T", "individual", games_per_pair=2))
        for name in ("A", "B", "C"):
            self.store.add_individual(self.tid, NewIndividual(f"Person {name}", f"wbc{name.lower()}"))

    def game(self, a, b, result_a):
        r = {"win": (WIN, LOSS), "loss": (LOSS, WIN), "draw": (DRAW, DRAW)}[result_a]
        self.store.record_game(self.tid, GameRecord(a, b, *r))

    def names(self, **kwargs):
        return [(r.name, r.rank, r.tied) for r in self.store.standings(self.tid, SCORING, **kwargs)]

    def test_head_to_head_breaks_tie_on_points_and_difference(self):
        for name in ("D", "E"):
            self.store.add_individual(self.tid, NewIndividual(f"Person {name}", f"wbc{name.lower()}"))
        self.game("wbca", "wbcb", "win")
        self.game("wbca", "wbcd", "loss")
        self.game("wbcb", "wbce", "win")
        # d: 1 point, +1; a and b: 1 point, 0; e: 0. Only a-b is decided by the direct encounter.
        self.assertEqual([("wbcd", 1, False), ("wbca", 2, False), ("wbcb", 3, False), ("wbce", 4, False)][:3],
                         self.names()[:3])

    def test_circle_of_results_stays_tied(self):
        self.game("wbca", "wbcb", "win")
        self.game("wbcb", "wbcc", "win")
        self.game("wbcc", "wbca", "win")
        self.assertEqual([("wbca", 1, True), ("wbcb", 1, True), ("wbcc", 1, True)], self.names())

    def test_sudden_death_recorded_and_used(self):
        self.game("wbca", "wbcb", "draw")
        self.assertEqual([("wbca", 1, True), ("wbcb", 1, True)], self.names()[:2])
        self.store.with_actor("tester").record_sudden_death(self.tid, "wbcb", "wbca")
        self.assertEqual([("wbcb", 1, False), ("wbca", 2, False)], self.names()[:2])
        [entry] = self.store.list_sudden_death(self.tid)
        self.assertEqual(("wbcb", "wbca"), (entry["winner"], entry["loser"]))
        self.assertEqual("sudden_death", self.store.audit_log(1)[0]["entity"])

    def test_sudden_death_ignored_when_not_in_configured_tiebreaks(self):
        self.game("wbca", "wbcb", "draw")
        self.store.record_sudden_death(self.tid, "wbcb", "wbca")
        rules = RankingRules(tiebreaks=("score_difference", "head_to_head"))
        self.assertTrue(all(tied for _, _, tied in self.names(rules=rules)[:2]))

    def test_sudden_death_validation(self):
        self.store.record_sudden_death(self.tid, "wbca", "wbcb")
        with self.assertRaises(DuplicateError):
            self.store.record_sudden_death(self.tid, "wbcb", "wbca")   # contradicts the recorded decider
        with self.assertRaises(ValidationError):
            self.store.record_sudden_death(self.tid, "wbca", "wbca")
        with self.assertRaises(NotFoundError):
            self.store.record_sudden_death(self.tid, "wbca", "nobody")
        self.store.delete_sudden_death(self.tid, "wbca", "wbcb")
        self.assertEqual([], self.store.list_sudden_death(self.tid))


class TeamMatchPointsTests(unittest.TestCase):
    def test_match_points_are_summed_per_team_match(self):
        store = open_store(self)
        tid = store.create_tournament(TournamentSpec("T", "team", team_size=1, games_per_pair=2))
        for team in ("Alpha", "Beta", "Gamma"):
            store.add_team(tid, team)
            store.add_player(tid, team, NewPlayer(f"P {team}", f"wbc{team.lower()}", is_captain=True))
        # Alpha beats Beta 2-0, Gamma beats Beta 1.5-0.5 and Alpha 1.5-0.5
        store.record_game(tid, GameRecord("wbcalpha", "wbcbeta", WIN, LOSS))
        store.record_game(tid, GameRecord("wbcalpha", "wbcbeta", WIN, LOSS))
        store.record_game(tid, GameRecord("wbcgamma", "wbcbeta", WIN, LOSS))
        store.record_game(tid, GameRecord("wbcgamma", "wbcbeta", DRAW, DRAW))
        store.record_game(tid, GameRecord("wbcalpha", "wbcgamma", LOSS, WIN))
        store.record_game(tid, GameRecord("wbcalpha", "wbcgamma", DRAW, DRAW))
        rules = RankingRules(match_points=Scoring(1.0, 0.5, 0.0))
        table = store.standings(tid, SCORING, rules=rules)
        self.assertEqual(["Gamma", "Alpha", "Beta"], [r.name for r in table])
        self.assertEqual([2.0, 1.0, 0.0], [r.match_points for r in table])
        self.assertEqual(3.0, table[0].points)                       # game points stay available


class AdminCliTests(unittest.TestCase):
    def test_sudden_death_command_and_standings_output(self):
        import contextlib, io, os, tempfile
        from tools.admin_db import main
        with tempfile.TemporaryDirectory() as folder:
            db = os.path.join(folder, "t.db")
            def run(*argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = main(["--db", db, "--actor", "tester", *argv])
                return code, out.getvalue()
            run("tournament", "add", "T", "--format", "individual")
            run("individual", "add", "T", "Person A", "wbca")
            run("individual", "add", "T", "Person B", "wbcb")
            scoring = ("--win", "1", "--draw", "0.5", "--loss", "0")
            self.assertIn("1=", run("standings", "T", *scoring)[1])
            self.assertEqual(0, run("sudden-death", "add", "T", "wbcb", "wbca")[0])
            text = run("standings", "T", *scoring)[1]
            self.assertNotIn("=", text.split("\n", 2)[2].split()[0])
            self.assertIn("wbcb", run("sudden-death", "list", "T")[1])
            self.assertIn("sudden_death", run("audit")[1])


class MigrationTests(unittest.TestCase):
    def test_version_one_database_gets_sudden_death_table(self):
        import os, sqlite3, tempfile
        from storage.database import _SCHEMA_FILE
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "old.db")
            raw = sqlite3.connect(path)
            raw.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
            raw.execute("PRAGMA user_version = 1")
            raw.commit()
            raw.close()
            db = Database(path)
            try:
                self.assertEqual(0, db.scalar("SELECT COUNT(*) FROM sudden_death"))
                self.assertEqual(2, db.scalar("PRAGMA user_version"))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
