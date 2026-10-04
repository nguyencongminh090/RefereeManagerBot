"""Games: recording, voiding, deleting and listing finished games."""
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from domain.types import GameResult
from storage.audit import AuditEntry, Auditor
from storage.database import Database
from storage.errors import (DuplicateGameError, MicroMatchFullError, NotFoundError,
                            PlayerInactiveError, SameEntrantError, ValidationError)
from storage.lookups import participant_row, tournament_row
from storage.models import GameRecord, Ref

_WIN, _LOSS, _DRAW = 1, 2, 3
_CONSISTENT_RESULTS = ({_WIN, _LOSS}, {_DRAW})


@dataclass(frozen=True)
class _FixtureLookup:
    """What `_resolve_fixture` needs: the tournament, the two players and a requested fixture id."""
    tournament_id: int
    players: Tuple[sqlite3.Row, sqlite3.Row]
    fixture_id: Optional[int]


@dataclass(frozen=True)
class _PairQuota:
    """What `_check_pair_room` needs: the games-per-pair limit, the two players and the fixture."""
    limit: int
    players: Tuple[sqlite3.Row, sqlite3.Row]
    fixture_id: Optional[int]


def _result_value(result: Union[int, GameResult]) -> int:
    """Normalises a result to 1 (win), 2 (loss) or 3 (draw); raises ValidationError otherwise."""
    value = result.value if isinstance(result, GameResult) else int(result)
    if value not in (_WIN, _LOSS, _DRAW):
        raise ValidationError(f"result must be 1 (win), 2 (loss) or 3 (draw), got {value}")
    return value


def _check_players(a: sqlite3.Row, b: sqlite3.Row, force: bool) -> None:
    """Rejects a same-team pairing; unless `force`, also rejects inactive players."""
    if a["entrant_id"] == b["entrant_id"]:
        raise SameEntrantError(f"'{a['nickname']}' and '{b['nickname']}' are on the same team")
    if force:
        return
    for p in (a, b):
        if not p["active"]:
            raise PlayerInactiveError(f"'{p['nickname']}' is no longer active in this tournament")


