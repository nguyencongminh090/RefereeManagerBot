"""Row lookups and input cleaning shared by the storage modules."""
import sqlite3
from typing import Iterable, Optional

from storage.errors import NotFoundError, UnknownPlayerError, ValidationError
from storage.models import Ref


def clean(value: Optional[str], what: str, required: bool = True) -> Optional[str]:
    """Strips `value`; returns None for blank optional input.

    Raises:
        ValidationError: If the value is blank and required.
    """
    value = (value or "").strip()
    if not value:
        if required:
            raise ValidationError(f"{what} must not be empty")
        return None
    return value


def tournament_row(cx: sqlite3.Connection, ref: Ref) -> sqlite3.Row:
    """Finds a tournament by id or name; raises NotFoundError."""
    column = "id" if isinstance(ref, int) else "name"
    row = cx.execute(f"SELECT * FROM tournaments WHERE {column} = ?", (ref,)).fetchone()
    if row is None:
        raise NotFoundError(f"tournament '{ref}' not found")
    return row


def entrant_row(cx: sqlite3.Connection, tournament_id: int, ref: Ref) -> sqlite3.Row:
    """Finds a team or individual entrant by id or name; raises NotFoundError."""
    column = "id" if isinstance(ref, int) else "name"
    row = cx.execute(f"SELECT * FROM entrants WHERE tournament_id = ? AND {column} = ?",
                     (tournament_id, ref)).fetchone()
    if row is None:
        raise NotFoundError(f"entrant '{ref}' not found in this tournament")
    return row


def participant_row(cx: sqlite3.Connection, tournament_id: int, ref: Ref) -> sqlite3.Row:
    """Finds a player by participant id or nickname; raises UnknownPlayerError."""
    column = "id" if isinstance(ref, int) else "nickname"
    row = cx.execute(f"SELECT * FROM participants WHERE tournament_id = ? AND {column} = ?",
                     (tournament_id, ref)).fetchone()
    if row is None:
        raise UnknownPlayerError(f"player '{ref}' not found in this tournament")
    return row


def require_format(tournament: sqlite3.Row, fmt: str, action: str) -> None:
    """Raises ValidationError unless the tournament has format `fmt`."""
    if tournament["format"] != fmt:
        raise ValidationError(f"{action} only applies to {fmt}-format tournaments "
                              f"('{tournament['name']}' is {tournament['format']})")


def check_prefix(tournament: sqlite3.Row, nickname: str) -> None:
    """Raises ValidationError if the tournament requires a nickname prefix the nickname lacks."""
    prefix = tournament["nickname_prefix"]
    if prefix and not nickname.lower().startswith(prefix.lower()):
        raise ValidationError(f"nickname '{nickname}' must start with '{prefix}'")


def game_count_for_participants(cx: sqlite3.Connection, participant_ids: Iterable[int]) -> int:
    """Counts the games (voided ones included) played by any of the participants."""
    ids = list(participant_ids)
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    return cx.execute(f"SELECT COUNT(*) FROM games WHERE p1_id IN ({marks}) OR p2_id IN ({marks})",
                      ids + ids).fetchone()[0]


def delete_games_of(cx: sqlite3.Connection, participant_ids: Iterable[int]) -> None:
    """Deletes every game played by any of the participants."""
    ids = list(participant_ids)
    if ids:
        marks = ",".join("?" * len(ids))
        cx.execute(f"DELETE FROM games WHERE p1_id IN ({marks}) OR p2_id IN ({marks})", ids + ids)


def purge_orphan_persons(cx: sqlite3.Connection) -> None:
    """Deletes persons that no tournament refers to any more."""
    cx.execute("DELETE FROM persons WHERE id NOT IN (SELECT person_id FROM participants)")
