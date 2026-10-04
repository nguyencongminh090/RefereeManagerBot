import unittest
from dataclasses import replace
from unittest.mock import patch

from client                 import Client, MAX_CONSECUTIVE_DRIVER_ERRORS, build_client
from referee.commands.dispatcher    import CommandDispatcher
from referee.commands.handlers      import CommandHandlers
from referee.driver                 import SilentSeleniumDriver
from referee.driver_port            import DriverError, IDriver
from referee.page_parser       import LobbyTable, Seat
from network.messages       import RequestType, ResponseType
from referee.session         import SessionState
from network.ports          import IClientSocket
from tests.test_page_parser import SETTINGS

ADMIN = SETTINGS.tournament.admins[0]
PLAYERS = [{"name": "wbca1", "team": "Alpha", "active": True}, {"name": "wbcb1", "team": "Beta", "active": True},
           {"name": "wbcz9", "team": "Beta", "active": False}]
TABLE = LobbyTable(7, "1m+1s", (Seat("wbca1", 1200), Seat("wbcb1", 1200)), joinable=False)


class FakeDriver(IDriver):
    def __init__(self):
        self.tables, self.joined, self.said, self.can_join = [TABLE], [], [], True
        self.failing = False
        self.invite, self.names, self.inbox = None, ("wbca1", "wbcb1"), []

    def lobby_tables(self): return self.tables
    def join_table(self, number):
        self.raise_if_failing()
        self.joined.append(number)
        return self.can_join
    def raise_if_failing(self):
        if self.failing:
            raise DriverError("page changed")
    def check_for_invitation(self):
        self.raise_if_failing()
        return self.invite
    def accept_invitation(self): return True
    def get_table_number(self): return 7
    def get_players_name(self): return self.names
    def receive_messages(self):
        fresh, self.inbox = self.inbox, []
        return fresh
    def send_message(self, text): self.said.append(text)
    def leave_table(self): ...
    def open_site(self): ...
    def goto_lobby(self): ...
    def login(self, username, password): return True
    def quit(self): ...


class FakeSocket(IClientSocket):
    def __init__(self, on_receive):
        self.on_receive, self.packets = on_receive, []

    def send_packet(self, payload): self.packets.append(payload)
    def connect(self): ...
    def disconnect(self): ...

    def kinds(self):
        return [p["type"] for p in self.packets]


