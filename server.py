"""Central referee server: composition root and CLI entry point."""
import argparse
import hmac
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing   import Any, Callable, Dict, List, Optional, Tuple

from config.settings       import ConfigError, ConfigLoader, ServerConfig, Settings
from domain.ports           import IScoreObserver, ITeamRepository, MatchRecord, PairScoreChange
from domain.types          import Scoring
from network.messages      import RequestType, ResponseType
from network.options       import ServerSocketOptions
from network.server_socket import TcpServerSocket
from serverapp.backup      import BackupSchedule, DatabaseBackup
from serverapp.bot_status  import BotStatusProvider
from serverapp.claims      import ClaimRegistry
from serverapp.match_request import (MatchResultRequest, SetScoreRequest, match_id_of,
                                     parse_match_result, parse_set_score)
from serverapp.router      import PacketRouter
from serverapp.sessions    import AuthLockout, SessionRegistry
from storage               import Database, RepositoryOptions, SqliteTeamRepository, TournamentStore
from storage.models        import RankingRules, TournamentSpec
from webui.actions         import ActionOptions, DashboardActions
from webui.http_server     import DashboardServer
from webui.public_server   import PublicServer
from webui.public_state    import CachedSnapshot, PublicOptions, PublicState
from webui.state           import DashboardOptions, DashboardState
from storage.errors        import (DuplicateGameError, MicroMatchFullError, NotFoundError,
                                   PlayerInactiveError, SameEntrantError, StorageError,
                                   UnknownPlayerError, ValidationError)


logger = logging.getLogger(__name__)

Addr = Tuple[str, int]

# most specific first: UnknownPlayerError is a NotFoundError
_ERROR_CODES = ((UnknownPlayerError, "UNKNOWN_PLAYER"), (PlayerInactiveError, "PLAYER_INACTIVE"),
                (SameEntrantError, "SAME_TEAM"), (MicroMatchFullError, "MICROMATCH_FULL"),
                (ValidationError, "BAD_RESULT"), (NotFoundError, "NOT_FOUND"))
_MISSED_HEARTBEATS    = 3
_MAX_SWEEP_TICK_SEC   = 1.0
_NO_TABLE_LABEL       = "-"
_SET_SCORE            = RequestType.SET_SCORE.value   # marks errors that answer a !set
_SERVER_ACTOR         = "server"   # audit-log name for changes the server makes itself
_EXIT_CONFIG          = 2          # the config or secrets are wrong
_EXIT_STARTUP         = 3          # the config is fine but the server cannot start (busy port)


class StartupError(Exception):
    """The server cannot start although the config is valid, for example a busy port."""


def _port_problem(source: str, section: str, bind: Tuple[str, int], exc: OSError) -> StartupError:
    host, port = bind
    return StartupError(
        f"the {section} cannot listen on {host}:{port}: {exc.strerror or exc}. "
        f"Stop the program that uses the port or change 'port' under [{section}] in {source}.")


def ranking_rules(settings: Settings) -> RankingRules:
    """Builds the standings order from the config.

    Team events rank by match points, individual ones by game points.
    """
    tournament = settings.tournament
    scoring    = tournament.scoring
    match_points = None
    if tournament.format == "team":
        match_points = Scoring(scoring.match_win, scoring.match_draw, scoring.match_loss)
    return RankingRules(scoring.tiebreaks, match_points)


def _socket_options(cfg: ServerConfig) -> ServerSocketOptions:
    return ServerSocketOptions(max_clients=cfg.max_clients, max_packet_bytes=cfg.max_packet_bytes,
                               send_timeout_seconds=cfg.send_timeout_seconds)


