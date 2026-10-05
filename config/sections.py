"""Builders of the process tables of the config: server, dashboard, public page and client."""
from typing import List

from config.process_config import (ClientConfig, DashboardConfig, PublicConfig, ServerConfig)
from config.reader         import Section

JOIN_MODES = ("auto", "invite")


def build_server(t: Section) -> ServerConfig:
    """Reads and validates the `[server]` table."""
    config = ServerConfig(
        host=t.text("host"), port=t.integer("port", 1),
        heartbeat_seconds=t.number("heartbeat_seconds", 0, True),
        backup_seconds=t.number("backup_seconds", 0, True),
        max_packet_bytes=t.integer("max_packet_bytes", 1),
        max_clients=t.integer("max_clients", 1),
        send_timeout_seconds=t.number("send_timeout_seconds", 0, True),
        auth_timeout_seconds=t.number("auth_timeout_seconds", 0, True),
        auth_max_failures=t.integer("auth_max_failures", 1),
        auth_lockout_seconds=t.number("auth_lockout_seconds", 0, True))
    t.finish()
    return config


def build_dashboard(t: Section) -> DashboardConfig:
    """Reads and validates the `[dashboard]` table."""
    config = DashboardConfig(
        enabled=t.boolean("enabled"), host=t.text("host"), port=t.integer("port", 1),
        refresh_seconds=t.number("refresh_seconds", 0, True),
        recent_games=t.integer("recent_games", 1), audit_entries=t.integer("audit_entries", 1),
        allow_edit=t.boolean("allow_edit"))
    t.finish()
    return config


def build_public(t: Section) -> PublicConfig:
    """Reads and validates the `[public]` table."""
    config = PublicConfig(
        enabled=t.boolean("enabled"), host=t.text("host"), port=t.integer("port", 1),
        refresh_seconds=t.number("refresh_seconds", 0, True),
        recent_games=t.integer("recent_games", 1))
    t.finish()
    return config


def build_client(t: Section) -> ClientConfig:
    """Reads and validates the `[client]` table."""
    config = ClientConfig(
        server_host=t.text("server_host"), server_port=t.integer("server_port", 1),
        poll_seconds=t.number("poll_seconds", 0, True),
        reconnect_max_seconds=t.number("reconnect_max_seconds", 0, True),
        join_mode=t.choice("join_mode", JOIN_MODES),
        lobby_scan_seconds=t.number("lobby_scan_seconds", 0, True),
        outbox_path=t.text("outbox_path", allow_empty=True),
        max_pending_results=t.integer("max_pending_results", 1),
        unreadable_polls_before_alert=t.integer("unreadable_polls_before_alert", 1))
    t.finish()
    return config


def check_page_ports(dashboard: DashboardConfig, public: PublicConfig,
                      problems: List[str]) -> None:
    """Records a problem when the dashboard and the public page would bind the same address."""
    if dashboard.enabled and public.enabled and (dashboard.host, dashboard.port) == (
            public.host, public.port):
        problems.append(f"'public.port' is {public.port}, the same address as the dashboard; "
                        "give the two pages different ports")