class GameLedger:
    """Stores finished games and keeps them reversible (void) or removable (delete)."""

    def __init__(self, db: Database, audit: Auditor):
        self._db = db
        self._audit = audit

    def record(self, tournament: Ref, game: GameRecord) -> int:
        """Records one finished game atomically and returns its id.

        Raises:
            ValidationError: If the results are invalid or inconsistent.
            UnknownPlayerError: If a nickname is not in the tournament.
            DuplicateGameError: If `game_uid` was recorded before.
            SameEntrantError: If both players are on the same team.
            PlayerInactiveError: If a player is inactive (skipped with `force`).
            MicroMatchFullError: If the pair already played games_per_pair games (skipped
                with `force`).
        """
        r1, r2 = _result_value(game.p1_result), _result_value(game.p2_result)
        if {r1, r2} not in _CONSISTENT_RESULTS:
            raise ValidationError(f"inconsistent results: {r1} and {r2}")
        game_uid = game.game_uid or str(uuid.uuid4())
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            a = participant_row(cx, t["id"], game.p1_nickname)
            b = participant_row(cx, t["id"], game.p2_nickname)
            self._check_new_uid(cx, game_uid)
            _check_players(a, b, game.force)
            fixture_id = self._resolve_fixture(
                cx, _FixtureLookup(t["id"], (a, b), game.fixture_id))
            if t["games_per_pair"] and not game.force:
                self._check_pair_room(cx, _PairQuota(t["games_per_pair"], (a, b), fixture_id))
            cur = cx.execute(
                "INSERT INTO games (game_uid, tournament_id, fixture_id, p1_id, p2_id,"
                " p1_result, p2_result, bot_name, table_no) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (game_uid, t["id"], fixture_id, a["id"], b["id"], r1, r2,
                 game.bot_name, game.table_no))
            self._audit.record(cx, AuditEntry(
                "record", "game", cur.lastrowid,
                {"p1": a["nickname"], "p2": b["nickname"], "results": [r1, r2],
                 "table": game.table_no}))
            return cur.lastrowid

    def pair_fixture(self, cx: sqlite3.Connection, tournament_id: int,
                     players: Tuple[sqlite3.Row, sqlite3.Row]) -> Optional[int]:
        """Returns the fixture a new game of these two players would join, or None."""
        return self._resolve_fixture(cx, _FixtureLookup(tournament_id, players, None))

    def _check_new_uid(self, cx: sqlite3.Connection, game_uid: str) -> None:
        existing = cx.execute("SELECT id FROM games WHERE game_uid = ?", (game_uid,)).fetchone()
        if existing:
            raise DuplicateGameError(game_uid, existing["id"])

    def _resolve_fixture(self, cx: sqlite3.Connection, lookup: "_FixtureLookup") -> Optional[int]:
        """Validates a given fixture id, or picks the earliest open fixture of the two teams."""
        if lookup.fixture_id is not None:
            if cx.execute("SELECT 1 FROM fixtures WHERE id = ? AND tournament_id = ?",
                          (lookup.fixture_id, lookup.tournament_id)).fetchone() is None:
                raise NotFoundError(f"fixture {lookup.fixture_id} not found in this tournament")
            return lookup.fixture_id
        a, b = lookup.players[0]["entrant_id"], lookup.players[1]["entrant_id"]
        row = cx.execute(
            "SELECT id FROM fixtures WHERE tournament_id = ? AND status = 'open'"
            " AND ((entrant_a_id = ? AND entrant_b_id = ?)"
            " OR (entrant_a_id = ? AND entrant_b_id = ?))"
            " ORDER BY round_no LIMIT 1", (lookup.tournament_id, a, b, b, a)).fetchone()
        return row["id"] if row else None

    def _check_pair_room(self, cx: sqlite3.Connection, quota: "_PairQuota") -> None:
        """Raises MicroMatchFullError if the pair already played `quota.limit` games."""
        a, b = quota.players
        # "fixture_id IS ?" also matches NULL, so games without a fixture count as one group.
        played = cx.execute(
            "SELECT COUNT(*) FROM games WHERE voided = 0 AND fixture_id IS ? AND"
            " ((p1_id = ? AND p2_id = ?) OR (p1_id = ? AND p2_id = ?))",
            (quota.fixture_id, a["id"], b["id"], b["id"], a["id"])).fetchone()[0]
        if played >= quota.limit:
            raise MicroMatchFullError(f"'{a['nickname']}' and '{b['nickname']}' already played"
                                      f" {played} of {quota.limit} games")

    def void(self, game_id: int, voided: bool) -> None:
        """Takes a game out of the standings without deleting it (reversible with voided=False).

        Raises:
            NotFoundError: If the game does not exist.
        """
        with self._db.transaction() as cx:
            updated = cx.execute("UPDATE games SET voided = ? WHERE id = ?",
                                 (int(voided), game_id)).rowcount
            if updated == 0:
                raise NotFoundError(f"game {game_id} not found")
            self._audit.record(cx, AuditEntry("void" if voided else "unvoid", "game", game_id))

    def delete(self, game_id: int) -> None:
        """Deletes a game for good; the audit log keeps its row.

        Raises:
            NotFoundError: If the game does not exist.
        """
        with self._db.transaction() as cx:
            row = cx.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"game {game_id} not found")
            cx.execute("DELETE FROM games WHERE id = ?", (game_id,))
            self._audit.record(cx, AuditEntry("delete", "game", game_id, dict(row)))

    def list_games(self, tournament: Ref, include_voided: bool) -> List[Dict[str, Any]]:
        """Returns the games of a tournament oldest first, with both nicknames."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            sql = ("SELECT g.id, g.game_uid, g.fixture_id, g.played_at, g.table_no, g.bot_name,"
                   " g.voided, a.nickname AS p1, b.nickname AS p2, g.p1_result, g.p2_result"
                   " FROM games g"
                   " JOIN participants a ON a.id = g.p1_id JOIN participants b ON b.id = g.p2_id"
                   " WHERE g.tournament_id = ?")
            if not include_voided:
                sql += " AND g.voided = 0"
            return [dict(r) for r in cx.execute(sql + " ORDER BY g.id", (t["id"],))]
