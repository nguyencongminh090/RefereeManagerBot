"""SQLite adapter of the ITeamRepository port."""
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from domain.ports import IScoreObserver, ITeamRepository, MatchRecord
from domain.types import Scoring
from storage.models import GameRecord, RankingRules, Ref
from storage.tournament_store import TournamentStore

_NO_TEAMS_TEXT = "No teams registered yet."


@dataclass(frozen=True)
class RepositoryOptions:
    """How a repository scores and ranks.

    Attributes:
        scoring: Points per game.
        ranking: Tie-break rules; None uses the defaults.
        bot_name: Reporting bot recorded when a match has none.
    """
    scoring: Scoring
    ranking: Optional[RankingRules] = None
    bot_name: Optional[str] = None


class SqliteTeamRepository(ITeamRepository):
    """ITeamRepository on top of the tournament database, bound to one tournament.

    Works for both formats: in a team tournament "team" means a team; in an individual
    tournament it means a single player (the entrant), so the server code is the same.
    """

    def __init__(self, store: TournamentStore, tournament: Ref, options: RepositoryOptions):
        self._store = store
        self._tournament = store.get_tournament(tournament)["id"]
        self._scoring = options.scoring
        self._bot_name = options.bot_name
        self._ranking = options.ranking
        self._lock = threading.Lock()
        self._observers: Set[IScoreObserver] = set()

    def subscribe(self, observer: IScoreObserver) -> None:
        """Adds an observer of standings updates."""
        with self._lock:
            self._observers.add(observer)

    def unsubscribe(self, observer: IScoreObserver) -> None:
        """Removes an observer; unknown observers are ignored."""
        with self._lock:
            self._observers.discard(observer)

    def notify_all(self) -> None:
        """Sends the current snapshot to every observer."""
        text = self.snapshot()
        with self._lock:
            observers = list(self._observers)
        for observer in observers:
            observer.on_score_updated(text)

    def record_match(self, record: MatchRecord) -> int:
        """Stores a game atomically and notifies observers.

        Raises:
            DuplicateGameError: For a repeated match_id.
            StorageError: UnknownPlayerError, PlayerInactiveError, SameEntrantError or
                MicroMatchFullError; nothing is stored then.
        """
        game_id = self._store.record_game(self._tournament, GameRecord(
            record.p1_name, record.p2_name, record.p1_result, record.p2_result,
            game_uid=record.match_id, bot_name=record.bot_name or self._bot_name,
            table_no=record.table_no))
        self.notify_all()
        return game_id

    def pair_score(self, game_id: int) -> Dict[str, Any]:
        """Returns the running micro-match score of the two players of a stored game."""
        return self._store.pair_score_for_game(game_id, self._scoring)

    def roster(self) -> List[Dict[str, Any]]:
        """Returns every player of the tournament."""
        return self._store.list_players(self._tournament)

    def snapshot(self) -> str:
        """Returns the standings as "A : B = 1 : 0"."""
        rows = self._store.standings(self._tournament, self._scoring, self._ranking)
        if not rows:
            return _NO_TEAMS_TEXT
        return " : ".join(r.name for r in rows) + " = " + " : ".join(f"{r.points:g}" for r in rows)
