"""Players (participants) of a tournament: add, change, move, remove, look up."""
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Union

from storage.audit import AuditEntry, Auditor
from storage.database import Database
from storage.errors import DuplicateError, HasGamesError, NotFoundError, ValidationError
from storage.lookups import (check_prefix, clean, delete_games_of, entrant_row,
                             game_count_for_participants, participant_row, purge_orphan_persons,
                             require_format, tournament_row)
from storage.models import NewIndividual, NewPlayer, Ref

_ROLES = ("main", "sub")
_PERSON_FIELDS = {"full_name", "country", "contact", "notes"}


@dataclass(frozen=True)
class _RoomCheck:
    """What `_check_room` needs: the team, the role to fill and a participant to ignore."""
    tournament: sqlite3.Row
    entrant_id: int
    role: str
    exclude_id: Optional[int] = None


@dataclass(frozen=True)
class _Seat:
    """Where a new participant sits: tournament, entrant and role."""
    tournament_id: int
    entrant_id: int
    role: str


class PlayerAdmin:
    """Adds, changes, moves and removes players, and looks them up."""

    def __init__(self, db: Database, audit: Auditor):
        self._db = db
        self._audit = audit

    # -------------------------------------------------------------------- add
    def add_individual(self, tournament: Ref, player: NewIndividual) -> int:
        """Adds a player to an individual-format tournament.

        The entrant is named after the nickname.

        Returns:
            The participant id.

        Raises:
            ValidationError: On a blank nickname, a nickname prefix mismatch or a team tournament.
            DuplicateError: If the nickname is already registered.
        """
        nickname = clean(player.nickname, "nickname")
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "individual", "add_individual")
            check_prefix(t, nickname)
            try:
                entrant_id = cx.execute(
                    "INSERT INTO entrants (tournament_id, name, country) VALUES (?, ?, ?)",
                    (t["id"], nickname, clean(player.country, "country", required=False))).lastrowid
                participant_id = self._insert_participant(
                    cx, _Seat(t["id"], entrant_id, "main"), replace(player, nickname=nickname))
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(f"player '{nickname}' is already registered") from exc
            self._audit.record(cx, AuditEntry("add", "individual", participant_id,
                                              {"nickname": nickname}))
            return participant_id

    def add_player(self, tournament: Ref, team: Ref, player: NewPlayer) -> int:
        """Adds a player to a team of a team-format tournament.

        Returns:
            The participant id.

        Raises:
            ValidationError: On a bad role, nickname prefix or a full team.
            DuplicateError: If the nickname (or person) is already in the tournament.
        """
        nickname = clean(player.nickname, "nickname")
        if player.role not in _ROLES:
            raise ValidationError("role must be 'main' or 'sub'")
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "add_player")
            e = entrant_row(cx, t["id"], team)
            check_prefix(t, nickname)
            self._check_room(cx, _RoomCheck(t, e["id"], player.role))
            try:
                participant_id = self._insert_participant(
                    cx, _Seat(t["id"], e["id"], player.role), replace(player, nickname=nickname))
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(f"nickname '{nickname}' (or this person) is already in the"
                                     " tournament") from exc
            if player.is_captain:
                self._set_captain(cx, participant_id)
            self._audit.record(cx, AuditEntry(
                "add", "player", participant_id,
                {"nickname": nickname, "team": e["name"], "role": player.role}))
            return participant_id

    def _insert_participant(self, cx: sqlite3.Connection, seat: _Seat,
                            person: Union[NewPlayer, NewIndividual]) -> int:
        """Inserts the person (unless reused) and the participant; returns the participant id."""
        person_id = person.person_id
        if person_id is None:
            person_id = cx.execute(
                "INSERT INTO persons (full_name, country, contact) VALUES (?, ?, ?)",
                (clean(person.full_name, "full name"),
                 clean(person.country, "country", required=False),
                 clean(person.contact, "contact", required=False))).lastrowid
        elif cx.execute("SELECT 1 FROM persons WHERE id = ?", (person_id,)).fetchone() is None:
            raise NotFoundError(f"person {person_id} not found")
        return cx.execute(
            "INSERT INTO participants (tournament_id, entrant_id, person_id, nickname, role)"
            " VALUES (?, ?, ?, ?, ?)",
            (seat.tournament_id, seat.entrant_id, person_id, person.nickname, seat.role)).lastrowid

    def _check_room(self, cx: sqlite3.Connection, check: _RoomCheck) -> None:
        """Raises ValidationError if the team has no free place for the role."""
        # "id IS NOT ?" with None excludes nothing, so a new player is counted against everyone.
        count = cx.execute("SELECT COUNT(*) FROM participants"
                           " WHERE entrant_id = ? AND role = ? AND active = 1 AND id IS NOT ?",
                           (check.entrant_id, check.role, check.exclude_id)).fetchone()[0]
        t = check.tournament
        limit = t["team_size"] if check.role == "main" else t["max_substitutes"]
        if count >= limit:
            raise ValidationError(
                f"team already has {count} {check.role} player(s) (limit {limit})")

    def _set_captain(self, cx: sqlite3.Connection, participant_id: int) -> None:
        """Demotes the team's captain and promotes the participant."""
        row = cx.execute("SELECT entrant_id FROM participants WHERE id = ?",
                         (participant_id,)).fetchone()
        cx.execute("UPDATE participants SET is_captain = 0 WHERE entrant_id = ?",
                   (row["entrant_id"],))
        cx.execute("UPDATE participants SET is_captain = 1 WHERE id = ?", (participant_id,))

    # ----------------------------------------------------------------- change
    def set_captain(self, tournament: Ref, player: Ref) -> None:
        """Makes a player the captain of their team (the previous captain is demoted)."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "set_captain")
            p = participant_row(cx, t["id"], player)
            self._set_captain(cx, p["id"])
            self._audit.record(cx, AuditEntry("set_captain", "player", p["id"],
                                              {"nickname": p["nickname"]}))

    def update_person(self, tournament: Ref, player: Ref, fields: Dict[str, Any]) -> None:
        """Changes full_name, country, contact or notes of a player's person record.

        Raises:
            ValidationError: If a field name is not updatable.
        """
        unknown = set(fields) - _PERSON_FIELDS
        if unknown:
            raise ValidationError(f"cannot update: {', '.join(sorted(unknown))}")
        if not fields:
            return
        with self._db.transaction() as cx:
            p = participant_row(cx, tournament_row(cx, tournament)["id"], player)
            # Column names come from the _PERSON_FIELDS whitelist; values are bound.
            sets = ", ".join(f"{k} = ?" for k in fields)
            cx.execute(f"UPDATE persons SET {sets} WHERE id = ?",
                       [*fields.values(), p["person_id"]])
            self._audit.record(cx, AuditEntry("update", "person", p["person_id"], fields))

    def rename_nickname(self, tournament: Ref, player: Ref, new_nickname: str) -> None:
        """Changes a player's nickname (and the entrant name in an individual event).

        Raises:
            DuplicateError: If the nickname is already taken.
            ValidationError: On a blank nickname or a nickname prefix mismatch.
        """
        new_nickname = clean(new_nickname, "nickname")
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            p = participant_row(cx, t["id"], player)
            check_prefix(t, new_nickname)
            try:
                cx.execute("UPDATE participants SET nickname = ? WHERE id = ?",
                           (new_nickname, p["id"]))
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(f"nickname '{new_nickname}' is already taken") from exc
            if t["format"] == "individual":
                cx.execute("UPDATE entrants SET name = ? WHERE id = ?",
                           (new_nickname, p["entrant_id"]))
            self._audit.record(cx, AuditEntry("rename", "player", p["id"],
                                              {"from": p["nickname"], "to": new_nickname}))

    def set_role(self, tournament: Ref, player: Ref, role: str) -> None:
        """Moves a player between "main" and "sub", respecting the team limits.

        Raises:
            ValidationError: On an unknown role or if the team has no free place for it.
        """
        if role not in _ROLES:
            raise ValidationError("role must be 'main' or 'sub'")
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "set_role")
            p = participant_row(cx, t["id"], player)
            if p["active"]:
                self._check_room(cx, _RoomCheck(t, p["entrant_id"], role, exclude_id=p["id"]))
            cx.execute("UPDATE participants SET role = ? WHERE id = ?", (role, p["id"]))
            self._audit.record(cx, AuditEntry("set_role", "player", p["id"], {"role": role}))

    def substitute(self, tournament: Ref, player_out: Ref, player_in: Ref) -> None:
        """Replaces a player: the outgoing one goes inactive, the incoming one becomes main.

        Raises:
            ValidationError: If the players are not on the same team.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "substitute")
            out = participant_row(cx, t["id"], player_out)
            inc = participant_row(cx, t["id"], player_in)
            if out["entrant_id"] != inc["entrant_id"]:
                raise ValidationError("both players must be on the same team")
            cx.execute("UPDATE participants SET active = 0, is_captain = 0 WHERE id = ?",
                       (out["id"],))
            cx.execute("UPDATE participants SET active = 1, role = 'main' WHERE id = ?",
                       (inc["id"],))
            self._audit.record(cx, AuditEntry("substitute", "player", out["id"],
                                              {"out": out["nickname"], "in": inc["nickname"]}))

    def set_active(self, tournament: Ref, player: Ref, active: bool) -> None:
        """Activates or deactivates a player (an inactive player cannot be given results)."""
        with self._db.transaction() as cx:
            p = participant_row(cx, tournament_row(cx, tournament)["id"], player)
            cx.execute("UPDATE participants SET active = ? WHERE id = ?",
                       (int(active), p["id"]))
            self._audit.record(cx, AuditEntry("set_active", "player", p["id"], {"active": active}))

    def move_player(self, tournament: Ref, player: Ref, new_team: Ref) -> None:
        """Moves a player without games to another team.

        Raises:
            HasGamesError: If the player already has recorded games.
            ValidationError: If the new team has no free place for the player's role.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            require_format(t, "team", "move_player")
            p = participant_row(cx, t["id"], player)
            if game_count_for_participants(cx, [p["id"]]):
                raise HasGamesError(f"'{p['nickname']}' has recorded games and cannot change team")
            e = entrant_row(cx, t["id"], new_team)
            self._check_room(cx, _RoomCheck(t, e["id"], p["role"]))
            cx.execute("UPDATE participants SET entrant_id = ?, is_captain = 0 WHERE id = ?",
                       (e["id"], p["id"]))
            self._audit.record(cx, AuditEntry("move", "player", p["id"], {"to": e["name"]}))

    def remove_player(self, tournament: Ref, player: Ref, cascade_games: bool) -> None:
        """Deletes a player; one with games should normally be deactivated instead.

        Raises:
            HasGamesError: If games exist and `cascade_games` is false.
        """
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            p = participant_row(cx, t["id"], player)
            games = game_count_for_participants(cx, [p["id"]])
            if games and not cascade_games:
                raise HasGamesError(f"'{p['nickname']}' has {games} recorded game(s); "
                                    "deactivate instead, or use cascade_games to delete them")
            delete_games_of(cx, [p["id"]])
            cx.execute("DELETE FROM participants WHERE id = ?", (p["id"],))
            if t["format"] == "individual":
                cx.execute("DELETE FROM entrants WHERE id = ?", (p["entrant_id"],))
            purge_orphan_persons(cx)
            self._audit.record(cx, AuditEntry(
                "remove", "player", p["id"], {"nickname": p["nickname"], "games_deleted": games}))

    # ----------------------------------------------------------------- lookup
    def find_player(self, tournament: Ref, nickname: str) -> Optional[Dict[str, Any]]:
        """Returns the player with this nickname (with person and team columns), or None."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            row = cx.execute(
                "SELECT pa.*, per.full_name, per.country, per.contact, e.name AS entrant_name"
                " FROM participants pa JOIN persons per ON per.id = pa.person_id"
                " JOIN entrants e ON e.id = pa.entrant_id"
                " WHERE pa.tournament_id = ? AND pa.nickname = ?", (t["id"], nickname)).fetchone()
            return None if row is None else dict(row)

    def list_players(self, tournament: Ref, team: Optional[Ref]) -> List[Dict[str, Any]]:
        """Returns the players of a tournament, or of one team, by team and captain first."""
        with self._db.transaction() as cx:
            t = tournament_row(cx, tournament)
            sql = ("SELECT pa.id, pa.nickname, per.full_name, per.country, per.contact,"
                   " pa.role, pa.active, pa.is_captain, e.name AS entrant_name"
                   " FROM participants pa"
                   " JOIN persons per ON per.id = pa.person_id"
                   " JOIN entrants e ON e.id = pa.entrant_id"
                   " WHERE pa.tournament_id = ?")
            params: list = [t["id"]]
            if team is not None:
                sql += " AND e.id = ?"
                params.append(entrant_row(cx, t["id"], team)["id"])
            sql += " ORDER BY e.name, pa.is_captain DESC, per.full_name"
            return [dict(r) for r in cx.execute(sql, params)]
