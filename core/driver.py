from typing import Tuple, List


class Driver:
    def __init__(self):
        self.previous_count = 0

    def open_site(self) -> None:
        ...

    def goto_lobby(self) -> None:
        ...

    def login(self, username: str, password: str) -> bool:
        ...

    def check_for_invitation(self) -> str:
        ...

    def accept_invitation(self) -> bool:
        ...
 
    def get_players_name(self) -> Tuple[str, str]:
        ...

    def receive_messages(self) -> List[Tuple[str, str]]:
        elements: List[str] = ...
        current_count       = len(elements)
        if current_count == self.previous_count:
            return []
        new_elements = elements[self.previous_count:]
        self.previous_count = current_count
        return new_elements

    def send_message(self, message: str):
        ...

    def leave_table(self):
        ...

    def quit(self):
        ...