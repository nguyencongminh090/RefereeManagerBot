"""Roster listings and the pre-start validation report."""
import sqlite3
from typing import Any, Dict, List

from storage.database import Database
from storage.lookups import require_format, tournament_row
from storage.models import Ref


def _prefix_issues(cx: sqlite3.Connection, t: sqlite3.Row) -> List[str]:
    prefix = t["nickname_prefix"]
    if not prefix:
        return []
    rows = cx.execute("SELECT nickname FROM participants WHERE tournament_id = ?", (t["id"],))
    return [f"nickname '{r['nickname']}' does not start with '{prefix}'"
            for r in rows if not r["nickname"].lower().startswith(prefix.lower())]


def _individual_issues(e: sqlite3.Row, members: List[sqlite3.Row]) -> List[str]:
    if len(members) == 1:
        return []
    return [f"'{e['name']}' must have exactly one player, has {len(members)}"]


def _team_issues(t: sqlite3.Row, e: sqlite3.Row, members: List[sqlite3.Row]) -> List[str]:
    issues: List[str] = []
    mains = [m for m in members if m["role"] == "main" and m["active"]]
    subs = [m for m in members if m["role"] == "sub" and m["active"]]
    if len(mains) != t["team_size"]:
        issues.append(f"team '{e['name']}' has {len(mains)} main player(s), "
                      f"expected {t['team_size']}")
    if len(subs) > t["max_substitutes"]:
        issues.append(f"team '{e['name']}' has {len(subs)} substitute(s), "
                      f"maximum {t['max_substitutes']}")
    captains = [m for m in members if m["is_captain"]]
    if not captains:
        issues.append(f"team '{e['name']}' has no captain")
    elif not captains[0]["active"]:
        issues.append(f"captain '{captains[0]['nickname']}' of '{e['name']}' is inactive")
    return issues


class RosterReport:
    """Roster listings and the validation report of a tournament."""

    def __init__(self, db: Database):
        self._db = db

    def team_roster(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns one row per player of a team tournament, with the team and captain columns."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "team_roster")
            return [dict(r) for r in cx.execute(
                "SELECT team_name, country, captain_name, captain_nickname, captain_contact,"
                " player_name, nickname, role, active, is_captain"
                " FROM v_team_roster WHERE tournament_id = ?"
                " ORDER BY team_name, is_captain DESC, player_name", (t["id"],))]

    def individual_list(self, tournament: Ref) -> List[Dict[str, Any]]:
        """Returns the players of an individual tournament by name."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "individual", "individual_list")
            return [dict(r) for r in cx.execute(
                "SELECT nickname, player_name, country, contact, active FROM v_individual_list"
                " WHERE tournament_id = ? ORDER BY player_name", (t["id"],))]

    def validate(self, tournament: Ref) -> List[str]:
        """Returns the problems to fix before the tournament starts (empty list means ready)."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            issues = _prefix_issues(cx, t)
            for e in cx.execute("SELECT id, name FROM entrants WHERE tournament_id = ?"
                                " ORDER BY name", (t["id"],)):
                members = cx.execute("SELECT nickname, role, active, is_captain"
                                     " FROM participants WHERE entrant_id = ?",
                                     (e["id"],)).fetchall()
                issues += (_individual_issues(e, members) if t["format"] == "individual"
                           else _team_issues(t, e, members))
        return issues
