"""Errors raised by the storage layer; callers catch `StorageError` to handle them all."""


class StorageError(Exception):
    """Base class for every error raised by the storage layer."""


class NotFoundError(StorageError):
    """A tournament, entrant, fixture, game or person does not exist."""


class DuplicateError(StorageError):
    """A record with the same unique name, nickname or pairing already exists."""


class ValidationError(StorageError):
    """The input is malformed or breaks a tournament rule."""


class HasGamesError(StorageError):
    """The record has games attached; pass cascade_games=True to delete them too."""


class DuplicateGameError(StorageError):
    """A game with the same `game_uid` is already recorded.

    Attributes:
        game_uid: The repeated idempotency key.
        game_id: Database id of the game stored earlier under that key.
    """

    def __init__(self, game_uid: str, game_id: int):
        super().__init__(f"game '{game_uid}' is already recorded (id {game_id})")
        self.game_uid = game_uid
        self.game_id  = game_id


class UnknownPlayerError(NotFoundError):
    """A nickname or participant id is not in the tournament."""


class PlayerInactiveError(StorageError):
    """A game involves a player who is no longer active in the tournament."""


class SameEntrantError(StorageError):
    """A game pairs two players of the same team."""


class MicroMatchFullError(StorageError):
    """The two teams have already played all games_per_pair games of their match."""
