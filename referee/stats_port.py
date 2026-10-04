"""Port of the PlayOK statistics pages: what `!sync` needs to know about past games."""
from abc         import ABC, abstractmethod
from dataclasses import dataclass
from datetime    import datetime
from typing      import List, Optional

from domain.types import GameResult


class StatsError(Exception):
    """The statistics page could not be fetched or understood; usually transient, so retry later."""


@dataclass(frozen=True)
class StatGame:
    """One row of a player's games list.

    Attributes:
        game_id: PlayOK game id such as `gm172668589`; unique and growing.
        played_at: Start time of the game, time zone aware.
        black: Nickname of the first player.
        white: Nickname of the second player.
        result: Outcome from the point of view of the profile owner.
    """
    game_id  : str
    played_at: datetime
    black    : str
    white    : str
    result   : GameResult


@dataclass(frozen=True)
class PairTotals:
    """The all-time record of a profile owner against one opponent (the `wn-ls-dr` column).

    Attributes:
        wins: Games the profile owner won.
        losses: Games the profile owner lost.
        draws: Drawn games.
    """
    wins  : int
    losses: int
    draws : int

    @property
    def games(self) -> int:
        """Number of games in the record."""
        return self.wins + self.losses + self.draws


class IStatsSource(ABC):
    """Read access to the games two players have played against each other."""

    @abstractmethod
    def pair_games(self, player: str, opponent: str) -> List[StatGame]:
        """Returns the recent games of `player` against `opponent`, newest first.

        Raises:
            StatsError: If the page cannot be fetched or understood.
        """

    @abstractmethod
    def pair_totals(self, player: str, opponent: str) -> Optional[PairTotals]:
        """Returns the all-time record of `player` against `opponent`, or None if they never met.

        Raises:
            StatsError: If the page cannot be fetched or understood.
        """
