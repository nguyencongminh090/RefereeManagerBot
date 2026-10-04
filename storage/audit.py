"""The audit log: who changed what."""
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from storage.database import Database


@dataclass(frozen=True)
class AuditEntry:
    """What happened: an action on an entity, with optional JSON-serialisable detail.

    Attributes:
        action: Verb, for example "add" or "void".
        entity: Kind of record changed, for example "game".
        entity_id: Database id of the record, or None when there is none.
        detail: Extra context stored as JSON (non-serialisable values become strings).
    """
    action: str
    entity: str
    entity_id: Optional[int]
    detail: Any = None


class Auditor:
    """Writes audit-log rows on behalf of one actor and reads the log back.

    Attributes:
        actor: Name recorded for every change made through this auditor.
    """

    def __init__(self, db: Database, actor: str):
        self._db = db
        self.actor = actor

    def record(self, cx: sqlite3.Connection, entry: AuditEntry) -> None:
        """Adds one row inside the caller's transaction (`cx`); the detail is stored as JSON."""
        detail = entry.detail
        cx.execute("INSERT INTO audit_log (actor, action, entity, entity_id, detail)"
                   " VALUES (?, ?, ?, ?, ?)",
                   (self.actor, entry.action, entry.entity, entry.entity_id,
                    None if detail is None
                    else json.dumps(detail, ensure_ascii=False, default=str)))

    def recent(self, limit: int) -> List[Dict[str, Any]]:
        """Returns the newest `limit` rows, newest first."""
        rows = self._db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
