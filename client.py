"""The referee bot: logs in to PlayOK, finds a table to referee and reports its games."""
import argparse
import logging
import queue
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from config.settings       import ConfigError, ConfigLoader, Settings
from network.client_socket import TcpClientSocket
from network.messages      import RequestType, ResponseType
from network.options       import ClientSocketOptions
from network.ports         import IClientSocket
from referee.browser       import create_firefox
from referee.commands.dispatcher import CommandDispatcher
from referee.commands.handlers   import CommandHandlers
from referee.driver        import SeleniumDriver, SilentSeleniumDriver
from referee.driver_port   import DriverError, IDriver
from referee.lobby         import LobbyWatcher
from referee.session       import MatchSession, SessionState

logger = logging.getLogger(__name__)

JOIN_AUTO     = "auto"
JOIN_INVITE   = "invite"
BOT_NAME_BASE = "referee-bot"
# consecutive main-loop passes that may fail on the page before the bot gives up (about 15 s)
MAX_CONSECUTIVE_DRIVER_ERRORS = 30
# attempts, and seconds between them, for opening PlayOK and entering the lobby at start-up
STARTUP_ATTEMPTS      = 5
STARTUP_RETRY_SECONDS = 3.0


class Client:
    """Runs the main loop: server messages, then the current table, or the search for a new one."""

    def __init__(self, settings: Settings, driver: IDriver,
                 socket_factory: Callable[[Callable[[Dict[str, Any]], None]], IClientSocket],
                 clock: Callable[[], float] = time.monotonic):
        """Wires the client to its ports.

        Args:
            settings: Validated configuration.
            driver: Browser access to PlayOK.
            socket_factory: Builds the server socket from the callback for incoming packets.
            clock: Returns monotonic seconds; injected so that tests need no wall clock.
        """
        self._settings   = settings
        self._driver     = driver
        self._socket     = socket_factory(self._handle_server_message)
        self._clock      = clock
        self._lobby      = LobbyWatcher()
        self._dispatcher = CommandDispatcher(driver, self._socket)
        self._session    : Optional[MatchSession] = None
        self._table      : Optional[int]          = None     # table of the current session
        self._claiming   : Optional[int]          = None     # table we asked the server to reserve
        self._messages   : "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._next_scan  = 0.0
        self._driver_errors = 0                              # failed passes in a row
        CommandHandlers(settings).register_on(self._dispatcher)

    def start(self) -> None:
        """Connects, logs in and referees until interrupted.

        Raises:
            ConnectionError: If the server is unreachable or refuses the token.
            RuntimeError: If PlayOK refuses the login.
        """
        secrets = self._settings.secrets
        try:
            self._socket.connect()
            self._retry_on_page_error(self._driver.open_site, "open PlayOK")
            if not self._driver.login(secrets.playok_user, secrets.playok_pass):
                raise RuntimeError("PlayOK login failed: login form not found")
            self._retry_on_page_error(self._driver.goto_lobby, "enter the lobby")
            self._socket.send_packet({"type": RequestType.ROSTER_QUERY.value, "data": {}})
            while True:
                self.step()
                time.sleep(self._settings.client.poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _retry_on_page_error(self, action: Callable[[], None], what: str) -> None:
        """Runs a start-up page action, retrying page errors up to STARTUP_ATTEMPTS times.

        Raises:
            RuntimeError: If every attempt fails on the page.
        """
        for attempt in range(1, STARTUP_ATTEMPTS + 1):
            try:
                action()
                return
            except DriverError as error:
                if attempt == STARTUP_ATTEMPTS:
                    raise RuntimeError(f"could not {what} after {attempt} attempts: {error}") from error
                logger.warning("could not %s (attempt %d of %d): %s", what, attempt,
                               STARTUP_ATTEMPTS, error)
                time.sleep(STARTUP_RETRY_SECONDS)

    def stop(self) -> None:
        """Releases the table, disconnects from the server and closes the browser."""
        self._release_table()
        self._socket.disconnect()
        self._driver.quit()

    def step(self) -> None:
        """One pass of the main loop; a page error is logged and the pass is retried next time.

        Raises:
            RuntimeError: If the page fails MAX_CONSECUTIVE_DRIVER_ERRORS passes in a row.
        """
        try:
            self._run_pass()
        except DriverError as error:
            self._driver_errors += 1
            if self._driver_errors >= MAX_CONSECUTIVE_DRIVER_ERRORS:
                raise RuntimeError(
                    f"PlayOK page failed {self._driver_errors} passes in a row: {error}") from error
            logger.warning("page error (%d in a row): %s", self._driver_errors, error)
            return
        self._driver_errors = 0

    def _run_pass(self) -> None:
        self._process_server_messages()
        self._finish_if_completed()
        if self._session is None:
            self._look_for_table()
            return
        self._session.poll()
        self._finish_if_completed()

    # ------------------------------------------------------------ server messages
    def _handle_server_message(self, packet: Dict[str, Any]) -> None:
        self._messages.put(packet)

    def _process_server_messages(self) -> None:
        while True:
            try:
                packet = self._messages.get_nowait()
            except queue.Empty:
                return
            self._on_server_message(packet)

    def _on_server_message(self, packet: Dict[str, Any]) -> None:
        handlers = {
            ResponseType.AUTH_OK.value     : self._on_connected,
            ResponseType.SCORE_DATA.value  : self._on_standings_text,
            ResponseType.BROADCAST.value   : self._on_standings_text,
            ResponseType.ROSTER_DATA.value : self._on_roster_data,
            ResponseType.CLAIM_OK.value    : self._on_claim_ok_packet,
            ResponseType.CLAIM_DENIED.value: self._on_claim_denied_packet,
            ResponseType.MATCH_ACK.value   : self._on_match_ack,
            ResponseType.SCORE_SET.value   : self._on_score_set,
            ResponseType.ERROR.value       : self._on_server_error,
        }
        handler = handlers.get(packet.get("type"))
        if handler is not None:
            handler(packet)

    def _on_connected(self, packet: Dict[str, Any]) -> None:
        """Re-asserts the claims after every (re)connection: the server forgets them when a link closes.

        The claim is idempotent for its owner, so asking again is always safe.
        """
        for table_no in {self._table, self._claiming} - {None}:
            self._socket.send_packet({"type": RequestType.TABLE_CLAIM.value,
                                      "data": {"table_no": table_no}})

    def _on_standings_text(self, packet: Dict[str, Any]) -> None:
        if self._session is not None:
            self._driver.send_message(str(packet.get("text", "")))

    def _on_roster_data(self, packet: Dict[str, Any]) -> None:
        players = (packet.get("data") or {}).get("players", [])
        self._lobby.set_roster({p["name"]: p["team"] for p in players if p.get("active")})

    def _on_claim_ok_packet(self, packet: Dict[str, Any]) -> None:
        self._on_claim_ok((packet.get("data") or {}).get("table_no"))

    def _on_claim_denied_packet(self, packet: Dict[str, Any]) -> None:
        self._on_claim_denied((packet.get("data") or {}).get("table_no"))

    def _on_match_ack(self, packet: Dict[str, Any]) -> None:
        if self._session is not None:
            self._session.on_ack(packet.get("data") or {})

    def _on_score_set(self, packet: Dict[str, Any]) -> None:
        if self._session is not None:
            self._session.on_score_set(packet.get("data") or {})

    def _on_server_error(self, packet: Dict[str, Any]) -> None:
        data     = packet.get("data") or {}
        match_id = data.get("match_id")
        if data.get("request") == RequestType.SET_SCORE.value and self._session is not None:
            self._session.on_set_refused(str(data.get("message", data.get("code"))))
            return
        if match_id and self._session is not None:
            self._session.on_rejected(str(data.get("code")), match_id)
            return
        logger.warning("server error %s: %s (match %s)",
                       data.get("code"), data.get("message"), match_id)

    def _on_claim_ok(self, table_no: Optional[int]) -> None:
        if table_no != self._claiming or self._session is not None:
            return
        self._claiming = None
        try:
            joined = self._driver.join_table(table_no)
        except DriverError as error:
            # a page glitch says nothing about the table: release it and let a later scan retry
            logger.warning("join of table %s failed on the page, will retry: %s", table_no, error)
            self._send_table_release(table_no)
            return
        if joined:
            self._start_session(table_no)
            return
        logger.warning("could not join table %s", table_no)
        self._lobby.skip(table_no)
        self._send_table_release(table_no)

    def _on_claim_denied(self, table_no: Optional[int]) -> None:
        if table_no == self._claiming:
            self._claiming = None
            self._next_scan = 0.0           # another bot was faster: look for a different table now
        if table_no is not None:
            self._lobby.skip(table_no)
        if table_no is not None and table_no == self._table:
            self._abandon_table()

    def _abandon_table(self) -> None:
        """Leaves a table another bot now holds, so that its games are not reported twice."""
        logger.warning("table %s is held by another bot now; leaving it", self._table)
        self._table = None                   # not ours any more: there is nothing to release
        if self._session is not None:
            self._session.leave()

    # ------------------------------------------------------------- finding tables
    def _look_for_table(self) -> None:
        mode = self._settings.client.join_mode
        if mode == JOIN_INVITE:
            self._accept_invitation()
        elif mode == JOIN_AUTO:
            self._scan_lobby()

    def _accept_invitation(self) -> None:
        inviter = self._driver.check_for_invitation()
        is_admin = inviter and self._settings.tournament.is_admin(inviter)
        if is_admin and self._driver.accept_invitation():
            self._start_session(self._driver.get_table_number())

    def _scan_lobby(self) -> None:
        now = self._clock()
        if self._claiming is not None or now < self._next_scan or not self._lobby.has_roster:
            return
        self._next_scan = now + self._settings.client.lobby_scan_seconds
        table = self._lobby.pick(self._driver.lobby_tables())
        if table is None:
            return
        self._claiming = table.number
        self._socket.send_packet({"type": RequestType.TABLE_CLAIM.value,
                                  "data": {"table_no": table.number}})

    # ------------------------------------------------------------------ sessions
    def _start_session(self, table_no: Optional[int]) -> None:
        self._table   = table_no
        self._session = MatchSession(self._driver, self._dispatcher, self._socket, self._settings)
        logger.info("refereeing table %s", table_no)

    def _finish_if_completed(self) -> None:
        if self._session is not None and self._session.context.state == SessionState.COMPLETED:
            self._finish_session()

    def _finish_session(self) -> None:
        if self._session is not None:
            self._session.ensure_left()      # a page error here is retried on the next pass
        self._release_table()
        self._session = None

    def _release_table(self) -> None:
        if self._table is None:
            return
        self._lobby.skip(self._table)             # the players may still sit there; do not re-enter
        self._send_table_release(self._table)
        self._table = None

    def _send_table_release(self, table_no: int) -> None:
        self._socket.send_packet({"type": RequestType.TABLE_RELEASE.value,
                                  "data": {"table_no": table_no}})


def build_client(settings: Settings, headless: bool = False, no_chat: bool = False) -> Client:
    """Wires the real browser and TCP socket into a Client.

    `no_chat` makes the bot read the table chat without ever writing to it.

    Raises:
        ConfigError: If a secret needed by the client is missing.
    """
    secrets = settings.secrets
    required = (("PLAYOK_USER", secrets.playok_user), ("PLAYOK_PASS", secrets.playok_pass),
                ("BOT_TOKEN", secrets.bot_token))
    missing = [name for name, value in required if not value]
    if missing:
        raise ConfigError(settings.source, [f"missing secret {name}" for name in missing])
    cfg = settings.client
    options = ClientSocketOptions(reconnect_max_seconds=cfg.reconnect_max_seconds,
                                  outbox_path=cfg.outbox_path or None)

    def socket_factory(on_receive: Callable[[Dict[str, Any]], None]) -> IClientSocket:
        """Opens a TCP client socket that reports its packets to `on_receive`."""
        return TcpClientSocket(cfg.server_host, cfg.server_port, on_receive, secrets.bot_token,
                               f"{BOT_NAME_BASE}-{secrets.playok_user}", options=options)

    driver_class = SilentSeleniumDriver if no_chat else SeleniumDriver
    driver = driver_class(settings.playok, create_firefox(headless))
    return Client(settings, driver, socket_factory)


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the referee client from the command line.

    Args:
        argv: Command-line arguments; None reads `sys.argv`.

    Returns:
        The process exit code: 0 after a normal stop, 2 for a configuration or connection error.
    """
    parser = argparse.ArgumentParser(description="Referee Manager Bot: referee client")
    parser.add_argument("--config",
                        help="config file (default: $REFEREE_CONFIG or config/config.toml)")
    parser.add_argument("--env", help="secrets file (default: .env)")
    parser.add_argument("--headless", action="store_true",
                        help="run Firefox without a window")
    parser.add_argument("--no-chat", action="store_true",
                        help="read the table chat but never write to it")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = ConfigLoader.load(args.config, env_file=args.env, role="client")
        client = build_client(settings, args.headless, args.no_chat)
        client.start()
    except (ConfigError, ConnectionError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
