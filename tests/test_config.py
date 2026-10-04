import os
import tempfile
import unittest
from pathlib import Path

from config.settings import ConfigError, ConfigLoader, parse_env_file

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "config.example.toml"


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.text = EXAMPLE.read_text(encoding="utf-8")

    def write(self, text=None, name="config.toml"):
        path = os.path.join(self.folder.name, name)
        Path(path).write_text(self.text if text is None else text, encoding="utf-8")
        return path

    def load(self, **kwargs):
        kwargs.setdefault("env_file", os.path.join(self.folder.name, "no.env"))
        kwargs.setdefault("environ", {})
        return ConfigLoader.load(self.write(), **kwargs)

    def problems(self, **kwargs):
        with self.assertRaises(ConfigError) as ctx:
            self.load(**kwargs)
        return ctx.exception.problems

    def test_example_loads_and_is_typed(self):
        s = self.load()
        self.assertEqual(12, s.tournament.total_matches)
        self.assertEqual(1.0, s.tournament.scoring.games.win)
        self.assertEqual(("score_difference", "head_to_head", "sudden_death"), s.tournament.scoring.tiebreaks)
        self.assertTrue(s.playok.patterns["draw"].match("draw"))
        self.assertEqual("dudai", s.playok.patterns["invitation"].match(
            "dudai [1093] invites you to table #104 (1m); accept?").group("user"))
        self.assertEqual("Europe/Warsaw", str(s.tournament.tzinfo))
        self.assertEqual("Replace with the rules reminder in English.", s.texts.rules)

    def test_stats_table_is_typed(self):
        s = self.load()
        self.assertEqual(("https://www.playok.com/en/stat.phtml", "gm", "UTC", 5.0),
                         (s.stats.url, s.stats.game_code, s.stats.timezone, s.stats.timeout_seconds))
        self.assertEqual("UTC", str(s.stats.tzinfo))

    def test_stats_keys_are_validated(self):
        self.text = (self.text.replace('timezone        = "UTC"', 'timezone        = "Mars/Base"')
                              .replace("\ntimeout_seconds = 5", "\ntimeout_seconds = 0"))
        found = self.problems()
        self.assertTrue(any("stats.timezone" in p for p in found), found)
        self.assertTrue(any("stats.timeout_seconds" in p for p in found), found)

    def test_round_start_is_optional_and_read_in_the_tournament_zone(self):
        self.assertIsNone(self.load().tournament.round_start)
        self.text = self.text.replace('year            = 2027', 'round_start    = "2026-10-04 18:00"\nyear            = 2027')
        start = self.load().tournament.round_start
        self.assertEqual("2026-10-04T18:00:00+02:00", start.isoformat())

    def test_a_malformed_round_start_stops_start_up(self):
        self.text = self.text.replace('year            = 2027', 'round_start    = "tonight"\nyear            = 2027')
        self.assertTrue(any("tournament.round_start" in p for p in self.problems()))

    def test_every_language_has_the_sync_texts(self):
        s = self.load()
        self.assertIn("{reason}", s.texts.sync_failed)
        self.assertTrue(s.texts.sync_usage)

    def test_hardening_keys_are_typed(self):
        s = self.load()
        self.assertEqual((1048576, 64, 5.0, 5.0, 5, 60.0),
                         (s.server.max_packet_bytes, s.server.max_clients, s.server.send_timeout_seconds,
                          s.server.auth_timeout_seconds, s.server.auth_max_failures, s.server.auth_lockout_seconds))
        self.assertEqual("data/outbox.jsonl", s.client.outbox_path)

    def test_empty_outbox_path_means_memory_only(self):
        self.text = self.text.replace('outbox_path = "data/outbox.jsonl"', 'outbox_path = ""')
        self.assertEqual("", self.load().client.outbox_path)

    def test_hardening_keys_are_validated(self):
        self.text = (self.text.replace("max_packet_bytes = 1048576", "max_packet_bytes = 0")
                              .replace("max_clients = 64", 'max_clients = "many"')
                              .replace("auth_timeout_seconds = 5", "auth_timeout_seconds = 0")
                              .replace("auth_max_failures = 5", "auth_max_failures = 0"))
        found = self.problems()
        for key in ("max_packet_bytes", "max_clients", "auth_timeout_seconds", "auth_max_failures"):
            self.assertTrue(any(f"server.{key}" in p for p in found), (key, found))

    def test_scoring_lives_in_core_types(self):
        from domain.types import Scoring
        self.assertIs(Scoring, type(self.load().tournament.scoring.games))

    def test_all_problems_reported_at_once(self):
        self.text = (self.text.replace("port              = 9000", 'port = "x"')
                              .replace("total_matches   = 12", "total_matches = 0")
                              .replace('join_mode             = "auto"', 'join_mode = "maybe"'))
        found = self.problems()
        self.assertEqual(3, len(found), found)
        self.assertTrue(any("server.port" in p for p in found))
        self.assertTrue(any("tournament.total_matches" in p for p in found))
        self.assertTrue(any("client.join_mode" in p and "auto" in p for p in found))

    def test_missing_key_and_missing_table(self):
        self.text = self.text.replace('name            = "Demo Tournament"\n', "")
        self.assertIn("missing key 'tournament.name'", self.problems())
        self.text = EXAMPLE.read_text(encoding="utf-8").replace("[commands]", "[commandz]")
        found = self.problems()
        self.assertTrue(any("[commands] is missing" in p for p in found))
        self.assertTrue(any("commandz" in p and "unknown" in p for p in found))

    def test_typo_gets_suggestion(self):
        self.text = self.text.replace("heartbeat_seconds", "heartbeat_secnds")
        found = self.problems()
        self.assertTrue(any("unknown key 'server.heartbeat_secnds'" in p and "did you mean 'heartbeat_seconds'" in p
                            for p in found), found)

    def test_bad_regex_and_missing_group(self):
        self.text = self.text.replace("win_p1     = '^player #1 wins$'", "win_p1     = '^player (#1 wins$'")
        self.text = self.text.replace("(?P<seat>[12])", "([12])")
        found = self.problems()
        self.assertTrue(any("playok.patterns.win_p1" in p and "regular expression" in p for p in found))
        self.assertTrue(any("playok.patterns.timeout" in p and "seat" in p for p in found))

    def english_block(self):
        start = self.text.index("[messages.en]")
        return self.text[start:]

    def with_language(self, name, edit=lambda block: block):
        block = edit(self.english_block().replace("[messages.en]", f"[messages.{name}]"))
        self.text = self.text.rstrip("\n") + "\n\n" + block

    def test_command_limits_and_chat_texts_are_typed(self):
        s = self.load()
        self.assertEqual((10.0, 15.0), (s.commands.cheer_cooldown_seconds, s.commands.break_max_minutes))
        self.assertGreaterEqual(len(s.texts.cheers), 3)
        self.assertTrue(all("{name}" in c for c in s.texts.cheers))
        self.assertIn("{max_minutes}", s.texts.break_usage)
        self.assertEqual("alice : bob = 3-2", s.texts.result_pair.format(p1="alice", p2="bob", s1="3", s2="2"))

    def test_every_language_has_its_own_texts_and_aliases_name_them(self):
        self.with_language("hu", lambda b: b.replace("Replace with the rules reminder in English.", "Szabalyok."))
        self.text = self.text.replace('eng = "en"', 'eng = "en"\nhun = "hu"')
        s = self.load()
        self.assertEqual(("en", "hu"), s.messages.language_names())
        self.assertEqual("Szabalyok.", s.messages.texts("hu").rules)
        self.assertEqual("en", s.messages.resolve(" ENG "))
        self.assertEqual("hu", s.messages.resolve("hun"))
        self.assertEqual("hu", s.messages.resolve("HU"))
        self.assertIsNone(s.messages.resolve("klingon"))
        self.assertEqual(s.messages.texts("en"), s.texts)             # the tournament language is en

    def test_a_language_with_a_missing_key_stops_start_up(self):
        self.with_language("hu", lambda b: b.replace('bye              = "bye"\n', ""))
        self.assertTrue(any("messages.hu.bye" in p for p in self.problems()))

    def test_placeholders_are_checked_in_every_language(self):
        self.with_language("hu", lambda b: b.replace("{reason}", "{why}").replace("Go, {name}!", "Go!"))
        found = self.problems()
        self.assertTrue(any("messages.hu.set_failed" in p for p in found), found)
        self.assertTrue(any("messages.hu.cheers" in p for p in found), found)

    def test_the_tournament_language_needs_a_table_and_aliases_need_a_target(self):
        self.text = self.text.replace('language        = "en"', 'language = "fr"').replace('eng = "en"', 'eng = "en"\nxx = "zz"')
        found = self.problems()
        self.assertTrue(any("tournament.language" in p and "fr" in p for p in found), found)
        self.assertTrue(any("messages.aliases.xx" in p for p in found), found)

    def test_broken_placeholder_in_a_text(self):
        self.text = self.text.replace("{resume_time}", "{resume_at}")
        self.assertTrue(any("messages.en.break_text" in p for p in self.problems()))

    def test_command_limits_are_validated(self):
        self.text = (self.text.replace("cheer_cooldown_seconds = 10", "cheer_cooldown_seconds = -1")
                              .replace("break_max_minutes = 15", "break_max_minutes = 0"))
        found = self.problems()
        for key in ("cheer_cooldown_seconds", "break_max_minutes"):
            self.assertTrue(any(f"commands.{key}" in p for p in found), (key, found))

    def test_timezone_and_break_rules(self):
        self.text = self.text.replace('"Europe/Warsaw"', '"Mars/Olympus"').replace("break_after     = 0", "break_after = 10").replace("break_minutes   = 5", "break_minutes = 0")
        found = self.problems()
        self.assertTrue(any("time zone" in p for p in found))
        self.assertTrue(any("break_minutes" in p for p in found))

    def test_individual_format_does_not_need_team_keys(self):
        self.text = self.text.replace('format          = "team"', 'format = "individual"')
        self.text = self.text.replace("team_size       = 3 ", "# ").replace("max_substitutes = 1 ", "# ")
        s = self.load()
        self.assertIsNone(s.tournament.team_size)
        self.text = EXAMPLE.read_text(encoding="utf-8").replace("team_size       = 3 ", "# ")
        self.assertIn("missing key 'tournament.team_size'", self.problems())

    def test_secrets_by_role_and_env_override(self):
        env_file = os.path.join(self.folder.name, ".env")
        Path(env_file).write_text("# c\nPLAYOK_USER=bot1\nexport PLAYOK_PASS='p w'\nBOT_TOKEN=\"tok\"  \n", encoding="utf-8")
        s = self.load(role="client", env_file=env_file)
        self.assertEqual(("bot1", "p w", "tok"), (s.secrets.playok_user, s.secrets.playok_pass, s.secrets.bot_token))
        self.assertNotIn("tok", repr(s.secrets))
        s = self.load(role="server", env_file=env_file, environ={"BOT_TOKEN": "from-env"})
        self.assertEqual("from-env", s.secrets.bot_token)
        s = self.load(role="server", env_file=env_file, environ={"BOT_TOKEN": ""})
        self.assertEqual("tok", s.secrets.bot_token)          # an empty variable does not hide the .env value
        found = self.problems(role="client")
        self.assertEqual(3, len([p for p in found if p.startswith("secret ")]))
        self.assertEqual(1, len([p for p in self.problems(role="server")]))
        self.load()      # no role: secrets optional

    def test_path_resolution_and_errors(self):
        self.assertEqual("x.toml", ConfigLoader.resolve_path("x.toml", {"REFEREE_CONFIG": "y.toml"}))
        self.assertEqual("y.toml", ConfigLoader.resolve_path(None, {"REFEREE_CONFIG": "y.toml"}))
        self.assertEqual("config/config.toml", ConfigLoader.resolve_path(None, {}))
        with self.assertRaises(ConfigError) as ctx:
            ConfigLoader.load(os.path.join(self.folder.name, "missing.toml"), environ={})
        self.assertIn("not found", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            ConfigLoader.load(self.write("this is = = not toml"), environ={})
        self.assertIn("not valid TOML", str(ctx.exception))

    def test_helpers(self):
        s = self.load()
        self.assertTrue(s.tournament.is_admin("WBCEXAMPLEADMIN"))
        self.assertFalse(s.tournament.is_admin("someone"))
        self.assertTrue(s.commands.is_admin_only("!LEAVE"))
        self.assertFalse(s.commands.is_admin_only("!score"))

    def test_env_file_syntax_error(self):
        path = os.path.join(self.folder.name, "bad.env")
        Path(path).write_text("JUSTTEXT\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            parse_env_file(path)


if __name__ == "__main__":
    unittest.main()
