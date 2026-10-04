import json
import os
import socket
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from config.settings       import ConfigLoader
from domain.types           import GameResult
from network.messages      import RequestType, ResponseType
from network.client_socket import TcpClientSocket
from network.options       import ClientSocketOptions
from network.outbox        import ResultOutbox
from network.protocol      import PacketProtocol
from server                import Server
from tests.test_server     import EXAMPLE, ROSTER, TOKEN, free_port

WIN, LOSS = GameResult.WIN.value, GameResult.LOSS.value


def result_packet(match_id: str) -> dict:
    return {"type": RequestType.MATCH_RESULT.value, "data": {"players": ["wbca1", "wbcb1"], "scores": [WIN, LOSS]},
            "meta": {"match_id": match_id}}


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


class ServerBackedCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.port     = free_port()
        self.received = []
        self.server   = None

    def start_server(self, heartbeat_seconds: str = "15") -> None:
        text = EXAMPLE.read_text(encoding="utf-8").replace("heartbeat_seconds = 15", f"heartbeat_seconds = {heartbeat_seconds}")
        csv_path = os.path.join(self.folder.name, "teams.csv")
        Path(csv_path).write_text(ROSTER, encoding="utf-8")
        text = (text.replace("port              = 9000", f"port = {self.port}")
                    .replace('"data/tournament.db"', f'"{os.path.join(self.folder.name, "t.db")}"')
                    .replace('"data/teams.csv"', f'"{csv_path}"'))
        config = os.path.join(self.folder.name, "config.toml")
        Path(config).write_text(text, encoding="utf-8")
        settings = ConfigLoader.load(config, role="server", env_file=os.path.join(self.folder.name, "none.env"),
                                     environ={"BOT_TOKEN": TOKEN})
        self.server = Server(settings)
        self.server.start(block=False)

    def stop_server(self) -> None:
        self.server.stop()
        self.server = None

    def make_client(self, token: str = TOKEN, **option_kwargs) -> TcpClientSocket:
        defaults = dict(heartbeat_seconds=0.2, reconnect_max_seconds=0.3, connect_timeout_seconds=2.0)
        options  = ClientSocketOptions(**{**defaults, **option_kwargs})
        client   = TcpClientSocket("127.0.0.1", self.port, self.received.append, token, "test-bot", options)
        self.addCleanup(client.disconnect)
        return client

    def acks(self) -> list:
        return [p for p in self.received if p["type"] == ResponseType.MATCH_ACK.value]



class ClientSocketTests(ServerBackedCase):
    def test_connect_authenticates_and_result_is_acknowledged(self):
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        client = self.make_client()
        client.connect()
        self.assertTrue(client.is_connected)
        match_id = str(uuid.uuid4())
        client.send_packet(result_packet(match_id))
        self.assertTrue(wait_for(lambda: self.acks()))
        self.assertEqual(match_id, self.acks()[0]["data"]["match_id"])
        self.assertTrue(wait_for(lambda: client.pending_results == 0))

    def test_wrong_token_raises_connection_error(self):
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        client = self.make_client(token="nope")
        with self.assertRaises(ConnectionError):
            client.connect()
        self.assertFalse(client.is_connected)

    def test_unreachable_server_raises_connection_error(self):
        client = TcpClientSocket("127.0.0.1", self.port, self.received.append, TOKEN, "test-bot",
                                 ClientSocketOptions(reconnect_max_seconds=0.1, connect_timeout_seconds=0.3))
        self.addCleanup(client.disconnect)
        with self.assertRaises(ConnectionError):
            client.connect()

    def test_result_sent_while_offline_is_delivered_after_reconnect(self):
        self.start_server()
        client = self.make_client()
        client.connect()
        self.stop_server()
        self.assertTrue(wait_for(lambda: not client.is_connected))
        match_id = str(uuid.uuid4())
        client.send_packet(result_packet(match_id))
        self.assertEqual(1, client.pending_results)

        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        self.assertTrue(wait_for(lambda: self.acks()), "the queued result was not delivered")
        self.assertEqual(match_id, self.acks()[0]["data"]["match_id"])
        self.assertEqual(1, len(self.server._store.list_games("Demo Tournament")))

    def test_auth_ok_is_passed_on_at_every_connection(self):
        self.start_server()
        client = self.make_client()
        client.connect()
        auth_ok = lambda: [p for p in self.received if p["type"] == ResponseType.AUTH_OK.value]
        self.assertTrue(wait_for(lambda: len(auth_ok()) == 1), "no AUTH_OK on the first connection")
        self.stop_server()
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        self.assertTrue(wait_for(lambda: len(auth_ok()) == 2), "no AUTH_OK after the reconnect")

    def test_rejected_result_is_not_retried_forever(self):
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        client = self.make_client()
        client.connect()
        bad = result_packet(str(uuid.uuid4()))
        bad["data"]["players"] = ["wbca1", "nobody"]
        client.send_packet(bad)
        self.assertTrue(wait_for(lambda: client.pending_results == 0))
        errors = [p for p in self.received if p["type"] == ResponseType.ERROR.value]
        self.assertEqual("UNKNOWN_PLAYER", errors[0]["data"]["code"])

    def test_heartbeat_keeps_an_idle_connection_alive(self):
        self.start_server(heartbeat_seconds="0.2")          # the server drops a silent bot after 0.6 s
        self.addCleanup(lambda: self.server and self.server.stop())
        client = self.make_client()
        client.connect()
        time.sleep(1.5)
        self.assertTrue(client.is_connected)


