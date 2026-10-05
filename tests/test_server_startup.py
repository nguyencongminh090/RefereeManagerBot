import contextlib
import io
import socket
import tempfile
import unittest

from config.settings        import ConfigLoader
from server                 import Server, StartupError, main
from tests.server_fixtures  import ServerPorts, write_server_config
from tests.test_server      import free_port
from webui.http_server      import DashboardServer
from config.process_config import DashboardConfig


class Occupant:
    """Holds a TCP port the way another program would."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]

    def close(self):
        self.sock.close()


class ServerStartupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.occupant = Occupant()
        self.addCleanup(self.occupant.close)

    def paths(self, server_port=None, dashboard_port=None):
        ports = ServerPorts(server_port or free_port(), dashboard_port or free_port())
        return write_server_config(self.folder.name, ports)

    def settings(self, **ports):
        config, env = self.paths(**ports)
        return ConfigLoader.load(config, env_file=env, role="server", environ={})

    def test_busy_dashboard_port_is_a_startup_error_naming_the_port(self):
        with self.assertRaises(StartupError) as ctx:
            Server(self.settings(dashboard_port=self.occupant.port))
        message = str(ctx.exception)
        self.assertIn("dashboard", message)
        self.assertIn(str(self.occupant.port), message)
        self.assertIn("[dashboard]", message)

    def test_busy_bot_port_is_a_startup_error_naming_the_port(self):
        server = Server(self.settings(server_port=self.occupant.port))
        with self.assertRaises(StartupError) as ctx:
            server.start(block=False)
        self.assertIn(str(self.occupant.port), str(ctx.exception))
        self.assertIn("[server]", str(ctx.exception))

    def test_failed_start_releases_the_dashboard_port(self):
        dashboard_port = free_port()
        server = Server(self.settings(server_port=self.occupant.port, dashboard_port=dashboard_port))
        with self.assertRaises(StartupError):
            server.start(block=False)
        with socket.socket() as probe:                 # the port is free again
            probe.bind(("127.0.0.1", dashboard_port))

    def test_a_dashboard_that_was_never_started_can_be_stopped(self):
        config = DashboardConfig(True, "127.0.0.1", free_port(), 5, 1, 1, False)
        server = DashboardServer(config, "t", object())
        server.stop()
        server.stop()

    def run_main(self, **ports):
        config, env = self.paths(**ports)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["--config", config, "--env", env])
        return code, err.getvalue()

    def test_main_reports_a_busy_dashboard_port_without_a_traceback(self):
        code, text = self.run_main(dashboard_port=self.occupant.port)
        self.assertEqual(3, code)
        self.assertIn(str(self.occupant.port), text)
        self.assertNotIn("Traceback", text)

    def test_main_reports_a_busy_bot_port_without_a_traceback(self):
        code, text = self.run_main(server_port=self.occupant.port)
        self.assertEqual(3, code)
        self.assertIn(str(self.occupant.port), text)
        self.assertNotIn("Traceback", text)


if __name__ == "__main__":
    unittest.main()