class ClientTests(unittest.TestCase):
    def build(self, join_mode="auto"):
        client_cfg = replace(SETTINGS.client, join_mode=join_mode)
        self.now = 100.0
        self.driver = FakeDriver()
        self.client = Client(replace(SETTINGS, client=client_cfg), self.driver, self.make_socket, clock=lambda: self.now)
        return self.client

    def make_socket(self, on_receive):
        self.socket = FakeSocket(on_receive)
        return self.socket

    def reply(self, kind, **data):
        self.socket.on_receive({"type": kind.value, "data": data})

    def give_roster(self):
        self.reply(ResponseType.ROSTER_DATA, players=PLAYERS)

    def test_no_claim_before_the_roster_arrives(self):
        self.build().step()
        self.assertEqual([], self.socket.packets)

    def test_claims_an_eligible_table_then_joins_when_granted(self):
        self.build()
        self.give_roster()
        self.client.step()
        self.assertEqual({"type": RequestType.TABLE_CLAIM.value, "data": {"table_no": 7}}, self.socket.packets[-1])
        self.reply(ResponseType.CLAIM_OK, table_no=7)
        self.client.step()
        self.assertEqual([7], self.driver.joined)

    def test_denied_claim_skips_the_table(self):
        self.build()
        self.give_roster()
        self.client.step()
        self.reply(ResponseType.CLAIM_DENIED, table_no=7, held_by="other")
        self.client.step()
        self.now += 10
        self.client.step()
        self.assertEqual(1, self.socket.kinds().count(RequestType.TABLE_CLAIM.value))
        self.assertEqual([], self.driver.joined)

    def test_failed_join_releases_the_claim(self):
        self.build()
        self.driver.can_join = False
        self.give_roster()
        self.client.step()
        self.reply(ResponseType.CLAIM_OK, table_no=7)
        self.client.step()
        self.assertEqual(RequestType.TABLE_RELEASE.value, self.socket.packets[-1]["type"])

    def test_join_that_hits_a_page_error_releases_the_claim_instead_of_crashing(self):
        self.build()
        self.give_roster()
        self.client.step()
        self.driver.failing = True
        self.reply(ResponseType.CLAIM_OK, table_no=7)
        self.client.step()
        self.assertEqual(RequestType.TABLE_RELEASE.value, self.socket.packets[-1]["type"])
        self.assertEqual([], self.driver.joined)

    def test_a_page_error_in_a_step_is_survived(self):
        self.build("invite")
        self.driver.failing = True
        self.client.step()

    def test_a_persistent_page_error_stops_the_bot(self):
        self.build("invite")
        self.driver.failing = True
        for _ in range(MAX_CONSECUTIVE_DRIVER_ERRORS - 1):
            self.client.step()
        with self.assertRaises(RuntimeError):
            self.client.step()

    def test_a_good_step_resets_the_error_count(self):
        self.build("invite")
        for _ in range(2):
            self.driver.failing = True
            for _ in range(MAX_CONSECUTIVE_DRIVER_ERRORS - 1):
                self.client.step()
            self.driver.failing = False
            self.client.step()

    def test_inactive_players_are_not_in_the_roster(self):
        self.build()
        self.reply(ResponseType.ROSTER_DATA, players=[PLAYERS[0], PLAYERS[2]])
        self.client.step()
        self.assertEqual([], self.socket.packets)

    def test_scan_is_throttled(self):
        self.build()
        self.give_roster()
        self.driver.tables = []
        self.client.step()
        self.driver.tables = [TABLE]
        self.client.step()                              # too soon after the last scan
        self.assertEqual([], self.socket.packets)
        self.now += SETTINGS.client.lobby_scan_seconds
        self.client.step()
        self.assertEqual(1, len(self.socket.packets))

    def joined_session(self):
        self.build()
        self.give_roster()
        self.client.step()
        self.reply(ResponseType.CLAIM_OK, table_no=7)
        self.client.step()

    def test_finished_session_releases_the_table_and_is_not_rejoined(self):
        self.joined_session()
        for _ in range(SETTINGS.tournament.total_matches):
            self.driver.inbox = [("+", "player #1 wins")]
            self.client.step()
        self.assertEqual(RequestType.TABLE_RELEASE.value, self.socket.packets[-1]["type"])
        self.now += 10
        before = len(self.socket.packets)
        self.client.step()
        self.assertEqual(before, len(self.socket.packets))

    def sent_match_id(self):
        return next(p["meta"]["match_id"] for p in reversed(self.socket.packets)
                    if p["type"] == RequestType.MATCH_RESULT.value)

    def play_one_game(self):
        self.joined_session()
        self.driver.inbox = [("+", "player #1 wins")]
        self.client.step()

    def test_match_ack_reaches_the_session(self):
        self.play_one_game()
        self.reply(ResponseType.MATCH_ACK, match_id=self.sent_match_id(), duplicate=False, games=5,
                   complete=False, points=[0, 0])
        self.client.step()
        self.assertEqual(5, self.client._session.context.games_played)

    def test_complete_ack_finishes_the_session_and_releases_the_table(self):
        self.play_one_game()
        self.reply(ResponseType.MATCH_ACK, match_id=self.sent_match_id(), duplicate=False, games=12,
                   complete=True, points=[6, 6])
        self.client.step()
        self.assertEqual(RequestType.TABLE_RELEASE.value, self.socket.packets[-1]["type"])

    def test_micromatch_full_error_finishes_the_session(self):
        self.play_one_game()
        self.reply(ResponseType.ERROR, code="MICROMATCH_FULL", message="full", match_id=self.sent_match_id())
        self.client.step()
        self.assertEqual(RequestType.TABLE_RELEASE.value, self.socket.packets[-1]["type"])

    def test_error_without_match_id_is_only_logged(self):
        self.play_one_game()
        self.reply(ResponseType.ERROR, code="BAD_PACKET", message="x")
        self.client.step()
        self.assertEqual(SessionState.IN_PROGRESS, self.client._session.context.state)

    def test_ack_without_a_session_is_ignored(self):
        self.build()
        self.reply(ResponseType.MATCH_ACK, match_id="x", games=1, complete=True)
        self.client.step()
        self.assertEqual([], self.socket.packets)

    def test_standings_are_written_to_the_table_chat_only_during_a_session(self):
        self.build()
        self.socket.on_receive({"type": ResponseType.BROADCAST.value, "text": "A : B = 1 : 0"})
        self.client.step()
        self.assertEqual([], self.driver.said)
        self.joined_session()
        self.socket.on_receive({"type": ResponseType.BROADCAST.value, "text": "A : B = 2 : 0"})
        self.client.step()
        self.assertEqual(["A : B = 2 : 0"], self.driver.said)

    def test_invite_mode_accepts_only_admins(self):
        self.build(join_mode="invite")
        self.driver.invite = "stranger"
        self.client.step()
        self.assertEqual([], self.socket.packets)
        self.driver.invite = ADMIN
        self.client.step()
        self.driver.inbox = [("+", "player #1 wins")]
        self.client.step()
        self.assertEqual(RequestType.MATCH_RESULT.value, self.socket.packets[-1]["type"])


class BuildClientTests(unittest.TestCase):
    def build(self, **options):
        secrets = replace(SETTINGS.secrets, playok_user="u", playok_pass="p", bot_token="t")
        with patch("client.create_firefox"):
            return build_client(replace(SETTINGS, secrets=secrets), **options)

    def test_chat_is_written_by_default(self):
        self.assertNotIsInstance(self.build()._driver, SilentSeleniumDriver)

    def test_no_chat_builds_a_silent_driver(self):
        self.assertIsInstance(self.build(no_chat=True)._driver, SilentSeleniumDriver)


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeDriver()
        self.socket = FakeSocket(None)
        self.dispatcher = CommandDispatcher(self.driver, self.socket)
        CommandHandlers(SETTINGS).register_on(self.dispatcher)

    def test_score_asks_the_server(self):
        self.dispatcher.dispatch("anyone", "!score")
        self.assertEqual([RequestType.SCORE_QUERY.value], self.socket.kinds())

    def test_rules_writes_the_configured_text(self):
        self.dispatcher.dispatch("anyone", "!rules")
        self.assertEqual([SETTINGS.messages.rules_for(SETTINGS.tournament.language, "en")], self.driver.said)


if __name__ == "__main__":
    unittest.main()
