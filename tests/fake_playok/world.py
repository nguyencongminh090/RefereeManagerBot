"""State of the fake PlayOK site: shared lobby tables and chats, plus one bot's seat and invitation."""
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

BOT_RATING = 1200


@dataclass
class FakeTable:
    """One table: its clock setting, the players in seats #1/#2 and the chat lines."""
    number: int
    time: str
    players: List[str] = field(default_factory=list)
    chat: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class FakeInvitation:
    """An invitation shown to the bot."""
    user: str
    rating: int
    table: int
    info: str


class FakeBoard:
    """The part of the site every bot sees alike: lobby tables and their chats.

    Chat lines are stored as the page shows them: "name: text" or "+ system text".
    `version` grows with every change. The lock is shared with the worlds built on the board.
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.version = 0
        self.tables: Dict[int, FakeTable] = {}

    def changed(self) -> None:
        """Marks that something on the site changed."""
        with self.lock:
            self.version += 1


class FakeWorld:
    """One bot's view of the site; the HTTP handler and the test thread both use it.

    Several worlds may share one `FakeBoard` (several bots on one site); each has its own seat,
    invitation and site conditions. `version` grows with every change the bot could notice, so
    that the page can poll for updates cheaply.

    Site conditions a test may switch on: `latency_sec` delays every GET answer, and `churn` makes
    the page rebuild its elements on every poll, so elements the driver found go stale.
    """

    def __init__(self, bot_name: str, board: Optional[FakeBoard] = None):
        self.bot_name = bot_name
        self.bot_table: Optional[int] = None
        self.invitation: Optional[FakeInvitation] = None
        self.latency_sec = 0.0
        self.churn = False
        self._board = board or FakeBoard()
        self._own_version = 0
        self._lock = self._board.lock

    @property
    def version(self) -> int:
        """Counts every change on the board and to this bot's own state."""
        return self._board.version + self._own_version

    def add_lobby_table(self, number: int, time: str, players=()) -> None:
        """Adds a table to the lobby; `players` fill seats #1 and #2 in order."""
        with self._lock:
            self._board.tables[number] = FakeTable(number, time, list(players))
            self._board.changed()

    def tables(self) -> List[FakeTable]:
        """Returns copies of the lobby tables, ordered by number."""
        with self._lock:
            return [FakeTable(t.number, t.time, list(t.players), list(t.chat))
                    for _, t in sorted(self._board.tables.items())]

    def chat(self, number: int) -> List[str]:
        """Returns the chat lines of a table, oldest first."""
        with self._lock:
            return list(self._board.tables[number].chat)

    def say(self, number: int, sender: str, text: str) -> None:
        """Adds a player's chat line to a table."""
        self._append(number, f"{sender}: {text}")

    def system(self, number: int, text: str) -> None:
        """Adds a system line ("+ text") to a table."""
        self._append(number, f"+ {text}")

    def seat(self, number: int, seat_no: int, name: Optional[str]) -> None:
        """Puts a player into seat 1 or 2 of a table; `None` empties the seat."""
        with self._lock:
            players = self._board.tables[number].players
            players.extend([""] * (seat_no - len(players)))
            players[seat_no - 1] = name or ""
            self._board.changed()

    def invite(self, user: str, rating: int, table: int, info: str) -> None:
        """Shows the bot an invitation to a table."""
        with self._lock:
            self.invitation = FakeInvitation(user, rating, table, info)
            self._own_version += 1

    def bot_join(self, number: int) -> bool:
        """Seats the bot as observer at a lobby table; False when the table does not exist."""
        with self._lock:
            if number not in self._board.tables:
                return False
            self.bot_table = number
            self._own_version += 1
            self.system(number, f"{self.bot_name} [{BOT_RATING}] joins")
            return True

    def bot_accept_invitation(self) -> bool:
        """Accepts the pending invitation and joins its table; False when there is none."""
        with self._lock:
            invitation, self.invitation = self.invitation, None
            if invitation is None:
                return False
            self._own_version += 1
            return self.bot_join(invitation.table)

    def decline_invitation(self) -> None:
        """Dismisses the pending invitation."""
        with self._lock:
            self.invitation = None
            self._own_version += 1

    def bot_say(self, text: str) -> None:
        """Writes into the chat of the table the bot sits at; ignored in the lobby."""
        with self._lock:
            if self.bot_table is not None:
                self.say(self.bot_table, self.bot_name, text)

    def bot_leave(self) -> None:
        """Takes the bot back to the lobby."""
        with self._lock:
            if self.bot_table is not None:
                self.system(self.bot_table, f"{self.bot_name} leaves")
            self.bot_table = None
            self._own_version += 1

    def _append(self, number: int, line: str) -> None:
        with self._lock:
            self._board.tables[number].chat.append(line)
            self._board.changed()
