"""HTTP front of the dashboard: token check, one JSON route and the static page."""
import hmac
import json
import logging
import threading
from http.cookies    import SimpleCookie
from http.server     import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing          import Any, Dict, Optional, Protocol
from urllib.parse    import parse_qs, quote, unquote, urlsplit

from config.process_config import DashboardConfig
from webui.page            import PAGE_HTML

logger = logging.getLogger(__name__)

COOKIE_NAME      = "dashboard_token"
_PAGE_PATH       = "/"
_STATE_PATH      = "/api/state"
_JSON_TYPE       = "application/json; charset=utf-8"
_HTML_TYPE       = "text/html; charset=utf-8"
_TEXT_TYPE       = "text/plain; charset=utf-8"
_CSP             = "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
_ACTION_PATH     = "/api/action"
_CSRF_HEADER     = "X-Dashboard-Action"
_MAX_BODY_BYTES  = 4096
_JOIN_TIMEOUT_SEC = 5.0
_POLL_INTERVAL_SEC = 0.1   # how quickly stop() is noticed by the serving loop


class StateSource(Protocol):
    """What the HTTP layer needs from the state builder."""

    def snapshot(self) -> Dict[str, Any]:
        """Returns the JSON-ready dashboard state."""


class ActionSource(Protocol):
    """What the HTTP layer needs from the edit actions."""

    def perform(self, request: Any) -> None:
        """Applies one action; raises ValueError (ActionError) with a message to show."""


class _Settings:
    """The values a request handler needs, bundled so the handler class stays closure-free."""

    def __init__(self, token: str, state: StateSource, refresh_seconds: float,
                 actions: Optional[ActionSource]) -> None:
        self.token           = token
        self.state           = state
        self.refresh_seconds = refresh_seconds
        self.actions         = actions


def _make_handler(settings: _Settings) -> type:
    class Handler(_DashboardHandler):
        config = settings
    return Handler


