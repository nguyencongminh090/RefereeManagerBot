from enum import Enum


class GameResult(Enum):
    WIN  = 1
    LOSS = 2
    DRAW = 3


class SessionState(Enum):
    PENDING     = 0
    IN_PROGRESS = 1
    BREAK_TIME  = 2
    COMPLETED   = 3