def start_connecting(client: TcpClientSocket) -> None:
    """Runs connect() in the background; its ConnectionError is irrelevant to the scripted tests."""
    def run():
        try:
            client.connect()
        except ConnectionError:
            pass
    threading.Thread(target=run, daemon=True).start()


class FakeServer:
    """Accepts one connection at a time and lets a test script the replies."""

    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]

    def accept(self) -> socket.socket:
        self.listener.settimeout(3.0)
        conn, _ = self.listener.accept()
        conn.settimeout(3.0)
        return conn

    def close(self) -> None:
        self.listener.close()


class DurableOutboxTests(ServerBackedCase):
    def outbox_path(self) -> str:
        return os.path.join(self.folder.name, "sub", "outbox.jsonl")

    def test_result_is_written_to_the_file_before_it_is_sent(self):
        path   = self.outbox_path()
        client = self.make_client(outbox_path=path)
        client.send_packet(result_packet("m-1"))             # offline: nothing can be sent
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        self.assertEqual(["m-1"], [json.loads(line)["match_id"] for line in lines])
        self.assertEqual(1, client.pending_results)

    def test_unconfirmed_result_survives_a_crash_and_is_counted_once(self):
        path = self.outbox_path()
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        match_id = str(uuid.uuid4())
        first = self.make_client(outbox_path=path)
        first.send_packet(result_packet(match_id))           # never connected: "crash" before delivery
        restarted = self.make_client(outbox_path=path)
        self.assertEqual(1, restarted.pending_results)
        restarted.connect()
        self.assertTrue(wait_for(lambda: self.acks()))
        self.assertTrue(wait_for(lambda: restarted.pending_results == 0))
        third = self.make_client(outbox_path=path)           # crash again after the ack
        self.assertEqual(0, third.pending_results)
        third.connect()
        time.sleep(0.2)
        self.assertEqual(1, len(self.server._store.list_games("Demo Tournament")))

    def test_resend_of_an_already_counted_result_is_not_double_counted(self):
        path = self.outbox_path()
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        match_id = str(uuid.uuid4())
        client = self.make_client(outbox_path=os.path.join(self.folder.name, "a.jsonl"))
        client.connect()
        client.send_packet(result_packet(match_id))
        self.assertTrue(wait_for(lambda: self.acks()))
        ResultOutbox(path).add(match_id, result_packet(match_id))   # crash before the ack was stored
        again = self.make_client(outbox_path=path)
        again.connect()
        self.assertTrue(wait_for(lambda: len(self.acks()) == 2))
        self.assertEqual(1, len(self.server._store.list_games("Demo Tournament")))

    def test_corrupt_trailing_line_is_ignored(self):
        path = self.outbox_path()
        outbox = ResultOutbox(path)
        outbox.add("m-1", result_packet("m-1"))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"match_id": "m-2", "pac')
        with self.assertLogs("network.outbox", level="WARNING"):
            reloaded = ResultOutbox(path)
        self.assertEqual(["m-1"], [p["meta"]["match_id"] for p in reloaded.pending()])
        reloaded.add("m-3", result_packet("m-3"))
        self.assertEqual(2, len(ResultOutbox(path)))

    def test_ack_removes_the_result_from_the_file(self):
        path = self.outbox_path()
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        client = self.make_client(outbox_path=path)
        client.connect()
        client.send_packet(result_packet(str(uuid.uuid4())))
        self.assertTrue(wait_for(lambda: client.pending_results == 0))
        self.assertEqual("", Path(path).read_text(encoding="utf-8"))

    def test_error_removes_the_result_from_the_file(self):
        path = self.outbox_path()
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        client = self.make_client(outbox_path=path)
        client.connect()
        bad = result_packet(str(uuid.uuid4()))
        bad["data"]["players"] = ["wbca1", "nobody"]
        client.send_packet(bad)
        self.assertTrue(wait_for(lambda: client.pending_results == 0))
        self.assertEqual("", Path(path).read_text(encoding="utf-8"))


