"""Ports of the network layer: what the client and the server need from a socket."""
from abc    import ABC, abstractmethod
from typing import Any, Dict, Tuple


class IClientSocket(ABC):
    """Client side of the connection to the referee server."""

    @abstractmethod
    def connect(self) -> None:
        """Opens the connection and authenticates; raises ConnectionError on failure."""

    @abstractmethod
    def disconnect(self) -> None:
        """Closes the connection and stops the background threads."""

    @abstractmethod
    def send_packet(self, payload: Dict[str, Any]) -> None:
        """Sends one JSON-serializable packet to the server."""


class IServerSocket(ABC):
    """Server side: accepts clients and talks to them by address."""

    @abstractmethod
    def start_listening(self) -> None:
        """Binds the port and starts accepting clients in the background."""

    @abstractmethod
    def stop(self) -> None:
        """Stops accepting and closes every client connection."""

    @abstractmethod
    def send_packet(self, client_addr: Tuple[str, int], payload: Dict[str, Any]) -> None:
        """Sends a packet to one client; an unknown address is ignored."""

    @abstractmethod
    def broadcast(self, payload: Dict[str, Any]) -> None:
        """Sends a packet to every connected client."""

    @abstractmethod
    def close_client(self, client_addr: Tuple[str, int]) -> None:
        """Closes one client connection; an unknown address is ignored."""
