"""Decides which lobby table a referee bot should enter next."""
import random
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set

from referee.page_parser import LobbyTable, eligible_tables


class LobbyWatcher:
    """Remembers the roster and the tables that must not be entered again.

    A table is skipped after another bot claimed it or after this bot finished refereeing it. The
    skip ends when the table disappears from the lobby, so a new game at the same number is
    considered again. The table is chosen at random among the eligible ones so that several idle bots
    rarely ask the server for the same table.
    """

    def __init__(self, choose: Callable[[Sequence[LobbyTable]], LobbyTable] = random.choice):
        """Creates a watcher with an empty roster.

        Args:
            choose: Picks one table from a non-empty list; replaced in tests.
        """
        self._choose = choose
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
        """Returns one of the tables worth entering, or None when there is none."""
        self._skipped &= {t.number for t in tables}
        candidates = [t for t in eligible_tables(tables, self._roster) if t.number not in self._skipped]
        return self._choose(candidates) if candidates else None
