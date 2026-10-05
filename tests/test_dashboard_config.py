import os
import tempfile
import unittest
from pathlib import Path

from config.settings import ConfigError, ConfigLoader

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "config.example.toml"


class DashboardConfigTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.text = EXAMPLE.read_text(encoding="utf-8")

    def load(self, role="server", environ=None):
        path = os.path.join(self.folder.name, "config.toml")
        Path(path).write_text(self.text, encoding="utf-8")
        env = {"BOT_TOKEN": "t"} if environ is None else environ
        return ConfigLoader.load(path, env_file=os.path.join(self.folder.name, "no.env"),
                                 role=role, environ=env)

    def problems(self, **kwargs):
        with self.assertRaises(ConfigError) as ctx:
            self.load(**kwargs)
        return ctx.exception.problems

    def test_example_is_disabled_and_binds_loopback(self):
        dashboard = self.load().dashboard
        self.assertFalse(dashboard.enabled)
        self.assertEqual("127.0.0.1", dashboard.host)

    def test_editing_is_off_by_default_and_can_be_switched_on(self):
        self.assertFalse(self.load().dashboard.allow_edit)
        self.text = self.text.replace("allow_edit      = false", "allow_edit      = true")
        self.assertTrue(self.load().dashboard.allow_edit)

    def test_enabled_without_token_is_refused_for_the_server(self):
        self.text = self.text.replace("enabled         = false", "enabled         = true")
        self.assertTrue(any("DASHBOARD_TOKEN" in p for p in self.problems()))

    def test_enabled_with_token_loads_it(self):
        self.text = self.text.replace("enabled         = false", "enabled         = true")
        settings = self.load(environ={"BOT_TOKEN": "t", "DASHBOARD_TOKEN": "secret"})
        self.assertEqual("secret", settings.secrets.dashboard_token)

    def test_enabled_does_not_need_a_token_for_the_client(self):
        self.text = self.text.replace("enabled         = false", "enabled         = true")
        self.assertTrue(self.load(role="client", environ={
            "PLAYOK_USER": "u", "PLAYOK_PASS": "p", "BOT_TOKEN": "t"}).dashboard.enabled)

    def test_port_and_refresh_must_be_positive(self):
        self.text = self.text.replace("port            = 8080", "port            = 0")
        self.text = self.text.replace("refresh_seconds = 5 ", "refresh_seconds = 0 ")
        found = " ".join(self.problems())
        self.assertIn("dashboard.port", found)
        self.assertIn("dashboard.refresh_seconds", found)

    def test_unknown_key_is_reported(self):
        self.text = self.text.replace("[dashboard]\n", "[dashboard]\nenabeld = true\n")
        self.assertTrue(any("enabeld" in p for p in self.problems()))


if __name__ == "__main__":
    unittest.main()
