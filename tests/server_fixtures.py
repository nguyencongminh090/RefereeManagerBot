"""Shared set-up for tests that start a real Server: a config and a secrets file on disk."""
import os
from dataclasses import dataclass
from pathlib     import Path
from typing      import Tuple

from tests.test_server import EXAMPLE, ROSTER, TOKEN

DASH_TOKEN = "dash-token"


@dataclass(frozen=True)
class ServerPorts:
    """Ports and switches of a test server.

    Attributes:
        server: Port of the bot socket.
        dashboard: Port of the dashboard.
        dashboard_enabled: Whether `[dashboard] enabled` is true.
        allow_edit: Whether `[dashboard] allow_edit` is true.
        public: Port of the public page.
        public_enabled: Whether `[public] enabled` is true.
    """
    server           : int
    dashboard        : int
    dashboard_enabled: bool = True
    allow_edit       : bool = False
    public           : int  = 0
    public_enabled   : bool = False


def write_server_config(folder: str, ports: ServerPorts) -> Tuple[str, str]:
    """Writes config.toml, teams.csv and a secrets file into `folder`.

    Returns:
        The paths of the config file and the secrets file.
    """
    csv_path = os.path.join(folder, "teams.csv")
    Path(csv_path).write_text(ROSTER, encoding="utf-8")
    db_path  = os.path.join(folder, "t.db")
    text = (EXAMPLE.read_text(encoding="utf-8")
            .replace("port              = 9000", f"port = {ports.server}")
            .replace('"data/tournament.db"', f'"{db_path}"')
            .replace('"data/teams.csv"', f'"{csv_path}"')
            .replace("[dashboard]\nenabled         = false",
                     f"[dashboard]\nenabled         = {str(ports.dashboard_enabled).lower()}")
            .replace("allow_edit      = false", f"allow_edit      = {str(ports.allow_edit).lower()}")
            .replace("port            = 8080", f"port            = {ports.dashboard}")
            .replace("[public]\nenabled         = false",
                     f"[public]\nenabled         = {str(ports.public_enabled).lower()}")
            .replace("port            = 8081", f"port            = {ports.public or 8081}"))
    config = os.path.join(folder, "config.toml")
    Path(config).write_text(text, encoding="utf-8")
    env = os.path.join(folder, "test.env")
    Path(env).write_text(f"BOT_TOKEN={TOKEN}\nDASHBOARD_TOKEN={DASH_TOKEN}\n", encoding="utf-8")
    return config, env
