"""A whole micro-match: real server and SQLite, real client and TCP socket, real Firefox, fake PlayOK."""
import dataclasses
from dataclasses import dataclass
import threading
import time
import unittest
from pathlib import Path

from client import Client
from config.settings import ConfigLoader
from domain.types import GameResult
from network.client_socket import TcpClientSocket
from network.messages import RequestType
from network.options import ClientSocketOptions
from referee.driver import SeleniumDriver
from tests.fake_playok.server import FakePlayokServer
from tests.fake_playok.world import FakeBoard, FakeWorld
from tests.test_client_socket import ServerBackedCase
from tests.test_fake_playok_browser import BOT, create_firefox, wait_until
from tests.test_server import TOKEN
from tests.test_page_parser import SETTINGS

TOURNAMENT = "Demo Tournament"
TABLE = 104
OTHER_TABLE = 105
OTHER_BOT = "wbcref2"
ADMIN = SETTINGS.tournament.admins[0]
POINTS = {GameResult.WIN.value: 1.0, GameResult.DRAW.value: 0.5, GameResult.LOSS.value: 0.0}
OUTCOMES = ("player #1 wins",) * 7 + ("player #2 wins",) * 4 + ("draw",)
GAMES = len(OUTCOMES)
EXPECTED_P1_RESULTS = ([GameResult.WIN.value] * 7 + [GameResult.LOSS.value] * 4 + [GameResult.DRAW.value])
MATCH_TIMEOUT_SEC = 60.0
STEP_SEC = 0.1
SLOW_SITE_SEC = 0.4
OUTAGE_SEC = 2.0
CLAIM_SETTLE_SEC = 4.0
CLIENT_CONFIG_EDITS = (("server_port           = 9000", "server_port = {port}"),
                       ("poll_seconds          = 0.5", "poll_seconds = 0.1"),
                       ("lobby_scan_seconds    = 2", "lobby_scan_seconds = 0.2"),
                       ('outbox_path = "data/outbox.jsonl"', 'outbox_path = "{outbox}"'))
SECRETS = {"BOT_TOKEN": TOKEN, "PLAYOK_PASS": "unused"}


@dataclass
class Rig:
    """One bot's side of the test: its nickname, its view of the site, the site server and a browser."""
    name: str
    world: FakeWorld
    site: FakePlayokServer
    browser: object


