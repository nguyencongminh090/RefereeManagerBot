"""Fetches the pair games from the public PlayOK statistics pages over HTTP."""
import urllib.request
from typing import Callable, List, Optional
from urllib.parse import urlencode

from config.settings    import StatsConfig
from referee.stats_parser import parse_pair_games, parse_pair_totals
from referee.stats_port import IStatsSource, PairTotals, StatGame, StatsError

GAMES_TAB = "2"            # `sk=2` is the games tab of the profile
OPPONENTS_TAB = "3"        # `sk=3` lists the opponents; `sid=<name>` puts that name on the page
USER_AGENT = "RefereeManagerBot"


class HttpStatsSource(IStatsSource):
    """Reads `stat.phtml?u=<player>&g=<game>&sk=2&oid=<opponent>`; one request per call, no retry."""

    def __init__(self, config: StatsConfig, opener: Callable = urllib.request.urlopen):
        """Creates the source.

        Args:
            config: Page address, time zone and request timeout.
            opener: Called as `opener(request, timeout)`; injected so that tests need no network.
        """
        self._config = config
        self._opener = opener

    def pair_games(self, player: str, opponent: str) -> List[StatGame]:
        """Returns the page's games of `player` against `opponent`; see `IStatsSource`."""
        html = self._fetch(player, {"sk": GAMES_TAB, "oid": opponent})
        return parse_pair_games(html, self._config.tzinfo)

    def pair_totals(self, player: str, opponent: str) -> Optional[PairTotals]:
        """Returns the opponents-tab record of `player` against `opponent`; see `IStatsSource`."""
        return parse_pair_totals(self._fetch(player, {"sk": OPPONENTS_TAB, "sid": opponent}), opponent)

    def _fetch(self, player: str, parameters: dict) -> str:
        query = urlencode({"u": player, "g": self._config.game_code, **parameters})
        request = urllib.request.Request(f"{self._config.url}?{query}",
                                         headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=self._config.timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")
        except OSError as exc:                       # URLError, HTTPError and timeouts
            raise StatsError(f"cannot fetch the statistics of {player}: {exc}") from exc
