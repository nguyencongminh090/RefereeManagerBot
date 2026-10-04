import io
import os
import tempfile
import threading
import unittest

from domain.ports import MatchRecord
from domain.types import GameResult, Scoring
from storage import Database, RepositoryOptions, SqliteTeamRepository, TournamentStore
from storage.models import EntrantPair, GameRecord, NewFixture, NewIndividual, NewPlayer, TournamentSpec
from storage.errors import (DuplicateError, DuplicateGameError, HasGamesError, MicroMatchFullError,
                            NotFoundError, PlayerInactiveError, SameEntrantError, UnknownPlayerError,
                            ValidationError)

SCORING = Scoring(win=1.0, draw=0.5, loss=0.0)   # test values only; real ones come from the config
WIN, LOSS, DRAW = GameResult.WIN, GameResult.LOSS, GameResult.DRAW


def open_store(case):
    store = TournamentStore(Database())
    case.addCleanup(store.db.close)
    return store


def make_team_tournament(store, name="Demo Team 2026", year=2026, games_per_pair=4):
    tid = store.create_tournament(TournamentSpec(name, "team", year=year, team_size=3, max_substitutes=1, games_per_pair=games_per_pair, nickname_prefix="wbc"))
    for team, letters in (("Alpha", "abcd"), ("Beta", "efgh")):
        store.add_team(tid, team, country="Demoland")
        for i, letter in enumerate(letters):
            store.add_player(tid, team, NewPlayer(f"Person {letter.upper()}", f"wbc{letter}", role="sub" if i == 3 else "main", is_captain=(i == 0)))
    return tid


class TeamFormatTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store(self)
        self.tid   = make_team_tournament(self.store)

    def test_roster_view_has_captain_and_players(self):
        rows = self.store.team_roster(self.tid)
        self.assertEqual(8, len(rows))
        alpha = [r for r in rows if r["team_name"] == "Alpha"]
        self.assertTrue(all(r["captain_nickname"] == "wbca" for r in alpha))
        self.assertEqual("wbca", alpha[0]["nickname"])          # captain listed first

    def test_validate_ready_and_problems(self):
        self.assertEqual([], self.store.validate(self.tid))
        self.store.set_active(self.tid, "wbcb", False)
        self.assertTrue(any("main player" in i for i in self.store.validate(self.tid)))

    def test_team_size_and_prefix_enforced(self):
        with self.assertRaises(ValidationError):
            self.store.add_player(self.tid, "Alpha", NewPlayer("Extra", "wbcx"))          # 4th main
        self.store.add_team(self.tid, "Gamma")
        with self.assertRaises(ValidationError):
            self.store.add_player(self.tid, "Gamma", NewPlayer("No Prefix", "someone"))   # wrong prefix

    def test_duplicate_nickname_case_insensitive(self):
        self.store.add_team(self.tid, "Gamma")
        with self.assertRaises(DuplicateError):
            self.store.add_player(self.tid, "Gamma", NewPlayer("Other", "WBCA"))

    def test_one_captain_per_team(self):
        self.store.set_captain(self.tid, "wbcb")
        captains = [p["nickname"] for p in self.store.list_players(self.tid, "Alpha") if p["is_captain"]]
        self.assertEqual(["wbcb"], captains)

    def test_record_game_and_standings(self):
        self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        self.store.record_game(self.tid, GameRecord("wbcb", "wbcf", DRAW, DRAW))
        self.store.record_game(self.tid, GameRecord("wbce", "wbca", WIN, LOSS))      # seat order does not matter
        table = self.store.standings(self.tid, SCORING)
        by_name = {r.name: r for r in table}
        self.assertEqual(1.5, by_name["Alpha"].points)
        self.assertEqual(1.5, by_name["Beta"].points)
        self.assertEqual((3, 3), (by_name["Alpha"].games, by_name["Beta"].games))
        self.assertEqual(["Alpha", "Beta"], [r.name for r in table])      # tie broken by name

    def test_record_game_rejections_store_nothing(self):
        with self.assertRaises(UnknownPlayerError):
            self.store.record_game(self.tid, GameRecord("wbca", "nobody", WIN, LOSS))
        with self.assertRaises(SameEntrantError):
            self.store.record_game(self.tid, GameRecord("wbca", "wbcb", WIN, LOSS))
        with self.assertRaises(ValidationError):
            self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, WIN))
        self.assertEqual([], self.store.list_games(self.tid, include_voided=True))

    def test_duplicate_game_uid(self):
        self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS, game_uid="g-1"))
        with self.assertRaises(DuplicateGameError):
            self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS, game_uid="g-1"))
        self.assertEqual(1, len(self.store.list_games(self.tid)))

    def test_micro_match_limit_and_force(self):
        for _ in range(4):
            self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        with self.assertRaises(MicroMatchFullError):
            self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS, force=True))
        self.store.record_game(self.tid, GameRecord("wbca", "wbcf", WIN, LOSS))      # another pair is unaffected

    def test_substitution_blocks_replaced_player(self):
        self.store.substitute(self.tid, "wbcc", "wbcd")
        with self.assertRaises(PlayerInactiveError):
            self.store.record_game(self.tid, GameRecord("wbcc", "wbce", WIN, LOSS))
        self.store.record_game(self.tid, GameRecord("wbcd", "wbce", WIN, LOSS))

    def test_void_and_delete_game(self):
        game_id = self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        self.store.void_game(game_id)
        self.assertEqual(0, self.store.standings(self.tid, SCORING)[0].games)
        self.store.void_game(game_id, voided=False)
        self.assertEqual(1, self.store.standings(self.tid, SCORING)[0].games)
        self.store.delete_game(game_id)
        with self.assertRaises(NotFoundError):
            self.store.delete_game(game_id)

    def test_delete_protection_and_cascade(self):
        self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        with self.assertRaises(HasGamesError):
            self.store.delete_entrant(self.tid, "Alpha")
        with self.assertRaises(HasGamesError):
            self.store.remove_player(self.tid, "wbca")
        with self.assertRaises(HasGamesError):
            self.store.move_player(self.tid, "wbca", "Beta")
        self.store.delete_entrant(self.tid, "Alpha", cascade_games=True)
        self.assertEqual([], self.store.list_games(self.tid))
        self.assertEqual(["Beta"], [e["name"] for e in self.store.list_entrants(self.tid)])

    def test_remove_player_cleans_orphan_person(self):
        before = self.store.db.scalar("SELECT COUNT(*) FROM persons")
        self.store.remove_player(self.tid, "wbcd")
        self.assertEqual(before - 1, self.store.db.scalar("SELECT COUNT(*) FROM persons"))

    def test_rename_and_edit(self):
        self.store.rename_entrant(self.tid, "Alpha", "Alpha Prime")
        self.store.rename_nickname(self.tid, "wbca", "wbcaa")
        self.store.update_person(self.tid, "wbcaa", contact="demo@example.invalid")
        self.assertEqual("demo@example.invalid", self.store.find_player(self.tid, "wbcaa")["contact"])
        self.assertEqual("Alpha Prime", self.store.find_player(self.tid, "wbcaa")["entrant_name"])

    def test_fixture_inference_and_cross_table(self):
        fixture = self.store.add_fixture(self.tid, NewFixture(1, "Alpha", "Beta"))
        self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        self.store.record_game(self.tid, GameRecord("wbce", "wbca", DRAW, DRAW))
        self.assertEqual(2, self.store.list_fixtures(self.tid)[0]["games"])
        self.assertEqual(fixture, self.store.db.scalar("SELECT fixture_id FROM games LIMIT 1"))
        cross = self.store.cross_table(self.tid, EntrantPair("Alpha", "Beta"), SCORING)
        cell = cross["rows"][0]["cells"][0]            # wbca vs wbce
        self.assertEqual((2, 1.5, 0.5), (cell["games"], cell["points"], cell["opponent_points"]))
        self.assertFalse(cell["complete"])

    def test_deleting_fixture_keeps_games(self):
        fixture = self.store.add_fixture(self.tid, NewFixture(1, "Alpha", "Beta"))
        self.store.record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        self.store.delete_fixture(fixture)
        self.assertEqual(1, len(self.store.list_games(self.tid)))

    def test_audit_log_written(self):
        self.store.with_actor("referee1").record_game(self.tid, GameRecord("wbca", "wbce", WIN, LOSS))
        entry = self.store.audit_log(1)[0]
        self.assertEqual(("referee1", "record", "game"), (entry["actor"], entry["action"], entry["entity"]))

    def test_csv_round_trip_is_idempotent(self):
        out = io.StringIO()
        self.assertEqual(8, self.store.export_roster_csv(self.tid, out))
        other = self.store.create_tournament(TournamentSpec("Demo Copy", "team", year=2027, team_size=3, max_substitutes=1))
        counts = self.store.import_roster_csv(other, io.StringIO(out.getvalue()))
        self.assertEqual({"teams_added": 2, "players_added": 8, "skipped": 0}, counts)
        again = self.store.import_roster_csv(other, io.StringIO(out.getvalue()))
        self.assertEqual({"teams_added": 0, "players_added": 0, "skipped": 8}, again)
        self.assertEqual([], self.store.validate(other))

    def test_csv_bad_row_aborts_everything(self):
        bad = ("team,country,full_name,nickname,role,captain,contact\n"
               "Zed,X,P One,wbcz1,main,yes,\n"
               "Zed,X,P Two,wbcz2,bogus,,\n")
        other = self.store.create_tournament(TournamentSpec("Demo Bad", "team", team_size=3))
        with self.assertRaises(ValidationError):
            self.store.import_roster_csv(other, io.StringIO(bad))
        self.assertEqual([], self.store.list_entrants(other))             # nothing half-imported


