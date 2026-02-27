from enum import Enum


class GameResult(Enum):
    WIN  = 1
    LOSS = 2
    DRAW = 3


class SessionState(Enum):
    IN_PROGRESS = 1
    BREAK_TIME  = 2
    COMPLETED   = 3
    