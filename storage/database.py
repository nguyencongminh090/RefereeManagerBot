"""The SQLite connection, schema migrations and transactions of the tournament database."""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib    import Path
from typing     import Any, Iterator, List, Optional


SCHEMA_VERSION = 2
_SCHEMA_FILE   = Path(__file__).with_name("schema.sql")   # baseline, version 1
# Version -> script that upgrades the database from the previous version.
_MIGRATIONS    = {2: Path(__file__).with_name("migration_002_sudden_death.sql")}


class Database:
    """One shared SQLite connection, guarded by a re-entrant lock.

    Every server client thread may call into it; `transaction()` makes a group of
    statements atomic and can be nested (the outermost call commits or rolls back).
    """

    def __init__(self, path: str = ":memory:"):
        self._path  = path
        self._lock  = threading.RLock()
        self._depth = 0
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    @property
    def path(self) -> str:
        """The database file path, or ":memory:"."""
        return self._path

    def _migrate(self) -> None:
        """Creates a new database from the baseline, then applies every newer migration."""
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"database schema {version} is newer than this code"
                                   f" ({SCHEMA_VERSION})")
            if version == 0:
                self._conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
                version = 1
            for target in range(version + 1, SCHEMA_VERSION + 1):
                self._apply_migration(target)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))

    def _apply_migration(self, target: int) -> None:
        """Runs one migration script atomically (the script must not contain BEGIN/COMMIT)."""
        script = _MIGRATIONS[target].read_text(encoding="utf-8")
        self._conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Runs the `with` body atomically and yields the connection.

        Holds the database lock for the whole body. Nested calls join the outermost
        transaction; an exception rolls everything back and is re-raised.
        """
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Runs a read statement and returns all rows."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Runs a read statement and returns the first row, or None."""
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple = ()) -> Any:
        """Returns the first column of the first row, or None if there is no row."""
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    def backup(self, dest_path: str) -> None:
        """Consistent copy of the live database (safe while other threads write)."""
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        dest = sqlite3.connect(dest_path)
        try:
            with self._lock:
                self._conn.backup(dest)
        finally:
            dest.close()

    def close(self) -> None:
        """Closes the connection; the object cannot be used afterwards."""
        with self._lock:
            self._conn.close()
