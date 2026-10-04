"""Runs the real SeleniumDriver in headless Firefox against the fake PlayOK site.

Skipped when selenium is missing (use `.venv/bin/python`) or Firefox cannot start.
"""
import dataclasses
import time
import unittest

from referee.driver import SeleniumDriver
from tests.fake_playok.server import FakePlayokServer
from tests.fake_playok.world import FakeWorld
from tests.test_page_parser import SETTINGS

try:
    from referee.browser import create_firefox
    import selenium  # noqa: F401
except ImportError:
    create_firefox = None

BOT = "wbcreferee"
WAIT_SEC = 5.0
POLL_SEC = 0.1


def wait_until(condition, what: str, timeout: float = WAIT_SEC):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(POLL_SEC)
    raise AssertionError(f"timed out waiting for {what}")


@unittest.skipIf(create_firefox is None, "selenium is not installed")
class BrowserDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.browser = create_firefox(headless=True)
        except Exception as error:
            raise unittest.SkipTest(f"Firefox cannot start: {error}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()

    def setUp(self):
        self.world = FakeWorld(bot_name=BOT)
        self.world.add_lobby_table(104, time="1m+1s", players=["alice", "bob"])
        self.server = FakePlayokServer(self.world)
        self.server.start()
        self.addCleanup(self.server.stop)
        playok = dataclasses.replace(SETTINGS.playok, site_url=self.server.site_url)
        self.driver = SeleniumDriver(playok, self.browser, pause_seconds=0.3)
        self.driver.open_site()
        self.driver.goto_lobby()

    def test_lobby_lists_the_table(self):
        tables = wait_until(self.driver.lobby_tables, "the lobby list")
        self.assertEqual([104], [t.number for t in tables])

    def test_joins_a_table_and_reads_seats_and_number(self):
        self.assertTrue(self.driver.join_table(104))
        wait_until(lambda: self.world.bot_table == 104, "the bot at table 104")
        self.assertEqual(104, wait_until(self.driver.get_table_number, "the table title"))
        self.assertEqual(("alice", "bob"), self.driver.get_players_name())

    def test_reads_new_chat_lines_once(self):
        self.driver.join_table(104)
        wait_until(lambda: self.world.bot_table == 104, "the bot at table 104")
        self.world.say(104, "alice", "gl hf")
        self.world.system(104, "player #1 wins")
        lines = []
        expected = [("alice", "gl hf"), ("+", "player #1 wins")]
        wait_until(lambda: lines.extend(self.driver.receive_messages()) or set(expected) <= set(lines),
                   "both chat lines")
        self.assertEqual(1, lines.count(expected[0]))
        self.assertEqual([], self.driver.receive_messages())

    def test_sends_a_chat_message(self):
        self.driver.join_table(104)
        wait_until(lambda: self.world.bot_table == 104, "the bot at table 104")
        wait_until(self.driver.get_table_number, "the table panel")
        self.driver.send_message("hello table")
        wait_until(lambda: f"{BOT}: hello table" in self.world.chat(104), "the message in the chat")

    def test_accepts_an_invitation(self):
        self.world.invite("alice", 1093, 104, "1m")
        self.assertEqual("alice", wait_until(self.driver.check_for_invitation, "the invitation"))
        self.assertTrue(self.driver.accept_invitation())
        wait_until(lambda: self.world.bot_table == 104, "the bot at table 104")

    def test_leaves_the_table(self):
        self.driver.join_table(104)
        wait_until(self.driver.get_table_number, "the table panel")
        self.driver.leave_table()
        wait_until(lambda: self.world.bot_table is None, "the bot back in the lobby")


if __name__ == "__main__":
    unittest.main()
