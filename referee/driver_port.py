"""Port of the browser driver: what a match session needs from the PlayOK page."""
from abc    import ABC, abstractmethod
from typing import List, Optional, Tuple

from referee.page_parser import LobbyTable


class DriverError(Exception):
    """The PlayOK page could not be read or clicked; usually transient (stale or hidden element)."""


class IDriver(ABC):
    """Browser access to PlayOK: lobby, invitations, table chat and seats."""

    @abstractmethod
    def open_site(self) -> None:
        """Opens PlayOK and accepts the cookie banner."""

    @abstractmethod
    def goto_lobby(self) -> None:
        """Switches to the game tab and selects the tournament room."""

    @abstractmethod
    def login(self, username: str, password: str) -> bool:
        """Logs in; returns False when the login form cannot be found."""

    @abstractmethod
    def check_for_invitation(self) -> Optional[str]:
        """Returns the nickname of the player who invited the bot, or None."""

    @abstractmethod
    def accept_invitation(self) -> bool:
        """Accepts the pending invitation; returns False when there is none."""

    @abstractmethod
    def get_players_name(self) -> Tuple[str, str]:
        """Returns the nicknames of seat 1 and seat 2, or empty strings when unreadable."""

    @abstractmethod
    def get_table_number(self) -> Optional[int]:
        """Returns the number of the current table, or None when unreadable."""

    @abstractmethod
    def lobby_tables(self) -> List[LobbyTable]:
        """Returns the tables currently listed in the lobby."""

    @abstractmethod
    def join_table(self, number: int) -> bool:
        """Joins the lobby table with this number; returns False when that is not possible."""

    @abstractmethod
    def receive_messages(self) -> List[Tuple[str, str]]:
        """Returns the chat lines that are new since the previous call, as (sender, text)."""

    @abstractmethod
    def send_message(self, text: str) -> None:
        """Writes a message to the table chat."""

    @abstractmethod
    def leave_table(self) -> None:
        """Leaves the current table."""

    @abstractmethod
    def quit(self) -> None:
        """Closes the browser."""
