"""Turns PlayOK page HTML into plain Python objects: chat lines, game results, seat names,
invitations, table settings and the lobby table list.

Every selector and every regular expression comes from the config (`PlayOkConfig`), so when PlayOK
changes its markup only `config.toml` changes. The input is an HTML string (`driver.page_source`
on the live page, or a saved page in tests); nothing here needs a browser.

    parser = PageParser(settings.playok)
    dom    = parser.parse(driver.page_source)
    for line in parser.chat_lines(dom): ...
"""
import re
from dataclasses import dataclass
from typing      import FrozenSet, Iterable, List, Mapping, Optional, Pattern, Tuple

from referee.html_dom import Node, parse_html
from domain.types    import GameResult


# ----------------------------------------------------------------------------- value objects
@dataclass(frozen=True)
class ChatLine:
    """One line of a chat panel.

    `system` lines start with '+ ' on the page (joins, results, ...).
    """
    sender: Optional[str]          # None for system lines
    text  : str
    system: bool

    def as_tuple(self) -> Tuple[str, str]:
        """('+', text) for system lines, (sender, text) for chat."""
        return ("+", self.text) if self.system else (self.sender or "", self.text)


@dataclass(frozen=True)
class GameOutcome:
    """How one game ended: who won, or that it was drawn, and whether the clock decided it."""
    winner_seat   : Optional[int]       # 1 or 2; None for a draw
    drawn         : bool
    timed_out_seat: Optional[int]       # the seat whose clock ran out, when the game ended on time

    def results(self) -> Tuple[GameResult, GameResult]:
        """(result of seat 1, result of seat 2)."""
        if self.drawn:
            return GameResult.DRAW, GameResult.DRAW
        if self.winner_seat == 1:
            return GameResult.WIN, GameResult.LOSS
        return GameResult.LOSS, GameResult.WIN


@dataclass(frozen=True)
class Invitation:
    """A table invitation shown on the page: inviter, rating, table and its settings."""
    user : str
    elo  : int
    table: int
    info : str


@dataclass(frozen=True)
class TableInfo:
    """The settings of a table, read from its title (`raw` is the unparsed title)."""
    number           : int
    base_minutes     : int
    increment_seconds: int
    flags            : FrozenSet[str]     # e.g. {"sw", "x"}: swap2, not rated
    raw              : str


@dataclass(frozen=True)
class Seat:
    """A taken seat of a lobby table."""
    name  : str
    rating: Optional[int]


@dataclass(frozen=True)
class LobbyTable:
    """One row of the lobby table list."""
    number      : int
    time_control: str
    seats       : Tuple[Seat, ...]      # taken seats only
    joinable    : bool                  # the row offers the join button (a seat is still free)


# ----------------------------------------------------------------------------- helpers
_NUMBER = re.compile(r"\d+")
_TIME   = re.compile(r"(\d+)m(?:\+(\d+)s)?")


def _text(node: Node) -> str:
    return node.get_text().replace("\xa0", " ").strip()


class SeatTracker:
    """Seats are emptied the moment a game ends, so the names must be read while it runs.
    Feed every reading; ask for the names when the result line arrives."""

    def __init__(self):
        """Creates a tracker that has seen no names yet."""
        self._last: Optional[Tuple[str, str]] = None

    def update(self, names: Tuple[Optional[str], Optional[str]]) -> None:
        """Remembers the names when both seats are taken; partial readings are ignored."""
        if names[0] and names[1]:
            self._last = (names[0], names[1])

    def names_for_result(self) -> Optional[Tuple[str, str]]:
        """Returns the last complete pair of names, or None."""
        return self._last

    def reset(self) -> None:
        """Forgets the names, for example when the bot moves to another table."""
        self._last = None


class ResultTracker:
    """Reads the system lines of a table chat and reports each finished game once.

    A timeout line ("#2 exceeded time for game") precedes the win line; it is remembered and
    attached to the next outcome."""

    def __init__(self, patterns: Mapping[str, Pattern[str]]):
        """Creates a tracker over the compiled result patterns of the config."""
        self._p = patterns
        self._pending_timeout: Optional[int] = None

    def feed(self, line: ChatLine) -> Optional[GameOutcome]:
        """Reads one chat line; returns the outcome when it ends a game, else None."""
        if not line.system:
            return None
        m = self._p["timeout"].match(line.text)
        if m:
            self._pending_timeout = int(m["seat"])
            return None
        outcome = None
        if self._p["win_p1"].match(line.text):
            outcome = GameOutcome(1, False, self._pending_timeout)
        elif self._p["win_p2"].match(line.text):
            outcome = GameOutcome(2, False, self._pending_timeout)
        elif self._p["draw"].match(line.text):
            outcome = GameOutcome(None, True, self._pending_timeout)
        if outcome is not None:
            self._pending_timeout = None
        return outcome


