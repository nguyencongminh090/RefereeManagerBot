import json
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from referee.page_parser import PageParser
from tests.fake_playok.pages import render_fragment, render_page
from tests.fake_playok.server import FakePlayokServer
from tests.fake_playok.world import FakeBoard, FakeWorld
from tests.test_page_parser import SETTINGS

PARSER = PageParser(SETTINGS.playok)
BOT = "wbcreferee"


def parsed(world: FakeWorld):
    return PARSER.parse(render_page(world))


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.world = FakeWorld(bot_name=BOT)
        self.world.add_lobby_table(104, time="1m", players=["alice"])

    def test_bot_is_in_the_lobby_until_it_joins_a_table(self):
        self.assertIsNone(self.world.bot_table)

    def test_joining_a_table_seats_the_bot_as_observer_and_posts_a_join_line(self):
        self.assertTrue(self.world.bot_join(104))
        self.assertEqual(104, self.world.bot_table)
        self.assertIn(f"+ {BOT} [1200] joins", self.world.chat(104))

    def test_joining_an_unknown_table_fails(self):
        self.assertFalse(self.world.bot_join(999))
        self.assertIsNone(self.world.bot_table)

    def test_accepting_an_invitation_joins_the_invited_table(self):
        self.world.invite("alice", 1093, 104, "1m")
        self.assertTrue(self.world.bot_accept_invitation())
        self.assertEqual(104, self.world.bot_table)
        self.assertIsNone(self.world.invitation)

    def test_accepting_without_invitation_fails(self):
        self.assertFalse(self.world.bot_accept_invitation())

    def test_say_and_system_lines_keep_their_order(self):
        self.world.say(104, "alice", "gl")
        self.world.system(104, "player #1 wins")
        self.assertEqual(["alice: gl", "+ player #1 wins"], self.world.chat(104))

    def test_bot_say_is_attributed_to_the_bot(self):
        self.world.bot_join(104)
        self.world.bot_say("hello")
        self.assertIn(f"{BOT}: hello", self.world.chat(104))

    def test_leaving_clears_the_bot_table(self):
        self.world.bot_join(104)
        self.world.bot_leave()
        self.assertIsNone(self.world.bot_table)

    def test_every_change_bumps_the_version(self):
        before = self.world.version
        self.world.say(104, "alice", "gl")
        self.assertGreater(self.world.version, before)


class SharedBoardTests(unittest.TestCase):
    """Two bots on one site: they share tables and chat, but not seats or invitations."""

    def setUp(self):
        self.board = FakeBoard()
        self.first = FakeWorld(bot_name="wbcref1", board=self.board)
        self.second = FakeWorld(bot_name="wbcref2", board=self.board)
        self.first.add_lobby_table(104, time="1m", players=["alice", "bob"])
        self.first.add_lobby_table(105, time="1m", players=["carol", "dave"])

    def test_both_worlds_see_the_same_tables_and_chat(self):
        self.first.say(104, "alice", "gl")
        self.assertEqual([104, 105], [t.number for t in self.second.tables()])
        self.assertEqual(["alice: gl"], self.second.chat(104))

    def test_each_bot_sits_at_its_own_table(self):
        self.first.bot_join(104)
        self.second.bot_join(105)
        self.assertEqual((104, 105), (self.first.bot_table, self.second.bot_table))

    def test_invitations_are_per_bot(self):
        self.first.invite("alice", 1093, 104, "1m")
        self.assertIsNone(self.second.invitation)

    def test_a_change_through_one_world_bumps_the_version_of_the_other(self):
        before = self.second.version
        self.first.say(104, "alice", "gl")
        self.assertGreater(self.second.version, before)

    def test_a_world_without_a_board_has_its_own(self):
        alone = FakeWorld(bot_name="wbcref3")
        self.assertEqual([], alone.tables())


