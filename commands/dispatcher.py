from typing import Callable


class CommandDispatcher:
    def __init__(self):
        self.commands: dict[str, Callable] = {}

    def register(self, command: str, handler: Callable):
        self.commands[command] = handler

    def dispatch(self, command: str):
        if command in self.commands:
            self.commands[command]()
        else:
            print(f"Command {command} not found")   