class IndividualFormatTests(unittest.TestCase):
    def setUp(self):
        self.store = open_store(self)
        self.tid = self.store.create_tournament(TournamentSpec("Demo Individual 2026", "individual", year=2026, games_per_pair=2))
        for nick, name in (("playerA", "Ann Demo"), ("playerB", "Ben Demo"), ("playerC", "Cy Demo")):
            self.store.add_individual(self.tid, NewIndividual(name, nick, country="Demoland"))

    def test_no_team_operations(self):
        with self.assertRaises(ValidationError):
            self.store.add_team(self.tid, "Nope")
        with self.assertRaises(ValidationError):
            self.store.team_roster(self.tid)
        self.assertEqual(3, len(self.store.individual_list(self.tid)))

    def test_standings_rank_players(self):
        self.store.record_game(self.tid, GameRecord("playerA", "playerB", WIN, LOSS))
        self.store.record_game(self.tid, GameRecord("playerA", "playerC", DRAW, DRAW))
        self.store.record_game(self.tid, GameRecord("playerB", "playerC", WIN, LOSS))
        table = self.store.standings(self.tid, SCORING)
        self.assertEqual(["playerA", "playerB", "playerC"], [r.name for r in table])
        self.assertEqual([1.5, 1.0, 0.5], [r.points for r in table])

    def test_games_per_pair_applies(self):
        self.store.record_game(self.tid, GameRecord("playerA", "playerB", WIN, LOSS))
        self.store.record_game(self.tid, GameRecord("playerB", "playerA", WIN, LOSS))
        with self.assertRaises(MicroMatchFullError):
            self.store.record_game(self.tid, GameRecord("playerA", "playerB", WIN, LOSS))

    def test_remove_player_removes_entrant(self):
        self.store.remove_player(self.tid, "playerC")
        self.assertEqual(2, len(self.store.list_entrants(self.tid)))

    def test_rename_nickname_renames_entrant(self):
        self.store.rename_nickname(self.tid, "playerA", "playerAA")
        self.assertIn("playerAA", [e["name"] for e in self.store.list_entrants(self.tid)])