class _DashboardHandler(BaseHTTPRequestHandler):
    config: _Settings

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        """Serves the page and the state after the token check."""
        url, query_token = self._authenticate()
        if url is None:
            return
        cookie = self._cookie_header(query_token)
        if url.path == _PAGE_PATH:
            return self._reply(200, _HTML_TYPE, PAGE_HTML.encode("utf-8"), cookie)
        if url.path == _STATE_PATH:
            return self._send_state(cookie)
        self._reply(404, _TEXT_TYPE, b"not found")

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        """Applies an edit action after the token, CSRF and content checks."""
        url, _ = self._authenticate()
        if url is None:
            return
        if url.path != _ACTION_PATH:
            return self._reply(404, _TEXT_TYPE, b"not found")
        if self.config.actions is None:
            return self._reply_error(403, "editing is disabled (dashboard.allow_edit)")
        rejection = self._csrf_rejection()
        if rejection:
            logger.warning("dashboard: refused action from %s: %s", self.client_address[0],
                           rejection)
            return self._reply_error(403, rejection)
        request = self._read_json()
        if request is not None:
            self._run_action(request)

    def _authenticate(self):
        """Returns (parsed URL, query token) for an authorized request, else replies 401."""
        url = urlsplit(self.path)
        query_token = (parse_qs(url.query).get("token") or [""])[0]
        if self._authorized(query_token):
            return url, query_token
        logger.warning("dashboard: refused %s from %s: missing or wrong token",
                       url.path, self.client_address[0])
        self._reply(401, _TEXT_TYPE, b"unauthorized")
        return None, ""

    def _csrf_rejection(self) -> str:
        """Explains why a POST looks cross-site, or returns "" when it is fine."""
        if self.headers.get(_CSRF_HEADER) != "1":
            return f"the {_CSRF_HEADER} header is missing"
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host', '')}":
            return "the request comes from another origin"
        return ""

    def _read_json(self) -> Any:
        """Reads the JSON body; replies and returns None when it is unusable."""
        if self.headers.get_content_type() != "application/json":
            self._reply_error(415, "send application/json")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reply_error(411, "Content-Length is required")
            return None
        if length > _MAX_BODY_BYTES:
            self._reply_error(413, "the request is too large")
            return None
        try:
            return json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            self._reply_error(400, "the body is not valid JSON")
            return None

    def _run_action(self, request: Any) -> None:
        try:
            self.config.actions.perform(request)
        except ValueError as exc:               # ActionError: a refusal the organizer can read
            return self._reply_error(400, str(exc))
        except Exception:
            logger.exception("dashboard: action failed")
            return self._reply_error(500, "internal error")
        self._reply(200, _JSON_TYPE, b'{"ok": true}')

    def _reply_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._reply(status, _JSON_TYPE, body)

    def _reject_method(self) -> None:
        self._reply(405, _TEXT_TYPE, b"method not allowed", extra={"Allow": "GET"})

    do_PUT = do_DELETE = do_PATCH = do_HEAD = _reject_method

    def _send_state(self, cookie: Optional[str]) -> None:
        try:
            state = dict(self.config.state.snapshot())
            state["refresh_seconds"] = self.config.refresh_seconds
            body = json.dumps(state, ensure_ascii=False).encode("utf-8")
        except Exception:
            logger.exception("dashboard: could not build the state")
            return self._reply(500, _TEXT_TYPE, b"internal error")
        self._reply(200, _JSON_TYPE, body, cookie)

    def _authorized(self, query_token: str) -> bool:
        supplied = query_token or self._cookie_token()
        return bool(supplied) and hmac.compare_digest(
            supplied.encode("utf-8"), self.config.token.encode("utf-8"))

    def _cookie_token(self) -> str:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:                       # a malformed Cookie header just means no token
            return ""
        morsel = jar.get(COOKIE_NAME)
        return unquote(morsel.value) if morsel else ""

    def _cookie_header(self, query_token: str) -> Optional[str]:
        """Builds Set-Cookie only when the caller proved the token in the query string."""
        if not query_token:
            return None
        return f"{COOKIE_NAME}={quote(query_token, safe='')}; Path=/; HttpOnly; SameSite=Strict"

    def _reply(self, status: int, content_type: str, body: bytes,
               cookie: Optional[str] = None, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (base signature)
        logger.debug("dashboard: %s " + format, self.client_address[0], *args)


class DashboardServer:
    """Serves the dashboard on a background thread until stopped."""

    def __init__(self, config: DashboardConfig, token: str, state: StateSource,
                 actions: Optional[ActionSource] = None) -> None:
        """Binds the listening socket.

        Args:
            config: The `[dashboard]` settings.
            token: Shared secret the page must present.
            state: Source of the JSON-ready state.
            actions: Applies edit requests; None answers every action with 403.

        Raises:
            OSError: If the address cannot be bound.
        """
        handler = _make_handler(_Settings(token, state, config.refresh_seconds, actions))
        self._httpd   = ThreadingHTTPServer((config.host, config.port), handler)
        self._httpd.daemon_threads = True
        self._thread  = threading.Thread(
            target=self._httpd.serve_forever, args=(_POLL_INTERVAL_SEC,), daemon=True,
            name="dashboard")
        self._stopped = False

    @property
    def port(self) -> int:
        """The TCP port actually bound (differs from the config when it says 0)."""
        return self._httpd.server_address[1]

    def start(self) -> None:
        """Starts serving."""
        self._thread.start()
        logger.info("dashboard listening on %s:%s", *self._httpd.server_address[:2])

    def stop(self) -> None:
        """Stops serving and releases the port; safe to call twice."""
        if self._stopped:
            return
        self._stopped = True
        if self._thread.is_alive():          # shutdown() would wait forever on a loop never started
            self._httpd.shutdown()
            self._thread.join(_JOIN_TIMEOUT_SEC)
        self._httpd.server_close()
