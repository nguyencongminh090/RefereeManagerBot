"""Sets the micro-match score of a pair by recording or voiding corrective games."""
import sqlite3
import uuid
from dataclasses import dataclass
from typing      import List, Optional, Tuple

from domain.types      import GameResult, Scoring
from storage.audit     import AuditEntry, Auditor
from storage.database  import Database
from storage.errors    import MicroMatchFullError, ValidationError
from storage.games     import GameLedger
from storage.lookups   import participant_row, tournament_row
from storage.models    import GameRecord, Ref

# Points are compared in thousandths so that 0.5 + 0.5 == 1.0 holds without float noise.
_UNITS_PER_POINT = 1000
_OPPOSITE        = {GameResult.WIN: GameResult.LOSS, GameResult.LOSS: GameResult.WIN,
                    GameResult.DRAW: GameResult.DRAW}


@dataclass(frozen=True)
class PairTarget:
    """The score a pair should have after the adjustment.

    Attributes:
        nickname_a: First player.
        nickname_b: Second player.
        points_a: Game points of the first player in the pair's micro-match.
        points_b: Game points of the second player.
    """
    nickname_a: str
    nickname_b: str
    points_a  : float
    points_b  : float


@dataclass(frozen=True)
class PairGame:
    """A counted game of the pair, seen from the first player.

    Attributes:
        game_id: Database id.
        result_a: 1 win, 2 loss or 3 draw of the first player.
    """
    game_id : int
    result_a: int


@dataclass(frozen=True)
class Additions:
    """The corrective games that close the gap to the target."""
    wins_a: int
    wins_b: int
    draws : int

    @property
    def total(self) -> int:
        """Number of games to record."""
        return self.wins_a + self.wins_b + self.draws


def _units(points: float) -> int:
    return round(points * _UNITS_PER_POINT)


def _game_units(scoring: Scoring, result: GameResult) -> Tuple[int, int]:
    return (_units(scoring.points_for(result)), _units(scoring.points_for(_OPPOSITE[result])))


def pair_units(games: List[PairGame], scoring: Scoring) -> Tuple[int, int]:
    """Returns the points of both players over these games, in thousandths of a point."""
    first = second = 0
    for game in games:
        a, b = _game_units(scoring, GameResult(game.result_a))
        first, second = first + a, second + b
    return first, second


def games_to_void(games: List[PairGame], target: Tuple[int, int], scoring: Scoring) -> List[int]:
    """Picks the newest games to void until neither player is above the target (thousandths)."""
    first, second = pair_units(games, scoring)
    chosen: List[int] = []
    for game in reversed(games):
        if first <= target[0] and second <= target[1]:
            break
        a, b = _game_units(scoring, GameResult(game.result_a))
        first, second = first - a, second - b
        chosen.append(game.game_id)
    return chosen


def plan_additions(gap_a: int, gap_b: int, scoring: Scoring, max_games: int) -> Optional[Additions]:
    """Finds the fewest games that give the first player `gap_a` and the second `gap_b`.

    Args:
        gap_a: Points still missing for the first player, in thousandths.
        gap_b: Points still missing for the second player.
        scoring: Points per game.
        max_games: Most games that may be added.

    Returns:
        The wins and draws to record, or None when no combination fits.
    """
    win, draw, loss = (_units(p) for p in (scoring.win, scoring.draw, scoring.loss))
    determinant = win * win - loss * loss
    if determinant == 0:
        return None
    best: Optional[Additions] = None
    for draws in range(max_games + 1):
        rest_a, rest_b = gap_a - draws * draw, gap_b - draws * draw
        wins_a, left_a = divmod(rest_a * win - rest_b * loss, determinant)
        wins_b, left_b = divmod(rest_b * win - rest_a * loss, determinant)
        if left_a or left_b or wins_a < 0 or wins_b < 0:
            continue
        candidate = Additions(wins_a, wins_b, draws)
        if candidate.total <= max_games and (best is None or candidate.total < best.total):
            best = candidate
    return best


@dataclass(frozen=True)
class _Request:
    """What one adjustment works on, so that the helpers need few parameters."""
    tournament_id: int
    target       : PairTarget
    scoring      : Scoring
    limit        : Optional[int]
    fixture      : Optional[int]

    @property
    def goal(self) -> Tuple[int, int]:
        """The target in thousandths of a point."""
        return _units(self.target.points_a), _units(self.target.points_b)


