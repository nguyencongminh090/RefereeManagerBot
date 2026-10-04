"""Length-prefixed JSON framing shared by the server and client sockets."""
import json
import socket
import struct
import sys
from typing import Optional

DEFAULT_MAX_PACKET_BYTES = 1_048_576
_MAX_FRAME_BYTES         = 0xFFFFFFFF
_HEADER                  = struct.Struct(">I")
_TIMEVAL                 = struct.Struct("@ll")


class PacketTooLargeError(ValueError):
    """Raised when a peer declares a packet body above the allowed size."""


def set_send_timeout(sock: socket.socket, seconds: float) -> None:
    """Makes sends on a blocking socket fail after `seconds` while reads stay blocking.

    Uses SO_SNDTIMEO (struct timeval on POSIX, milliseconds on Windows); a timed-out send
    raises OSError. Python's own settimeout is not used because it would also bound recv.

    Args:
        sock: Connected socket.
        seconds: Timeout, positive.
    """
    if sys.platform == "win32":
        value = int(seconds * 1000).to_bytes(4, "little")
    else:
        whole = int(seconds)
        value = _TIMEVAL.pack(whole, int((seconds - whole) * 1_000_000))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, value)


class PacketProtocol:
    """Encodes and decodes 4-byte big-endian length + UTF-8 JSON packets."""

    @staticmethod
    def encode(payload: dict) -> bytes:
        """Frames a payload.

        Raises:
            ValueError: If the encoded body does not fit the 4-byte length header.
        """
        data_bytes = json.dumps(payload).encode("utf-8")
        if len(data_bytes) > _MAX_FRAME_BYTES:
            raise ValueError(f"payload of {len(data_bytes)} bytes exceeds the frame limit")
        return _HEADER.pack(len(data_bytes)) + data_bytes

    @staticmethod
    def recv_exact(sock, n: int) -> Optional[bytes]:
        """Reads exactly n bytes; returns None if the peer closed first."""
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(min(n - len(data), 65536))
            if not packet:
                return None
            data.extend(packet)
        return bytes(data)

    @staticmethod
    def receive_packet(sock, max_bytes: int = DEFAULT_MAX_PACKET_BYTES) -> Optional[dict]:
        """Reads one packet; returns None when the connection is closed.

        Args:
            sock: Socket to read from.
            max_bytes: Largest body accepted; checked before the body is read.

        Raises:
            PacketTooLargeError: If the declared length exceeds max_bytes.
            ValueError: If the body is not valid JSON.
        """
        raw_header = PacketProtocol.recv_exact(sock, _HEADER.size)
        if raw_header is None:
            return None
        length = _HEADER.unpack(raw_header)[0]
        if length > max_bytes:
            raise PacketTooLargeError(
                f"declared packet of {length} bytes exceeds the {max_bytes} byte cap")
        raw_data = PacketProtocol.recv_exact(sock, length)
        if raw_data is None:
            return None
        return json.loads(raw_data.decode("utf-8"))
