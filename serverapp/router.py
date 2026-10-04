"""Maps packet types to handler functions."""
from typing import Any, Callable, Dict, Tuple

Addr    = Tuple[str, int]
Handler = Callable[[Addr, Dict[str, Any]], None]


class PacketRouter:
    """Dispatches a decoded packet to the handler registered for its `type`."""

    def __init__(self, handlers: Dict[int, Handler]) -> None:
        self._handlers = dict(handlers)

    def route(self, addr: Addr, packet: Any) -> bool:
        """Calls the handler for the packet.

        Args:
            addr: Sender.
            packet: Decoded payload; anything that is not a dict has no type.

        Returns:
            False when no handler is registered for the packet type.
        """
        packet_type = packet.get('type') if isinstance(packet, dict) else None
        handler     = self._handlers.get(packet_type)
        if handler is None:
            return False
        handler(addr, packet)
        return True
