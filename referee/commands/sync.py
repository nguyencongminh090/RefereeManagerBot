"""The admin command `!sync`: sets the pair's score from PlayOK's own list of their games."""
from datetime import datetime, time
from typing   import Callable, List, Optional, Tuple

from config.settings import Settings
from domain.types    import GameResult
from network.messages import RequestType
from referee.commands.dispatcher import CommandContext, CommandDispatcher
from referee.stats_port import IStatsSource, PairTotals, StatGame, StatsError

TIME_FORMAT = "%H:%M"
DATE_TIME_FORMAT = "%Y-%m-%d %H:%M"
MAX_ARGUMENT_WORDS = 2         # "2026-10-04 18:30"
TOTAL_ARGUMENT = "total"       # `!sync total`: the all-time record, no round start


class SyncCommand:
    """Sets the pair's score from PlayOK's statistics pages: `!sync [[date] time]`, `!sync total`.

    A WBC match is played in one day, so `!sync` counts the games of the two seated players since
    midnight today (tournament time zone). A start can be given instead: the time in the command
    (today's date when only a time is written), else `tournament.round_start`. `!sync total` takes
    the all-time record of the pair (right when they never met before this match). The score goes
    to the server as `SET_SCORE`, so every rule and the audit log of `!set` apply. Nothing is sent
    when no game, or more games than a match has, are found: a wrong window or an old record would
    otherwise void correct games.
    """

    def __init__(self, settings: Settings, stats: IStatsSource,
                 *, clock: Optional[Callable[[], datetime]] = None):
        """Creates the command.

        Args:
            settings: Validated configuration.
            stats: Where the games of a pair come from.
            clock: Returns the current time in the tournament zone; injected for tests.
        """
        self._settings = settings
        self._stats    = stats
        zone           = settings.tournament.tzinfo
        self._clock    = clock or (lambda: datetime.now(zone))

    def register_on(self, dispatcher: CommandDispatcher) -> None:
        """Registers `sync` under the configured command prefix."""
        dispatcher.register(f"{self._settings.commands.prefix}sync", self.sync)

    def sync(self, ctx: CommandContext) -> None:
        """Counts the pair's games and asks the server to set that score; see the class docstring."""
        texts = self._settings.texts
        if ctx.args and not self._is_total(ctx.args) and self._parse_start(ctx.args) is None:
            ctx.driver.send_message(texts.sync_usage)
            return
        players = ctx.driver.get_players_name()
        if not all(players):
            ctx.driver.send_message(texts.seats_unreadable)
            return
        try:
            scores = self._scores(players, ctx.args)
        except StatsError as exc:
            ctx.driver.send_message(texts.sync_failed.format(reason=exc))
            return
        ctx.socket.send_packet({"type": RequestType.SET_SCORE.value,
                                "data": {"sender": ctx.sender, "players": list(players),
                                         "scores": list(scores)}})

    @staticmethod
    def _is_total(args: List[str]) -> bool:
        return [word.lower() for word in args] == [TOTAL_ARGUMENT]

    def _scores(self, players: Tuple[str, str], args: List[str]) -> Tuple[float, float]:
        """Chooses the source: `total`, or the games since the chosen start."""
        if self._is_total(args):
            return self._total_scores(players)
        return self._scores_since(players, self._start(args))

    def _start(self, args: List[str]) -> datetime:
        """The start from the command, else `tournament.round_start`, else midnight today."""
        if args:
            return self._parse_start(args)
        configured = self._settings.tournament.round_start
        if configured is not None:
            return configured
        return datetime.combine(self._clock().date(), time.min, tzinfo=self._settings.tournament.tzinfo)

    def _parse_start(self, args: List[str]) -> Optional[datetime]:
        """The start written in the command (`18:30` or `2026-10-04 18:30`); None if unreadable."""
        if len(args) > MAX_ARGUMENT_WORDS:
            return None
        zone, text = self._settings.tournament.tzinfo, " ".join(args)
        try:
            return datetime.strptime(text, DATE_TIME_FORMAT).replace(tzinfo=zone)
        except ValueError:
            pass
        try:
            at = datetime.strptime(text, TIME_FORMAT).time()
        except ValueError:
            return None
        return datetime.combine(self._clock().date(), time(at.hour, at.minute), tzinfo=zone)

    def _scores_since(self, players: Tuple[str, str], since: datetime) -> Tuple[float, float]:
        """Points of the left and right player from their games on or after `since`.

        Raises:
            StatsError: If the page fails, has no game in the window or more than `total_matches`.
        """
        left, right = players
        games = [g for g in self._stats.pair_games(left, right) if g.played_at >= since]
        if not games:
            raise StatsError(f"no games of {left} and {right} since {since:{DATE_TIME_FORMAT}}")
        self._check_length(len(games), f"since {since:{DATE_TIME_FORMAT}}")
        return self._points(self._record(games))

    @staticmethod
    def _record(games: List[StatGame]) -> PairTotals:
        wins  = sum(g.result is GameResult.WIN for g in games)
        draws = sum(g.result is GameResult.DRAW for g in games)
        return PairTotals(wins, len(games) - wins - draws, draws)

    def _total_scores(self, players: Tuple[str, str]) -> Tuple[float, float]:
        """Points of the left and right player from their all-time record.

        Raises:
            StatsError: If the page fails, the pair never met or the record is longer than a match.
        """
        left, right = players
        totals = self._stats.pair_totals(left, right)
        if totals is None or totals.games == 0:
            raise StatsError(f"{left} and {right} have no games against each other")
        self._check_length(totals.games, "in their record")
        return self._points(totals)

    def _check_length(self, games: int, where: str) -> None:
        total = self._settings.tournament.total_matches
        if games > total:
            raise StatsError(f"{games} games {where}, a match has {total}")

    def _points(self, record: PairTotals) -> Tuple[float, float]:
        scoring = self._settings.tournament.scoring.games
        return (scoring.points(record.wins, record.draws, record.losses),
                scoring.points(record.losses, record.draws, record.wins))
