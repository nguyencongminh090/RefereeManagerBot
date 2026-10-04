"""Validation of the packets that carry a game result or a score correction."""
import math
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


@dataclass(frozen=True)
class SetScoreRequest:
    """A validated request to set the score of a pair.

    Attributes:
        sender: PlayOK nickname of the person who typed the command.
        players: The two nicknames.
        points: Game points wanted for each player.
    """
    sender: str
    players: Tuple[str, str]
    points : Tuple[float, float]


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


def _is_points(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(value) and value >= 0


def parse_set_score(packet: Dict[str, Any]) -> SetScoreRequest:
    """Validates the fields of a SET_SCORE packet.

    Raises:
        ValueError: If a field is missing or has the wrong shape.
    """
    data = packet.get('data') or {}
    sender, players, scores = data.get('sender'), data.get('players'), data.get('scores')
    if not (isinstance(sender, str) and sender.strip()):
        raise ValueError("data.sender must be the nickname of the person who gave the command")
    is_pair = isinstance(players, (list, tuple)) and len(players) == 2
    if not (is_pair and all(isinstance(p, str) for p in players)):
        raise ValueError("data.players must be two names")
    if not (isinstance(scores, (list, tuple)) and len(scores) == 2 and all(map(_is_points, scores))):
        raise ValueError("data.scores must be two numbers, zero or more")
    return SetScoreRequest(sender, (players[0], players[1]), (float(scores[0]), float(scores[1])))
