"""The audience's snapshot: standings, results and live tables only, nothing organizer-side."""
import threading
import time
from dataclasses import dataclass
from datetime    import datetime, timezone
from typing      import Any, Callable, Dict, List, Optional, Protocol

from domain.ports   import IBotStatusSource
from domain.types   import Scoring
from storage        import TournamentStore
from storage.models import RankingRules
from webui.state    import result_text, standing_row


class SnapshotSource(Protocol):
    """Anything that builds a JSON-ready snapshot."""

    def snapshot(self) -> Dict[str, Any]:
        """Returns the JSON-ready state."""


@dataclass(frozen=True)
class PublicOptions:
    """How the public snapshot is scored and how much of the results it carries.

    Attributes:
        scoring: Points per game.
        ranking: Standings order; None uses the store defaults.
        recent_games: Rows in the recent-results list.
    """
    scoring     : Scoring
    ranking     : Optional[RankingRules]
    recent_games: int


class PublicState:
    """Builds the snapshot the audience page shows; reads only.

    The result rows hold only time, table, players and outcome. Bot names, addresses, game ids,
    voided games, validation problems and the audit log never leave the server.
    """

    def __init__(self, store: TournamentStore, tournament_id: int, bots: IBotStatusSource,
                 options: PublicOptions) -> None:
        self._store         = store
        self._tournament_id = tournament_id
        self._bots          = bots
        self._options       = options

    def snapshot(self) -> Dict[str, Any]:
        """Returns the current public state as plain dicts, lists, numbers and strings."""
        row = self._store.get_tournament(self._tournament_id)
        rows = self._store.standings(self._tournament_id, self._options.scoring,
                                     self._options.ranking)
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tournament"  : {"name": row["name"], "format": row["format"]},
            "standings"   : [standing_row(r) for r in rows],
            "recent_games": self._recent_games(),
            "live_tables" : self._live_tables(),
        }

    def _recent_games(self) -> List[Dict[str, Any]]:
        games = self._store.list_games(self._tournament_id, include_voided=False)
        newest_first = reversed(games[-self._options.recent_games:])
        return [{"at": g["played_at"], "table_no": g["table_no"], "p1": g["p1"], "p2": g["p2"],
                 "result": result_text(g["p1_result"])} for g in newest_first]

    def _live_tables(self) -> List[int]:
        return sorted({table for bot in self._bots.bots() for table in bot.tables})


class CachedSnapshot:
    """Reuses one snapshot for a short time so many viewers cost one database read.

    Thread-safe: the lock keeps simultaneous requests from each rebuilding the snapshot. A failed
    build is not cached.
    """

    def __init__(self, source: SnapshotSource, ttl_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._source   = source
        self._ttl      = ttl_seconds
        self._clock    = clock
        self._lock     = threading.Lock()
        self._built_at = 0.0
        self._value: Optional[Dict[str, Any]] = None

    def snapshot(self) -> Dict[str, Any]:
        """Returns the cached snapshot, rebuilding it once the window has passed."""
        with self._lock:
            now = self._clock()
            if self._value is None or now - self._built_at >= self._ttl:
                self._value    = self._source.snapshot()
                self._built_at = now
            return dict(self._value)
