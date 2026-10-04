"""Fixtures: the schedule of meetings between entrants."""
import sqlite3
from typing import Any, Dict, List, Optional

from storage.audit import AuditEntry, Auditor
from storage.database import Database
from storage.errors import DuplicateError, NotFoundError, ValidationError
from storage.lookups import entrant_row, tournament_row
from storage.models import NewFixture, Ref

_FIXTURE_FIELDS = {"round_no", "scheduled_at", "status"}


class FixtureAdmin:
    """Adds, changes, deletes and lists fixtures."""

    def __init__(self, db: Database, audit: Auditor):
        self._db = db
        self._audit = audit

    def add(self, tournament: Ref, fixture: NewFixture) -> int:
        """Schedules two different entrants in a round and returns the fixture id.

        Raises:
            ValidationError: If both entrants are the same.
            DuplicateError: If the fixture already exists.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            a = entrant_row(cx, t["id"], fixture.entrant_a)
            b = entrant_row(cx, t["id"], fixture.entrant_b)
            if a["id"] == b["id"]:
                raise ValidationError("a fixture needs two different entrants")
            try:
                cur = cx.execute(
                    "INSERT INTO fixtures"
                    " (tournament_id, round_no, entrant_a_id, entrant_b_id, scheduled_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (t["id"], fixture.round_no, a["id"], b["id"], fixture.scheduled_at))
            except sqlite3.IntegrityError as exc:
                raise DuplicateError("this fixture already exists") from exc
            self._audit.record(cx, AuditEntry(
                "add", "fixture", cur.lastrowid,
                {"round": fixture.round_no, "a": a["name"], "b": b["name"]}))
            return cur.lastrowid

    def update(self, fixture_id: int, fields: Dict[str, Any]) -> None:
        """Changes round_no, scheduled_at or status of a fixture.

        Raises:
            ValidationError: If a field name is not updatable or the database rejects the value.
            NotFoundError: If the fixture does not exist.
        """
        unknown = set(fields) - _FIXTURE_FIELDS
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        if not fields:
            return
        with self._db.transaction() as cx:
            if cx.execute("SELECT 1 FROM fixtures WHERE id = ?", (fixture_id,)).fetchone() is None:
                raise NotFoundError(f"fixture {fixture_id} not found")
            # Column names come from the _FIXTURE_FIELDS whitelist; values are bound.
            sets = ", ".join(f"{k} = ?" for k in fields)
            try:
                cx.execute(f"UPDATE fixtures SET {sets} WHERE id = ?",
                           [*fields.values(), fixture_id])
            except sqlite3.IntegrityError as exc:
                raise ValidationError(str(exc)) from exc
            self._audit.record(cx, AuditEntry("update", "fixture", fixture_id, fields))

    def delete(self, fixture_id: int) -> None:
        """Deletes a fixture; games already played keep existing but lose the link to it.

        Raises:
            NotFoundError: If the fixture does not exist.
        """
        with self._db.transaction() as cx:
            if cx.execute("DELETE FROM fixtures WHERE id = ?", (fixture_id,)).rowcount == 0:
                raise NotFoundError(f"fixture {fixture_id} not found")
            self._audit.record(cx, AuditEntry("delete", "fixture", fixture_id))

    def list_all(self, tournament: Ref, round_no: Optional[int]) -> List[Dict[str, Any]]:
        """Returns the fixtures (optionally of one round) with their game counts."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            sql = ("SELECT f.id, f.round_no, f.scheduled_at, f.status,"
                   " a.name AS entrant_a, b.name AS entrant_b,"
                   " (SELECT COUNT(*) FROM games g WHERE g.fixture_id = f.id AND g.voided = 0)"
                   " AS games"
                   " FROM fixtures f JOIN entrants a ON a.id = f.entrant_a_id"
                   " JOIN entrants b ON b.id = f.entrant_b_id"
                   " WHERE f.tournament_id = ?")
            params: list = [t["id"]]
            if round_no is not None:
                sql += " AND f.round_no = ?"
                params.append(round_no)
            return [dict(r) for r in cx.execute(sql + " ORDER BY f.round_no, f.id", params)]