class Server(IScoreObserver):
    """Composition root of the central server: wires the socket, database and collaborators.

    Receives game results from the referee bots, records them in the tournament database and
    broadcasts the new standings. A payload that is not valid JSON makes the socket layer close
    that client's connection; a well-formed packet with bad fields or a rejected result gets an
    ERROR packet and the connection stays open.
    """

    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        """Opens the database, loads or checks the tournament and builds the collaborators.

        Args:
            settings: Validated configuration.
            clock: Monotonic time source in seconds; replaced in tests.

        Raises:
            ConfigError: If the database holds a tournament of another format than the config.
        """
        self.settings = settings
        cfg           = settings.server
        self._db      = Database(settings.database.path)
        self._store   = TournamentStore(self._db)
        self._admin   = self._store.with_actor(_SERVER_ACTOR)
        tournament_id = self._ensure_tournament()
        self._load_teams_file(tournament_id)

        self._repo: ITeamRepository = SqliteTeamRepository(
            self._store, tournament_id,
            RepositoryOptions(settings.tournament.scoring.games, ranking=ranking_rules(settings)))
        self._repo.subscribe(self)

        self._sessions = SessionRegistry(clock, cfg.heartbeat_seconds * _MISSED_HEARTBEATS,
                                         cfg.auth_timeout_seconds)
        self._lockout  = AuthLockout(cfg.auth_max_failures, cfg.auth_lockout_seconds, clock)
        self._claims   = ClaimRegistry()
        self._backups  = BackupSchedule(DatabaseBackup(self._db).run, cfg.backup_seconds, clock)
        self._router   = PacketRouter(self._handlers())
        self._dashboard = self._open_dashboard(tournament_id)
        self._public    = self._open_public(tournament_id)
        self._running  = threading.Event()
        self._stopped  = False

        self._socket = TcpServerSocket(
            host                   = cfg.host,
            port                   = cfg.port,
            on_receive_callback    = self._handle_client_message,
            on_connect_callback    = self._on_connect,
            on_disconnect_callback = self._on_disconnect,
            options                = _socket_options(cfg),
        )

    @property
    def port(self) -> int:
        """The TCP port actually bound (differs from the config when it says 0)."""
        return self._socket.port

    @property
    def dashboard_port(self) -> Optional[int]:
        """The TCP port of the organizer web page, or None when the dashboard is disabled."""
        return self._dashboard.port if self._dashboard else None

    @property
    def public_port(self) -> Optional[int]:
        """The TCP port of the audience page, or None when it is disabled."""
        return self._public.port if self._public else None

    def _open_public(self, tournament_id: int) -> Optional[PublicServer]:
        """Builds the audience page; a busy port closes what is already open and aborts."""
        config = self.settings.public
        if not config.enabled:
            return None
        options = PublicOptions(self.settings.tournament.scoring.games, ranking_rules(self.settings),
                                config.recent_games)
        state = PublicState(self._store, tournament_id,
                            BotStatusProvider(self._sessions, self._claims), options)
        try:
            return PublicServer(config, CachedSnapshot(state, config.refresh_seconds / 2))
        except OSError as exc:
            if self._dashboard:
                self._dashboard.stop()
            self._db.close()
            raise _port_problem(self.settings.source, "public",
                                (config.host, config.port), exc) from exc

    def _open_dashboard(self, tournament_id: int) -> Optional[DashboardServer]:
        """Builds the dashboard; a busy port becomes a StartupError and closes the database."""
        try:
            return self._build_dashboard(tournament_id)
        except OSError as exc:
            self._db.close()
            config = self.settings.dashboard
            raise _port_problem(self.settings.source, "dashboard",
                                (config.host, config.port), exc) from exc

    def _build_dashboard(self, tournament_id: int) -> Optional[DashboardServer]:
        config = self.settings.dashboard
        if not config.enabled:
            return None
        scoring = self.settings.tournament.scoring.games
        options = DashboardOptions(scoring, ranking_rules(self.settings), config.recent_games,
                                   config.audit_entries, config.allow_edit)
        state = DashboardState(self._store, tournament_id,
                               BotStatusProvider(self._sessions, self._claims), options)
        actions = None
        if config.allow_edit:
            # a change from the page re-broadcasts the standings to the bots, like a bot result
            actions = DashboardActions(self._store, tournament_id,
                                       ActionOptions(scoring, self._repo.notify_all))
        return DashboardServer(config, self.settings.secrets.dashboard_token or "", state,
                               actions)

    def _handlers(self) -> Dict[int, Callable[[Addr, Dict[str, Any]], None]]:
        return {
            RequestType.MATCH_RESULT.value  : self._on_match_result,
            RequestType.SCORE_QUERY.value   : self._on_score_query,
            RequestType.SET_SCORE.value     : self._on_set_score,
            RequestType.ROSTER_QUERY.value  : self._on_roster_query,
            RequestType.TABLE_CLAIM.value   : self._on_table_claim,
            RequestType.TABLE_RELEASE.value : self._on_table_release,
            RequestType.HEARTBEAT.value     : lambda addr, packet: None,
        }

    # ------------------------------------------------------------------ start-up
    def _ensure_tournament(self) -> int:
        t = self.settings.tournament
        try:
            existing = self._store.get_tournament(t.name)
        except NotFoundError:
            tid = self._admin.create_tournament(TournamentSpec(
                t.name, t.format, year=t.year, team_size=t.team_size,
                max_substitutes=t.max_substitutes or 0, games_per_pair=t.total_matches,
                nickname_prefix=t.table_rules.player_prefix or None))
            logger.info("created tournament '%s' (%s)", t.name, t.format)
            return tid
        if existing["format"] != t.format:
            raise ConfigError(self.settings.source, [
                f"tournament '{t.name}' is a {existing['format']} tournament in the database "
                f"but the config says {t.format}"])
        if existing["games_per_pair"] != t.total_matches:
            logger.warning("database has games_per_pair=%s but the config says total_matches=%s "
                           "(database wins; change it with admin_db tournament set)",
                           existing["games_per_pair"], t.total_matches)
        return existing["id"]

    def _load_teams_file(self, tournament_id: int) -> None:
        path = self.settings.database.teams_file
        if not path:
            return
        if not os.path.isfile(path):
            logger.warning("teams file %s not found; using the roster already in the database",
                           path)
            return
        summary = self._admin.import_roster_csv(tournament_id, path)
        logger.info("roster import from %s: %s", path, summary)

    def start(self, block: bool = True) -> None:
        """Starts listening and housekeeping.

        Args:
            block: If true, waits until stopped (Ctrl+C or request_stop), then stops the server.
        """
        try:
            self._socket.start_listening()
        except OSError as exc:
            self.stop()
            cfg = self.settings.server
            raise _port_problem(self.settings.source, "server", (cfg.host, cfg.port), exc) from exc
        for page in (self._dashboard, self._public):
            if page:
                page.start()
        self._running.set()
        threading.Thread(target=self._housekeeping, daemon=True, name="housekeeping").start()
        logger.info("server listening on %s:%s", self.settings.server.host, self.port)
        if block:
            try:
                while self._running.is_set():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            self.stop()

    def request_stop(self) -> None:
        """Asks a blocking `start` to return (safe to call from a signal handler)."""
        self._running.clear()

    def stop(self) -> None:
        """Closes the socket, writes a last backup and closes the database; safe to call twice."""
        if self._stopped:
            return
        self._stopped = True
        self._running.clear()
        self._socket.stop()
        for page in (self._dashboard, self._public):
            if page:
                page.stop()
        self._backup_now()
        self._db.close()
        logger.info("server stopped")

    # --------------------------------------------------------------- housekeeping
    def _housekeeping(self) -> None:
        cfg  = self.settings.server
        tick = min(_MAX_SWEEP_TICK_SEC, cfg.heartbeat_seconds, cfg.backup_seconds,
                   cfg.auth_timeout_seconds)
        while self._running.is_set():
            time.sleep(tick)
            for addr, reason in self._sessions.pop_expired():
                logger.warning("closing %s: %s", addr, reason)
                self._socket.close_client(addr)
            if self._running.is_set():
                self._backups.tick()

    def _backup_now(self) -> None:
        try:
            DatabaseBackup(self._db).run()
        except Exception:
            logger.exception("backup of %s failed", self._db.path)

    # ------------------------------------------------------------------ callbacks
    def _on_connect(self, addr: Addr) -> None:
        if self._lockout.is_locked(addr[0]):
            logger.info("refusing %s: address is locked out", addr)
            self._socket.close_client(addr)
            return
        self._sessions.connect(addr)

    def _on_disconnect(self, addr: Addr) -> None:
        self._sessions.disconnect(addr)
        self._claims.release_all_for(addr)

    def on_score_updated(self, snapshot: str) -> None:
        """Broadcasts the new standings text to every authenticated bot."""
        for addr in self._sessions.authenticated():
            self._socket.send_packet(addr, {'type': ResponseType.BROADCAST.value, 'text': snapshot})

    # ------------------------------------------------------------------- packets
    def _reply(self, addr: Addr, response: ResponseType, **data: Any) -> None:
        self._socket.send_packet(addr, {'type': response.value, 'data': data})

    def _error(self, addr: Addr, code: str, message: str, **extra: Any) -> None:
        self._reply(addr, ResponseType.ERROR, code=code, message=message, **extra)

    def _handle_client_message(self, client_addr: Addr, packet: Dict[str, Any]):
        self._sessions.touch(client_addr)
        try:
            packet_type = packet.get('type') if isinstance(packet, dict) else None
            if packet_type == RequestType.AUTH.value:
                return self._on_auth(client_addr, packet)
            if not self._sessions.is_authenticated(client_addr):
                return self._error(client_addr, "NOT_AUTHENTICATED", "send AUTH first")
            if not self._router.route(client_addr, packet):
                self._error(client_addr, "UNKNOWN_TYPE", f"unknown packet type {packet_type!r}")
        except Exception:
            logger.exception("failed to handle packet from %s", client_addr)
            self._error(client_addr, "INTERNAL", "the server could not process this packet")

    def _on_auth(self, addr: Addr, packet: Dict[str, Any]) -> None:
        data     = packet.get('data') or {}
        token    = str(data.get('token') or "")
        expected = self.settings.secrets.bot_token or ""
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return self._reject_auth(addr)
        name = str(data.get('bot_name') or f"{addr[0]}:{addr[1]}")
        self._lockout.clear(addr[0])
        self._sessions.authenticate(addr, name)
        logger.info("bot '%s' authenticated from %s", name, addr)
        server_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._reply(addr, ResponseType.AUTH_OK, server_time=server_time,
                    tournament=self.settings.tournament.name,
                    heartbeat_seconds=self.settings.server.heartbeat_seconds)

    def _reject_auth(self, addr: Addr) -> None:
        logger.warning("authentication failed from %s", addr)
        if self._lockout.record_failure(addr[0]):
            logger.warning("address %s locked out for %ss after repeated failed authentication",
                           addr[0], self.settings.server.auth_lockout_seconds)
        self._error(addr, "AUTH_FAILED", "wrong token")
        self._socket.close_client(addr)

    def _on_match_result(self, addr: Addr, packet: Dict[str, Any]) -> None:
        try:
            request = parse_match_result(packet)
        except ValueError as exc:
            match_id = match_id_of(packet)
            logger.info("result %s rejected (BAD_PACKET): %s", match_id, exc)
            return self._error(addr, "BAD_PACKET", str(exc), match_id=match_id)
        self._record_result(addr, request)

    def _record_result(self, addr: Addr, request: MatchResultRequest) -> None:
        bot_name = self._sessions.name_of(addr)
        try:
            game_id = self._repo.record_match(MatchRecord(
                request.players[0], request.results[0], request.players[1], request.results[1],
                match_id=request.match_id, table_no=request.table_no, bot_name=bot_name))
            duplicate = False
            table_label = _NO_TABLE_LABEL if request.table_no is None else request.table_no
            logger.info("result %s accepted from bot '%s': table %s, %s vs %s",
                        request.match_id, bot_name, table_label, *request.players)
        except DuplicateGameError as exc:           # a retry: confirm again, record nothing
            game_id, duplicate = exc.game_id, True
            logger.info("result %s duplicate from bot '%s': confirmed again",
                        request.match_id, bot_name)
        except StorageError as exc:
            code = next((c for cls, c in _ERROR_CODES if isinstance(exc, cls)), "REJECTED")
            logger.info("result %s rejected (%s): %s", request.match_id, code, exc)
            return self._error(addr, code, str(exc), match_id=request.match_id)
        self._reply(addr, ResponseType.MATCH_ACK, match_id=request.match_id, duplicate=duplicate,
                    **self._repo.pair_score(game_id))

    def _on_set_score(self, addr: Addr, packet: Dict[str, Any]) -> None:
        try:
            request = parse_set_score(packet)
        except ValueError as exc:
            logger.info("set-score request rejected (BAD_PACKET): %s", exc)
            return self._error(addr, "BAD_PACKET", str(exc), request=_SET_SCORE)
        if not self.settings.tournament.is_admin(request.sender):
            logger.warning("set-score request from '%s' refused: not an admin", request.sender)
            return self._error(addr, "NOT_ADMIN", f"'{request.sender}' is not a tournament admin",
                               request=_SET_SCORE)
        self._apply_set_score(addr, request)

    def _apply_set_score(self, addr: Addr, request: SetScoreRequest) -> None:
        change = PairScoreChange(*request.players, *request.points, actor=request.sender)
        try:
            score = self._repo.set_pair_score(change)
        except StorageError as exc:
            code = next((c for cls, c in _ERROR_CODES if isinstance(exc, cls)), "REJECTED")
            logger.info("set-score by '%s' rejected (%s): %s", request.sender, code, exc)
            return self._error(addr, code, str(exc), request=_SET_SCORE)
        logger.info("score of %s and %s set to %s by '%s'", *request.players, request.points,
                    request.sender)
        self._reply(addr, ResponseType.SCORE_SET, **score)

    def _on_score_query(self, addr: Addr, packet: Dict[str, Any]) -> None:
        self._socket.send_packet(
            addr, {'type': ResponseType.SCORE_DATA.value, 'text': self._repo.snapshot()})

    def _on_roster_query(self, addr: Addr, packet: Dict[str, Any]) -> None:
        players = [{'name': p['nickname'], 'team': p['entrant_name'], 'role': p['role'],
                    'active': bool(p['active'])} for p in self._repo.roster()]
        self._reply(addr, ResponseType.ROSTER_DATA, players=players)

    def _table_no(self, addr: Addr, packet: Dict[str, Any]) -> Optional[int]:
        table_no = (packet.get('data') or {}).get('table_no')
        if isinstance(table_no, int) and not isinstance(table_no, bool):
            return table_no
        self._error(addr, "BAD_PACKET", "data.table_no must be a number")
        return None

    def _on_table_claim(self, addr: Addr, packet: Dict[str, Any]) -> None:
        table_no = self._table_no(addr, packet)
        if table_no is None:
            return
        owner = self._claims.claim(table_no, addr)
        if owner != addr and self._is_same_bot(owner, addr):
            # the bot reconnected before the server dropped its old link: the table stays with the bot
            owner = addr if self._claims.take_over(table_no, owner, addr) else owner
        if owner == addr:
            self._reply(addr, ResponseType.CLAIM_OK, table_no=table_no)
        else:
            self._reply(addr, ResponseType.CLAIM_DENIED, table_no=table_no,
                        held_by=self._sessions.name_of(owner))

    def _is_same_bot(self, first: Addr, second: Addr) -> bool:
        name = self._sessions.name_of(first)
        return name is not None and name == self._sessions.name_of(second)

    def _on_table_release(self, addr: Addr, packet: Dict[str, Any]) -> None:
        table_no = self._table_no(addr, packet)
        if table_no is None:
            return
        if not self._claims.release(table_no, addr):
            self._error(addr, "NOT_OWNER", f"table {table_no} is not claimed by this bot")


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the server from the command line; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Referee Manager Bot: central server")
    parser.add_argument("--config",
                        help="config file (default: $REFEREE_CONFIG or config/config.toml)")
    parser.add_argument("--env", help="secrets file (default: .env)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        server = Server(ConfigLoader.load(args.config, env_file=args.env, role="server"))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return _EXIT_CONFIG
    except StartupError as exc:
        print(exc, file=sys.stderr)
        return _EXIT_STARTUP
    signal.signal(signal.SIGTERM, lambda *_: server.request_stop())
    try:
        server.start()
    except StartupError as exc:
        print(exc, file=sys.stderr)
        return _EXIT_STARTUP
    return 0


if __name__ == "__main__":
    sys.exit(main())
