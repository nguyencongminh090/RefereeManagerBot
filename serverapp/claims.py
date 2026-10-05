"""Registry of which bot referees which table."""
import threading
from typing import Dict, Optional, Tuple

Addr = Tuple[str, int]


class ClaimRegistry:
    """Thread-safe map from table number to the bot connection refereeing it."""

    def __init__(self) -> None:
        self._lock   : threading.Lock   = threading.Lock()
        self._owners : Dict[int, Addr]  = {}

    def claim(self, table_no: int, addr: Addr) -> Addr:
        """Claims a table unless someone holds it.

        Args:
            table_no: Table number.
            addr: Connection asking for the table.

        Returns:
            The owner after the call: `addr` when the claim succeeded or was already held.
        """
        with self._lock:
            return self._owners.setdefault(table_no, addr)

    def take_over(self, table_no: int, old_addr: Addr, new_addr: Addr) -> bool:
        """Moves a table from one connection to another; False when `old_addr` is not the owner."""
        with self._lock:
            if self._owners.get(table_no) != old_addr:
                return False
            self._owners[table_no] = new_addr
            return True

    def release(self, table_no: int, addr: Addr) -> bool:
        """Releases a table; returns False when `addr` does not own it."""
        with self._lock:
            if self._owners.get(table_no) != addr:
                return False
            del self._owners[table_no]
            return True

    def release_all_for(self, addr: Addr) -> None:
        """Releases every table held by a connection (used on disconnect)."""
        with self._lock:
            for table_no in [t for t, owner in self._owners.items() if owner == addr]:
                del self._owners[table_no]

    def owner(self, table_no: int) -> Optional[Addr]:
        """Returns the connection that holds the table, or None."""
        with self._lock:
            return self._owners.get(table_no)

    def tables_of(self, addr: Addr) -> Tuple[int, ...]:
        """Returns the tables held by a connection, ascending."""
        with self._lock:
            return tuple(sorted(t for t, owner in self._owners.items() if owner == addr))
