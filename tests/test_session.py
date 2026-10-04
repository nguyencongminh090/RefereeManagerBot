import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from referee.commands.dispatcher   import CommandDispatcher
from referee.driver_port           import IDriver
from referee.session          import MAX_PENDING_RESULTS, UNREADABLE_POLLS_BEFORE_ALERT, MatchSession
from domain.types           import GameResult
from network.messages      import RequestType
from referee.session       import SessionState
from network.ports import IClientSocket
from tests.test_page_parser import SETTINGS

ADMIN = SETTINGS.tournament.admins[0]
WIN_P1, WIN_P2, DRAW = "player #1 wins", "player #2 wins", "#draw"
START = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


class FakeDriver(IDriver):
    def __init__(self, names=("alice", "bob")):
        self.names, self.inbox, self.said, self.left, self.table_no = names, [], [], 0, None

    def get_players_name(self):
        return self.names

    def get_table_number(self): return self.table_no
    def lobby_tables(self): return []
    def join_table(self, number): return False

    def receive_messages(self):
        fresh, self.inbox = self.inbox, []
        return fresh

    def send_message(self, text):
        self.said.append(text)

    def leave_table(self):
        self.left += 1

    def open_site(self): ...
    def goto_lobby(self): ...
    def login(self, username, password): return True
    def check_for_invitation(self): return None
    def accept_invitation(self): return True
    def quit(self): ...


class FakeSocket(IClientSocket):
    def __init__(self):
        self.packets = []

    def send_packet(self, payload):
        self.packets.append(payload)

    def connect(self): ...
    def disconnect(self): ...


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


def make_settings(total=12, break_after=0):
    tournament = replace(SETTINGS.tournament, total_matches=total, break_after=break_after, break_minutes=5)
    return replace(SETTINGS, tournament=tournament)


class SessionTestCase(unittest.TestCase):
    def build(self, names=("alice", "bob"), total=12, break_after=0):
        self.driver, self.socket, self.clock = FakeDriver(names), FakeSocket(), Clock()
        self.calls = []
        dispatcher = CommandDispatcher(self.driver, self.socket)
        dispatcher.register("!rules", lambda ctx: self.calls.append(("rules", ctx.sender)))
        dispatcher.register("!hello", lambda ctx: self.calls.append(("hello", ctx.sender)))
        self.session = MatchSession(self.driver, dispatcher, self.socket, make_settings(total, break_after),
                                    clock=self.clock)
        return self.session

    def result(self, index=0):
        return self.socket.packets[index]["data"]


