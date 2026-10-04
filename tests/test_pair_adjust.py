import unittest

from domain.types import Scoring
from storage.errors import MicroMatchFullError, ValidationError
from storage.models import GameRecord, NewPlayer, TournamentSpec
from storage.pair_adjust import (Additions, PairGame, PairTarget, games_to_void, pair_units,
                                 plan_additions)
from tests.test_storage import LOSS, SCORING, WIN, open_store

UNIT = 1000   # points are compared in thousandths


class PlanAdditionsTests(unittest.TestCase):
    def test_wins_only(self):
        self.assertEqual(Additions(2, 1, 0), plan_additions(2 * UNIT, 1 * UNIT, SCORING, 12))

    def test_a_draw_gives_half_a_point_to_both(self):
        self.assertEqual(Additions(1, 0, 1), plan_additions(1500, 500, SCORING, 12))

    def test_nothing_to_add(self):
        self.assertEqual(Additions(0, 0, 0), plan_additions(0, 0, SCORING, 12))

    def test_halves_that_draws_cannot_make_are_impossible(self):
        self.assertIsNone(plan_additions(1000, 500, SCORING, 12))   # 1-0.5: one draw gives 0.5 to both

    def test_more_games_than_allowed_is_impossible(self):
        self.assertIsNone(plan_additions(3 * UNIT, 0, SCORING, 2))

    def test_uses_the_configured_points_not_fixed_ones(self):
        scoring = Scoring(win=3.0, draw=1.0, loss=0.0)
        self.assertEqual(Additions(1, 0, 1), plan_additions(4 * UNIT, 1 * UNIT, scoring, 12))


class PairUnitsAndVoidTests(unittest.TestCase):
    GAMES = [PairGame(1, 1), PairGame(2, 2), PairGame(3, 3)]     # A wins, B wins, draw

    def test_points_seen_from_both_players(self):
        self.assertEqual((1500, 1500), pair_units(self.GAMES, SCORING))

    def test_newest_games_are_voided_until_both_totals_fit(self):
        self.assertEqual([3], games_to_void(self.GAMES, (1000, 1500), SCORING))
        self.assertEqual([3, 2], games_to_void(self.GAMES, (1000, 0), SCORING))

    def test_nothing_is_voided_when_the_target_is_not_lower(self):
        self.assertEqual([], games_to_void(self.GAMES, (2000, 1500), SCORING))


class SetPairScoreTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store(self)
        self.tid = self.store.create_tournament(
            TournamentSpec("T", "team", team_size=2, games_per_pair=6))
        for team, letter in (("Alpha", "a"), ("Beta", "b")):
            self.store.add_team(self.tid, team)
            self.store.add_player(self.tid, team, NewPlayer(f"P {letter}", f"wbc{letter}"))
        self.admin = self.store.with_actor("referee1")

    def play(self, results):
        for first, second in results:
            self.store.record_game(self.tid, GameRecord("wbca", "wbcb", first, second))

    def set_score(self, points_a, points_b):
        return self.admin.set_pair_score(self.tid, PairTarget("wbca", "wbcb", points_a, points_b),
                                         SCORING)

    def score(self, game_id):
        return self.store.pair_score_for_game(game_id, SCORING)

    def test_missing_games_are_added(self):
        self.play([(WIN, LOSS)])
        score = self.score(self.set_score(3.0, 2.0))
        self.assertEqual([3.0, 2.0], score["points"])
        self.assertEqual(5, score["games"])
        self.assertEqual([3.0, 2.0], score["team_points"])

    def test_same_target_twice_changes_nothing(self):
        self.play([(WIN, LOSS)])
        self.set_score(3.0, 2.0)
        before = len(self.store.list_games(self.tid))
        self.set_score(3.0, 2.0)
        self.assertEqual(before, len(self.store.list_games(self.tid)))

    def test_half_points_are_one_draw(self):
        self.play([(WIN, LOSS)])
        score = self.score(self.set_score(1.5, 0.5))
        self.assertEqual([1.5, 0.5], score["points"])

    def test_a_lower_target_voids_the_newest_games(self):
        self.play([(WIN, LOSS), (WIN, LOSS), (LOSS, WIN)])
        score = self.score(self.set_score(2.0, 0.0))
        self.assertEqual([2.0, 0.0], score["points"])
        self.assertEqual(2, score["games"])
        self.assertEqual(3, len(self.store.list_games(self.tid, include_voided=True)))

    def test_players_may_sit_the_other_way_round(self):
        self.play([(WIN, LOSS)])
        self.store.record_game(self.tid, GameRecord("wbcb", "wbca", WIN, LOSS))
        score = self.score(self.set_score(1.0, 1.0))
        self.assertEqual([1.0, 1.0], score["points"])

    def test_target_that_needs_too_many_games_is_refused_and_nothing_changes(self):
        self.play([(WIN, LOSS)])
        with self.assertRaises(MicroMatchFullError):
            self.set_score(4.0, 3.0)                      # 7 games, only 6 allowed per pair
        self.assertEqual(1, len(self.store.list_games(self.tid, include_voided=True)))

    def test_unreachable_target_is_refused_and_nothing_changes(self):
        self.play([(WIN, LOSS)])
        with self.assertRaises(ValidationError):
            self.set_score(2.0, 0.5)
        self.assertEqual(1, len(self.store.list_games(self.tid, include_voided=True)))

    def test_pair_without_games_and_zero_target_is_refused(self):
        with self.assertRaises(ValidationError):
            self.set_score(0.0, 0.0)

    def test_changes_are_audited_under_the_admin(self):
        self.play([(WIN, LOSS)])
        self.set_score(2.0, 1.0)
        entries = self.store.audit_log(10)
        self.assertEqual("referee1", entries[0]["actor"])
        self.assertEqual("set_score", entries[0]["action"])
        self.assertTrue(all(e["actor"] == "referee1" for e in entries[:3]))
