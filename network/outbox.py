"""Durable queue of match results the server has not confirmed yet."""
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ResultOutbox:
    """Keeps unconfirmed MATCH_RESULT packets in memory and, optionally, in a JSON-lines file.

    Each packet is appended and fsynced before it is sent; removal rewrites the file atomically
    (temp file + os.replace). A corrupt line, such as a half-written last line after a crash,
    is skipped with a warning and dropped from the file on load. Thread-safe.
    """

    def __init__(self, path: Optional[str] = None):
        """Opens the outbox and reloads unconfirmed packets.

        Args:
            path: File to persist to (parent directory is created); None keeps memory only.
        """
        self._path = path
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self._load()

    def add(self, match_id: str, payload: Dict[str, Any]) -> None:
        """Stores a packet durably; call before sending it."""
        with self._lock:
            self._pending[match_id] = payload
            if self._path:
                self._append(match_id, payload)

    def remove(self, match_id: str) -> None:
        """Forgets a packet the server confirmed or rejected; unknown ids are ignored."""
        with self._lock:
            if self._pending.pop(match_id, None) is not None and self._path:
                self._rewrite()

    def pending(self) -> List[Dict[str, Any]]:
        """Returns the unconfirmed packets, oldest first."""
        with self._lock:
            return list(self._pending.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._pending)

    def _append(self, match_id: str, payload: Dict[str, Any]) -> None:
        line = json.dumps({"match_id": match_id, "packet": payload})
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _rewrite(self) -> None:
        temp_path = self._path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            for match_id, payload in self._pending.items():
                handle.write(json.dumps({"match_id": match_id, "packet": payload}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self._path)

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        has_corrupt_line = False
        with open(self._path, encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if not self._load_line(line, number):
                    has_corrupt_line = True
        if has_corrupt_line:
            self._rewrite()

    def _load_line(self, line: str, number: int) -> bool:
        """Parses one stored line into the pending map; returns False if it is corrupt."""
        try:
            record = json.loads(line)
            match_id, packet = record["match_id"], record["packet"]
            if not isinstance(match_id, str) or not isinstance(packet, dict):
                raise ValueError("wrong field types")
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("outbox %s line %d ignored (%s)", self._path, number, exc)
            return False
        self._pending[match_id] = packet
        return True
