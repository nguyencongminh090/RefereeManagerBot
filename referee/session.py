"""One referee session at one table: reads game results from the chat and reports them."""
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from enum        import Enum
from datetime import datetime, timedelta, tzinfo
from typing      import Any, Callable, Deque, Dict, Optional, Set

from config.settings       import Settings
from network.messages      import RequestType
from network.ports         import IClientSocket
from referee.commands.dispatcher import CommandDispatcher
from referee.driver_port   import IDriver
from referee.page_parser   import ChatLine, ResultTracker

logger = logging.getLogger(__name__)

SYSTEM_SENDER = "+"
LEAVE_COMMAND = "leave"
BREAK_TIME_FORMAT = "%H:%M"
MAX_PENDING_RESULTS = 5
UNREADABLE_POLLS_BEFORE_ALERT = 10
CODE_MICROMATCH_FULL = "MICROMATCH_FULL"


class SessionState(Enum):
    """Phase of a session: games running, a break between games, or finished."""

    IN_PROGRESS = 1
    BREAK_TIME  = 2
    COMPLETED   = 3


@dataclass(frozen=True)
class SessionRules:
    """The settings a session reads repeatedly, copied once so the session needs no deep lookups.

    Attributes:
        total_matches: Games in one micro-match.
        break_after: Games between breaks, 0 for no break.
        break_minutes: Length of a break.
        prefix: Command prefix of the table chat.
        bye: Goodbye text.
        break_text: Break announcement with `{curr_time}` and `{resume_time}`.
        tz: Time zone of the tournament.
        is_admin: Tells whether a nickname is a tournament admin.
        is_admin_only: Tells whether a command is restricted to admins.
    """
    total_matches: int
    break_after  : int
    break_minutes: int
    prefix       : str
    bye          : str
    break_text   : str
    tz           : tzinfo
    is_admin     : Callable[[str], bool]
    is_admin_only: Callable[[str], bool]

    @classmethod
    def from_settings(cls, settings: Settings) -> "SessionRules":
        """Builds the rules from the validated settings."""
        tournament = settings.tournament
        return cls(tournament.total_matches, tournament.break_after, tournament.break_minutes,
                   settings.commands.prefix, settings.messages.bye, settings.messages.break_text,
                   tournament.tzinfo, tournament.is_admin, settings.commands.is_admin_only)


@dataclass
class MatchContext:
    """What the session knows about its pair of players.

    Attributes:
        p1_name: Nickname in seat 1; empty until read.
        p2_name: Nickname in seat 2; empty until read.
        total_matches: Games in one micro-match.
        games_played: Games the server has counted for this pair.
        state: Current phase of the session.
    """
    p1_name      : str          = ""
    p2_name      : str          = ""
    total_matches: int          = 0
    games_played : int          = 0
    state        : SessionState = SessionState.IN_PROGRESS


@dataclass(frozen=True)
class PendingResult:
    """A finished game that has not been sent yet.

    Attributes:
        match_id: Id that makes sending it again safe.
        scores: Results of seat 1 and seat 2.
    """
    match_id: str
    scores  : tuple


