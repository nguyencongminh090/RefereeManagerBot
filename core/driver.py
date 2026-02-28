from abc    import ABC, abstractmethod
from typing import Tuple, List, Optional


class IDriver(ABC):
    @abstractmethod
    def open_site(self) -> None: ...

    @abstractmethod
    def goto_lobby(self) -> None: ...

    @abstractmethod
    def login(self, username: str, password: str) -> bool: ...

    @abstractmethod
    def check_for_invitation(self) -> Optional[str]: ...

    @abstractmethod
    def accept_invitation(self) -> bool: ...

    @abstractmethod
    def get_players_name(self) -> Tuple[str, str]: ...

    @abstractmethod
    def receive_messages(self) -> List[Tuple[str, str]]: ...

    @abstractmethod
    def send_message(self, text: str) -> None: ...

    @abstractmethod
    def leave_table(self) -> None: ...

    @abstractmethod
    def quit(self) -> None: ...

    def _parse_message(self, raw: str) -> Tuple[str, str]:
        if raw.startswith('+ '):
            return ('+', raw[2:])
        parts = raw.split(': ', 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
        return ('', raw)

class SeleniumDriver(IDriver):
    def __init__(self):
        self.__last_message_text = ''

    def open_site(self) -> None:
        ...

    def goto_lobby(self) -> None:
        ...

    def login(self, username: str, password: str) -> bool:
        ...

    def check_for_invitation(self) -> Optional[str]:
        ...

    def accept_invitation(self) -> bool:
        ...
 
    def get_players_name(self) -> Tuple[str, str]:
        ... 


    def receive_messages(self) -> List[Tuple[str, str]]:
        elements: List[str] = ...

        if not elements:
            return []
        if elements[-1] == self.__last_message_text:
            return []
        
        try:
            reversed_idx = elements[::-1].index(self.__last_message_text)
            self.__last_message_text = elements[-1]
            new_elements = elements[len(elements) - reversed_idx:]
        except ValueError:
            self.__last_message_text = elements[-1]
            new_elements = elements

        return [self._parse_message(raw) for raw in new_elements]

    def send_message(self, message: str) -> None:
        self.__last_message_text = message
        ...

    def leave_table(self) -> None:
        self.__last_message_text = ''
        ...

    def quit(self) -> None:
        self.__last_message_text = ''