class YearsAndFormatsTests(unittest.TestCase):
    def test_same_db_holds_both_formats_and_several_years(self):
        store = open_store(self)
        team_2026 = make_team_tournament(store, "Demo Team 2026", 2026)
        indiv_2027 = store.create_tournament(TournamentSpec("Demo Individual 2027", "individual", year=2027))
        person = store.db.scalar("SELECT person_id FROM participants WHERE nickname = 'wbca'")
        store.add_individual(indiv_2027, NewIndividual("Person A", "newnick", person_id=person))     # same person, new year, new nickname
        self.assertEqual([2027, 2026], [t["year"] for t in store.list_tournaments()])
        self.assertEqual(1, store.db.scalar("SELECT COUNT(DISTINCT person_id) FROM participants WHERE nickname IN ('wbca','newnick')"))
        store.record_game(team_2026, GameRecord("wbca", "wbce", WIN, LOSS))
        store.delete_tournament(indiv_2027)
        self.assertEqual(1, len(store.list_games(team_2026)))      # other tournament untouched
        self.assertIsNotNone(store.find_player(team_2026, "wbca")) # person kept: still used in 2026

    def test_same_nickname_in_different_tournaments(self):
        store = open_store(self)
        a = make_team_tournament(store, "Demo A", 2026)
        b = make_team_tournament(store, "Demo B", 2027)
        self.assertEqual(8, len(store.list_players(a)))
        self.assertEqual(8, len(store.list_players(b)))
        store.record_game(a, GameRecord("wbca", "wbce", WIN, LOSS))
        self.assertEqual([], store.list_games(b))

    def test_game_cannot_mix_tournaments(self):
        store = open_store(self)
        a = make_team_tournament(store, "Demo A", 2026)
        make_team_tournament(store, "Demo B", 2027)
        with self.assertRaises(UnknownPlayerError):
            store.record_game(a, GameRecord("wbca", "ghost", WIN, LOSS))


def make_repository(case, bot_name=None):
    """A repository over a two-team tournament with players a1 (Alpha) and b1 (Beta)."""
    store = open_store(case)
    tid = store.create_tournament(TournamentSpec("Demo Repo", "team", team_size=3))
    for team, nick in (("Alpha", "a1"), ("Beta", "b1")):
        store.add_team(tid, team)
        store.add_player(tid, team, NewPlayer(nick, nick))
    return store, tid, SqliteTeamRepository(store, tid, RepositoryOptions(SCORING, bot_name=bot_name))


class ActorAndRosterFileTests(unittest.TestCase):
    def test_with_actor_logs_under_that_actor_and_leaves_the_original_alone(self):
        store = open_store(self)
        store.create_tournament(TournamentSpec("Demo A", "individual"))
        store.with_actor("referee2").create_tournament(TournamentSpec("Demo B", "individual"))
        actors = [e["actor"] for e in store.audit_log(2)]
        self.assertEqual(["referee2", "system"], actors)

    def test_roster_import_from_file_path_and_bad_line_is_named(self):
        store = open_store(self)
        tid = store.create_tournament(TournamentSpec("Demo Files", "team", team_size=3))
        with tempfile.TemporaryDirectory() as folder:
            good, bad = os.path.join(folder, "good.csv"), os.path.join(folder, "bad.csv")
            with open(good, "w", encoding="utf-8") as handle:
                handle.write("team,full_name,nickname,captain\nAlpha,Person A,a1,yes\n")
            with open(bad, "w", encoding="utf-8") as handle:
                handle.write("team,full_name,nickname\nBeta,Person B,b1\nBeta,,b2\n")
            self.assertEqual({"teams_added": 1, "players_added": 1, "skipped": 0}, store.import_roster_csv(tid, good))
            with self.assertRaisesRegex(ValidationError, "CSV line 3"):
                store.import_roster_csv(tid, bad)
        self.assertEqual(["Alpha"], [e["name"] for e in store.list_entrants(tid)])


