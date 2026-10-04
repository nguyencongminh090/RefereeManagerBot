import socket
import struct
import threading
import time
import unittest
from unittest import mock

from network.options       import ServerSocketOptions
from network.protocol      import PacketProtocol
from network.server_socket import TcpServerSocket

TIMEOUT = 3.0


def wait_for(condition, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class ServerSocketTests(unittest.TestCase):
    def setUp(self):
        self.received     = []
        self.connected    = []
        self.disconnected = []

    def start(self, **option_kwargs) -> TcpServerSocket:
        server = TcpServerSocket("127.0.0.1", 0, lambda addr, pkt: self.received.append((addr, pkt)),
                                 self.connected.append, self.disconnected.append,
                                 options=ServerSocketOptions(**option_kwargs))
        server.start_listening()
        self.addCleanup(server.stop)
        return server

    def dial(self, server: TcpServerSocket, rcvbuf: int = 0) -> socket.socket:
        sock = socket.socket()
        if rcvbuf:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        sock.settimeout(TIMEOUT)
        sock.connect(("127.0.0.1", server.port))
        self.addCleanup(sock.close)
        return sock

    @staticmethod
    def is_closed_by_peer(sock: socket.socket) -> bool:
        try:
            return sock.recv(1) == b""
        except (ConnectionError, socket.timeout):
            return False

    def test_connections_above_max_clients_are_refused(self):
        server = self.start(max_clients=1)
        first  = self.dial(server)
        self.assertTrue(wait_for(lambda: len(self.connected) == 1))
        second = self.dial(server)
        self.assertTrue(self.is_closed_by_peer(second))
        self.assertEqual(1, len(self.connected))
        first.sendall(PacketProtocol.encode({"type": "PING"}))
        self.assertTrue(wait_for(lambda: self.received))

    def test_slot_is_freed_when_a_client_leaves(self):
        server = self.start(max_clients=1)
        first  = self.dial(server)
        self.assertTrue(wait_for(lambda: len(self.connected) == 1))
        first.close()
        self.assertTrue(wait_for(lambda: len(self.disconnected) == 1))
        self.dial(server)
        self.assertTrue(wait_for(lambda: len(self.connected) == 2))

    def test_oversized_packet_closes_only_that_client(self):
        server = self.start(max_packet_bytes=100)
        good   = self.dial(server)
        bad    = self.dial(server)
        self.assertTrue(wait_for(lambda: len(self.connected) == 2))
        bad.sendall(struct.pack(">I", 101))
        self.assertTrue(self.is_closed_by_peer(bad))
        good.sendall(PacketProtocol.encode({"type": "OK"}))
        self.assertTrue(wait_for(lambda: self.received))
        self.assertEqual(1, len(self.disconnected))

    def test_stalled_client_does_not_block_a_broadcast(self):
        server  = self.start(send_timeout_seconds=0.3)
        stalled = self.dial(server, rcvbuf=4096)          # never reads
        healthy = self.dial(server)
        self.assertTrue(wait_for(lambda: len(self.connected) == 2))
        payload, count, got = {"blob": "x" * 400_000}, 30, []

        def read_all():
            for _ in range(count):
                got.append(PacketProtocol.receive_packet(healthy))

        reader = threading.Thread(target=read_all, daemon=True)
        reader.start()
        started = time.monotonic()
        for _ in range(count):
            server.broadcast(payload)
        reader.join(TIMEOUT)
        self.assertFalse(reader.is_alive(), "healthy client starved")
        self.assertEqual(count, len(got))
        self.assertLess(time.monotonic() - started, TIMEOUT)
        self.assertTrue(wait_for(lambda: len(self.disconnected) == 1))
        self.assertIsNotNone(stalled)

    def test_concurrent_sends_to_one_client_keep_frames_intact(self):
        server = self.start()
        sock   = self.dial(server)
        self.assertTrue(wait_for(lambda: self.connected))
        addr, threads, per_thread = self.connected[0], [], 20
        for n in range(8):
            payload = {"sender": n, "pad": str(n) * 200_000}
            threads.append(threading.Thread(target=lambda p=payload: [server.send_packet(addr, p)
                                                                      for _ in range(per_thread)]))
        for thread in threads:
            thread.start()
        seen = [PacketProtocol.receive_packet(sock) for _ in range(8 * per_thread)]
        for thread in threads:
            thread.join()
        for packet in seen:
            self.assertEqual(str(packet["sender"]) * 200_000, packet["pad"])

    def test_failing_accept_is_logged_and_does_not_spin(self):
        server = TcpServerSocket("127.0.0.1", 0, lambda a, p: None)
        fake   = mock.Mock()
        fake.accept.side_effect = OSError("too many open files")
        server._server_sock = fake
        server._running     = True
        thread = threading.Thread(target=server._run_acceptor_loop, daemon=True)
        with self.assertLogs("network.server_socket", level="WARNING") as logs:
            thread.start()
            time.sleep(0.5)
            server._running = False
            thread.join(2.0)
        self.assertLess(fake.accept.call_count, 20)
        self.assertEqual(1, len(logs.records))


if __name__ == "__main__":
    unittest.main()
