"""Sudden-death deciders between tied entrants (tie-break 3)."""
import sqlite3
from typing import Any, Dict, List, Tuple

from storage.audit import AuditEntry, Auditor
from storage.database import Database
from storage.errors import DuplicateError, NotFoundError, ValidationError
from storage.lookups import entrant_row, tournament_row
from storage.models import Ref


def deciders_of(cx: sqlite3.Connection, tournament_id: int) -> List[Tuple[int, int]]:
    """Returns (winner entrant id, loser entrant id) of every recorded decider."""
    return [(r["winner_id"], r["loser_id"]) for r in cx.execute(
        "SELECT winner_id, loser_id FROM sudden_death WHERE tournament_id = ?", (tournament_id,))]


class SuddenDeathLedger:
    """Records, corrects and lists sudden-death results."""

    def __init__(self, db: Database, audit: Auditor):
        self._db = db
        self._audit = audit

    def record(self, tournament: Ref, winner: Ref, loser: Ref) -> int:
        """Records that entrant `winner` won the decider against `loser`.

        Raises:
            ValidationError: If both are the same entrant.
            NotFoundError: If an entrant does not exist.
            DuplicateError: If a decider between the two is already recorded (either way round);
                delete it first to correct a mistake.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            won, lost = entrant_row(cx, t["id"], winner), entrant_row(cx, t["id"], loser)
            if won["id"] == lost["id"]:
                raise ValidationError("a sudden-death decider needs two different entrants")
            if cx.execute("SELECT 1 FROM sudden_death WHERE tournament_id = ? AND"
                          " ((winner_id = ? AND loser_id = ?) OR (winner_id = ? AND loser_id = ?))",
                          (t["id"], won["id"], lost["id"], lost["id"], won["id"])).fetchone():
                raise DuplicateError(f"a decider between '{won['name']}' and '{lost['name']}'"
                                     " is already recorded")
            cur = cx.execute(
                "INSERT INTO sudden_death (tournament_id, winner_id, loser_id) VALUES (?, ?, ?)",
                (t["id"], won["id"], lost["id"]))
            self._audit.record(cx, AuditEntry("record", "sudden_death", cur.lastrowid,
                                              {"winner": won["name"], "loser": lost["name"]}))
            return cur.lastrowid

    def delete(self, tournament: Ref, winner: Ref, loser: Ref) -> None:
        """Removes a recorded decider (to correct a mistake).

        Raises:
            NotFoundError: If no such decider is recorded.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            won, lost = entrant_row(cx, t["id"], winner), entrant_row(cx, t["id"], loser)
            row = cx.execute("SELECT id FROM sudden_death"
                             " WHERE tournament_id = ? AND winner_id = ? AND loser_id = ?",
                             (t["id"], won["id"], lost["id"])).fetchone()
            if row is None:
                raise NotFoundError(f"no decider won by '{won['name']}' against '{lost['name']}'")
            cx.execute("DELETE FROM sudden_death WHERE id = ?", (row["id"],))
            self._audit.record(cx, AuditEntry("delete", "sudden_death", row["id"],
                                              {"winner": won["name"], "loser": lost["name"]}))

    def list_all(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the recorded deciders with entrant names, oldest first."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            return [dict(r) for r in cx.execute(
                "SELECT s.id, w.name AS winner, l.name AS loser, s.decided_at FROM sudden_death s"
                " JOIN entrants w ON w.id = s.winner_id JOIN entrants l ON l.id = s.loser_id"
                " WHERE s.tournament_id = ? ORDER BY s.id", (t["id"],))]
