"""Ports of the score store: what the server needs from a repository of results."""
from abc         import ABC, abstractmethod
from dataclasses import dataclass
from typing      import Any, Dict, List, Optional

from domain.types import GameResult


class IScoreObserver(ABC):
    """Receives the standings text each time a result is recorded."""

    @abstractmethod
    def on_score_updated(self, snapshot: str) -> None:
        """Called with the new standings snapshot."""


class IScoreSubject(ABC):
    """Publisher of standings updates."""

    @abstractmethod
    def subscribe(self, observer: IScoreObserver) -> None:
        """Adds an observer (idempotent)."""

    @abstractmethod
    def unsubscribe(self, observer: IScoreObserver) -> None:
        """Removes an observer; unknown observers are ignored."""

    @abstractmethod
    def notify_all(self) -> None:
        """Sends the current snapshot to every observer."""


@dataclass(frozen=True)
class MatchRecord:
    """One finished game, as reported by a bot.

    Attributes:
        p1_name: Nickname of the first player.
        p1_result: Result of the first player.
        p2_name: Nickname of the second player.
        p2_result: Result of the second player.
        match_id: Client-chosen id that makes retries safe; None generates one.
        table_no: Table number, or None when unknown.
        bot_name: Reporting bot; None uses the repository default.
    """
    p1_name  : str
    p1_result: GameResult
    p2_name  : str
    p2_result: GameResult
    match_id : Optional[str] = None
    table_no : Optional[int] = None
    bot_name : Optional[str] = None


class ITeamRepository(IScoreSubject):
    """The use-cases the server runs against the result store, bound to one tournament."""

    @abstractmethod
    def record_match(self, record: MatchRecord) -> int:
        """Stores a game atomically and notifies observers.

        Returns:
            The id of the stored game.

        Raises:
            StorageError: A subclass describing why the game was rejected; nothing is stored then.
        """

    @abstractmethod
    def pair_score(self, game_id: int) -> Dict[str, Any]:
        """Returns the running micro-match score of the two players of a stored game."""

    @abstractmethod
    def roster(self) -> List[Dict[str, Any]]:
        """Returns every player of the tournament (one dict per player)."""

    @abstractmethod
    def snapshot(self) -> str:
        """Returns the standings as one line of text."""
