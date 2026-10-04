"""Validation of a MATCH_RESULT packet."""
from dataclasses import dataclass
from typing      import Any, Dict, Optional, Tuple

from domain.types import GameResult


@dataclass(frozen=True)
class MatchResultRequest:
    """A validated game result.

    Attributes:
        match_id: Client-chosen id that makes retries safe.
        players: The two nicknames.
        results: Result of each player.
        table_no: Table number, or None when the bot did not report it.
    """
    match_id: str
    players : Tuple[str, str]
    results : Tuple[GameResult, GameResult]
    table_no: Optional[int]


def match_id_of(packet: Dict[str, Any]) -> Optional[str]:
    """Returns `meta.match_id` when it is a string (used to label errors), else None."""
    match_id = (packet.get('meta') or {}).get('match_id')
    return match_id if isinstance(match_id, str) else None


def parse_match_result(packet: Dict[str, Any]) -> MatchResultRequest:
    """Validates the fields of a MATCH_RESULT packet.

    Raises:
        ValueError: If a field is missing or has the wrong shape.
    """
    data     = packet.get('data') or {}
    match_id = match_id_of(packet)
    players, scores = data.get('players'), data.get('scores')
    if not match_id:
        raise ValueError("meta.match_id is required (it makes retries safe)")
    is_pair = isinstance(players, (list, tuple)) and len(players) == 2
    if not (is_pair and all(isinstance(p, str) for p in players)):
        raise ValueError("data.players must be two names")
    if not (isinstance(scores, (list, tuple)) and len(scores) == 2):
        raise ValueError("data.scores must be two results")
    table_no = data.get('table_no')
    if table_no is not None and not isinstance(table_no, int):
        raise ValueError("data.table_no must be a number")
    return MatchResultRequest(match_id, (players[0], players[1]),
                              (GameResult(scores[0]), GameResult(scores[1])), table_no)
