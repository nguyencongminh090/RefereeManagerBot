"""Config tables of the processes: server, dashboard, public page, database and client (`[server]` and so on)."""
from dataclasses import dataclass
from typing      import Optional


@dataclass(frozen=True)
class ServerConfig:
    """The `[server]` table.

    Attributes:
        host: Interface to bind.
        port: TCP port, 1 or more.
        heartbeat_seconds: Heartbeat interval announced to bots.
        backup_seconds: Seconds between database backups.
        max_packet_bytes: Largest accepted packet body, in bytes.
        max_clients: Simultaneous connections allowed.
        send_timeout_seconds: Seconds a send to one bot may block.
        auth_timeout_seconds: Seconds a new connection has to authenticate.
        auth_max_failures: Failed authentications per address before lockout.
        auth_lockout_seconds: Failure window and lockout length, in seconds.
    """
    host             : str
    port             : int
    heartbeat_seconds: float
    backup_seconds   : float
    max_packet_bytes     : int
    max_clients          : int
    send_timeout_seconds : float
    auth_timeout_seconds : float
    auth_max_failures    : int
    auth_lockout_seconds : float


@dataclass(frozen=True)
class DashboardConfig:
    """The `[dashboard]` table.

    Attributes:
        enabled: Whether the server serves the organizer web page.
        host: Interface to bind.
        port: TCP port, 1 or more.
        refresh_seconds: How often the page polls the server.
        recent_games: Rows in the recent-games list.
        audit_entries: Rows in the latest-changes list.
        allow_edit: Whether the page may change data (void a game, correct a score, sudden death).
    """
    enabled        : bool
    host           : str
    port           : int
    refresh_seconds: float
    recent_games   : int
    audit_entries  : int
    allow_edit     : bool


@dataclass(frozen=True)
class PublicConfig:
    """The `[public]` table: the read-only page for the audience.

    Attributes:
        enabled: Whether the server serves the public page.
        host: Interface to bind.
        port: TCP port, 1 or more; must differ from the dashboard's when both are on.
        refresh_seconds: How often the page polls, and how long one snapshot is reused.
        recent_games: Rows in the recent-results list.
    """
    enabled        : bool
    host           : str
    port           : int
    refresh_seconds: float
    recent_games   : int


@dataclass(frozen=True)
class DatabaseConfig:
    """The `[database]` table.

    Attributes:
        path: SQLite file.
        teams_file: Roster CSV imported at start-up, or None.
    """
    path      : str
    teams_file: Optional[str]


@dataclass(frozen=True)
class ClientConfig:
    """The `[client]` table.

    Attributes:
        server_host: Host of the central server.
        server_port: Port of the central server.
        poll_seconds: Seconds between chat polls.
        reconnect_max_seconds: Upper bound of the reconnect backoff.
        join_mode: One of JOIN_MODES.
        lobby_scan_seconds: Seconds between lobby scans.
        outbox_path: File for unconfirmed results; empty keeps them in memory.
        max_pending_results: Finished games kept while the player names cannot be read.
        unreadable_polls_before_alert: Polls with unsent games before the log escalates.
    """
    server_host          : str
    server_port          : int
    poll_seconds         : float
    reconnect_max_seconds: float
    join_mode            : str
    lobby_scan_seconds   : float
    outbox_path          : str
    max_pending_results  : int
    unreadable_polls_before_alert: int
