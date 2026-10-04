"""One referee session at one table: reads game results from the chat and reports them."""
import logging
import uuid
from collections import deque
from dataclasses import dataclass, replace
from enum        import Enum
from datetime import datetime, timedelta, tzinfo
from typing      import Any, Callable, Deque, Dict, Optional, Set

from config.messages       import MessageTexts
from config.settings       import Settings
from network.messages      import RequestType
from network.ports         import IClientSocket
from referee.commands.dispatcher import CommandDispatcher
from referee.driver_port   import DriverError, IDriver
from referee.info_text     import info_key
from referee.page_parser   import ChatLine, ResultTracker

logger = logging.getLogger(__name__)

SYSTEM_SENDER = "+"
LEAVE_COMMAND = "leave"
BREAK_COMMAND = "break"
TEAM_FORMAT   = "team"
BREAK_TIME_FORMAT = "%H:%M"
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
        tz: Time zone of the tournament.
        is_admin: Tells whether a nickname is a tournament admin.
        is_admin_only: Tells whether a command is restricted to admins.
        max_pending: Finished games kept while the player names cannot be read.
        alert_polls: Polls with unsent games before the log escalates.
        break_max_minutes: Longest break `!break` may announce.
        is_team: True in a team event, where results also show the team score.
        texts: Chat texts in the tournament language.
    """
    total_matches: int
    break_after  : int
    break_minutes: int
    prefix       : str
    tz           : tzinfo
    is_admin     : Callable[[str], bool]
    is_admin_only: Callable[[str], bool]
    max_pending  : int
    alert_polls  : int
    break_max_minutes: float
    is_team      : bool
    texts        : MessageTexts

    @classmethod
    def from_settings(cls, settings: Settings) -> "SessionRules":
        """Builds the rules from the validated settings."""
        tournament, client = settings.tournament, settings.client
        return cls(tournament.total_matches, tournament.break_after, tournament.break_minutes,
                   settings.commands.prefix, tournament.tzinfo, tournament.is_admin,
                   settings.commands.is_admin_only, client.max_pending_results,
                   client.unreadable_polls_before_alert, settings.commands.break_max_minutes,
                   tournament.format == TEAM_FORMAT, settings.texts)


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
        players: Nicknames of seat 1 and seat 2 when the game ended; None if never read.
    """
    match_id: str
    scores  : tuple
    players : Optional[tuple] = None


