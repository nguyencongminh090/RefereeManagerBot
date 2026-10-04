"""Routes chat commands to registered handlers."""
import logging
from typing import Callable, List, Dict, Optional
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class CommandContext:
    """What a command handler gets: who sent it, its arguments and the ports to act on.

    Attributes:
        sender: Nickname of the chat user who wrote the command.
        args: Words after the command name.
        driver: Browser access to the table.
        socket: Connection to the server.
    """
    sender: str
    args  : List[str]
    driver: 'IDriver'
    socket: 'IClientSocket'

class CommandDispatcher:
    """Looks up a chat command by name and runs its handler; a failing handler is logged."""

    def __init__(self, driver: 'IDriver', socket: 'IClientSocket'):
        """Creates a dispatcher without handlers."""
        self.driver          : 'IDriver'                                   = driver
        self.socket          : 'IClientSocket'                             = socket
        self.commands        : Dict[str, Callable[[CommandContext], None]] = {}

    def register(self, command: str, handler: Callable[[CommandContext], None]):
        """Binds a lower-case command word, prefix included, to its handler."""
        self.commands[command] = handler

    def dispatch(self, sender: str, full_text: str):
        """Runs the handler of the first word of `full_text`; unknown commands are only logged."""
        parts    = full_text.split()
        cmd_name = parts[0].lower()
        args     = parts[1:]
        if cmd_name in self.commands:
            ctx = CommandContext(
                sender = sender,
                args   = args,
                driver = self.driver,
                socket = self.socket
            )
            try:
                self.commands[cmd_name](ctx)
            except Exception as e:
                logger.error("Command '%s' failed: %s", cmd_name, e, exc_info=True)
        else:
            logger.warning("Unknown command: '%s'", cmd_name)