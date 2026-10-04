"""Decides which lobby table a referee bot should enter next."""
from typing import Dict, List, Mapping, Optional, Set

from referee.page_parser import LobbyTable, eligible_tables


class LobbyWatcher:
    """Remembers the roster and the tables that must not be entered again.

    A table is skipped after another bot claimed it or after this bot finished refereeing it. The
    skip ends when the table disappears from the lobby, so a new game at the same number is
    considered again.
    """

    def __init__(self):
        """Creates a watcher with an empty roster."""
        self._roster: Dict[str, str] = {}
        self._skipped: Set[int]      = set()

    @property
    def has_roster(self) -> bool:
        """Tells whether a roster has been received."""
        return bool(self._roster)

    def set_roster(self, roster: Mapping[str, str]) -> None:
        """Replaces the roster (nickname -> team name)."""
        self._roster = dict(roster)

    def skip(self, table_number: int) -> None:
        """Ignores this table until it leaves the lobby."""
        self._skipped.add(table_number)

    def pick(self, tables: List[LobbyTable]) -> Optional[LobbyTable]:
        """Returns the first table worth entering, or None."""
        self._skipped &= {t.number for t in tables}
        for table in eligible_tables(tables, self._roster):
            if table.number not in self._skipped:
                return table
        return None