class TestReporting(SessionTestCase):
    def test_win_for_player_one(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        packet = self.socket.packets[0]
        self.assertEqual(packet["type"], RequestType.MATCH_RESULT.value)
        self.assertEqual(packet["data"]["players"], ["alice", "bob"])
        self.assertEqual(packet["data"]["scores"], [GameResult.WIN.value, GameResult.LOSS.value])
        self.assertIsNone(packet["data"]["table_no"])

    def test_result_carries_the_table_number(self):
        self.build()
        self.driver.table_no = 116
        self.session.process_message("+", WIN_P1)
        self.assertEqual(116, self.result()["table_no"])

    def test_win_for_player_two(self):
        self.build()
        self.session.process_message("+", WIN_P2)
        self.assertEqual(self.result()["scores"], [GameResult.LOSS.value, GameResult.WIN.value])

    def test_draw(self):
        self.build()
        self.session.process_message("+", DRAW)
        self.assertEqual(self.result()["scores"], [GameResult.DRAW.value, GameResult.DRAW.value])

    def test_repeated_identical_lines_are_two_games_with_distinct_ids(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        self.session.process_message("+", WIN_P1)
        ids = {p["meta"]["match_id"] for p in self.socket.packets}
        self.assertEqual(len(self.socket.packets), 2)
        self.assertEqual(len(ids), 2)

    def test_line_delivered_once_by_the_driver_is_reported_once(self):
        self.build()
        self.driver.inbox = [("+", WIN_P1)]
        self.session.poll()
        self.session.poll()
        self.assertEqual(len(self.socket.packets), 1)

    def test_ignores_chat_that_looks_like_a_result(self):
        self.build()
        self.session.process_message("bob", WIN_P1)
        self.session.process_message("+", "alice joins")
        self.assertEqual(self.socket.packets, [])


class TestNames(SessionTestCase):
    def test_names_are_read_when_the_session_is_created(self):
        self.build()
        self.assertEqual((self.session.context.p1_name, self.session.context.p2_name), ("alice", "bob"))

    def test_names_read_later_are_used(self):
        self.build(names=("", ""))
        self.driver.names = ("carol", "dave")
        self.session.process_message("+", WIN_P1)
        self.assertEqual(self.result()["players"], ["carol", "dave"])

    def test_unreadable_names_send_nothing_and_do_not_count(self):
        self.build(names=("alice", ""))
        self.session.process_message("+", WIN_P1)
        self.assertEqual(self.socket.packets, [])
        self.assertEqual(self.session.context.games_played, 0)
        self.assertEqual(self.session.context.state, SessionState.IN_PROGRESS)


class TestCompletion(SessionTestCase):
    def test_completes_after_configured_games(self):
        self.build(total=2)
        self.session.process_message("+", WIN_P1)
        self.assertEqual(self.session.context.state, SessionState.IN_PROGRESS)
        self.session.process_message("+", DRAW)
        self.assertEqual(self.session.context.state, SessionState.COMPLETED)
        self.assertEqual(self.driver.left, 1)
        self.assertEqual(self.driver.said, [SETTINGS.messages.bye])

    def test_nothing_is_reported_after_completion(self):
        self.build(total=1)
        self.session.process_message("+", WIN_P1)
        self.session.process_message("+", WIN_P1)
        self.assertEqual(len(self.socket.packets), 1)

    def test_no_break_when_break_after_is_zero(self):
        self.build(total=4, break_after=0)
        self.session.process_message("+", WIN_P1)
        self.assertEqual(self.session.context.state, SessionState.IN_PROGRESS)
        self.assertEqual(self.driver.said, [])

    def test_break_announced_and_ended_by_the_clock(self):
        self.build(total=4, break_after=2)
        self.session.process_message("+", WIN_P1)
        self.session.process_message("+", WIN_P2)
        self.assertEqual(self.session.context.state, SessionState.BREAK_TIME)
        self.assertEqual(self.driver.said, [SETTINGS.messages.break_text.format(curr_time="10:00", resume_time="10:05")])
        self.clock.now += timedelta(minutes=5)
        self.session.poll()
        self.assertEqual(self.session.context.state, SessionState.IN_PROGRESS)


def ack(match_id, games, complete=False):
    return {"match_id": match_id, "duplicate": False, "games": games, "limit": 12, "complete": complete,
            "players": ["alice", "bob"], "points": [0.0, 0.0]}


class TestServerAuthority(SessionTestCase):
    def sent_id(self, index=0):
        return self.socket.packets[index]["meta"]["match_id"]

    def test_sending_a_result_is_logged_with_players_and_table(self):
        self.build()
        self.driver.table_no = 116
        with self.assertLogs("referee.session", level="INFO") as logs:
            self.session.process_message("+", WIN_P1)
        self.assertTrue(any("sent" in line and "alice" in line and "bob" in line and "116" in line
                            for line in logs.output), logs.output)

    def test_an_ack_is_logged_with_the_games_played(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        with self.assertLogs("referee.session", level="INFO") as logs:
            self.session.on_ack(ack(self.sent_id(), 7))
        self.assertTrue(any("acknowledged" in line and "7" in line for line in logs.output), logs.output)

    def test_ack_sets_the_games_played(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        self.assertEqual(0, self.session.context.games_played)
        self.session.on_ack(ack(self.sent_id(), 7))
        self.assertEqual(7, self.session.context.games_played)

    def test_complete_ack_ends_the_session(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        self.session.on_ack(ack(self.sent_id(), 12, complete=True))
        self.assertEqual(SessionState.COMPLETED, self.session.context.state)
        self.assertEqual(1, self.driver.left)

    def test_ack_of_another_session_is_ignored(self):
        self.build()
        self.session.on_ack(ack("foreign", 12, complete=True))
        self.assertEqual(SessionState.IN_PROGRESS, self.session.context.state)

    def test_rejected_game_does_not_count_for_the_local_fallback(self):
        self.build(total=2)
        self.session.process_message("+", WIN_P1)
        self.session.on_ack(ack(self.sent_id(0), 1))
        self.session.process_message("+", WIN_P1)
        self.session.on_rejected("BAD_RESULT", self.sent_id(1))
        self.session.process_message("+", WIN_P1)
        self.assertEqual(SessionState.IN_PROGRESS, self.session.context.state)

    def test_session_keeps_waiting_for_the_server_once_acks_arrive(self):
        self.build(total=2)
        self.session.process_message("+", WIN_P1)
        self.session.on_ack(ack(self.sent_id(0), 1))
        self.session.process_message("+", WIN_P1)
        self.assertEqual(SessionState.IN_PROGRESS, self.session.context.state)
        self.session.on_ack(ack(self.sent_id(1), 2, complete=True))
        self.assertEqual(SessionState.COMPLETED, self.session.context.state)

    def test_micromatch_full_ends_the_session(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        self.session.on_rejected("MICROMATCH_FULL", self.sent_id())
        self.assertEqual(SessionState.COMPLETED, self.session.context.state)

    def test_other_rejection_is_only_logged(self):
        self.build()
        self.session.process_message("+", WIN_P1)
        with self.assertLogs("referee.session", "WARNING") as logs:
            self.session.on_rejected("SAME_TEAM", self.sent_id())
        self.assertIn(self.sent_id(), logs.output[0])
        self.assertEqual(SessionState.IN_PROGRESS, self.session.context.state)
        self.assertEqual([], self.driver.said)


class TestRetainedResults(SessionTestCase):
    def test_result_is_sent_once_the_names_appear(self):
        self.build(names=("", ""))
        self.session.process_message("+", WIN_P1)
        self.assertEqual([], self.socket.packets)
        self.driver.names = ("carol", "dave")
        self.session.poll()
        self.session.poll()
        self.assertEqual(1, len(self.socket.packets))
        self.assertEqual(["carol", "dave"], self.result()["players"])

    def test_retained_results_keep_their_order_and_own_ids(self):
        self.build(names=("", ""))
        for line in (WIN_P1, WIN_P2, DRAW):
            self.session.process_message("+", line)
        self.driver.names = ("carol", "dave")
        self.session.poll()
        scores = [p["data"]["scores"][0] for p in self.socket.packets]
        self.assertEqual([GameResult.WIN.value, GameResult.LOSS.value, GameResult.DRAW.value], scores)
        self.assertEqual(3, len({p["meta"]["match_id"] for p in self.socket.packets}))

    def test_pending_results_are_bounded(self):
        self.build(names=("", ""))
        for _ in range(MAX_PENDING_RESULTS + 2):
            self.session.process_message("+", WIN_P1)
        self.driver.names = ("carol", "dave")
        self.session.poll()
        self.assertEqual(MAX_PENDING_RESULTS, len(self.socket.packets))

    def test_alert_after_many_failed_polls_logs_every_time(self):
        self.build(names=("", ""))
        self.session.process_message("+", WIN_P1)
        for _ in range(UNREADABLE_POLLS_BEFORE_ALERT - 1):
            self.session.poll()
        with self.assertLogs("referee.session", "ERROR") as logs:
            self.session.poll()
            self.session.poll()
        self.assertEqual(2, len(logs.output))

    def test_games_played_waits_for_the_ack(self):
        self.build(names=("", ""))
        self.session.process_message("+", WIN_P1)
        self.driver.names = ("carol", "dave")
        self.session.poll()
        self.assertEqual(0, self.session.context.games_played)


class TestCommands(SessionTestCase):
    def test_admin_may_run_admin_only_command(self):
        self.build()
        self.session.process_message(ADMIN, "!rules")
        self.assertEqual(self.calls, [("rules", ADMIN)])

    def test_admin_check_ignores_case(self):
        self.build()
        self.session.process_message(ADMIN.upper(), "!RULES")
        self.assertEqual(len(self.calls), 1)

    def test_non_admin_is_ignored_silently(self):
        self.build()
        self.session.process_message("alice", "!rules")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.driver.said, [])

    def test_non_admin_cannot_end_the_session(self):
        self.build()
        self.session.process_message("alice", "!leave")
        self.assertEqual(self.session.context.state, SessionState.IN_PROGRESS)
        self.assertEqual(self.driver.left, 0)

    def test_admin_can_end_the_session(self):
        self.build()
        self.session.process_message(ADMIN, "!leave")
        self.assertEqual(self.session.context.state, SessionState.COMPLETED)
        self.assertEqual(self.driver.left, 1)

    def test_commands_that_are_not_admin_only_are_open_to_everyone(self):
        self.build()
        self.session.process_message("alice", "!hello")
        self.assertEqual(self.calls, [("hello", "alice")])

    def test_plain_chat_is_not_a_command(self):
        self.build()
        self.session.process_message("alice", "good luck")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
