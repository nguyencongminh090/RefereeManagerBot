"""The few changes the organizer may make from the dashboard, each validated at the boundary.

Every change goes through `TournamentStore`, so it is atomic and audit-logged under `ACTOR`.
"""
import math
from dataclasses import dataclass
from typing      import Any, Callable, Dict

from domain.types   import Scoring
from storage        import TournamentStore
from storage.errors import StorageError
from storage.pair_adjust import PairTarget

ACTOR = "dashboard"   # audit-log name of changes made from the page


class ActionError(ValueError):
    """A request the dashboard refuses; the message is safe to show to the organizer."""


@dataclass(frozen=True)
class ActionOptions:
    """What the actions need besides the store.

    Attributes:
        scoring: Points per game, used when a pair's score is corrected.
        on_changed: Called after every successful change (the server re-broadcasts standings).
    """
    scoring   : Scoring
    on_changed: Callable[[], None]


class DashboardActions:
    """Validates one action request and applies it to the tournament."""

    def __init__(self, store: TournamentStore, tournament_id: int,
                 options: ActionOptions) -> None:
        self._store         = store.with_actor(ACTOR)
        self._tournament_id = tournament_id
        self._options       = options
        self._handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {
            "void_game"           : self._void_game,
            "set_pair_score"      : self._set_pair_score,
            "record_sudden_death" : self._record_sudden_death,
            "delete_sudden_death" : self._delete_sudden_death,
        }

    def perform(self, request: Any) -> None:
        """Applies one action.

        Args:
            request: Decoded JSON; `{"action": <name>, ...fields}`.

        Raises:
            ActionError: If the request is malformed or the store rejects the change; nothing
                is changed then.
        """
        if not isinstance(request, dict):
            raise ActionError("the request must be a JSON object")
        handler = self._handlers.get(request.get("action"))
        if handler is None:
            raise ActionError(f"unknown action {request.get('action')!r}")
        try:
            handler(request)
        except StorageError as exc:
            raise ActionError(str(exc)) from exc
        self._options.on_changed()

    def _void_game(self, request: Dict[str, Any]) -> None:
        game_id, voided = _integer(request, "game_id"), _boolean(request, "voided")
        known = {g["id"] for g in self._store.list_games(self._tournament_id, True)}
        if game_id not in known:
            raise ActionError(f"game {game_id} is not in this tournament")
        self._store.void_game(game_id, voided)

    def _set_pair_score(self, request: Dict[str, Any]) -> None:
        target = PairTarget(_text(request, "p1"), _text(request, "p2"),
                            _points(request, "points1"), _points(request, "points2"))
        self._store.set_pair_score(self._tournament_id, target, self._options.scoring)

    def _record_sudden_death(self, request: Dict[str, Any]) -> None:
        self._store.record_sudden_death(self._tournament_id, _integer(request, "winner_id"),
                                        _integer(request, "loser_id"))

    def _delete_sudden_death(self, request: Dict[str, Any]) -> None:
        self._store.delete_sudden_death(self._tournament_id, _integer(request, "winner_id"),
                                        _integer(request, "loser_id"))


def _integer(request: Dict[str, Any], key: str) -> int:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActionError(f"'{key}' must be a whole number")
    return value


def _boolean(request: Dict[str, Any], key: str) -> bool:
    value = request.get(key)
    if not isinstance(value, bool):
        raise ActionError(f"'{key}' must be true or false")
    return value


def _text(request: Dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"'{key}' must be a non-empty text")
    return value.strip()


def _points(request: Dict[str, Any], key: str) -> float:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ActionError(f"'{key}' must be a number of points, 0 or more")
    return float(value)
