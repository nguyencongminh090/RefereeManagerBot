"""Chat commands that the referee bot understands besides `!leave` and `!break` (the session)."""
import random
import re
import time
from typing import Callable, Optional, Tuple

from referee.commands.dispatcher import CommandContext, CommandDispatcher
from config.settings     import Settings
from network.messages      import RequestType

SCORE_ARGUMENT    = re.compile(r"(\d+(?:[.,]\d+)?)-(\d+(?:[.,]\d+)?)")   # "3-2", "2,5-1.5"
SEAT_ARGUMENTS    = ("1", "2")


class CommandHandlers:
    """Handlers for `!score`, `!rules`, `!set` and `!cheer`; they need the configuration."""

    def __init__(self, settings: Settings, *, rng: Optional[random.Random] = None,
                 clock: Callable[[], float] = time.monotonic):
        """Keeps the settings the handlers read at call time.

        Args:
            settings: Validated configuration.
            rng: Picks the cheering sentence; injected so that tests are deterministic.
            clock: Returns monotonic seconds for the cheer cooldown.
        """
        self._settings   = settings
        self._rng        = rng or random.Random()
        self._clock      = clock
        self._last_cheer : Optional[float] = None

    def register_on(self, dispatcher: CommandDispatcher) -> None:
        """Registers every handler under the configured command prefix."""
        prefix = self._settings.commands.prefix
        dispatcher.register(f"{prefix}score", self.score)
        dispatcher.register(f"{prefix}rules", self.rules)
        dispatcher.register(f"{prefix}set", self.set_score)
        dispatcher.register(f"{prefix}cheer", self.cheer)

    def score(self, ctx: CommandContext) -> None:
        """Asks the server for the standings; the answer goes to the table chat on arrival."""
        ctx.socket.send_packet({"type": RequestType.SCORE_QUERY.value, "data": {}})

    def rules(self, ctx: CommandContext) -> None:
        """Writes the rules reminder: `!rules` in the tournament language, `!rules hu` in another.

        A language is a configured name or alias; an unknown one is answered with the known ones.
        """
        messages = self._settings.messages
        language = self._settings.tournament.language
        if ctx.args:
            language = messages.resolve(ctx.args[0])
        if language is None:
            names = ", ".join(messages.language_names())
            ctx.driver.send_message(self._settings.texts.rules_unknown.format(languages=names))
            return
        ctx.driver.send_message(messages.texts(language).rules)

    def set_score(self, ctx: CommandContext) -> None:
        """Asks the server to set the pair's score: `!set 3-2` = left seat 3, right seat 2.

        The seats are read when the command arrives, so a player who changed seats is followed.
        """
        messages = self._settings.texts
        match = SCORE_ARGUMENT.fullmatch("".join(ctx.args))
        if match is None:
            ctx.driver.send_message(messages.set_usage)
            return
        players = self._seated_players(ctx)
        if players is None:
            return
        scores = [float(part.replace(",", ".")) for part in match.groups()]
        ctx.socket.send_packet({"type": RequestType.SET_SCORE.value,
                                "data": {"sender": ctx.sender, "players": list(players),
                                         "scores": scores}})

    def cheer(self, ctx: CommandContext) -> None:
        """Cheers the player in seat 1 or 2 with a random sentence; limited by a cooldown."""
        messages = self._settings.texts
        if len(ctx.args) != 1 or ctx.args[0] not in SEAT_ARGUMENTS:
            ctx.driver.send_message(messages.cheer_usage)
            return
        if not self._cheer_allowed():
            return
        players = self._seated_players(ctx)
        if players is None:
            return
        name = players[SEAT_ARGUMENTS.index(ctx.args[0])]
        self._last_cheer = self._clock()
        ctx.driver.send_message(self._rng.choice(messages.cheers).format(name=name))

    def _cheer_allowed(self) -> bool:
        cooldown = self._settings.commands.cheer_cooldown_seconds
        return self._last_cheer is None or self._clock() - self._last_cheer >= cooldown

    def _seated_players(self, ctx: CommandContext) -> Optional[Tuple[str, str]]:
        """The names in seat 1 and 2 now; says so in the chat and returns None if unreadable."""
        players = ctx.driver.get_players_name()
        if all(players):
            return players
        ctx.driver.send_message(self._settings.texts.seats_unreadable)
        return None