@unittest.skipIf(create_firefox is None, "selenium is not installed")
class FullMatchTests(ServerBackedCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.browser = create_firefox(headless=True)
        except Exception as error:
            raise unittest.SkipTest(f"Firefox cannot start: {error}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.quit()

    def setUp(self):
        super().setUp()
        self.start_server()
        self.addCleanup(lambda: self.server and self.server.stop())
        self.board = FakeBoard()
        self.rig = self.new_rig(BOT, self.browser)
        self.world, self.site = self.rig.world, self.rig.site
        self.world.add_lobby_table(TABLE, time="1m+1s", players=["wbca1", "wbcb1"])
        self.errors = []
        self.stopping = threading.Event()

    def new_rig(self, name: str, browser) -> Rig:
        world = FakeWorld(bot_name=name, board=self.board)
        site = FakePlayokServer(world)
        site.start()
        self.addCleanup(site.stop)
        return Rig(name, world, site, browser)

    def new_browser(self):
        browser = create_firefox(headless=True)
        self.addCleanup(browser.quit)
        return browser

    def start_client(self, rig: Rig = None) -> None:
        rig = rig or self.rig
        text = (Path(self.folder.name) / "config.toml").read_text(encoding="utf-8")
        for old, new in CLIENT_CONFIG_EDITS:
            self.assertIn(old, text)
            text = text.replace(old, new.format(port=self.port, outbox=Path(self.folder.name) / "outbox.jsonl"))
        path = Path(self.folder.name) / "client.toml"
        path.write_text(text, encoding="utf-8")
        settings = ConfigLoader.load(str(path), role="client", env_file=str(Path(self.folder.name) / "none.env"),
                                     environ={**SECRETS, "PLAYOK_USER": rig.name})
        driver = SeleniumDriver(dataclasses.replace(settings.playok, site_url=rig.site.site_url),
                                rig.browser, pause_seconds=0.2)
        options = ClientSocketOptions(heartbeat_seconds=0.5, reconnect_max_seconds=0.5, connect_timeout_seconds=2.0)
        sockets = []

        def socket_factory(on_receive):
            sockets.append(TcpClientSocket("127.0.0.1", self.port, on_receive, TOKEN, f"test-{rig.name}", options))
            return sockets[0]

        client = Client(settings, driver, socket_factory)
        sockets[0].connect()
        self.addCleanup(sockets[0].disconnect)
        driver.open_site()
        driver.goto_lobby()
        sockets[0].send_packet({"type": RequestType.ROSTER_QUERY.value, "data": {}})
        thread = threading.Thread(target=self.run_client, args=(client,), daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.stopping.set)

    def run_client(self, client: Client) -> None:
        try:
            while not self.stopping.is_set():
                client.step()
                time.sleep(STEP_SEC)
        except Exception as error:      # reported by the test thread
            self.errors.append(error)

    def stored_games(self) -> list:
        return self.server._store.list_games(TOURNAMENT)

    def play_whole_match(self, churn: bool = False) -> None:
        self.start_client()
        self.world.churn = churn          # after the start-up calls, which have no retry
        wait_until(lambda: self.world.bot_table == TABLE, "the bot at the table", MATCH_TIMEOUT_SEC)
        for outcome in OUTCOMES:
            self.world.system(TABLE, outcome)
        wait_until(lambda: len(self.stored_games()) == GAMES, f"{GAMES} stored games", MATCH_TIMEOUT_SEC)
        wait_until(lambda: self.world.bot_table is None, "the bot leaving the table", MATCH_TIMEOUT_SEC)
        self.assertEqual([], self.errors)

    def assert_games_stored_correctly(self) -> None:
        games = self.stored_games()
        self.assertEqual({("wbca1", "wbcb1", TABLE)}, {(g["p1"], g["p2"], g["table_no"]) for g in games})
        self.assertEqual(EXPECTED_P1_RESULTS, [g["p1_result"] for g in games])

    def test_bot_referees_a_whole_micro_match(self):
        self.play_whole_match()
        self.assert_games_stored_correctly()
        self.assertTrue(any(line.startswith(f"{BOT}: ") for line in self.world.chat(TABLE)))

    def test_survives_a_page_that_rebuilds_its_elements_on_every_poll(self):
        self.play_whole_match(churn=True)
        self.assert_games_stored_correctly()

    def test_survives_a_slow_site(self):
        self.world.latency_sec = SLOW_SITE_SEC
        self.play_whole_match()
        self.assert_games_stored_correctly()

    def test_two_bots_referee_two_tables_without_clashing(self):
        self.world.add_lobby_table(OTHER_TABLE, time="1m+1s", players=["wbca2", "wbcb2"])
        other = self.new_rig(OTHER_BOT, self.new_browser())
        self.start_client(self.rig)
        self.start_client(other)
        both = (self.world, other.world)
        wait_until(lambda: all(w.bot_table is not None for w in both), "both bots seated", MATCH_TIMEOUT_SEC)
        self.assertEqual({TABLE, OTHER_TABLE}, {w.bot_table for w in both})
        for table in (TABLE, OTHER_TABLE):
            for outcome in OUTCOMES:
                self.world.system(table, outcome)
        wait_until(lambda: len(self.stored_games()) == 2 * GAMES, f"{2 * GAMES} stored games", MATCH_TIMEOUT_SEC)
        wait_until(lambda: all(w.bot_table is None for w in both), "both bots leaving", MATCH_TIMEOUT_SEC)
        self.assertEqual([], self.errors)
        games = self.stored_games()
        by_table = {t: [g for g in games if g["table_no"] == t] for t in (TABLE, OTHER_TABLE)}
        self.assertEqual([GAMES, GAMES], [len(by_table[t]) for t in (TABLE, OTHER_TABLE)])
        for table, pair in ((TABLE, ("wbca1", "wbcb1")), (OTHER_TABLE, ("wbca2", "wbcb2"))):
            self.assertEqual({pair}, {(g["p1"], g["p2"]) for g in by_table[table]})
            self.assertEqual(EXPECTED_P1_RESULTS, [g["p1_result"] for g in by_table[table]])
        self.assertEqual(2, len({g["bot_name"] for g in games}))
        self.assertTrue(all(len({g["bot_name"] for g in by_table[t]}) == 1 for t in by_table))

    def seat_the_bot(self) -> None:
        self.start_client()
        wait_until(lambda: self.world.bot_table == TABLE, "the bot at the table", MATCH_TIMEOUT_SEC)

    def wait_for_games(self, count: int) -> None:
        wait_until(lambda: len(self.stored_games()) == count, f"{count} stored games", MATCH_TIMEOUT_SEC)

    def points_of_left_player(self) -> float:
        return sum(POINTS[g["p1_result"]] for g in self.stored_games())

    def test_a_result_is_kept_when_a_player_leaves_right_after_the_game(self):
        self.seat_the_bot()
        self.world.system(TABLE, OUTCOMES[0])
        self.wait_for_games(1)                 # the bot has read the seat names by now
        for outcome in OUTCOMES[1:6]:
            self.world.system(TABLE, outcome)
        self.world.seat(TABLE, 2, None)
        self.world.system(TABLE, "wbcb1 leaves")
        self.wait_for_games(6)
        self.world.seat(TABLE, 2, "wbcb1")
        self.world.system(TABLE, "wbcb1 [1200] joins")
        for outcome in OUTCOMES[6:]:
            self.world.system(TABLE, outcome)
        self.wait_for_games(GAMES)
        wait_until(lambda: self.world.bot_table is None, "the bot leaving the table", MATCH_TIMEOUT_SEC)
        self.assertEqual([], self.errors)
        self.assert_games_stored_correctly()

    def test_admin_set_corrects_the_score_and_completes_the_match(self):
        self.seat_the_bot()
        for outcome in OUTCOMES[:3]:
            self.world.system(TABLE, outcome)
        self.wait_for_games(3)
        self.world.say(TABLE, ADMIN, "!set 6-6")
        self.wait_for_games(GAMES)
        wait_until(lambda: self.world.bot_table is None, "the bot leaving the table", MATCH_TIMEOUT_SEC)
        self.assertEqual([], self.errors)
        self.assertEqual(6.0, self.points_of_left_player())

    def test_set_from_a_player_who_is_not_an_admin_is_ignored(self):
        self.seat_the_bot()
        self.world.system(TABLE, OUTCOMES[0])
        self.wait_for_games(1)
        self.world.say(TABLE, "wbca1", "!set 6-6")
        self.world.system(TABLE, OUTCOMES[1])
        self.wait_for_games(2)
        self.assertEqual(TABLE, self.world.bot_table)
        self.assertEqual([], self.errors)

    def test_results_finished_while_the_server_is_down_are_replayed_when_it_returns(self):
        self.seat_the_bot()
        for outcome in OUTCOMES[:4]:
            self.world.system(TABLE, outcome)
        self.wait_for_games(4)
        self.stop_server()
        for outcome in OUTCOMES[4:]:
            self.world.system(TABLE, outcome)
        time.sleep(OUTAGE_SEC)                     # the bot reads them and queues the results
        self.assertEqual(TABLE, self.world.bot_table)
        self.start_server()
        self.wait_for_games(GAMES)
        wait_until(lambda: self.world.bot_table is None, "the bot leaving the table", MATCH_TIMEOUT_SEC)
        self.assertEqual([], self.errors)
        self.assert_games_stored_correctly()

    def test_a_match_played_entirely_while_the_server_is_down_is_stored_afterwards(self):
        self.seat_the_bot()
        self.stop_server()
        for outcome in OUTCOMES:
            self.world.system(TABLE, outcome)
        wait_until(lambda: self.world.bot_table is None, "the bot finishing by its own count", MATCH_TIMEOUT_SEC)
        self.assertEqual([], self.errors)
        self.start_server()
        self.wait_for_games(GAMES)
        self.assert_games_stored_correctly()

    def test_a_second_bot_does_not_take_a_table_that_is_refereed_across_a_server_restart(self):
        self.seat_the_bot()
        self.stop_server()
        self.start_server()                        # the new server has forgotten every claim
        other = self.new_rig(OTHER_BOT, self.new_browser())
        self.start_client(other)
        time.sleep(CLAIM_SETTLE_SEC)               # about ten lobby scans of the second bot
        self.assertIsNone(other.world.bot_table)
        self.assertEqual(TABLE, self.world.bot_table)


if __name__ == "__main__":
    unittest.main()
