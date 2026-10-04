import os
import socket
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from config.settings    import ConfigLoader
from domain.types       import GameResult
from network.messages  import RequestType, ResponseType
from network.protocol   import PacketProtocol
from server             import Server, ranking_rules

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "config.example.toml"
TOKEN   = "test-token"
ROSTER  = ("team,country,full_name,nickname,role,captain,contact\n"
           "Alpha,Demoland,A One,wbca1,main,yes,\nAlpha,Demoland,A Two,wbca2,main,,\nAlpha,Demoland,A Three,wbca3,main,,\n"
           "Beta,Demoland,B One,wbcb1,main,yes,\nBeta,Demoland,B Two,wbcb2,main,,\nBeta,Demoland,B Three,wbcb3,main,,\n")
WIN, LOSS, DRAW = GameResult.WIN.value, GameResult.LOSS.value, GameResult.DRAW.value


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Bot:
    """A fake referee bot speaking the real wire protocol."""
    def __init__(self, port: int):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)

    def send(self, kind: RequestType, data=None, meta=None) -> None:
        packet = {"type": kind.value, "data": data or {}}
        if meta is not None:
            packet["meta"] = meta
        self.sock.sendall(PacketProtocol.encode(packet))

    def recv(self, timeout: float = 2.0):
        self.sock.settimeout(timeout)
        return PacketProtocol.receive_packet(self.sock)       # None = connection closed

    def recv_type(self, response: ResponseType, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while True:
            packet = self.recv(max(0.05, deadline - time.monotonic()))
            if packet is None or packet["type"] == response.value:
                return packet

    def assert_quiet(self, test: unittest.TestCase, timeout: float = 0.3):
        with test.assertRaises(TimeoutError):
            self.recv(timeout)

    def close(self):
        self.sock.close()

    def result(self, p1, r1, p2, r2, match_id=None, **data):
        match_id = match_id or str(uuid.uuid4())
        self.send(RequestType.MATCH_RESULT, {"players": [p1, p2], "scores": [r1, r2], **data}, {"match_id": match_id})
        return match_id


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.bots = []

    def start(self, **replace) -> Server:
        text = EXAMPLE.read_text(encoding="utf-8")
        csv_path = os.path.join(self.folder.name, "teams.csv")
        Path(csv_path).write_text(ROSTER, encoding="utf-8")
        self.db_path = os.path.join(self.folder.name, "t.db")
        text = (text.replace("port              = 9000", f"port = {free_port()}")
                    .replace('"data/tournament.db"', f'"{self.db_path}"')
                    .replace('"data/teams.csv"', f'"{csv_path}"'))
        for old, new in replace.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        config = os.path.join(self.folder.name, "config.toml")
        Path(config).write_text(text, encoding="utf-8")
        settings = ConfigLoader.load(config, role="server", env_file=os.path.join(self.folder.name, "none.env"),
                                     environ={"BOT_TOKEN": TOKEN})
        self.server = Server(settings)
        self.server.start(block=False)
        self.addCleanup(self.server.stop)
        return self.server

    def bot(self, name="bot", auth=True) -> Bot:
        b = Bot(self.server.port)
        self.bots.append(b)
        self.addCleanup(b.close)
        if auth:
            b.send(RequestType.AUTH, {"bot_name": name, "token": TOKEN})
            self.assertEqual(ResponseType.AUTH_OK.value, b.recv()["type"])
        return b

    def test_team_events_rank_by_match_points_and_follow_configured_tiebreaks(self):
        settings = ConfigLoader.load(str(EXAMPLE), role="server", env_file="/nonexistent", environ={"BOT_TOKEN": TOKEN})
        rules = ranking_rules(settings)
        self.assertEqual(settings.tournament.scoring.tiebreaks, rules.tiebreaks)
        self.assertEqual((1.0, 0.5, 0.0), (rules.match_points.win, rules.match_points.draw, rules.match_points.loss))

    # ------------------------------------------------------------------ auth
    def test_packets_before_auth_are_refused_but_connection_survives(self):
        self.start()
        b = self.bot(auth=False)
        b.send(RequestType.SCORE_QUERY)
        self.assertEqual("NOT_AUTHENTICATED", b.recv()["data"]["code"])
        b.send(RequestType.AUTH, {"bot_name": "late", "token": TOKEN})
        self.assertEqual(ResponseType.AUTH_OK.value, b.recv()["type"])

    def test_wrong_token_is_refused_and_closed(self):
        self.start()
        b = self.bot(auth=False)
        b.send(RequestType.AUTH, {"bot_name": "x", "token": "nope"})
        self.assertEqual("AUTH_FAILED", b.recv()["data"]["code"])
        self.assertIsNone(b.recv())

    def test_auth_ok_carries_the_heartbeat_interval(self):
        self.start(**{"heartbeat_seconds = 15": "heartbeat_seconds = 7"})
        b = self.bot(auth=False)
        b.send(RequestType.AUTH, {"bot_name": "x", "token": TOKEN})
        reply = b.recv()
        self.assertEqual(ResponseType.AUTH_OK.value, reply["type"])
        self.assertEqual(7, reply["data"]["heartbeat_seconds"])

    def test_connection_without_auth_is_closed_after_the_deadline(self):
        self.start(**{"auth_timeout_seconds = 5": "auth_timeout_seconds = 0.3"})
        idle = self.bot(auth=False)
        self.assertIsNone(idle.recv(3))

    def test_repeated_failed_auth_locks_the_ip_out(self):
        self.start(**{"auth_max_failures = 5": "auth_max_failures = 2"})
        with self.assertLogs(level="WARNING") as logs:
            for _ in range(3):
                b = self.bot(auth=False)
                b.send(RequestType.AUTH, {"bot_name": "x", "token": "nope"})
                self.assertEqual("AUTH_FAILED", b.recv()["data"]["code"])
        self.assertTrue(any("locked out" in line and "127.0.0.1" in line for line in logs.output))
        self.assertFalse(any("nope" in line for line in logs.output))
        locked = self.bot(auth=False)
        locked.send(RequestType.AUTH, {"bot_name": "good", "token": TOKEN})
        self.assertIsNone(self._closed_or_error(locked))

    def _closed_or_error(self, bot: Bot):
        try:
            return bot.recv()
        except (ConnectionError, TimeoutError):
            return None

    def test_result_log_lines_carry_the_match_id(self):
        self.start()
        b = self.bot("bot1")
        with self.assertLogs(level="INFO") as logs:
            ok_id = b.result("wbca1", WIN, "wbcb1", LOSS, table_no=7)
            b.recv_type(ResponseType.MATCH_ACK)
            b.result("wbca1", WIN, "wbcb1", LOSS, match_id=ok_id)
            b.recv_type(ResponseType.MATCH_ACK)
            bad_id = b.result("ghost", WIN, "wbcb1", LOSS)
            b.recv_type(ResponseType.ERROR)
        text = "\n".join(logs.output)
        accepted = [l for l in logs.output if "accepted" in l]
        self.assertEqual(1, len(accepted))
        self.assertTrue(all(ok_id in l for l in accepted) and "bot1" in accepted[0] and "table 7" in accepted[0])
        self.assertTrue(any("duplicate" in l and ok_id in l for l in logs.output), text)
        self.assertTrue(any("rejected" in l and bad_id in l for l in logs.output), text)

    # --------------------------------------------------------------- results
    def test_result_is_recorded_acknowledged_and_broadcast(self):
        self.start()
        b1, b2 = self.bot("bot1"), self.bot("bot2")
        match_id = b1.result("wbca1", WIN, "wbcb1", LOSS, table_no=7)
        ack = b1.recv_type(ResponseType.MATCH_ACK)["data"]
        self.assertEqual((match_id, False, 1, [1.0, 0.0], False), (ack["match_id"], ack["duplicate"], ack["games"], ack["points"], ack["complete"]))
        self.assertEqual("Alpha : Beta = 1 : 0", b2.recv_type(ResponseType.BROADCAST)["text"])
        game = self.server._store.list_games("Demo Tournament")[0]
        self.assertEqual((7, "wbca1", "wbcb1"), (game["table_no"], game["p1"], game["p2"]))
        self.assertEqual("bot1", game["bot_name"])

    def test_retry_with_same_match_id_is_confirmed_without_double_count(self):
        self.start()
        b1, b2 = self.bot("bot1"), self.bot("bot2")
        match_id = b1.result("wbca1", WIN, "wbcb1", LOSS)
        b1.recv_type(ResponseType.MATCH_ACK)
        b2.recv_type(ResponseType.BROADCAST)
        b1.result("wbca1", WIN, "wbcb1", LOSS, match_id=match_id)
        ack = b1.recv_type(ResponseType.MATCH_ACK)["data"]
        self.assertTrue(ack["duplicate"])
        self.assertEqual(1, ack["games"])
        b2.assert_quiet(self)                                  # nothing broadcast for a retry
        self.assertEqual(1, len(self.server._store.list_games("Demo Tournament")))

    def test_rejected_results_get_an_error_and_do_not_drop_the_connection(self):
        server = self.start(**{"total_matches   = 12": "total_matches   = 2"})
        b = self.bot()
        cases = [(("ghost", WIN, "wbcb1", LOSS), "UNKNOWN_PLAYER"),
                 (("wbca1", WIN, "wbca2", LOSS), "SAME_TEAM"),
                 (("wbca1", WIN, "wbcb1", WIN), "BAD_RESULT")]
        for args, code in cases:
            match_id = b.result(*args)
            err = b.recv_type(ResponseType.ERROR)["data"]
            self.assertEqual((code, match_id), (err["code"], err["match_id"]))
        server._store.set_active("Demo Tournament", "wbca3", False)
        b.result("wbca3", WIN, "wbcb1", LOSS)
        self.assertEqual("PLAYER_INACTIVE", b.recv_type(ResponseType.ERROR)["data"]["code"])
        for _ in range(2):
            b.result("wbca1", WIN, "wbcb1", LOSS)
            self.assertFalse(b.recv_type(ResponseType.MATCH_ACK)["data"]["duplicate"])
        b.result("wbca1", WIN, "wbcb1", LOSS)
        self.assertEqual("MICROMATCH_FULL", b.recv_type(ResponseType.ERROR)["data"]["code"])
        b.send(RequestType.SCORE_QUERY)                         # still connected
        self.assertIn("Alpha", b.recv_type(ResponseType.SCORE_DATA)["text"])
        self.assertEqual(2, len(server._store.list_games("Demo Tournament")))

    def test_malformed_packets(self):
        self.start()
        b = self.bot()
        b.send(RequestType.MATCH_RESULT, {"players": ["wbca1", "wbcb1"], "scores": [WIN, LOSS]})          # no match_id
        self.assertEqual("BAD_PACKET", b.recv_type(ResponseType.ERROR)["data"]["code"])
        b.send(RequestType.MATCH_RESULT, {"players": ["wbca1"], "scores": [WIN, LOSS]}, {"match_id": "m"})
        self.assertEqual("BAD_PACKET", b.recv_type(ResponseType.ERROR)["data"]["code"])
        b.send(RequestType.MATCH_RESULT, {"players": ["wbca1", "wbcb1"], "scores": [9, 9]}, {"match_id": "m"})
        self.assertEqual("BAD_PACKET", b.recv_type(ResponseType.ERROR)["data"]["code"])
        b.sock.sendall(PacketProtocol.encode({"type": 999}))
        self.assertEqual("UNKNOWN_TYPE", b.recv_type(ResponseType.ERROR)["data"]["code"])
        b.sock.sendall(PacketProtocol.encode(["not", "a", "dict"]))
        self.assertEqual("UNKNOWN_TYPE", b.recv_type(ResponseType.ERROR)["data"]["code"])

    # ----------------------------------------------------------- roster/tables
    def test_roster_query(self):
        self.start()
        b = self.bot()
        b.send(RequestType.ROSTER_QUERY)
        players = b.recv_type(ResponseType.ROSTER_DATA)["data"]["players"]
        self.assertEqual(6, len(players))
        self.assertIn({"name": "wbca1", "team": "Alpha", "role": "main", "active": True}, players)

    def test_table_claims_and_release_on_disconnect(self):
        self.start()
        b1, b2 = self.bot("bot1"), self.bot("bot2")
        b1.send(RequestType.TABLE_CLAIM, {"table_no": 5})
        self.assertEqual(ResponseType.CLAIM_OK.value, b1.recv_type(ResponseType.CLAIM_OK)["type"])
        b1.send(RequestType.TABLE_CLAIM, {"table_no": 5})                       # re-claim by the owner is fine
        self.assertIsNotNone(b1.recv_type(ResponseType.CLAIM_OK))
        b2.send(RequestType.TABLE_CLAIM, {"table_no": 5})
        denied = b2.recv_type(ResponseType.CLAIM_DENIED)["data"]
        self.assertEqual(("bot1", 5), (denied["held_by"], denied["table_no"]))
        b2.send(RequestType.TABLE_RELEASE, {"table_no": 5})
        self.assertEqual("NOT_OWNER", b2.recv_type(ResponseType.ERROR)["data"]["code"])
        b1.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:                                       # server frees the table on disconnect
            b2.send(RequestType.TABLE_CLAIM, {"table_no": 5})
            if b2.recv()["type"] == ResponseType.CLAIM_OK.value:
                break
            time.sleep(0.05)
        else:
            self.fail("table was not released after the owner disconnected")
        b2.send(RequestType.TABLE_CLAIM, {"table_no": "five"})
        self.assertEqual("BAD_PACKET", b2.recv_type(ResponseType.ERROR)["data"]["code"])

    # ---------------------------------------------------------- housekeeping
    def test_silent_client_is_dropped_after_missed_heartbeats(self):
        self.start(**{"heartbeat_seconds = 15": "heartbeat_seconds = 0.2"})
        quiet = self.bot("quiet")
        alive = self.bot("alive")
        start = time.monotonic()
        while time.monotonic() - start < 3:
            alive.send(RequestType.HEARTBEAT)
            try:
                if quiet.recv(0.1) is None:
                    break
            except TimeoutError:
                pass
        else:
            self.fail("silent client was not closed")
        alive.send(RequestType.SCORE_QUERY)
        self.assertIsNotNone(alive.recv_type(ResponseType.SCORE_DATA))

    def test_results_survive_restart_and_backup_is_written(self):
        server = self.start()
        b = self.bot()
        b.result("wbca1", DRAW, "wbcb1", DRAW)
        b.recv_type(ResponseType.MATCH_ACK)
        server.stop()
        self.assertTrue(os.path.isfile(self.db_path + ".bak"))
        self.start()
        b = self.bot()
        b.send(RequestType.SCORE_QUERY)
        self.assertEqual("Alpha : Beta = 0.5 : 0.5", b.recv_type(ResponseType.SCORE_DATA)["text"])


if __name__ == "__main__":
    unittest.main()
