"""Tunable limits for the TCP sockets, grouped so constructors stay short."""
from dataclasses import dataclass
from typing      import Optional


@dataclass(frozen=True)
class ServerSocketOptions:
    """Limits applied by TcpServerSocket.

    Attributes:
        max_clients: Connections accepted at the same time; further ones are closed at once.
        max_packet_bytes: Largest declared packet body accepted from a client, in bytes.
        send_timeout_seconds: Seconds a single send to one client may block before it is dropped.
    """
    max_clients: int = 64
    max_packet_bytes: int = 1_048_576
    send_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ClientSocketOptions:
    """Limits applied by TcpClientSocket.

    Attributes:
        heartbeat_seconds: Heartbeat interval in seconds, used until the server announces its own.
        reconnect_max_seconds: Upper bound of the reconnect backoff, in seconds.
        connect_timeout_seconds: Seconds to wait for a TCP connection and for the first
            authentication.
        send_timeout_seconds: Seconds a single send may block before the connection is dropped.
        max_packet_bytes: Largest declared packet body accepted from the server, in bytes.
        outbox_path: File holding unconfirmed match results across restarts; None keeps them in
            memory.
    """
    heartbeat_seconds: float = 15.0
    reconnect_max_seconds: float = 30.0
    connect_timeout_seconds: float = 10.0
    send_timeout_seconds: float = 5.0
    max_packet_bytes: int = 1_048_576
    outbox_path: Optional[str] = None