class PairAdjuster:
    """Brings the counted games of one pair to a target score, all in one transaction."""

    def __init__(self, db: Database, audit: Auditor, ledger: GameLedger):
        self._db     = db
        self._audit  = audit
        self._ledger = ledger

    def set_score(self, tournament: Ref, target: PairTarget, scoring: Scoring) -> int:
        """Voids and records games so that the pair's counted score equals the target.

        Recording the same target again changes nothing.

        Returns:
            The id of the newest game of the pair; read the new score from it.

        Raises:
            ValidationError: If the pair has no games and the target is 0-0, or no combination
                of games gives exactly the target.
            MicroMatchFullError: If the target needs more games than the pair may play.
            UnknownPlayerError: If a nickname is not in the tournament.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            players = (participant_row(cx, t["id"], target.nickname_a),
                       participant_row(cx, t["id"], target.nickname_b))
            fixture = self._ledger.pair_fixture(cx, t["id"], players)
            request = _Request(t["id"], target, scoring, t["games_per_pair"], fixture)
            rows = self._pair_rows(cx, request, players)
            kept = [PairGame(r["id"], r["result_a"]) for r in rows if not r["voided"]]
            voids = games_to_void(kept, request.goal, scoring)
            remaining = [g for g in kept if g.game_id not in voids]
            additions = self._plan(remaining, request)
            for game_id in voids:
                self._ledger.void(game_id, True)
            last = self._record(additions, request) or (rows[-1]["id"] if rows else None)
            if last is None:
                raise ValidationError(
                    f"'{target.nickname_a}' and '{target.nickname_b}' have no games to set")
            self._audit.record(cx, AuditEntry("set_score", "pair", last, {
                "players": [target.nickname_a, target.nickname_b],
                "target": [target.points_a, target.points_b],
                "voided": voids, "added": additions.total}))
            return last

    @staticmethod
    def _pair_rows(cx: sqlite3.Connection, request: _Request,
                   players: Tuple[sqlite3.Row, sqlite3.Row]) -> List[sqlite3.Row]:
        """The games of the pair in this fixture, oldest first, results seen from the first."""
        a, b = players[0]["id"], players[1]["id"]
        return cx.execute(
            "SELECT id, voided, CASE WHEN p1_id = ? THEN p1_result ELSE p2_result END AS result_a"
            " FROM games WHERE tournament_id = ? AND fixture_id IS ?"
            " AND ((p1_id = ? AND p2_id = ?) OR (p1_id = ? AND p2_id = ?)) ORDER BY id",
            (a, request.tournament_id, request.fixture, a, b, b, a)).fetchall()

    @staticmethod
    def _plan(remaining: List[PairGame], request: _Request) -> Additions:
        have = pair_units(remaining, request.scoring)
        gap = (request.goal[0] - have[0], request.goal[1] - have[1])
        if request.limit:
            room = request.limit - len(remaining)
        else:
            room = _search_bound(gap, request.scoring)
        plan = plan_additions(gap[0], gap[1], request.scoring, max(room, 0))
        if plan is None:
            target = request.target
            if request.limit and plan_additions(gap[0], gap[1], request.scoring,
                                                _search_bound(gap, request.scoring)):
                raise MicroMatchFullError(
                    f"{target.points_a:g}-{target.points_b:g} needs more than the "
                    f"{request.limit} games a pair may play")
            raise ValidationError(
                f"no combination of games gives {target.points_a:g}-{target.points_b:g} for "
                f"'{target.nickname_a}' and '{target.nickname_b}' within the games a pair may play")
        return plan

    def _record(self, additions: Additions, request: _Request) -> Optional[int]:
        """Records the corrective games; returns the id of the last one, or None."""
        results = ([(GameResult.WIN, GameResult.LOSS)] * additions.wins_a
                   + [(GameResult.LOSS, GameResult.WIN)] * additions.wins_b
                   + [(GameResult.DRAW, GameResult.DRAW)] * additions.draws)
        game_id = None
        for first, second in results:
            game_id = self._ledger.record(request.tournament_id, GameRecord(
                request.target.nickname_a, request.target.nickname_b, first, second,
                game_uid=str(uuid.uuid4()), bot_name=self._audit.actor,
                fixture_id=request.fixture))
        return game_id


def _search_bound(gap: Tuple[int, int], scoring: Scoring) -> int:
    """Games needed at most when the pair has no game limit: the biggest gap in smallest steps."""
    smallest = min(_units(p) for p in (scoring.win, scoring.draw) if p > 0)
    return -(-max(gap) // smallest) + 1
