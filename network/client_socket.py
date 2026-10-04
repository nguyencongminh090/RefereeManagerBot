"""Reconnecting TCP client socket with heartbeats and a durable result outbox."""
import logging
import socket
import threading
from typing           import Callable, Dict, Any, Optional
from network.messages import RequestType, ResponseType
from network.options  import ClientSocketOptions
from network.ports    import IClientSocket
from network.outbox   import ResultOutbox
from network.protocol import PacketProtocol, set_send_timeout


logger = logging.getLogger(__name__)

_FIRST_RECONNECT_DELAY_SEC = 1.0
_AUTH_FAILED_CODE          = "AUTH_FAILED"
_MISSED_HEARTBEATS         = 3          # the server drops a silent bot after this many


class TcpClientSocket(IClientSocket):
    """Keeps an authenticated connection to the server, reconnecting with backoff.

    Match results are never lost: each one is kept until the server answers MATCH_ACK (or an ERROR
    naming it) and is re-sent after every reconnect. The server is idempotent on `meta.match_id`,
    so a repeat is confirmed without being counted twice. Other packets sent while offline are
    dropped.

    Threads: one connection loop (also runs the receive callback) and one heartbeat loop.
    """

    def __init__(self,
                 host: str,
                 port: int,
                 on_receive_callback: Callable[[Dict], None],
                 token: str,
                 bot_name: str,
                 options: ClientSocketOptions = ClientSocketOptions()):
        """Creates the client; nothing connects until connect().

        Args:
            host: Server host name or address.
            port: Server port.
            on_receive_callback: Called with each server packet on the connection thread, except
                the authentication answers and MATCH_ACK/ERROR handling done by the socket itself;
                exceptions it raises are logged and swallowed.
            token: Secret sent in the AUTH packet.
            bot_name: Name announced to the server.
            options: Timeouts, heartbeat, packet cap and outbox file.
        """
        self._host                = host
        self._port                = port
        self._on_receive_callback = on_receive_callback
        self._token               = token
        self._bot_name            = bot_name
        self._options             = options

        self._sock         : Optional[socket.socket]  = None
        # Guards _sock and _heartbeat_interval only.
        self._lock         : threading.Lock           = threading.Lock()
        # Keeps frames whole; held at most one send timeout.
        self._send_lock    : threading.Lock           = threading.Lock()
        self._outbox       : ResultOutbox             = ResultOutbox(options.outbox_path)
        self._heartbeat_interval: float               = options.heartbeat_seconds
        # Wakes the heartbeat loop on a new interval or stop.
        self._heartbeat_changed : threading.Event     = threading.Event()
        self._stop         : threading.Event          = threading.Event()
        self._authed       : threading.Event          = threading.Event()
        self._first_answer : threading.Event          = threading.Event()  # AUTH_OK or refused
        self._auth_refused : bool                     = False
        self._threads      : list[threading.Thread]   = []

    @property
    def is_connected(self) -> bool:
        """True while the connection is open and the server has accepted the token."""
        return self._authed.is_set()

    @property
    def pending_results(self) -> int:
        """Number of match results the server has not confirmed yet."""
        return len(self._outbox)

    def connect(self) -> None:
        """Starts the background connection and waits for the first authentication.

        Raises:
            ConnectionError: If the token is refused or the server does not answer in time.
                The connection is closed in both cases.
        """
        if self._threads:
            return
        self._stop.clear()
        self._heartbeat_changed.clear()
        loops = ((self._run_loop, "client-socket"), (self._heartbeat_loop, "client-heartbeat"))
        for target, name in loops:
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            self._threads.append(thread)

        answered = self._first_answer.wait(self._options.connect_timeout_seconds + 1.0)
        if self._auth_refused or not answered:
            self.disconnect()
            reason = "the server refused the token" if self._auth_refused else \
                     f"no answer from {self._host}:{self._port}"
            raise ConnectionError(reason)

    def disconnect(self) -> None:
        """Stops both loops and closes the socket; unconfirmed results stay in the outbox."""
        self._stop.set()
        self._heartbeat_changed.set()
        self._drop_connection()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._threads.clear()
        self._authed.clear()
        self._first_answer.clear()

    def send_packet(self, payload: Dict[str, Any]) -> None:
        """Sends a packet; a match result is stored in the outbox first and survives offline time.

        Any other packet is dropped (with a warning) while the client is not authenticated.
        Thread-safe: sends are serialized by a lock.
        """
        match_id = self._result_id(payload)
        if match_id is not None:
            self._outbox.add(match_id, payload)         # durable before it is sent
        if not self._authed.is_set():
            if match_id is None:
                logger.warning("offline, dropped packet of type %s", payload.get("type"))
            return
        self._send(payload)

    @staticmethod
    def _result_id(payload: Dict[str, Any]) -> Optional[str]:
        if payload.get("type") != RequestType.MATCH_RESULT.value:
            return None
        match_id = (payload.get("meta") or {}).get("match_id")
        return match_id if isinstance(match_id, str) and match_id else None

    # --------------------------------------------------------------- connection
    def _run_loop(self) -> None:
        delay = _FIRST_RECONNECT_DELAY_SEC
        while not self._stop.is_set():
            sock = self._open()
            if sock is not None:
                was_authed = self._serve(sock)
                if self._auth_refused:
                    return
                delay = _FIRST_RECONNECT_DELAY_SEC if was_authed else delay
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self._options.reconnect_max_seconds)

    def _open(self) -> Optional[socket.socket]:
        try:
            sock = socket.create_connection((self._host, self._port),
                                            timeout=self._options.connect_timeout_seconds)
        except OSError as exc:
            logger.warning("cannot reach %s:%s (%s), retrying", self._host, self._port, exc)
            return None
        sock.settimeout(None)
        set_send_timeout(sock, self._options.send_timeout_seconds)
        with self._lock:
            self._sock = sock
        return sock

    def _serve(self, sock: socket.socket) -> bool:
        """Runs one connection until it ends; returns True if the server authenticated us."""
        was_authed = False
        try:
            self._send({"type": RequestType.AUTH.value,
                        "data": {"bot_name": self._bot_name, "token": self._token}})
            while not self._stop.is_set():
                packet = PacketProtocol.receive_packet(sock, self._options.max_packet_bytes)
                if packet is None:
                    break
                was_authed = self._handle_packet(packet) or was_authed
        except (OSError, ValueError) as exc:         # ValueError: bad JSON, PacketTooLargeError
            if not self._stop.is_set():
                logger.warning("connection to the server lost: %s", exc)
        finally:
            self._drop_connection()
        return was_authed

    def _drop_connection(self) -> None:
        self._authed.clear()
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)          # wakes the reader blocked in recv
        except OSError:
            pass
        sock.close()

    def _send(self, payload: Dict[str, Any]) -> None:
        """Sends one frame; a failed or timed-out send drops the connection (loop reconnects)."""
        with self._lock:
            sock = self._sock
        if sock is None:
            return
        try:
            with self._send_lock:
                sock.sendall(PacketProtocol.encode(payload))
        except OSError as exc:
            logger.warning("send failed (%s), reconnecting", exc)
            self._drop_connection()

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                interval = self._heartbeat_interval
            if self._heartbeat_changed.wait(interval):
                self._heartbeat_changed.clear()         # new interval or stop: re-read
                continue
            if self._authed.is_set():
                self._send({"type": RequestType.HEARTBEAT.value, "data": {}})

    def _adopt_server_heartbeat(self, data: Dict[str, Any]) -> None:
        """Sets the heartbeat interval to a third of the server's limit, else the configured one."""
        announced = data.get("heartbeat_seconds")
        is_number = isinstance(announced, (int, float)) and not isinstance(announced, bool)
        is_valid  = is_number and announced > 0
        interval  = announced / _MISSED_HEARTBEATS if is_valid else self._options.heartbeat_seconds
        with self._lock:
            self._heartbeat_interval = interval
        self._heartbeat_changed.set()

    # ------------------------------------------------------------------ packets
    def _handle_packet(self, packet: Dict[str, Any]) -> bool:
        """Handles one packet from the server; returns True if it was AUTH_OK."""
        kind = packet.get("type")
        data = packet.get("data") or {}
        if kind == ResponseType.AUTH_OK.value:
            self._adopt_server_heartbeat(data)
            self._authed.set()
            self._first_answer.set()
            self._resend_pending()
            self._deliver(packet)               # the client re-asserts what it holds on every connection
            return True
        if kind == ResponseType.ERROR.value and data.get("code") == _AUTH_FAILED_CODE:
            logger.error("the server refused our token")
            self._auth_refused = True
            self._first_answer.set()
            return False
        if kind in (ResponseType.MATCH_ACK.value, ResponseType.ERROR.value):
            self._outbox.remove(data.get("match_id"))   # answered, accepted or rejected
        self._deliver(packet)
        return False

    def _deliver(self, packet: Dict[str, Any]) -> None:
        try:
            self._on_receive_callback(packet)
        except Exception:
            logger.exception("receive callback failed")

    def _resend_pending(self) -> None:
        unconfirmed = self._outbox.pending()
        if unconfirmed:
            logger.info("re-sending %d unconfirmed result(s)", len(unconfirmed))
        for payload in unconfirmed:
            self._send(payload)