class RepositoryAdapterTests(unittest.TestCase):
    def test_interface_behaviour_and_observer(self):
        store, tid, repo = make_repository(self, bot_name="bot1")
        seen = []

        class Observer:
            def on_score_updated(self, snapshot):
                seen.append(snapshot)

        repo.subscribe(Observer())
        game_id = repo.record_match(MatchRecord("a1", WIN, "b1", LOSS, match_id="m-1"))
        self.assertEqual(["Alpha : Beta = 1 : 0"], seen)
        self.assertEqual("Alpha : Beta = 1 : 0", repo.snapshot())
        self.assertEqual({"games": 1, "limit": None, "players": ["a1", "b1"], "points": [1.0, 0.0],
                          "complete": False}, repo.pair_score(game_id))
        self.assertEqual(["a1", "b1"], [p["nickname"] for p in repo.roster()])
        self.assertEqual("bot1", store.list_games(tid)[0]["bot_name"])
        with self.assertRaises(DuplicateGameError):
            repo.record_match(MatchRecord("a1", WIN, "b1", LOSS, match_id="m-1"))
        self.assertEqual(1, len(seen))                       # nothing broadcast for the rejected duplicate

    def test_unsubscribed_observer_gets_nothing_and_record_bot_overrides_default(self):
        store, tid, repo = make_repository(self, bot_name="bot1")
        seen = []

        class Observer:
            def on_score_updated(self, snapshot):
                seen.append(snapshot)

        observer = Observer()
        repo.subscribe(observer)
        repo.unsubscribe(observer)
        repo.record_match(MatchRecord("a1", WIN, "b1", LOSS, bot_name="bot2", table_no=7))
        self.assertEqual([], seen)
        game = store.list_games(tid)[0]
        self.assertEqual(("bot2", 7), (game["bot_name"], game["table_no"]))

    def test_empty_tournament_snapshot(self):
        store = open_store(self)
        tid = store.create_tournament(TournamentSpec("Demo Empty", "team", team_size=3))
        self.assertEqual("No teams registered yet.", SqliteTeamRepository(store, tid, RepositoryOptions(SCORING)).snapshot())

    def test_unknown_player_does_not_break_state(self):
        store, tid, repo = make_repository(self)
        with self.assertRaises(UnknownPlayerError):
            repo.record_match(MatchRecord("a1", WIN, "ghost", LOSS))
        self.assertEqual(0, store.player_record(tid, "a1")["wins"])


class PersistenceTests(unittest.TestCase):
    def test_file_database_survives_reopen_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "nested", "t.db")
            store = TournamentStore(Database(path))
            tid = make_team_tournament(store)
            store.record_game(tid, GameRecord("wbca", "wbce", WIN, LOSS))
            store.db.backup(os.path.join(folder, "backup", "copy.db"))
            store.db.close()
            for p in (path, os.path.join(folder, "backup", "copy.db")):
                again = TournamentStore(Database(p))
                self.assertEqual(1, len(again.list_games("Demo Team 2026")))
                again.db.close()

    def test_concurrent_recording_is_consistent(self):
        store = open_store(self)
        tid = make_team_tournament(store, games_per_pair=None)
        errors = []

        def work():
            try:
                for _ in range(25):
                    store.record_game(tid, GameRecord("wbca", "wbce", WIN, LOSS))
            except Exception as exc:        # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual([], errors)
        self.assertEqual(100, len(store.list_games(tid)))

    def test_failed_transaction_rolls_back(self):
        store = open_store(self)
        tid = make_team_tournament(store)
        try:
            with store.db.transaction() as cx:
                cx.execute("DELETE FROM games")
                store.add_team(tid, "Gamma")
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertEqual(2, len(store.list_entrants(tid)))


if __name__ == "__main__":
    unittest.main()
