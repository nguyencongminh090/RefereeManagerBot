"""Live bot and table state for the dashboard, built from the server's registries."""
from typing import List

from domain.ports       import BotStatus, IBotStatusSource
from serverapp.claims   import ClaimRegistry
from serverapp.sessions import SessionRegistry


class BotStatusProvider(IBotStatusSource):
    """Joins the session and claim registries into BotStatus rows."""

    def __init__(self, sessions: SessionRegistry, claims: ClaimRegistry) -> None:
        self._sessions = sessions
        self._claims   = claims

    def bots(self) -> List[BotStatus]:
        """Returns the authenticated bots, ordered by name."""
        rows = [BotStatus(name, f"{addr[0]}:{addr[1]}", self._claims.tables_of(addr), idle)
                for addr, name, idle in self._sessions.snapshot()]
        return sorted(rows, key=lambda bot: bot.name)
