import json
import unittest
import urllib.error
import urllib.request

from config.process_config import DashboardConfig
from webui.actions          import ActionError
from webui.http_server      import DashboardServer

TOKEN = "s3cret"
STATE = {"tournament": {"name": "Cup", "format": "team"}, "standings": []}


class FakeState:
    def __init__(self, error=None):
        self.error = error

    def snapshot(self):
        if self.error:
            raise self.error
        return dict(STATE)


class FakeActions:
    def __init__(self):
        self.requests = []
        self.error    = None

    def perform(self, request):
        if self.error:
            raise self.error
        self.requests.append(request)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self.state   = FakeState()
        self.actions = FakeActions()
        self.opener  = urllib.request.build_opener(NoRedirect)
        self.server  = self.serve(self.actions)

    def serve(self, actions):
        config = DashboardConfig(True, "127.0.0.1", 0, 5, 20, 20, actions is not None)
        server = DashboardServer(config, TOKEN, self.state, actions)
        server.start()
        self.addCleanup(server.stop)
        return server

    def get(self, path, headers=None, method="GET", body=None, server=None):
        port = (server or self.server).port
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                         headers=headers or {}, method=method)
        try:
            response = self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        self.addCleanup(response.close)
        return response

    def test_api_state_returns_json_with_valid_token(self):
        response = self.get(f"/api/state?token={TOKEN}")
        self.assertEqual(200, response.status)
        body = json.loads(response.read())
        self.assertEqual("Cup", body["tournament"]["name"])
        self.assertEqual(5, body["refresh_seconds"])

    def test_missing_token_is_401_with_no_data(self):
        response = self.get("/api/state")
        self.assertEqual(401, response.status)
        self.assertNotIn(b"Cup", response.read())

    def test_wrong_token_is_401(self):
        self.assertEqual(401, self.get("/api/state?token=nope").status)

    def test_token_in_query_sets_httponly_cookie_then_cookie_alone_works(self):
        first = self.get(f"/?token={TOKEN}")
        cookie = first.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        second = self.get("/api/state", {"Cookie": cookie.split(";")[0]})
        self.assertEqual(200, second.status)

    def test_index_serves_html(self):
        response = self.get(f"/?token={TOKEN}")
        self.assertEqual(200, response.status)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assertIn(b"/static/dashboard.js", response.read())

    def test_static_files_need_the_token_and_have_the_right_type(self):
        self.assertEqual(401, self.get("/static/dashboard.css").status)
        css = self.get(f"/static/dashboard.css?token={TOKEN}")
        self.assertEqual(200, css.status)
        self.assertIn("text/css", css.headers["Content-Type"])
        js = self.get(f"/static/dashboard.js?token={TOKEN}")
        self.assertIn("javascript", js.headers["Content-Type"])

    def test_static_route_serves_only_listed_files(self):
        for path in ("/static/nope.css", "/static/..%2fassets.py", "/static/", "/static/index.html/x"):
            self.assertEqual(404, self.get(f"{path}?token={TOKEN}").status, path)

    def test_index_without_token_is_401(self):
        self.assertEqual(401, self.get("/").status)

    def test_unknown_path_is_404_and_put_is_405(self):
        self.assertEqual(404, self.get(f"/nope?token={TOKEN}").status)
        self.assertEqual(405, self.get(f"/api/state?token={TOKEN}", method="PUT").status)

    def test_state_exception_is_500_without_details(self):
        self.state.error = RuntimeError("secret detail")
        response = self.get(f"/api/state?token={TOKEN}")
        self.assertEqual(500, response.status)
        self.assertNotIn(b"secret detail", response.read())

    def test_security_headers_present(self):
        headers = self.get(f"/api/state?token={TOKEN}").headers
        self.assertEqual("no-store", headers["Cache-Control"])
        self.assertEqual("nosniff", headers["X-Content-Type-Options"])
        policy = headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)

    def post(self, body=b'{"action": "void_game"}', path=f"/api/action?token={TOKEN}",
             headers=None, server=None):
        sent = {"Content-Type": "application/json", "X-Dashboard-Action": "1"}
        sent.update(headers or {})
        return self.get(path, sent, "POST", body, server)

    def test_action_is_performed_and_acknowledged(self):
        response = self.post(b'{"action": "void_game", "game_id": 3, "voided": true}')
        self.assertEqual(200, response.status)
        self.assertEqual({"ok": True}, json.loads(response.read()))
        self.assertEqual([{"action": "void_game", "game_id": 3, "voided": True}],
                         self.actions.requests)

    def test_action_without_a_token_is_401_and_changes_nothing(self):
        self.assertEqual(401, self.post(path="/api/action").status)
        self.assertEqual([], self.actions.requests)

    def test_action_works_with_the_cookie_alone(self):
        cookie = self.get(f"/?token={TOKEN}").headers["Set-Cookie"].split(";")[0]
        self.assertEqual(200, self.post(path="/api/action", headers={"Cookie": cookie}).status)

    def test_action_without_the_csrf_header_is_403(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.port}/api/action?token={TOKEN}", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            self.opener.open(request, timeout=3)
        except urllib.error.HTTPError as error:
            self.addCleanup(error.close)
            self.assertEqual(403, error.code)
        self.assertEqual([], self.actions.requests)

    def test_action_with_a_foreign_origin_is_403(self):
        response = self.post(headers={"Origin": "http://evil.example"})
        self.assertEqual(403, response.status)
        self.assertEqual([], self.actions.requests)

    def test_action_with_the_pages_own_origin_is_accepted(self):
        origin = f"http://127.0.0.1:{self.server.port}"
        self.assertEqual(200, self.post(headers={"Origin": origin}).status)

    def test_action_needs_a_json_content_type(self):
        self.assertEqual(415, self.post(headers={"Content-Type": "text/plain"}).status)

    def test_action_error_is_400_with_its_message(self):
        self.actions.error = ActionError("game 9 is not in this tournament")
        response = self.post()
        self.assertEqual(400, response.status)
        self.assertEqual("game 9 is not in this tournament", json.loads(response.read())["error"])

    def test_invalid_json_is_400_and_oversized_body_is_413(self):
        self.assertEqual(400, self.post(b"{not json").status)
        self.assertEqual(413, self.post(b" " * 100_000).status)
        self.assertEqual([], self.actions.requests)

    def test_unexpected_failure_is_500_without_details(self):
        self.actions.error = RuntimeError("secret detail")
        response = self.post()
        self.assertEqual(500, response.status)
        self.assertNotIn(b"secret detail", response.read())

    def test_editing_disabled_refuses_actions(self):
        read_only = self.serve(None)
        self.assertEqual(403, self.post(server=read_only).status)

    def test_post_to_another_path_is_404(self):
        self.assertEqual(404, self.post(path=f"/api/state?token={TOKEN}").status)

    def test_stop_releases_the_port(self):
        port = self.server.port
        self.server.stop()
        with self.assertRaises(OSError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)


if __name__ == "__main__":
    unittest.main()
