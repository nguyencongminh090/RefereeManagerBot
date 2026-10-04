import contextlib
import io
import os
import tempfile
import unittest

from storage import Database, TournamentStore
from tools.admin_cli import COMMANDS
from tools.admin_db import build_parser, main

SCORING = ("--win", "1", "--draw", "0.5", "--loss", "0")


class AdminCliTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.db = os.path.join(self.folder, "t.db")

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--db", self.db, "--actor", "tester", *argv])
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv):
        code, out, err = self.run_cli(*argv)
        self.assertEqual((0, ""), (code, err), f"{argv}: {err}")
        return out

    def test_every_registered_command_runs(self):
        self.build_team_event()
        steps = [
            ("init",), ("tournament", "list"), ("tournament", "set", "T", "year", "2027"),
            ("team", "list", "T"), ("team", "rename", "T", "Beta", "Bravo"), ("team", "country", "T", "Bravo", "X"),
            ("player", "list", "T"), ("player", "edit", "T", "wbcb", "--country", "X"),
            ("player", "rename", "T", "wbcb", "wbcbee"), ("player", "captain", "T", "wbcsub"),
            ("player", "role", "T", "wbcsub", "sub"), ("player", "deactivate", "T", "wbcsub"),
            ("player", "activate", "T", "wbcsub"), ("player", "move", "T", "wbcsub", "Bravo"),
            ("player", "move", "T", "wbcsub", "Alpha"), ("player", "substitute", "T", "wbca", "wbcsub"), ("fixture", "list", "T"),
            ("fixture", "set", "1", "--status", "done"), ("game", "list", "T"), ("game", "unvoid", "1"),
            ("game", "void", "1"), ("sudden-death", "add", "T", "Alpha", "Bravo"),
            ("sudden-death", "list", "T"), ("sudden-death", "delete", "T", "Alpha", "Bravo"),
            ("standings", "T", *SCORING), ("cross", "T", "Alpha", "Bravo", *SCORING), ("roster", "T"),
            ("validate", "T"), ("export", "T"), ("audit",), ("game", "delete", "1"),
            ("fixture", "delete", "1"), ("player", "remove", "T", "wbcsub"), ("team", "delete", "T", "Bravo"),
            ("tournament", "delete", "T", "--yes"),
        ]
        ran = set()
        for argv in steps:
            code = self.run_cli(*argv)[0]
            self.assertEqual(1 if argv[0] == "validate" else 0, code, argv)
            ran.add(argv[:2] if argv[:2] in COMMANDS else (argv[0], None))
        self.ok("tournament", "add", "I", "--format", "individual")
        self.ok("individual", "add", "I", "Person A", "wbca")
        self.ok("individual", "list", "I")
        ran |= {("individual", "add"), ("individual", "list"), ("team", "add"), ("player", "add"),
                ("tournament", "add"), ("fixture", "add"), ("game", "add"), ("import", None), ("backup", None)}
        self.assertEqual(set(), set(COMMANDS) - ran)

    def build_team_event(self):
        self.ok("tournament", "add", "T", "--format", "team", "--team-size", "1", "--max-subs", "1",
                "--games-per-pair", "2", "--nick-prefix", "wbc", "--year", "2026")
        for team, nick in (("Alpha", "wbca"), ("Beta", "wbcb")):
            self.ok("team", "add", "T", team, "--country", "Demoland")
            self.ok("player", "add", "T", team, f"Person {nick}", nick, "--captain")
        self.ok("player", "add", "T", "Alpha", "Sub A", "wbcsub", "--sub")
        self.ok("fixture", "add", "T", "1", "Alpha", "Beta", "--when", "2026-10-05 10:00")
        self.ok("game", "add", "T", "wbca", "wbcb", "win")
        self.ok("game", "void", "1")

    def test_tournament_commands(self):
        self.ok("tournament", "add", "T", "--format", "individual", "--year", "2026")
        self.assertIn("2026", self.ok("tournament", "list"))
        self.ok("tournament", "set", "T", "games_per_pair", "4")
        self.assertIn("4", self.ok("tournament", "list").splitlines()[2])
        self.ok("tournament", "set", "T", "year", "none")
        code, _, err = self.run_cli("tournament", "delete", "T")
        self.assertEqual(1, code)
        self.assertIn("repeat with --yes", err)
        self.ok("tournament", "delete", "T", "--yes")
        self.assertEqual("(none)\n", self.ok("tournament", "list"))

    def test_team_and_player_commands(self):
        self.build_team_event()
        self.ok("team", "rename", "T", "Beta", "Bravo")
        self.ok("team", "country", "T", "Bravo", "Exampleland")
        self.assertIn("Exampleland", self.ok("team", "list", "T"))
        self.ok("player", "edit", "T", "wbcb", "--full-name", "Renamed Person")
        self.ok("player", "rename", "T", "wbcb", "wbcbee")
        self.ok("player", "role", "T", "wbcsub", "sub")
        self.ok("player", "activate", "T", "wbca")
        self.ok("player", "deactivate", "T", "wbca")
        self.ok("player", "captain", "T", "wbcsub")
        self.ok("player", "move", "T", "wbcsub", "Bravo")
        listing = self.ok("player", "list", "T", "--team", "Alpha")
        self.assertNotIn("wbcsub", listing)             # moved to Bravo
        self.assertIn("wbcsub", self.ok("player", "list", "T", "--team", "Bravo"))
        self.ok("player", "remove", "T", "wbcsub", "--cascade-games")
        self.ok("team", "delete", "T", "Bravo", "--cascade-games")
        self.assertNotIn("Bravo", self.ok("team", "list", "T"))

    def test_substitute_command(self):
        self.build_team_event()
        self.ok("player", "substitute", "T", "wbca", "wbcsub")
        db = Database(self.db)
        self.addCleanup(db.close)
        players = {p["nickname"]: p for p in TournamentStore(db).list_players("T")}
        self.assertEqual((0, 1), (players["wbca"]["active"], players["wbcsub"]["active"]))
        self.assertEqual("main", players["wbcsub"]["role"])

    def test_individual_commands(self):
        self.ok("tournament", "add", "I", "--format", "individual")
        self.ok("individual", "add", "I", "Person A", "wbca", "--country", "Demoland", "--contact", "a@example.invalid")
        listing = self.ok("individual", "list", "I")
        self.assertIn("Person A", listing)
        self.assertIn("a@example.invalid", self.ok("roster", "I"))

    def test_fixture_commands(self):
        self.build_team_event()
        self.ok("fixture", "set", "1", "--status", "done", "--round", "2")
        listing = self.ok("fixture", "list", "T", "--round", "2")
        self.assertIn("done", listing)
        self.ok("fixture", "delete", "1")
        self.assertEqual("(none)\n", self.ok("fixture", "list", "T"))

    def test_game_commands_and_reports(self):
        self.build_team_event()
        self.assertIn("1", self.ok("game", "list", "T", "--all"))
        self.assertEqual("(none)\n", self.ok("game", "list", "T"))          # voided games are hidden
        self.ok("game", "unvoid", "1")
        self.assertIn("wbca", self.ok("game", "list", "T"))
        self.assertIn("Alpha", self.ok("standings", "T", *SCORING))
        cross = self.ok("cross", "T", "Alpha", "Beta", *SCORING)
        self.assertIn("1-0 (1)", cross)
        self.assertIn("* = micro-match complete", cross)
        self.assertIn("captain_nickname", self.ok("roster", "T"))
        code, out, _ = self.run_cli("validate", "T")
        self.assertEqual(0, code)
        self.assertIn("ready", out)

    def test_validate_exit_code_one_when_problems(self):
        self.ok("tournament", "add", "T", "--format", "team", "--team-size", "2")
        self.ok("team", "add", "T", "Alpha")
        code, out, _ = self.run_cli("validate", "T")
        self.assertEqual(1, code)
        self.assertIn("expected 2", out)

    def test_sudden_death_commands(self):
        self.build_team_event()
        self.ok("sudden-death", "add", "T", "Beta", "Alpha")
        self.assertIn("Beta", self.ok("sudden-death", "list", "T"))
        self.ok("sudden-death", "delete", "T", "Beta", "Alpha")
        self.assertEqual("(none)\n", self.ok("sudden-death", "list", "T"))

    def test_import_export_audit_backup(self):
        self.build_team_event()
        target = os.path.join(self.folder, "roster.csv")
        self.assertIn("rows written", self.ok("export", "T", target))
        self.assertIn("wbca", self.ok("export", "T"))                      # no file: written to stdout
        self.ok("tournament", "add", "Copy", "--format", "team", "--team-size", "1", "--max-subs", "1")
        self.assertIn("'teams_added': 2", self.ok("import", "Copy", target))
        self.assertIn("tester", self.ok("audit", "--limit", "5"))
        backup = os.path.join(self.folder, "bak", "copy.db")
        self.assertIn("backup written", self.ok("backup", backup))
        self.assertTrue(os.path.exists(backup))

    def test_storage_errors_exit_one_with_message(self):
        code, _, err = self.run_cli("team", "list", "missing")
        self.assertEqual(1, code)
        self.assertIn("error:", err)

    def test_parser_knows_every_registered_command(self):
        parser = build_parser()
        for group, name in COMMANDS:
            with self.subTest(group=group, name=name):
                self.assertIn(group, parser.format_help())


if __name__ == "__main__":
    unittest.main()
