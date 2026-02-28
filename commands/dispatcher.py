from typing import Callable, List, Dict, Optional
from dataclasses import dataclass


@dataclass
class CommandContext:
    sender: str
    args  : List[str]
    driver: 'IDriver'
    socket: 'IClientSocket'

class CommandDispatcher:
    def __init__(self, driver: 'IDriver', socket: 'IClientSocket'):   
        self.driver          : 'IDriver'                                   = driver
        self.socket          : 'IClientSocket'                             = socket
        self.commands        : Dict[str, Callable[[CommandContext], None]] = {}

    def register(self, command: str, handler: Callable[[CommandContext], None]):
        self.commands[command] = handler

    def dispatch(self, sender: str, full_text: str):
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
            except Exception:
                pass
        else:
            pass