class PagesTests(unittest.TestCase):
    """The rendered page must be readable with the production selectors and patterns."""

    def setUp(self):
        self.world = FakeWorld(bot_name=BOT)
        self.world.add_lobby_table(104, time="1m+1s", players=["alice", "bob"])
        self.world.add_lobby_table(105, time="3m", players=["carol"])

    def test_lobby_lists_tables_with_their_players(self):
        tables = PARSER.lobby_tables(parsed(self.world))
        self.assertEqual([104, 105], [t.number for t in tables])
        self.assertEqual(["alice", "bob"], [seat.name for seat in tables[0].seats])
        self.assertFalse(tables[0].joinable)
        self.assertTrue(tables[1].joinable)

    def test_invitation_is_parsed(self):
        self.world.invite("alice", 1093, 104, "1m")
        invitation = PARSER.invitation(parsed(self.world))
        self.assertEqual(("alice", 1093, 104), (invitation.user, invitation.elo, invitation.table))

    def test_no_invitation_when_none_is_pending(self):
        self.assertIsNone(PARSER.invitation(parsed(self.world)))

    def test_table_panel_shows_title_seats_and_chat_after_joining(self):
        self.world.bot_join(104)
        self.world.say(104, "alice", "gl hf")
        self.world.system(104, "player #1 wins")
        dom = parsed(self.world)
        self.assertEqual(104, PARSER.table_info(dom).number)
        self.assertEqual(("alice", "bob"), PARSER.seat_names(dom))
        lines = [line.as_tuple() for line in PARSER.chat_lines(dom)]
        self.assertIn(("alice", "gl hf"), lines)
        self.assertIn(("+", "player #1 wins"), lines)

    def test_no_table_panel_in_the_lobby(self):
        self.assertIsNone(PARSER.table_info(parsed(self.world)))
        self.assertEqual([], PARSER.chat_lines(parsed(self.world)))

    def test_chat_text_is_escaped(self):
        self.world.bot_join(104)
        self.world.say(104, "alice", "<b>x</b> & y")
        lines = PARSER.chat_lines(parsed(self.world))
        self.assertEqual("<b>x</b> & y", lines[-1].text)

    def test_fragment_is_part_of_the_page(self):
        self.assertIn(render_fragment(self.world), render_page(self.world))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.world = FakeWorld(bot_name=BOT)
        self.world.add_lobby_table(104, time="1m", players=["alice"])
        self.server = FakePlayokServer(self.world)
        self.server.start()
        self.addCleanup(self.server.stop)

    def get(self, path: str) -> str:
        with urllib.request.urlopen(self.server.url + path) as response:
            return response.read().decode()

    def post(self, path: str, **fields) -> int:
        data = urllib.parse.urlencode(fields).encode()
        with urllib.request.urlopen(urllib.request.Request(self.server.url + path, data=data)) as response:
            return response.status

    def test_serves_the_page_and_the_fragment(self):
        self.assertEqual(render_page(self.world), self.get("/"))
        self.assertEqual(render_fragment(self.world), self.get("/fragment"))

    def test_version_endpoint_follows_the_world(self):
        before = json.loads(self.get("/version"))["version"]
        self.world.say(104, "alice", "gl")
        self.assertGreater(json.loads(self.get("/version"))["version"], before)

    def test_version_endpoint_reports_churn(self):
        self.assertFalse(json.loads(self.get("/version"))["churn"])
        self.world.churn = True
        self.assertTrue(json.loads(self.get("/version"))["churn"])

    def test_latency_delays_every_get(self):
        self.world.latency_sec = 0.3
        started = time.monotonic()
        self.get("/version")
        self.assertGreaterEqual(time.monotonic() - started, 0.3)

    def test_join_action_seats_the_bot(self):
        self.post("/_act/join", table="104")
        self.assertEqual(104, self.world.bot_table)

    def test_say_action_writes_to_the_table_chat(self):
        self.world.bot_join(104)
        self.post("/_act/say", text="hello")
        self.assertIn(f"{BOT}: hello", self.world.chat(104))

    def test_accept_and_leave_actions(self):
        self.world.invite("alice", 1093, 104, "1m")
        self.post("/_act/accept")
        self.assertEqual(104, self.world.bot_table)
        self.post("/_act/leave")
        self.assertIsNone(self.world.bot_table)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/nope")
        caught.exception.close()
        self.assertEqual(404, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
