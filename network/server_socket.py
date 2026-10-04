"""Threaded TCP server socket with client, size and send-time limits."""
import socket
import logging
import threading
import time
from typing           import Callable, Dict, Any, Tuple, Optional
from network.options  import ServerSocketOptions
from network.ports    import IServerSocket
from network.protocol import PacketProtocol, PacketTooLargeError, set_send_timeout


logger = logging.getLogger(__name__)

_ACCEPT_RETRY_DELAY_SEC = 0.1
_ACCEPT_LOG_INTERVAL_SEC = 30.0


class _ClientConnection:
    """One accepted socket plus the lock that keeps its outgoing frames whole."""

    def __init__(self, sock: socket.socket):
        self.sock: socket.socket = sock
        self.send_lock: threading.Lock = threading.Lock()


class TcpServerSocket(IServerSocket):
    """Threaded TCP server with a client cap, a packet-size cap and per-client send timeouts.

    Sends use SO_SNDTIMEO on the accepted socket, so a bot that stops reading is dropped after
    `options.send_timeout_seconds` while reads stay blocking. Each client has its own send lock;
    the shared clients lock is never held while sending.
    """

    def __init__(self,
                 host: str,
                 port: int,
                 on_receive_callback: Callable[[Tuple[str, int], Dict], None],
                 on_connect_callback: Optional[Callable[[Tuple[str, int]], None]] = None,
                 on_disconnect_callback: Optional[Callable[[Tuple[str, int]], None]] = None,
                 options: ServerSocketOptions = ServerSocketOptions()):
        """Creates the server; nothing is bound until start_listening().

        Args:
            host: Interface to bind.
            port: Port to bind; 0 picks a free one (see `port`).
            on_receive_callback: Called with (address, packet) on the client's own reader thread,
                so one slow call delays only that client.
            on_connect_callback: Called with the address on the acceptor thread after admission.
            on_disconnect_callback: Called with the address on the client's reader thread
                after the connection is gone.
            options: Client cap, packet cap and send timeout.
        """
        self._host = host
        self._port = port
        self._on_receive_callback = on_receive_callback
        self._on_connect_callback = on_connect_callback
        self._on_disconnect_callback = on_disconnect_callback
        self._options = options

        self._server_sock: Optional[socket.socket] = None
        self._running: bool = False
        self._clients: Dict[Tuple[str, int], _ClientConnection] = {}
        self._clients_lock: threading.Lock = threading.Lock()   # guards _clients only
        self._last_accept_error: Optional[str] = None
        self._last_accept_log: float = 0.0

    def start_listening(self):
        """Binds the port and starts the acceptor thread; raises OSError if the bind fails."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._host, self._port))
        self._server_sock.listen()
        self._port = self._server_sock.getsockname()[1]
        self._running = True

        acceptor_thread = threading.Thread(target=self._run_acceptor_loop, daemon=True)
        acceptor_thread.start()

    def _run_acceptor_loop(self):
        while self._running:
            try:
                client_sock, client_addr = self._server_sock.accept()
            except Exception as exc:
                if not self._running:
                    break
                self._report_accept_error(exc)
                time.sleep(_ACCEPT_RETRY_DELAY_SEC)
                continue
            try:
                self._admit(client_sock, client_addr)
            except Exception:
                logger.exception("could not set up client %s", client_addr)
                client_sock.close()

    def _report_accept_error(self, exc: Exception) -> None:
        """Logs an accept failure once per distinct message, repeated at most every 30 s."""
        message = f"{type(exc).__name__}: {exc}"
        now = time.monotonic()
        is_repeat = message == self._last_accept_error
        if is_repeat and now - self._last_accept_log < _ACCEPT_LOG_INTERVAL_SEC:
            return
        self._last_accept_error, self._last_accept_log = message, now
        logger.warning("accept() failed: %s", message)

    def _admit(self, client_sock: socket.socket, client_addr: Tuple[str, int]) -> None:
        connection = _ClientConnection(client_sock)
        with self._clients_lock:
            is_full = len(self._clients) >= self._options.max_clients
            if not is_full:
                self._clients[client_addr] = connection
        if is_full:
            logger.warning("refused %s: %d clients connected",
                           client_addr, self._options.max_clients)
            client_sock.close()
            return
        set_send_timeout(client_sock, self._options.send_timeout_seconds)
        logger.info("Client connected: %s", client_addr)
        if self._on_connect_callback:
            self._on_connect_callback(client_addr)
        threading.Thread(target=self._handle_single_client, args=(client_sock, client_addr),
                         daemon=True).start()

    def _handle_single_client(self, client_sock: socket.socket, client_addr: Tuple[str, int]):
        while self._running:
            try:
                packet = PacketProtocol.receive_packet(client_sock, self._options.max_packet_bytes)
                if not packet:
                    break
                self._on_receive_callback(client_addr, packet)
            except PacketTooLargeError as exc:
                logger.warning("Client %s sent an oversized packet, closing: %s",
                               client_addr, exc)
                break
            except Exception as e:
                with self._clients_lock:
                    still_connected = client_addr in self._clients
                if still_connected:                  # a socket we closed ourselves is not an error
                    logger.warning("Client %s error: %s", client_addr, e)
                break

        logger.info("Client disconnected: %s", client_addr)
        self._remove_client(client_addr, client_sock)
        if self._on_disconnect_callback:
            try:
                self._on_disconnect_callback(client_addr)
            except Exception:
                logger.exception("disconnect callback failed for %s", client_addr)

    def send_packet(self, client_addr: Tuple[str, int], payload: Dict[str, Any]):
        """Sends a packet to one client; a failed send drops that client. Thread-safe."""
        with self._clients_lock:
            connection = self._clients.get(client_addr)
        if connection:
            self._send_to(client_addr, connection, PacketProtocol.encode(payload))

    def broadcast(self, payload: Dict[str, Any]):
        """Sends a packet to every client; one failing client does not stop the others."""
        data_bytes = PacketProtocol.encode(payload)
        with self._clients_lock:
            targets = list(self._clients.items())
        for addr, connection in targets:
            self._send_to(addr, connection, data_bytes)

    def _send_to(self, addr: Tuple[str, int], connection: _ClientConnection,
                 data_bytes: bytes) -> None:
        """Sends one whole frame under the client's send lock; drops the client on failure."""
        try:
            with connection.send_lock:
                connection.sock.sendall(data_bytes)
        except Exception as e:
            logger.warning("Failed to send to %s: %s", addr, e)
            self._remove_client(addr, connection.sock)

    def _remove_client(self, addr, sock):
        with self._clients_lock:
            if addr in self._clients:
                del self._clients[addr]
        try:
            sock.shutdown(socket.SHUT_RDWR)      # wakes a reader blocked in recv, sends FIN
        except OSError:
            pass
        try:
            sock.close()
        except Exception:
            pass

    @property
    def port(self) -> int:
        """The port actually bound (useful when 0 was requested)."""
        return self._port

    def close_client(self, client_addr: Tuple[str, int]) -> None:
        """Closes one client connection; an unknown address is ignored."""
        with self._clients_lock:
            connection = self._clients.get(client_addr)
        if connection:
            self._remove_client(client_addr, connection.sock)

    def stop(self):
        """Stops accepting and closes every client; the disconnect callbacks still fire."""
        self._running = False
        if self._server_sock:
            try:
                # Wakes the acceptor blocked in accept(); close() alone keeps the port bound.
                self._server_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._server_sock.close()
        with self._clients_lock:
            clients = list(self._clients.items())
        for addr, connection in clients:
            self._remove_client(addr, connection.sock)