# ----------------------------------------------------------------------------- parser
class PageParser:
    """Reads chat, seats, table settings, invitations and the lobby from a parsed page."""

    def __init__(self, playok):
        """`playok` is the `PlayOkConfig` (selectors and compiled patterns) from the settings."""
        self._sel = playok.selectors
        self._pat = playok.patterns

    @staticmethod
    def parse(html: str) -> Node:
        """Parses a page source into a tree that the other methods read."""
        return parse_html(html)

    def result_tracker(self) -> ResultTracker:
        """Creates a result tracker over this parser's patterns."""
        return ResultTracker(self._pat)

    # --- chat
    def chat_lines(self, dom: Node) -> List[ChatLine]:
        """The chat of the table panel, oldest first. The selector is scoped so that the lobby chat
        and the private-message thread (which use the same line markup) are not mixed in."""
        return [self._line(n) for n in dom.select(self._sel["chat_messages"])]

    @staticmethod
    def _line(node: Node) -> ChatLine:
        full  = node.get_text().replace("\xa0", " ")
        bold  = next((c for c in node.elements() if c.tag == "b"), None)
        if bold is not None and full.startswith(bold.get_text()):
            name = bold.get_text().strip()
            rest = full[len(bold.get_text()):]
            rest = rest[1:].lstrip() if rest.startswith(":") else rest.strip()
            return ChatLine(name, rest, False)
        if full.startswith("+ "):
            return ChatLine(None, full[2:].strip(), True)
        head, sep, tail = full.partition(": ")
        if sep:
            return ChatLine(head.strip(), tail.strip(), False)
        return ChatLine("", full.strip(), False)

    # --- table
    def seat_names(self, dom: Node) -> Tuple[Optional[str], Optional[str]]:
        """(seat #1, seat #2); None where the seat is empty ('-')."""
        names = [_text(n) for n in dom.select(self._sel["seat_names"])[:2]]
        names += [""] * (2 - len(names))
        return tuple(None if n in ("", "-") else n  # type: ignore[return-value]
                     for n in names)

    def table_info(self, dom: Node) -> Optional[TableInfo]:
        """Returns the settings in the table title, or None when it is missing or unreadable."""
        node = dom.select_one(self._sel["table_title"])
        if node is None:
            return None
        text = _text(node)
        m = self._pat["table_header"].match(text)
        if not m:
            return None
        base, inc = _TIME.fullmatch(m["time"]).groups()
        raw_flags = (m.groupdict().get("flags") or "").split(",")
        flags = frozenset(f.strip().lower() for f in raw_flags if f.strip())
        return TableInfo(int(m["table"]), int(base), int(inc or 0), flags, text)

    # --- invitation
    def invitation(self, dom: Node) -> Optional[Invitation]:
        """Returns the invitation shown on the page, or None."""
        node = dom.select_one(self._sel["invitation_text"])
        if node is None:
            return None
        m = self._pat["invitation"].match(_text(node))
        return Invitation(m["user"], int(m["elo"]), int(m["table"]), m["info"]) if m else None

    # --- lobby
    def lobby_tables(self, dom: Node) -> List[LobbyTable]:
        """Returns the lobby rows that show a table number, in page order."""
        tables = []
        for row in dom.select(self._sel["lobby_rows"]):
            number = row.select_one(self._sel["lobby_table_no"])
            found  = _NUMBER.search(_text(number)) if number is not None else None
            if not found:
                continue
            time_node = row.select_one(self._sel["lobby_time"])
            seats = []
            for seat in row.select(self._sel["lobby_seats"]):
                rating_node = next((n for n in seat.descendants() if n.tag == "span"), None)
                rating = _text(rating_node) if rating_node is not None else ""
                seats.append(Seat(seat.own_text().strip(),
                                  int(rating) if rating.isdigit() else None))
            time_control = _text(time_node) if time_node is not None else ""
            tables.append(LobbyTable(int(found.group()), time_control, tuple(seats),
                                     "tavail" in row.classes))
        return tables


# ------------------------------------------------------------------ decisions on parsed data
def eligible_tables(tables: Iterable[LobbyTable], roster: Mapping[str, str]) -> List[LobbyTable]:
    """Tables a bot should referee: two seats taken, both on the roster, from different teams.
    `roster` maps nickname -> team (entrant) name; matching ignores case."""
    by_name = {name.lower(): team for name, team in roster.items()}
    chosen = []
    for table in tables:
        if len(table.seats) != 2:
            continue
        teams = [by_name.get(seat.name.lower()) for seat in table.seats]
        if None not in teams and teams[0] != teams[1]:
            chosen.append(table)
    return chosen


def table_rule_problems(info: TableInfo, rules) -> List[str]:
    """What is wrong with a table compared to `TableRules` from the config (empty list = fine).
    'No undo' and 'public' cannot be seen in the table title, so they are not checked here."""
    problems = []
    expected = _TIME.fullmatch(rules.time_control)
    if expected:
        base, inc = int(expected.group(1)), int(expected.group(2) or 0)
        if (info.base_minutes, info.increment_seconds) != (base, inc):
            problems.append(f"time control is {info.base_minutes}m+{info.increment_seconds}s, "
                            f"expected {rules.time_control}")
    if rules.swap2 != ("sw" in info.flags):
        problems.append("swap2 is " + ("off" if rules.swap2 else "on")
                        + f", expected {'on' if rules.swap2 else 'off'}")
    if rules.rated == ("x" in info.flags):
        problems.append("the game is " + ("not rated" if rules.rated else "rated")
                        + f", expected {'rated' if rules.rated else 'not rated'}")
    return problems
