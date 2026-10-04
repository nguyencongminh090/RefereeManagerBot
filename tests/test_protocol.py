import json
import struct
import unittest

from network.protocol import PacketProtocol, PacketTooLargeError


class FakeSocket:
    """Returns the queued chunks one recv at a time, then b'' (closed)."""

    def __init__(self, chunks):
        self.chunks   = list(chunks)
        self.requests = []

    def recv(self, n):
        self.requests.append(n)
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > n:
            self.chunks.insert(0, chunk[n:])
            chunk = chunk[:n]
        return chunk


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        frame = PacketProtocol.encode({"a": 1})
        self.assertEqual({"a": 1}, PacketProtocol.receive_packet(FakeSocket([frame])))

    def test_partial_reads_are_reassembled(self):
        frame = PacketProtocol.encode({"text": "xin chào"})
        sock  = FakeSocket([bytes([b]) for b in frame])
        self.assertEqual({"text": "xin chào"}, PacketProtocol.receive_packet(sock))

    def test_closed_connection_returns_none(self):
        self.assertIsNone(PacketProtocol.receive_packet(FakeSocket([])))
        self.assertIsNone(PacketProtocol.receive_packet(FakeSocket([b"\x00\x00"])))
        frame = PacketProtocol.encode({"a": 1})
        self.assertIsNone(PacketProtocol.receive_packet(FakeSocket([frame[:-2]])))

    def test_oversized_declaration_is_rejected_before_reading_body(self):
        sock = FakeSocket([struct.pack(">I", 5000) + b"x" * 10])
        with self.assertRaises(PacketTooLargeError):
            PacketProtocol.receive_packet(sock, max_bytes=1000)
        self.assertEqual([4], sock.requests)

    def test_packet_at_the_cap_is_accepted(self):
        body  = json.dumps({"k": "v"}).encode()
        frame = struct.pack(">I", len(body)) + body
        self.assertEqual({"k": "v"}, PacketProtocol.receive_packet(FakeSocket([frame]), max_bytes=len(body)))

    def test_too_large_error_is_a_value_error(self):
        self.assertTrue(issubclass(PacketTooLargeError, ValueError))


if __name__ == "__main__":
    unittest.main()
