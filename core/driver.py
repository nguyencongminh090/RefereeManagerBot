from typing import Tuple, List


class Driver:
    def __init__(self):
        self.__last_message_text = ''

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

    def receive_messages(self) -> List[str]:
        elements: List[str] = ...

        if not elements:
            return []

        if elements[-1] == self.__last_message_text:
            return []
        
        try:
            reversed_idx = elements[::-1].index(self.__last_message_text)
            self.__last_message_text = elements[-1]
            return elements[len(elements) - reversed_idx:]
        except ValueError:
            self.__last_message_text = elements[-1]
            return elements

    def send_message(self, message: str):
        self.__last_message_text = message
        ...

    def leave_table(self):
        self.__last_message_text = ''
        ...

    def quit(self):
        self.__last_message_text = ''