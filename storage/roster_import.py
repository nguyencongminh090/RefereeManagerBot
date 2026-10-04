"""Roster CSV import and export (team,country,full_name,nickname,role,captain,contact)."""
import csv
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Dict, Iterator, TextIO, Union

from storage.database import Database
from storage.errors import DuplicateError, NotFoundError, ValidationError
from storage.lookups import clean, tournament_row
from storage.models import NewIndividual, NewPlayer, Ref
from storage.players import PlayerAdmin
from storage.tournaments import TournamentAdmin

ROSTER_COLUMNS = ("team", "country", "full_name", "nickname", "role", "captain", "contact")
_REQUIRED_COLUMNS = {"full_name", "nickname"}
_TRUE_WORDS = {"1", "y", "yes", "true", "t", "x", "c", "captain"}
_CSV_FIRST_DATA_LINE = 2


@dataclass
class _ImportRun:
    """State of one import: the open transaction, the tournament and the running counts."""
    cx: sqlite3.Connection
    tournament: sqlite3.Row
    counts: Dict[str, int] = field(
        default_factory=lambda: {"teams_added": 0, "players_added": 0, "skipped": 0})


def _rows(handle: TextIO) -> Iterator[Dict[str, str]]:
    """Returns the CSV rows with lower-cased headers; raises ValidationError on missing columns."""
    reader = csv.DictReader(handle)
    reader.fieldnames = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
    missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing:
        raise ValidationError(f"CSV is missing column(s): {', '.join(sorted(missing))}")
    return reader


class RosterCsv:
    """Loads rosters from CSV and writes them back."""

    def __init__(self, db: Database, teams: TournamentAdmin, players: PlayerAdmin):
        self._db = db
        self._teams = teams
        self._players = players

    def import_csv(self, tournament: Ref, source: Union[str, TextIO]) -> Dict[str, int]:
        """Loads teams and players (team format) or players (individual format) from CSV.

        Safe to run again: nicknames that already exist are skipped. All-or-nothing: any bad row
        aborts the whole import.

        Args:
            tournament: Target tournament.
            source: A file path or an open text handle.

        Returns:
            Counts under "teams_added", "players_added" and "skipped".

        Raises:
            ValidationError: If a column is missing or a row is bad (the message names the
                CSV line).
        """
        opened = (open(source, newline="", encoding="utf-8") if isinstance(source, str)
                  else nullcontext(source))
        with opened as handle:
            rows = _rows(handle)
            with self._db.transaction() as cx:
                run = _ImportRun(cx, tournament_row(cx, tournament))
                for line, row in enumerate(rows, start=_CSV_FIRST_DATA_LINE):
                    self._import_row(run, line, row)
                return run.counts

    def _import_row(self, run: _ImportRun, line: int, row: Dict[str, str]) -> None:
        try:
            nickname = clean(row.get("nickname"), "nickname")
            if run.cx.execute("SELECT 1 FROM participants WHERE tournament_id = ? AND nickname = ?",
                              (run.tournament["id"], nickname)).fetchone():
                run.counts["skipped"] += 1
                return
            if run.tournament["format"] == "team":
                self._add_team_player(run, row, nickname)
            else:
                self._players.add_individual(run.tournament["id"], NewIndividual(
                    row.get("full_name"), nickname, row.get("country"), row.get("contact")))
            run.counts["players_added"] += 1
        except (ValidationError, DuplicateError, NotFoundError) as exc:
            raise ValidationError(f"CSV line {line}: {exc}") from exc

    def _add_team_player(self, run: _ImportRun, row: Dict[str, str], nickname: str) -> None:
        tid = run.tournament["id"]
        team = clean(row.get("team"), "team")
        known = run.cx.execute("SELECT 1 FROM entrants WHERE tournament_id = ? AND name = ?",
                               (tid, team)).fetchone()
        if known is None:
            self._teams.add_team(tid, team, row.get("country"))
            run.counts["teams_added"] += 1
        self._players.add_player(tid, team, NewPlayer(
            full_name=row.get("full_name"), nickname=nickname, country=row.get("country"),
            contact=row.get("contact"), role=(row.get("role") or "main").strip().lower() or "main",
            is_captain=(row.get("captain") or "").strip().lower() in _TRUE_WORDS))

    def export_csv(self, tournament: Ref, target: TextIO) -> int:
        """Writes the roster as CSV to `target` and returns the number of players written."""
        writer = csv.writer(target)
        writer.writerow(ROSTER_COLUMNS)
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            rows = cx.execute(
                "SELECT e.name AS team, COALESCE(per.country, e.country) AS country,"
                " per.full_name, pa.nickname, pa.role, pa.is_captain, per.contact"
                " FROM participants pa JOIN entrants e ON e.id = pa.entrant_id"
                " JOIN persons per ON per.id = pa.person_id WHERE pa.tournament_id = ?"
                " ORDER BY e.name, pa.is_captain DESC, per.full_name", (t["id"],)).fetchall()
            for r in rows:
                writer.writerow(["" if t["format"] == "individual" else r["team"],
                                 r["country"] or "", r["full_name"], r["nickname"], r["role"],
                                 "yes" if r["is_captain"] else "", r["contact"] or ""])
            return len(rows)
