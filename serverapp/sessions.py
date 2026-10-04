"""Connection bookkeeping: who is authenticated, who went quiet, who must be locked out."""
import threading
import time
from dataclasses import dataclass
from typing      import Callable, Dict, List, Optional, Tuple

Addr  = Tuple[str, int]
Clock = Callable[[], float]

REASON_NO_AUTH      = "no authentication"
REASON_NO_HEARTBEAT = "no heartbeat"


@dataclass
class _Session:
    connected_at: float
    last_seen   : float
    name        : Optional[str] = None


class SessionRegistry:
    """Thread-safe record of open connections with their bot name and activity times."""

    def __init__(self, clock: Clock, heartbeat_timeout_seconds: float,
                 auth_timeout_seconds: float) -> None:
        self._clock             = clock
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._auth_timeout      = auth_timeout_seconds
        self._lock              = threading.Lock()
        self._sessions: Dict[Addr, _Session] = {}

    def connect(self, addr: Addr) -> None:
        """Starts the auth deadline for a new connection."""
        now = self._clock()
        with self._lock:
            self._sessions[addr] = _Session(now, now)

    def touch(self, addr: Addr) -> None:
        """Records traffic from a connection (keeps it from being flagged as silent)."""
        with self._lock:
            session = self._sessions.get(addr)
            if session:
                session.last_seen = self._clock()

    def authenticate(self, addr: Addr, name: str) -> None:
        """Marks a connection as an authenticated bot."""
        with self._lock:
            session = self._sessions.get(addr)
            if session:
                session.name = name

    def disconnect(self, addr: Addr) -> None:
        """Forgets a connection."""
        with self._lock:
            self._sessions.pop(addr, None)

    def name_of(self, addr: Addr) -> Optional[str]:
        """Returns the bot name of an authenticated connection, else None."""
        with self._lock:
            session = self._sessions.get(addr)
            return session.name if session else None

    def is_authenticated(self, addr: Addr) -> bool:
        """Tells whether the connection has authenticated."""
        return self.name_of(addr) is not None

    def authenticated(self) -> List[Addr]:
        """Lists the authenticated connections."""
        with self._lock:
            return [a for a, s in self._sessions.items() if s.name is not None]

    def pop_expired(self) -> List[Tuple[Addr, str]]:
        """Removes and returns connections that missed the auth deadline or went silent.

        Returns:
            Pairs of (address, reason); the caller closes the sockets.
        """
        now = self._clock()
        expired: List[Tuple[Addr, str]] = []
        with self._lock:
            for addr, session in list(self._sessions.items()):
                reason = self._expiry_reason(session, now)
                if reason:
                    expired.append((addr, reason))
                    del self._sessions[addr]
        return expired

    def _expiry_reason(self, session: _Session, now: float) -> Optional[str]:
        if session.name is None and now - session.connected_at > self._auth_timeout:
            return REASON_NO_AUTH
        if now - session.last_seen > self._heartbeat_timeout:
            return REASON_NO_HEARTBEAT
        return None


class AuthLockout:
    """Locks an IP address out after too many failed authentications in a time window."""

    def __init__(self, max_failures: int, window_seconds: float,
                 clock: Clock = time.monotonic) -> None:
        self._max_failures = max_failures
        self._window       = window_seconds
        self._clock        = clock
        self._lock         = threading.Lock()
        self._failures    : Dict[str, List[float]] = {}
        self._locked_until: Dict[str, float]       = {}

    def record_failure(self, host: str) -> bool:
        """Counts a failed authentication.

        Args:
            host: IP address only, without the port.

        Returns:
            True when this failure pushed the host over the limit and locked it out.
        """
        now = self._clock()
        with self._lock:
            recent = [t for t in self._failures.get(host, []) if now - t < self._window]
            recent.append(now)
            self._failures[host] = recent
            if len(recent) <= self._max_failures:
                return False
            self._locked_until[host] = now + self._window
            del self._failures[host]
            return True

    def is_locked(self, host: str) -> bool:
        """Tells whether connections from the host are currently refused."""
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(host)
            if until is None:
                return False
            if now >= until:
                del self._locked_until[host]
                return False
            return True

    def clear(self, host: str) -> None:
        """Forgets earlier failures after a successful authentication."""
        with self._lock:
            self._failures.pop(host, None)
