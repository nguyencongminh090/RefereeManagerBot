import os
import re
import tempfile
import unittest
from pathlib import Path

from config.settings import ConfigError, ConfigLoader

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "config.example.toml"


class PublicConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.text = EXAMPLE.read_text(encoding="utf-8")

    def load(self, role="server"):
        path = os.path.join(self.folder.name, "config.toml")
        Path(path).write_text(self.text, encoding="utf-8")
        return ConfigLoader.load(path, env_file=os.path.join(self.folder.name, "no.env"),
                                 role=role, environ={"BOT_TOKEN": "t", "DASHBOARD_TOKEN": "d"})

    def problems(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load()
        return " ".join(ctx.exception.problems)

    def set_public(self, key, value):
        """Rewrites one `key = value` line of the [public] table, keeping its comment."""
        head, _, rest = self.text.partition("[public]\n")
        table, sep, tail = rest.partition("\n[")
        table = re.sub(rf"^{key}(\s*)=\s*\S+", rf"{key}\1= {value}", table, flags=re.M)
        self.text = head + "[public]\n" + table + sep + tail

    def enable(self):
        self.set_public("enabled", "true")

    def test_example_is_disabled_and_binds_loopback(self):
        public = self.load().public
        self.assertFalse(public.enabled)
        self.assertEqual("127.0.0.1", public.host)

    def test_enabling_it_needs_no_secret(self):
        self.enable()
        self.assertTrue(self.load().public.enabled)

    def test_port_refresh_and_recent_games_must_be_positive(self):
        for key in ("port", "refresh_seconds", "recent_games"):
            self.set_public(key, "0")
        found = self.problems()
        for key in ("public.port", "public.refresh_seconds", "public.recent_games"):
            self.assertIn(key, found)

    def test_unknown_key_is_reported(self):
        self.text = self.text.replace("[public]\n", "[public]\nenabeld = true\n")
        self.assertIn("enabeld", self.problems())

    def test_both_pages_on_one_address_are_refused(self):
        self.enable()
        self.text = self.text.replace("[dashboard]\nenabled         = false",
                                      "[dashboard]\nenabled         = true")
        self.set_public("port", "8080")
        self.assertIn("public.port", self.problems())
