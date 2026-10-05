import unittest

from domain.ports       import BotStatus
from serverapp.bot_status import BotStatusProvider
from serverapp.claims   import ClaimRegistry
from serverapp.sessions import SessionRegistry


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class BotStatusTypeTests(unittest.TestCase):
    def test_bot_status_is_immutable(self):
        status = BotStatus("bot-1", "10.0.0.5:5000", (1, 3), 2.5)
        with self.assertRaises(Exception):
            status.name = "x"


class BotStatusProviderTests(unittest.TestCase):
    def setUp(self):
        self.clock    = FakeClock()
        self.sessions = SessionRegistry(self.clock, 45, 5)
        self.claims   = ClaimRegistry()
        self.provider = BotStatusProvider(self.sessions, self.claims)

    def test_lists_only_authenticated_bots_with_their_tables(self):
        a, b = ("1.1.1.1", 1), ("2.2.2.2", 2)
        for addr in (a, b):
            self.sessions.connect(addr)
        self.sessions.authenticate(a, "bot-a")          # b never authenticates
        self.claims.claim(7, a)
        self.claims.claim(2, a)
        self.clock.now = 4.0
        self.assertEqual([BotStatus("bot-a", "1.1.1.1:1", (2, 7), 4.0)], self.provider.bots())

    def test_empty_when_nobody_is_connected(self):
        self.assertEqual([], self.provider.bots())

    def test_bots_are_ordered_by_name(self):
        for i, name in enumerate(("zed", "amy")):
            addr = ("1.1.1.1", i)
            self.sessions.connect(addr)
            self.sessions.authenticate(addr, name)
        self.assertEqual(["amy", "zed"], [b.name for b in self.provider.bots()])

    def test_released_tables_disappear(self):
        a = ("1.1.1.1", 1)
        self.sessions.connect(a)
        self.sessions.authenticate(a, "bot-a")
        self.claims.claim(1, a)
        self.claims.release_all_for(a)
        self.assertEqual((), self.provider.bots()[0].tables)


if __name__ == "__main__":
    unittest.main()
