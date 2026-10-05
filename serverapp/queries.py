"""Handlers of the read-only requests: standings and roster."""
from typing import Any, Dict

from domain.ports      import ITeamRepository
from network.messages  import ResponseType
from serverapp.link    import Addr, ClientLink


class QueryHandlers:
    """Answers SCORE_QUERY and ROSTER_QUERY from the repository."""

    def __init__(self, repo: ITeamRepository, link: ClientLink) -> None:
        self._repo = repo
        self._link = link

    def on_score_query(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Sends the standings text."""
        self._link.send(addr, {'type': ResponseType.SCORE_DATA.value, 'text': self._repo.snapshot()})

    def on_roster_query(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Sends every player with team, role and whether the player is still active."""
        players = [{'name': p['nickname'], 'team': p['entrant_name'], 'role': p['role'],
                    'active': bool(p['active'])} for p in self._repo.roster()]
        self._link.reply(addr, ResponseType.ROSTER_DATA, players=players)
