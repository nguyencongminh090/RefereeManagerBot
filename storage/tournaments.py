"""Tournaments and their entrants (teams, or the single players of an individual event)."""
import sqlite3
from typing import Any, Dict, List, Optional

from storage.audit import AuditEntry, Auditor
from storage.database import Database
from storage.errors import DuplicateError, HasGamesError, ValidationError
from storage.lookups import (clean, delete_games_of, entrant_row, game_count_for_participants,
                             purge_orphan_persons, require_format, tournament_row)
from storage.models import Ref, TournamentSpec

_TOURNAMENT_FIELDS = ("name", "year", "team_size", "max_substitutes", "games_per_pair",
                      "nickname_prefix", "status")
_FORMATS = ("team", "individual")


class TournamentAdmin:
    """Creates, changes and deletes tournaments, teams and entrants."""

    def __init__(self, db: Database, audit: Auditor):
        self._db = db
        self._audit = audit

    def create(self, spec: TournamentSpec) -> int:
        """Creates a tournament and returns its id.

        Raises:
            ValidationError: If the name is blank, the format is unknown or a team event has
                no team_size.
            DuplicateError: If the name is taken.
        """
        name = clean(spec.name, "tournament name")
        if spec.fmt not in _FORMATS:
            raise ValidationError("format must be 'team' or 'individual'")
        if spec.fmt == "team" and not spec.team_size:
            raise ValidationError("team format needs team_size")
        with self._db.transaction() as cx:
            tournament_id = self._insert(cx, spec, name)
            self._audit.record(cx, AuditEntry("create", "tournament", tournament_id,
                                              {"name": name, "format": spec.fmt}))
            return tournament_id

    def _insert(self, cx: sqlite3.Connection, spec: TournamentSpec, name: str) -> int:
        try:
            cur = cx.execute(
                "INSERT INTO tournaments (name, year, format, team_size, max_substitutes,"
                " games_per_pair, nickname_prefix) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, spec.year, spec.fmt, spec.team_size if spec.fmt == "team" else None,
                 spec.max_substitutes, spec.games_per_pair,
                 clean(spec.nickname_prefix, "nickname prefix", required=False)))
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"tournament '{name}' already exists") from exc
        return cur.lastrowid

    def get(self, ref: Ref) -> Dict[str, Any]:
        """Returns the tournament row as a dict; raises NotFoundError."""
        with self._db.transaction() as cx:
            return dict(tournament_row(cx, ref))

    def list_all(self) -> List[Dict[str, Any]]:
        """Returns all tournaments, newest year first."""
        rows = self._db.query("SELECT * FROM tournaments ORDER BY year DESC, id DESC")
        return [dict(r) for r in rows]

    def update(self, ref: Ref, fields: Dict[str, Any]) -> None:
        """Changes the given columns of a tournament.

        Raises:
            ValidationError: If a field name is not updatable or the database rejects the value.
        """
        unknown = set(fields) - set(_TOURNAMENT_FIELDS)
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        if not fields:
            return
        with self._db.transaction() as cx:
            t = tournament_row(cx, ref)
            # Column names come from the _TOURNAMENT_FIELDS whitelist; values are bound.
            sets = ", ".join(f"{k} = ?" for k in fields)
            try:
                cx.execute(f"UPDATE tournaments SET {sets} WHERE id = ?",
                           [*fields.values(), t["id"]])
            except sqlite3.IntegrityError as exc:
                raise ValidationError(str(exc)) from exc
            self._audit.record(cx, AuditEntry("update", "tournament", t["id"], fields))

    def delete(self, ref: Ref) -> None:
        """Deletes a tournament with all its games, entrants and players."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, ref)
            cx.execute("DELETE FROM games WHERE tournament_id = ?", (t["id"],))
            cx.execute("DELETE FROM tournaments WHERE id = ?", (t["id"],))
            purge_orphan_persons(cx)
            self._audit.record(cx, AuditEntry("delete", "tournament", t["id"], {"name": t["name"]}))

    def add_team(self, tournament: Ref, name: str, country: Optional[str]) -> int:
        """Adds a team to a team-format tournament and returns the entrant id.

        Raises:
            ValidationError: On a blank name or an individual-format tournament.
            DuplicateError: If the team name is taken.
        """
        name = clean(name, "team name")
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "add_team")
            try:
                cur = cx.execute(
                    "INSERT INTO entrants (tournament_id, name, country) VALUES (?, ?, ?)",
                    (t["id"], name, clean(country, "country", required=False)))
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(f"team '{name}' already exists") from exc
            self._audit.record(cx, AuditEntry("add", "team", cur.lastrowid, {"name": name}))
            return cur.lastrowid

    def rename_entrant(self, tournament: Ref, entrant: Ref, new_name: str) -> None:
        """Renames a team or individual entrant.

        Raises:
            DuplicateError: If the new name is taken.
        """
        new_name = clean(new_name, "name")
        with self._db.transaction() as cx:
            e = entrant_row(cx, tournament_row(cx, tournament)["id"], entrant)
            try:
                cx.execute("UPDATE entrants SET name = ? WHERE id = ?", (new_name, e["id"]))
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(f"'{new_name}' already exists") from exc
            self._audit.record(cx, AuditEntry("rename", "entrant", e["id"],
                                              {"from": e["name"], "to": new_name}))

    def update_entrant(self, tournament: Ref, entrant: Ref, country: Optional[str]) -> None:
        """Sets (or clears, with a blank value) the country of an entrant."""
        with self._db.transaction() as cx:
            e = entrant_row(cx, tournament_row(cx, tournament)["id"], entrant)
            cx.execute("UPDATE entrants SET country = ? WHERE id = ?",
                       (clean(country, "country", required=False), e["id"]))
            self._audit.record(cx, AuditEntry("update", "entrant", e["id"], {"country": country}))

    def delete_entrant(self, tournament: Ref, entrant: Ref, cascade_games: bool) -> None:
        """Deletes a team (and its players) or an individual.

        Raises:
            HasGamesError: If games exist and `cascade_games` is false.
        """
        with self._db.transaction() as cx:
            e = entrant_row(cx, tournament_row(cx, tournament)["id"], entrant)
            ids = [r[0] for r in cx.execute("SELECT id FROM participants WHERE entrant_id = ?",
                                            (e["id"],))]
            games = game_count_for_participants(cx, ids)
            if games and not cascade_games:
                raise HasGamesError(f"'{e['name']}' has {games} recorded game(s); "
                                    "use cascade_games to delete them too")
            delete_games_of(cx, ids)
            cx.execute("DELETE FROM entrants WHERE id = ?", (e["id"],))
            purge_orphan_persons(cx)
            self._audit.record(cx, AuditEntry("delete", "entrant", e["id"],
                                              {"name": e["name"], "games_deleted": games}))

    def list_entrants(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the entrants with their player counts, by name."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            return [dict(r) for r in cx.execute(
                "SELECT e.id, e.name, e.country, COUNT(pa.id) AS players FROM entrants e"
                " LEFT JOIN participants pa ON pa.entrant_id = e.id"
                " WHERE e.tournament_id = ? GROUP BY e.id ORDER BY e.name", (t["id"],))]
