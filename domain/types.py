"""Value types of the tournament domain."""
from dataclasses import dataclass
from enum        import Enum


class GameResult(Enum):
    """Outcome of one game for one player; the values are the wire codes."""

    WIN  = 1
    LOSS = 2
    DRAW = 3


@dataclass
class PlayerStats:
    """Win, loss and draw counts of one player.

    Attributes:
        wins: Games won.
        losses: Games lost.
        draws: Games drawn.
    """
    wins  : int = 0
    losses: int = 0
    draws : int = 0

    def __str__(self) -> str:
        """Formats the counts as `3W-1L-0D`."""
        return f"{self.wins}W-{self.losses}L-{self.draws}D"


@dataclass(frozen=True)
class Scoring:
    """Points per game. Always read from the configuration, never hard-coded.

    Attributes:
        win: Points for a win.
        draw: Points for a draw.
        loss: Points for a loss.
    """
    win : float
    draw: float
    loss: float

    def points(self, wins: int, draws: int, losses: int) -> float:
        """Returns the points of a player with these game counts."""
        return wins * self.win + draws * self.draw + losses * self.loss

    def points_against(self, wins: int, draws: int, losses: int) -> float:
        """Points the opponents scored in the same games."""
        return self.points(losses, draws, wins)
