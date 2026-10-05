"""The server's way of talking to one client: replies, errors and closing the link."""
from typing import Any, Dict, Tuple

from network.messages import ResponseType
from network.ports    import IServerSocket

Addr = Tuple[str, int]


class ClientLink:
    """Sends protocol replies to a client through the server socket."""

    def __init__(self, socket: IServerSocket) -> None:
        self._socket = socket

    def reply(self, addr: Addr, response: ResponseType, **data: Any) -> None:
        """Sends a packet of the given type whose `data` is the keyword arguments."""
        self._socket.send_packet(addr, {'type': response.value, 'data': data})

    def error(self, addr: Addr, code: str, message: str, **extra: Any) -> None:
        """Sends an ERROR packet; `extra` fields (such as match_id) name what failed."""
        self.reply(addr, ResponseType.ERROR, code=code, message=message, **extra)

    def send(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Sends a ready-made packet."""
        self._socket.send_packet(addr, packet)

    def close(self, addr: Addr) -> None:
        """Closes the connection to the client."""
        self._socket.close_client(addr)
