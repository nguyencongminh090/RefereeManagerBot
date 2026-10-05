"""Authentication of the bots by shared token, with an address lockout."""
import hmac
import logging
import time
from dataclasses import dataclass
from datetime    import datetime, timezone
from typing      import Any, Callable, Dict

from network.messages   import ResponseType
from serverapp.link     import Addr, ClientLink
from serverapp.sessions import AuthLockout, SessionRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthOptions:
    """Settings of the authentication.

    Attributes:
        bot_token: The shared secret; empty refuses every bot.
        tournament: Name sent back in AUTH_OK.
        heartbeat_seconds: Heartbeat interval sent back in AUTH_OK.
        max_failures: Failed attempts allowed per address within the window.
        lockout_seconds: Length of the failure window and of the lockout.
        clock: Monotonic time source in seconds.
    """
    bot_token        : str
    tournament       : str
    heartbeat_seconds: float
    max_failures     : int
    lockout_seconds  : float
    clock            : Callable[[], float] = time.monotonic


class AuthHandler:
    """Handles AUTH packets and remembers failed attempts per address."""

    def __init__(self, sessions: SessionRegistry, link: ClientLink, options: AuthOptions) -> None:
        self._sessions = sessions
        self._link     = link
        self._options  = options
        self._lockout  = AuthLockout(options.max_failures, options.lockout_seconds, options.clock)

    def is_locked(self, host: str) -> bool:
        """Tells whether connections from this IP address are refused."""
        return self._lockout.is_locked(host)

    def on_auth(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Authenticates the bot when the token matches, otherwise refuses and closes the link."""
        data  = packet.get('data') or {}
        token = str(data.get('token') or "")
        expected = self._options.bot_token
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return self._reject(addr)
        name = str(data.get('bot_name') or f"{addr[0]}:{addr[1]}")
        self._lockout.clear(addr[0])
        self._sessions.authenticate(addr, name)
        logger.info("bot '%s' authenticated from %s", name, addr)
        server_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._link.reply(addr, ResponseType.AUTH_OK, server_time=server_time,
                         tournament=self._options.tournament,
                         heartbeat_seconds=self._options.heartbeat_seconds)

    def _reject(self, addr: Addr) -> None:
        logger.warning("authentication failed from %s", addr)
        if self._lockout.record_failure(addr[0]):
            logger.warning("address %s locked out for %ss after repeated failed authentication",
                           addr[0], self._options.lockout_seconds)
        self._link.error(addr, "AUTH_FAILED", "wrong token")
        self._link.close(addr)
