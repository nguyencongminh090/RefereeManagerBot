import json
import tempfile
import unittest
import urllib.request
import uuid

from config.settings   import ConfigLoader
from domain.types      import GameResult
from network.messages  import RequestType, ResponseType
from server            import Server
from tests.server_fixtures import ServerPorts, write_server_config
from tests.test_server import TOKEN, Bot, free_port

WIN, LOSS = GameResult.WIN.value, GameResult.LOSS.value


class ServerPublicPageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def start(self, public: bool, dashboard: bool = False) -> Server:
        ports = ServerPorts(free_port(), free_port(), dashboard, public=free_port(),
                            public_enabled=public)
        config, env = write_server_config(self.folder.name, ports)
        settings = ConfigLoader.load(config, role="server", env_file=env, environ={})
        server = Server(settings)
        server.start(block=False)
        self.addCleanup(server.stop)
        return server

    def state(self, server: Server) -> dict:
        url = f"http://127.0.0.1:{server.public_port}/api/state"
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read())

    def bot(self, server: Server) -> Bot:
        bot = Bot(server.port)
        self.addCleanup(bot.close)
        bot.send(RequestType.AUTH, {"bot_name": "bot-x", "token": TOKEN})
        self.assertEqual(ResponseType.AUTH_OK.value, bot.recv()["type"])
        return bot

    def test_disabled_page_opens_no_port(self):
        self.assertIsNone(self.start(public=False).public_port)

    def test_page_shows_standings_without_any_token(self):
        state = self.state(self.start(public=True))
        self.assertEqual({"Alpha", "Beta"}, {row["name"] for row in state["standings"]})

    def test_page_shows_a_result_and_the_live_table_but_not_the_bot(self):
        server = self.start(public=True)
        bot = self.bot(server)
        bot.send(RequestType.TABLE_CLAIM, {"table_no": 3})
        bot.recv_type(ResponseType.CLAIM_OK)
        bot.result("wbca1", WIN, "wbcb1", LOSS, match_id=str(uuid.uuid4()), table_no=3)
        bot.recv_type(ResponseType.MATCH_ACK)
        state = self.state(server)
        self.assertEqual([3], state["live_tables"])
        self.assertEqual(("wbca1", "wbcb1"), (state["recent_games"][0]["p1"],
                                              state["recent_games"][0]["p2"]))
        self.assertNotIn("bot-x", json.dumps(state))

    def test_both_pages_can_run_together_and_stop_frees_the_public_port(self):
        server = self.start(public=True, dashboard=True)
        port = server.public_port
        self.assertNotEqual(port, server.dashboard_port)
        server.stop()
        with self.assertRaises(OSError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