class MatchSession:
    """Referees one micro-match (`tournament.total_matches` games) between two players.

    Each finished game is sent once, with a fresh `match_id`; a game whose player names cannot be
    read is kept (up to `client.max_pending_results`) and sent as soon as they can. The server is the
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
        self._has_left = False
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
        if not data.get("duplicate"):
            self._announce(data)
        if data.get("complete"):
            self.leave()

    def on_score_set(self, data: Dict[str, Any]) -> None:
        """Applies the server's answer to `!set`: writes the pair line and follows its count.

        Args:
            data: SCORE_SET payload with `players`, `points`, `games` and `complete`.
        """
        if self._context.state == SessionState.COMPLETED:
            return
        self._has_ack = True
        self._context.games_played = int(data.get("games", self._context.games_played))
        self._announce(data)
        if data.get("complete"):
            self.leave()

    def _announce(self, data: Dict[str, Any]) -> None:
        """Writes the server's answer to the chat; a page error loses the lines but nothing else.

        The result is already stored, so the session must still go on (and complete) when the
        chat cannot be written.
        """
        try:
            self._write_announcement(data)
        except DriverError as error:
            logger.warning("Score announcement lost on a page error: %s", error)

    def _write_announcement(self, data: Dict[str, Any]) -> None:
        """Writes the score lines of a server answer, then the info text for the game count."""
        texts = self._rules.texts
        if "players" not in data or "points" not in data:
            logger.warning("Server answer without players or points, nothing written: %s", data)
            return
        if self._rules.is_team and data.get("teams"):
            (team1, team2), (t1, t2) = data["teams"], data["team_points"]
            self._driver.send_message(texts.result_team.format(
                team1=team1, team2=team2, t1=f"{t1:g}", t2=f"{t2:g}"))
        (p1, p2), (s1, s2) = data["players"], data["points"]
        self._driver.send_message(texts.result_pair.format(
            p1=p1, p2=p2, s1=f"{s1:g}", s2=f"{s2:g}"))
        key = info_key(self._context.games_played, self._rules.total_matches,
                       self._rules.break_after)
        if key is not None:
            self._driver.send_message(getattr(texts, key))

    def on_set_refused(self, reason: str) -> None:
        """Tells the table why the server did not change the score."""
        if self._context.state != SessionState.COMPLETED:
            self._driver.send_message(self._rules.texts.set_failed.format(reason=reason))

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
        self._driver.send_message(self._rules.texts.bye)
        self.ensure_left()

    def ensure_left(self) -> None:
        """Leaves the table unless that already happened.

        `leave` may stop on a page error after the session is already completed; the client calls
        this again on its next pass until the table is really left.
        """
        if self._has_left:
            return
        self._driver.leave_table()
        self._has_left = True

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
        name = command.lower().removeprefix(self._rules.prefix)
        if name == LEAVE_COMMAND:
            self.leave()
            return
        if name == BREAK_COMMAND:
            self._start_manual_break(words[1:])
            return
        self._dispatcher.dispatch(sender, text)

    def _handle_system_line(self, text: str) -> None:
        outcome = self._tracker.feed(ChatLine(None, text, True))
        if outcome is None:
            return
        if len(self._pending) >= self._rules.max_pending:
            logger.error("Game result dropped: %d results already wait for player names",
                         len(self._pending))
            return
        self._refresh_names()
        self._pending.append(PendingResult(str(uuid.uuid4()), outcome.results(),
                                           self._seated_names()))
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
        if self._failed_polls >= self._rules.alert_polls:
            logger.error("%d results unsent for %d polls: player names are unreadable "
                         "(context: %s)",
                         len(self._pending), self._failed_polls, self._context)

    def _seated_names(self) -> Optional[tuple]:
        """The last pair of names read from both seats, or None before the first full reading."""
        pair = (self._context.p1_name, self._context.p2_name)
        return pair if all(pair) else None

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        self._refresh_names()
        while self._pending and self._context.state != SessionState.COMPLETED:
            players = self._pending[0].players or self._seated_names()
            if players is None:
                return
            self._send_result(replace(self._pending.popleft(), players=players))

    def _send_result(self, result: PendingResult) -> None:
        self._sent_ids.add(result.match_id)
        packet = self._result_packet(result)
        self._socket.send_packet(packet)
        logger.info("Result %s sent: table %s, %s vs %s, scores %s", result.match_id,
                    packet["data"]["table_no"], *result.players,
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
                "players"  : list(result.players),
                "scores"   : [score_p1.value, score_p2.value],
                "table_no" : self._driver.get_table_number(),
            },
        }

    def _start_break_if_due(self) -> None:
        sent = len(self._sent_ids)
        if not self._rules.break_after or sent % self._rules.break_after:
            return
        self._start_break(self._rules.break_minutes)

    def _start_manual_break(self, args: list) -> None:
        """`!break [minutes]`: announces a break of the given (default: configured) length."""
        limit = self._rules.break_max_minutes
        try:
            minutes = float(args[0]) if args else self._rules.break_minutes
        except ValueError:
            minutes = 0.0
        if len(args) > 1 or not 0 < minutes <= limit:
            self._driver.send_message(self._rules.texts.break_usage.format(max_minutes=f"{limit:g}"))
            return
        self._start_break(minutes)

    def _start_break(self, minutes: float) -> None:
        now = self._clock()
        self._resume_at = now + timedelta(minutes=minutes)
        self._context.state = SessionState.BREAK_TIME
        self._driver.send_message(self._rules.texts.break_text.format(
            curr_time=now.strftime(BREAK_TIME_FORMAT),
            resume_time=self._resume_at.strftime(BREAK_TIME_FORMAT)))

    def _end_break_if_over(self) -> None:
        if self._context.state != SessionState.BREAK_TIME or self._resume_at is None:
            return
        if self._clock() >= self._resume_at:
            self._context.state = SessionState.IN_PROGRESS
            self._resume_at = None
