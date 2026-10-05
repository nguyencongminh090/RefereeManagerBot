import json
import unittest
import urllib.error
import urllib.request

from config.process_config import PublicConfig
from webui.public_state    import CachedSnapshot
from webui.public_server   import PublicServer

STATE = {"tournament": {"name": "Cup", "format": "team"}, "standings": []}


class FakeState:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(STATE)


class PublicHttpTests(unittest.TestCase):
    def setUp(self):
        self.state = FakeState()
        config = PublicConfig(True, "127.0.0.1", 0, 10, 20)
        self.server = PublicServer(config, self.state)
        self.server.start()
        self.addCleanup(self.server.stop)

    def get(self, path, method="GET", body=None, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.port}{path}",
                                         data=body, method=method, headers=headers or {})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        self.addCleanup(response.close)
        return response

    def test_page_and_state_need_no_token_and_set_no_cookie(self):
        page = self.get("/")
        self.assertEqual(200, page.status)
        self.assertIn(b"/static/public.js", page.read())
        self.assertIsNone(page.headers["Set-Cookie"])
        state = self.get("/api/state")
        self.assertEqual("Cup", json.loads(state.read())["tournament"]["name"])

    def test_state_tells_the_page_how_often_to_refresh(self):
        self.assertEqual(10, json.loads(self.get("/api/state").read())["refresh_seconds"])

    def test_serves_only_the_public_scripts_not_the_organizer_one(self):
        for name in ("dashboard.css", "common.js", "public.js"):
            self.assertEqual(200, self.get(f"/static/{name}").status, name)
        for name in ("dashboard.js", "index.html", "nope.js"):
            self.assertEqual(404, self.get(f"/static/{name}").status, name)

    def test_nothing_can_be_changed(self):
        for path in ("/api/action", "/api/state", "/"):
            response = self.get(path, "POST", b"{}", {"Content-Type": "application/json",
                                                      "X-Dashboard-Action": "1"})
            self.assertEqual(405, response.status, path)

    def test_state_failure_is_500_without_details(self):
        self.state.error = RuntimeError("secret detail")
        response = self.get("/api/state")
        self.assertEqual(500, response.status)
        self.assertNotIn(b"secret detail", response.read())

    def test_policy_allows_no_inline_code(self):
        policy = self.get("/").headers["Content-Security-Policy"]
        self.assertNotIn("unsafe-inline", policy)


class CachedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.now    = 100.0
        self.source = FakeState()
        self.cached = CachedSnapshot(self.source, 2.0, lambda: self.now)

    def test_one_build_serves_every_request_inside_the_window(self):
        for _ in range(5):
            self.cached.snapshot()
        self.assertEqual(1, self.source.calls)

    def test_rebuilds_after_the_window(self):
        self.cached.snapshot()
        self.now += 2.5
        self.cached.snapshot()
        self.assertEqual(2, self.source.calls)

    def test_failure_is_not_cached(self):
        self.source.error = RuntimeError("down")
        with self.assertRaises(RuntimeError):
            self.cached.snapshot()
        self.source.error = None
        self.assertEqual("Cup", self.cached.snapshot()["tournament"]["name"])