class ScriptedServerTests(unittest.TestCase):
    def setUp(self):
        self.fake     = FakeServer()
        self.received = []
        self.addCleanup(self.fake.close)

    def make_client(self, **option_kwargs) -> TcpClientSocket:
        defaults = dict(heartbeat_seconds=5.0, reconnect_max_seconds=0.2, connect_timeout_seconds=2.0)
        options  = ClientSocketOptions(**{**defaults, **option_kwargs})
        client   = TcpClientSocket("127.0.0.1", self.fake.port, self.received.append, TOKEN, "test-bot", options)
        self.addCleanup(client.disconnect)
        return client

    def test_server_heartbeat_value_sets_a_third_as_the_interval(self):
        client = self.make_client()
        start_connecting(client)
        conn = self.fake.accept()
        self.addCleanup(conn.close)
        self.assertEqual(RequestType.AUTH.value, PacketProtocol.receive_packet(conn)["type"])
        conn.sendall(PacketProtocol.encode({"type": ResponseType.AUTH_OK.value, "data": {"heartbeat_seconds": 0.6}}))
        started = time.monotonic()
        packet  = PacketProtocol.receive_packet(conn)
        self.assertEqual(RequestType.HEARTBEAT.value, packet["type"])
        self.assertLess(time.monotonic() - started, 1.0)       # options say 5 s; the server says 0.6 / 3

    def test_stalled_server_makes_send_fail_and_reconnect(self):
        client = self.make_client(send_timeout_seconds=0.3)
        start_connecting(client)
        conn = self.fake.accept()
        self.addCleanup(conn.close)
        PacketProtocol.receive_packet(conn)
        conn.sendall(PacketProtocol.encode({"type": ResponseType.AUTH_OK.value, "data": {}}))
        self.assertTrue(wait_for(lambda: client.is_connected))
        started = time.monotonic()
        for _ in range(60):                                   # the fake never reads
            client.send_packet({"type": "CHAT", "data": {"pad": "x" * 200_000}})
            if not client.is_connected:
                break
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertTrue(wait_for(lambda: not client.is_connected))
        second = self.fake.accept()                           # it reconnects
        self.addCleanup(second.close)
        self.assertEqual(RequestType.AUTH.value, PacketProtocol.receive_packet(second)["type"])

    def test_oversized_server_packet_drops_the_connection(self):
        client = self.make_client(max_packet_bytes=100)
        start_connecting(client)
        conn = self.fake.accept()
        self.addCleanup(conn.close)
        PacketProtocol.receive_packet(conn)
        conn.sendall(b"\x00\x00\x10\x00")
        self.assertEqual(b"", conn.recv(1) or b"")
        self.assertTrue(self.fake.accept())


if __name__ == "__main__":
    unittest.main()
