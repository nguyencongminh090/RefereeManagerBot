"""HTTP server of the fake PlayOK site: serves the pages and applies the bot's clicks to the world."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict
from urllib.parse import parse_qs

from tests.fake_playok.pages import render_fragment, render_page
from tests.fake_playok.world import FakeWorld

ACT_PREFIX = "/_act/"


class _QuietHttpServer(ThreadingHTTPServer):
    """Ignores connections the browser aborted (it cancels its polls when a page is closed)."""

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class FakePlayokServer:
    """Runs the fake site on a free localhost port in a background thread."""

    def __init__(self, world: FakeWorld):
        self.world = world
        self._http = _QuietHttpServer(("127.0.0.1", 0), _handler_for(world))
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Base URL without a trailing slash."""
        return f"http://127.0.0.1:{self._http.server_port}"

    @property
    def site_url(self) -> str:
        """URL to use as `playok.site_url`."""
        return self.url + "/"

    def start(self) -> None:
        """Starts serving."""
        self._thread.start()

    def stop(self) -> None:
        """Stops serving and releases the port."""
        self._http.shutdown()
        self._http.server_close()
        self._thread.join()


def _actions(world: FakeWorld) -> Dict[str, Callable[[Dict[str, str]], None]]:
    def join(fields):
        table = fields.get("table", "")
        if table.isdigit():
            world.bot_join(int(table))

    return {
        "join": join,
        "accept": lambda fields: world.bot_accept_invitation(),
        "decline": lambda fields: world.decline_invitation(),
        "say": lambda fields: world.bot_say(fields.get("text", "")),
        "leave": lambda fields: world.bot_leave(),
    }


def _handler_for(world: FakeWorld):
    actions = _actions(world)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {"/": lambda: ("text/html", render_page(world)),
                      "/fragment": lambda: ("text/html", render_fragment(world)),
                      "/version": lambda: ("application/json",
                                           json.dumps({"version": world.version, "churn": world.churn}))}
            time.sleep(world.latency_sec)
            route = routes.get(self.path)
            if route is None:
                self._reply(404, "text/plain", "not found")
                return
            self._reply(200, *route())

        def do_POST(self):
            name = self.path[len(ACT_PREFIX):] if self.path.startswith(ACT_PREFIX) else ""
            if name not in actions:
                self._reply(404, "text/plain", "not found")
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            actions[name]({key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()})
            self._reply(200, "text/plain", "ok")

        def _reply(self, status: int, content_type: str, body: str) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    return Handler