class MatchSession:
    """Referees one micro-match (`tournament.total_matches` games) between two players.

    Each finished game is sent once, with a fresh `match_id`; a game whose player names cannot be
    read is kept (up to `MAX_PENDING_RESULTS`) and sent as soon as they can. The server is the
    authority on the game count: `games_played` follows its MATCH_ACK and the session ends when
    the server says the pair is complete. Without any ACK, the number of results sent is the
    fallback completion rule.
    """

    def __init__(self, driver: IDriver, dispatcher: CommandDispatcher, socket: IClientSocket,
                 settings: Settings, *, clock: Optional[Callable[[], datetime]] = None):
        """Creates the session and tries to read the player names right away.

        Args:
            driver: Browser access to the table.
            dispatcher: Runs the commands that this session does not handle itself.
            socket: Connection used to send results to the server.
            settings: Validated configuration (tournament, commands, messages, playok patterns).
            clock: Returns the current time; injected so that tests need no wall clock.
        """
        self._driver     = driver
        self._dispatcher = dispatcher
        self._socket     = socket
        self._rules      = SessionRules.from_settings(settings)
        self._tracker    = ResultTracker(settings.playok.patterns)
        self._clock      = clock or (lambda: datetime.now(self._rules.tz))
        self._resume_at  : Optional[datetime] = None
        self._context    = MatchContext(total_matches=self._rules.total_matches)
        self._pending    : Deque[PendingResult] = deque()
        self._sent_ids   : Set[str] = set()
        self._rejected   = 0
        self._has_ack    = False
        self._failed_polls = 0
        self._refresh_names()

    @property
    def context(self) -> MatchContext:
        """The players, game count and state of the session."""
        return self._context

    def poll(self) -> None:
        """Reads new chat lines, handles them and retries the results waiting for names."""
        if self._context.state == SessionState.COMPLETED:
            return
        self._end_break_if_over()
        for sender, text in self._driver.receive_messages():
            self.process_message(sender, text)
        self._retry_pending()

    def process_message(self, sender: str, text: str) -> None:
        """Handles one chat line: a system line may finish a game, a prefixed line is a command."""
        if self._context.state == SessionState.COMPLETED:
            return
        if sender == SYSTEM_SENDER:
            self._handle_system_line(text)
        elif text.startswith(self._rules.prefix):
            self._handle_command(sender, text)

    def on_ack(self, data: Dict[str, Any]) -> None:
        """Applies the server's MATCH_ACK for one of this session's results.

        Args:
            data: ACK payload with `match_id`, `games` and `complete`.
        """
        is_ours = data.get("match_id") in self._sent_ids
        if self._context.state == SessionState.COMPLETED or not is_ours:
            return
        self._has_ack = True
        self._context.games_played = int(data.get("games", self._context.games_played))
        logger.info("Result %s acknowledged: %d game(s) played, complete=%s", data.get("match_id"),
                    self._context.games_played, bool(data.get("complete")))
        if data.get("complete"):
            self.leave()

    def on_rejected(self, code: str, match_id: str) -> None:
        """Handles the server's ERROR for one of this session's results.

        Args:
            code: Server error code, for example `MICROMATCH_FULL`.
            match_id: Id of the rejected result.
        """
        if self._context.state == SessionState.COMPLETED or match_id not in self._sent_ids:
            return
        self._rejected += 1
        logger.warning("Result %s rejected by the server: %s", match_id, code)
        if code == CODE_MICROMATCH_FULL:
            self.leave()

    def leave(self) -> None:
        """Ends the session: says goodbye and leaves the table."""
        self._context.state = SessionState.COMPLETED
        if self._pending:
            logger.error("Leaving with %d unsent results: %s",
                         len(self._pending), list(self._pending))
        self._driver.send_message(self._rules.bye)
        self._driver.leave_table()

    def _refresh_names(self) -> bool:
        p1, p2 = self._driver.get_players_name()
        if not p1 or not p2:
            return False
        self._context.p1_name, self._context.p2_name = p1, p2
        return True

    def _handle_command(self, sender: str, text: str) -> None:
        words = text.split()
        if not words:
            return
        command = words[0]
        if self._rules.is_admin_only(command) and not self._rules.is_admin(sender):
            logger.info("Ignored admin-only command %r from %r", command, sender)
            return
        if command.lower().removeprefix(self._rules.prefix) == LEAVE_COMMAND:
            self.leave()
            return
        self._dispatcher.dispatch(sender, text)

    def _handle_system_line(self, text: str) -> None:
        outcome = self._tracker.feed(ChatLine(None, text, True))
        if outcome is None:
            return
        if len(self._pending) >= MAX_PENDING_RESULTS:
            logger.error("Game result dropped: %d results already wait for player names",
                         len(self._pending))
            return
        self._pending.append(PendingResult(str(uuid.uuid4()), outcome.results()))
        self._flush_pending()

    def _retry_pending(self) -> None:
        """Re-reads the names for waiting results; escalates in the log after repeated failures."""
        if not self._pending:
            self._failed_polls = 0
            return
        self._flush_pending()
        if not self._pending:
            self._failed_polls = 0
            return
        self._failed_polls += 1
        if self._failed_polls >= UNREADABLE_POLLS_BEFORE_ALERT:
            logger.error("%d results unsent for %d polls: player names are unreadable "
                         "(context: %s)",
                         len(self._pending), self._failed_polls, self._context)

    def _flush_pending(self) -> None:
        if not self._pending or not self._refresh_names():
            return
        while self._pending and self._context.state != SessionState.COMPLETED:
            self._send_result(self._pending.popleft())

    def _send_result(self, result: PendingResult) -> None:
        self._sent_ids.add(result.match_id)
        packet = self._result_packet(result)
        self._socket.send_packet(packet)
        logger.info("Result %s sent: table %s, %s vs %s, scores %s", result.match_id,
                    packet["data"]["table_no"], self._context.p1_name, self._context.p2_name,
                    packet["data"]["scores"])
        if self._local_count_complete():
            self.leave()
        else:
            self._start_break_if_due()

    def _local_count_complete(self) -> bool:
        """Fallback completion: only while the server has never answered."""
        accepted = len(self._sent_ids) - self._rejected
        return not self._has_ack and accepted >= self._rules.total_matches

    def _result_packet(self, result: PendingResult) -> dict:
        score_p1, score_p2 = result.scores
        return {
            "type": RequestType.MATCH_RESULT.value,
            "meta": {"match_id": result.match_id},
            "data": {
                "players"  : [self._context.p1_name, self._context.p2_name],
                "scores"   : [score_p1.value, score_p2.value],
                "table_no" : self._driver.get_table_number(),
            },
        }

    def _start_break_if_due(self) -> None:
        sent = len(self._sent_ids)
        if not self._rules.break_after or sent % self._rules.break_after:
            return
        now = self._clock()
        self._resume_at = now + timedelta(minutes=self._rules.break_minutes)
        self._context.state = SessionState.BREAK_TIME
        self._driver.send_message(self._rules.break_text.format(
            curr_time=now.strftime(BREAK_TIME_FORMAT),
            resume_time=self._resume_at.strftime(BREAK_TIME_FORMAT)))

    def _end_break_if_over(self) -> None:
        if self._context.state != SessionState.BREAK_TIME or self._resume_at is None:
            return
        if self._clock() >= self._resume_at:
            self._context.state = SessionState.IN_PROGRESS
            self._resume_at = None
