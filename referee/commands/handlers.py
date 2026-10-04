"""Chat commands that the referee bot understands besides `!leave` (the session handles it)."""
from referee.commands.dispatcher import CommandContext, CommandDispatcher
from config.settings     import Settings
from network.messages      import RequestType

FALLBACK_LANGUAGE = "en"


class CommandHandlers:
    """Handlers for `!score` and `!rules`; they need the configuration, so they form an object."""

    def __init__(self, settings: Settings):
        """Keeps the settings the handlers read at call time."""
        self._settings = settings

    def register_on(self, dispatcher: CommandDispatcher) -> None:
        """Registers every handler under the configured command prefix."""
        prefix = self._settings.commands.prefix
        dispatcher.register(f"{prefix}score", self.score)
        dispatcher.register(f"{prefix}rules", self.rules)

    def score(self, ctx: CommandContext) -> None:
        """Asks the server for the standings; the answer goes to the table chat on arrival."""
        ctx.socket.send_packet({"type": RequestType.SCORE_QUERY.value, "data": {}})

    def rules(self, ctx: CommandContext) -> None:
        """Writes the rules reminder in the tournament language."""
        language = self._settings.tournament.language
        text = self._settings.messages.rules_for(language, FALLBACK_LANGUAGE)
        ctx.driver.send_message(text)
