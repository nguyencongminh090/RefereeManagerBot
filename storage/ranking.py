"""Pure tournament ranking (TGWBC 2026 rules, section 3.4): no SQL, no I/O.

Entrants are ordered by points, then by the configured tie-break criteria in order. A criterion
is applied only to entrants still tied after the earlier ones:

* ``score_difference``: points scored minus points conceded over the whole tournament.
* ``head_to_head``: mini-table of the direct encounters between the still-tied entrants only
  (the rules say "result of the direct encounter"; for three or more entrants the points each
  one took from the others decide). Games against anyone else do not count.
* ``sudden_death``: recorded deciders between the still-tied entrants; each win is one point
  in the same kind of mini-table.

After a criterion splits a tie, the remaining sub-groups continue with the NEXT criterion (the
mini-tables are rebuilt for the smaller group). Entrants that no criterion separates share a
rank and are marked ``tied``; they keep name order so the output is stable.
"""
from dataclasses import dataclass
from typing      import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from domain.types import Scoring

SCORE_DIFFERENCE = "score_difference"
HEAD_TO_HEAD     = "head_to_head"
SUDDEN_DEATH     = "sudden_death"
CRITERIA         = (SCORE_DIFFERENCE, HEAD_TO_HEAD, SUDDEN_DEATH)

_PRECISION = 9   # points are multiples of 0.5 in practice; rounding only guards float noise


@dataclass(frozen=True)
class RankInput:
    """One entrant's totals. `points` is the ranking points (match or game points)."""
    entrant_id: int
    name      : str
    points    : float
    difference: float


@dataclass(frozen=True)
class Encounter:
    """Ranking points two entrants took from one direct encounter (a team match or a game set)."""
    entrant_a: int
    entrant_b: int
    points_a : float
    points_b : float


@dataclass(frozen=True)
class GameLine:
    """One valid game seen by entrant: `group` separates matches between the same two entrants."""
    entrant_a: int
    entrant_b: int
    group    : Optional[int]
    result_a : int   # domain.types.GameResult value from entrant_a's side: 1 win, 2 loss, 3 draw


@dataclass(frozen=True)
class Placement:
    """Final position of one entrant.

    Attributes:
        entrant_id: Database id of the entrant.
        rank: Place, 1 for the best; shared by entrants no criterion separated.
        tied: True if another entrant shares this rank.
    """
    entrant_id: int
    rank      : int
    tied      : bool


def encounters_from_games(games: Iterable[GameLine], scoring: Scoring,
                          match_points: Optional[Scoring]) -> List[Encounter]:
    """Groups games into encounters (same two entrants and group) and scores each one.

    Without `match_points` an encounter carries the game points. With it, the encounter is one
    team match: the side with more game points gets the match win, equal game points are a draw.
    """
    totals: Dict[Tuple[int, int, Optional[int]], List[float]] = {}
    for game in games:
        low, high = sorted((game.entrant_a, game.entrant_b))
        mine      = game.result_a if game.entrant_a == low else {1: 2, 2: 1, 3: 3}[game.result_a]
        points_lo = scoring.points(mine == 1, mine == 3, mine == 2)
        points_hi = scoring.points(mine == 2, mine == 3, mine == 1)
        entry     = totals.setdefault((low, high, game.group), [0.0, 0.0])
        entry[0] += points_lo
        entry[1] += points_hi
    return [_encounter((low, high), pts, match_points) for (low, high, _), pts in totals.items()]


def _encounter(pair: Tuple[int, int], points: List[float],
               match_points: Optional[Scoring]) -> Encounter:
    """Scores one encounter of the entrant pair (lower id first) from its summed game points."""
    low, high = pair
    if match_points is None:
        return Encounter(low, high, points[0], points[1])
    if points[0] > points[1]:
        return Encounter(low, high, match_points.win, match_points.loss)
    if points[0] < points[1]:
        return Encounter(low, high, match_points.loss, match_points.win)
    return Encounter(low, high, match_points.draw, match_points.draw)


def points_by_entrant(encounters: Iterable[Encounter]) -> Dict[int, float]:
    """Sums encounter points per entrant (match points when encounters are team matches)."""
    totals: Dict[int, float] = {}
    for enc in encounters:
        totals[enc.entrant_a] = totals.get(enc.entrant_a, 0.0) + enc.points_a
        totals[enc.entrant_b] = totals.get(enc.entrant_b, 0.0) + enc.points_b
    return totals


def rank_entrants(rows: Sequence[RankInput], encounters: Sequence[Encounter],
                  sudden_death: Sequence[Tuple[int, int]],
                  tiebreaks: Sequence[str]) -> List[Placement]:
    """Orders entrants, best first.

    Args:
        rows: Totals per entrant.
        encounters: Direct encounters, for `head_to_head`.
        sudden_death: (winner_id, loser_id) deciders, for `sudden_death`.
        tiebreaks: Criteria in order of use; a criterion not listed is not applied.

    Returns:
        One placement per row in final order; entrants sharing a rank have `tied` set.

    Raises:
        ValueError: If `tiebreaks` names an unknown criterion.
    """
    unknown = [name for name in tiebreaks if name not in CRITERIA]
    if unknown:
        raise ValueError(f"unknown tie-break criteria: {', '.join(unknown)}")
    keys = {SCORE_DIFFERENCE: lambda group: {r.entrant_id: r.difference for r in group},
            HEAD_TO_HEAD    : lambda group: _mini_table(group, _encounter_pairs(encounters)),
            SUDDEN_DEATH    : lambda group: _mini_table(group, _decider_pairs(sudden_death))}
    by_name = sorted(rows, key=lambda r: (r.name.lower(), r.entrant_id))
    groups = _split(by_name, lambda g: {r.entrant_id: r.points for r in g})
    for criterion in tiebreaks:
        groups = [part for group in groups for part in _refine(group, keys[criterion])]
    placements: List[Placement] = []
    for group in groups:
        rank = len(placements) + 1
        placements.extend(Placement(r.entrant_id, rank, len(group) > 1) for r in group)
    return placements


def _split(group: Sequence[RankInput], key_of: Callable) -> List[List[RankInput]]:
    """Splits into sub-groups of equal key, best key first; order inside a sub-group is kept."""
    keys: Dict[int, float] = key_of(group)
    buckets: Dict[float, List[RankInput]] = {}
    for row in group:
        buckets.setdefault(round(keys.get(row.entrant_id, 0.0), _PRECISION), []).append(row)
    return [buckets[k] for k in sorted(buckets, reverse=True)]


def _refine(group: List[RankInput], key_of: Callable) -> List[List[RankInput]]:
    return [group] if len(group) < 2 else _split(group, key_of)


def _encounter_pairs(encounters: Sequence[Encounter]) -> List[Tuple[int, int, float, float]]:
    return [(e.entrant_a, e.entrant_b, e.points_a, e.points_b) for e in encounters]


def _decider_pairs(sudden_death: Sequence[Tuple[int, int]]) -> List[Tuple[int, int, float, float]]:
    return [(winner, loser, 1.0, 0.0) for winner, loser in sudden_death]


def _mini_table(group: Sequence[RankInput],
                pairs: Sequence[Tuple[int, int, float, float]]) -> Dict[int, float]:
    """Points each member took from the other members only."""
    members = {row.entrant_id for row in group}
    table   = {entrant: 0.0 for entrant in members}
    for first, second, points_first, points_second in pairs:
        if first in members and second in members:
            table[first]  += points_first
            table[second] += points_second
    return table
