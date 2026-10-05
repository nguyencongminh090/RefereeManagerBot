import json
import tempfile
import unittest
import urllib.error
import urllib.request
import uuid

from config.settings   import ConfigLoader
from domain.types      import GameResult
from network.messages  import RequestType, ResponseType
from server            import Server
from tests.server_fixtures import DASH_TOKEN, ServerPorts, write_server_config
from tests.test_server import TOKEN, Bot, free_port

WIN, LOSS  = GameResult.WIN.value, GameResult.LOSS.value


class ServerDashboardTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def start(self, enabled: bool, allow_edit: bool = False) -> Server:
        ports = ServerPorts(free_port(), free_port(), enabled, allow_edit)
        config, env = write_server_config(self.folder.name, ports)
        settings = ConfigLoader.load(config, role="server", env_file=env, environ={})
        server = Server(settings)
        server.start(block=False)
        self.addCleanup(server.stop)
        return server

    def state(self, server: Server) -> dict:
        url = f"http://127.0.0.1:{server.dashboard_port}/api/state?token={DASH_TOKEN}"
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read())

    def bot(self, server: Server) -> Bot:
        bot = Bot(server.port)
        self.addCleanup(bot.close)
        bot.send(RequestType.AUTH, {"bot_name": "bot-x", "token": TOKEN})
        self.assertEqual(ResponseType.AUTH_OK.value, bot.recv()["type"])
        return bot

    def test_disabled_dashboard_opens_no_port(self):
        self.assertIsNone(self.start(enabled=False).dashboard_port)

    def test_enabled_dashboard_shows_roster_teams(self):
        state = self.state(self.start(enabled=True))
        self.assertEqual({"Alpha", "Beta"}, {row["name"] for row in state["standings"]})

    def test_dashboard_shows_connected_bot_and_its_table(self):
        server = self.start(enabled=True)
        bot = self.bot(server)
        bot.send(RequestType.TABLE_CLAIM, {"table_no": 3})
        bot.recv_type(ResponseType.CLAIM_OK)
        self.assertEqual([{"name": "bot-x", "tables": [3]}],
                         [{"name": b["name"], "tables": b["tables"]}
                          for b in self.state(server)["bots"]])

    def test_dashboard_shows_a_recorded_game(self):
        server = self.start(enabled=True)
        bot = self.bot(server)
        bot.result("wbca1", WIN, "wbcb1", LOSS, match_id=str(uuid.uuid4()), table_no=2)
        bot.recv_type(ResponseType.MATCH_ACK)
        game = self.state(server)["recent_games"][0]
        self.assertEqual(("wbca1", "wbcb1", 2), (game["p1"], game["p2"], game["table_no"]))

    def post(self, server: Server, body: dict) -> int:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.dashboard_port}/api/action?token={DASH_TOKEN}",
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Dashboard-Action": "1"})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status
        except urllib.error.HTTPError as error:
            error.close()
            return error.code

    def test_voiding_a_game_from_the_page_updates_standings_and_tells_the_bots(self):
        server = self.start(enabled=True, allow_edit=True)
        bot = self.bot(server)
        bot.result("wbca1", WIN, "wbcb1", LOSS, match_id=str(uuid.uuid4()), table_no=2)
        bot.recv_type(ResponseType.MATCH_ACK)       # the result's own broadcast arrives before it
        game_id = self.state(server)["recent_games"][0]["id"]
        self.assertEqual(200, self.post(server, {"action": "void_game", "game_id": game_id,
                                                 "voided": True}))
        self.assertIn("0 : 0", bot.recv_type(ResponseType.BROADCAST)["text"])
        state = self.state(server)
        self.assertTrue(state["recent_games"][0]["voided"])
        self.assertEqual(0, state["standings"][0]["games"])
        self.assertEqual(("dashboard", "void"), (state["audit"][0]["actor"],
                                                 state["audit"][0]["action"]))

    def test_actions_are_refused_while_editing_is_off(self):
        server = self.start(enabled=True)
        self.assertFalse(self.state(server)["can_edit"])
        self.assertEqual(403, self.post(server, {"action": "void_game", "game_id": 1,
                                                 "voided": True}))

    def test_stop_closes_the_dashboard_port(self):
        server = self.start(enabled=True)
        port = server.dashboard_port
        server.stop()
        with self.assertRaises(OSError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)


if __name__ == "__main__":
    unittest.main()
