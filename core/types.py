from enum        import Enum
from dataclasses import dataclass


class GameResult(Enum):
    WIN  = 1
    LOSS = 2
    DRAW = 3


class SessionState(Enum):
    IN_PROGRESS = 1
    BREAK_TIME  = 2
    COMPLETED   = 3


@dataclass
class PlayerStats:
    wins  : int = 0
    losses: int = 0
    draws : int = 0

    def total_score(self) -> float:
        return self.wins + self.draws * 0.5

    def __add__(self, other: 'PlayerStats') -> 'PlayerStats':
        return PlayerStats(
            wins   = self.wins   + other.wins,
            losses = self.losses + other.losses,
            draws  = self.draws  + other.draws
        )

    def __str__(self) -> str:
        return f"{self.wins}W-{self.losses}L-{self.draws}D"


class RequestType(Enum):
    AUTH         = 1
    MATCH_RESULT = 2
    SCORE_QUERY  = 3
    HEARTBEAT    = 4


class ResponseType(Enum):
    AUTH_OK    = 101
    SCORE_DATA = 102
    ERROR      = 103
    BROADCAST  = 104