"""Selenium implementation of the PlayOK driver port."""
import functools
import logging
import time
from typing           import Any, Callable, List, Optional, Sequence, Tuple
from referee.driver_port import DriverError, IDriver
from referee.page_parser import ChatLine, LobbyTable, PageParser, SeatTracker

logger = logging.getLogger(__name__)

# selenium's By.CSS_SELECTOR, spelled out so this module needs no selenium import
_CSS = "css selector"
# selenium's Keys.RETURN
_ENTER = "\ue006"
# lobby row class that marks a table with a free seat (only then is the join button shown)
_SEAT_FREE_CLASS = "tavail"


def new_lines(previous: Sequence[ChatLine], current: Sequence[ChatLine]) -> List[ChatLine]:
    """Returns the lines of `current` that were not in `previous`.

    The chat panel only grows, but PlayOK may drop the oldest lines, so the lines of `previous`
    can be a suffix-shifted prefix of `current`. The smallest shift that fits is taken, which
    keeps genuinely repeated lines (two identical consecutive lines are two lines) and loses none.
    """
    for shift in range(len(previous) + 1):
        kept = len(previous) - shift
        if list(previous[shift:]) == list(current[:kept]):
            return list(current[kept:])
    return list(current)


def _is_browser_error(error: Exception) -> bool:
    """Tells whether the error comes from selenium, without importing it."""
    return type(error).__module__.startswith("selenium.")


def _translate_browser_errors(method: Callable) -> Callable:
    """Wraps a driver method so that selenium errors surface as DriverError."""
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception as error:
            if not _is_browser_error(error):
                raise
            first_line = (str(error).splitlines() or [""])[0]
            raise DriverError(f"{method.__name__}: {type(error).__name__}: {first_line}") from error
    return wrapper


def _translating_browser_errors(cls: type) -> type:
    """Class decorator: applies the translation to every public method defined on the class."""
    for name, member in list(vars(cls).items()):
        if callable(member) and not name.startswith("_"):
            setattr(cls, name, _translate_browser_errors(member))
    return cls


@_translating_browser_errors
class SeleniumDriver(IDriver):
    """Drives PlayOK through a selenium-style browser object.

    `browser` needs `get`, `find_elements`, `page_source`, `window_handles`, `switch_to.window` and
    `quit` (any selenium WebDriver). All reading goes through `PageParser`, all selectors come from
    the config.
    """

    def __init__(self, playok, browser: Any, pause_seconds: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep):
        """Creates the driver around an already started browser.

        Args:
            playok: The `PlayOkConfig` (site URL, lobby room, selectors, patterns).
            browser: A selenium-style browser object.
            pause_seconds: Base wait after each page action, so the page can react.
            sleep: Waits for the given seconds; injected so that tests need no real delay.
        """
        self._playok = playok
        self._browser = browser
        self._pause_seconds = pause_seconds
        self._sleep = sleep
        self._parser = PageParser(playok)
        self._seats = SeatTracker()
        self._chat: List[ChatLine] = []

    def open_site(self) -> None:
        """Opens PlayOK and accepts the cookie banner when it shows."""
        self._browser.get(self._playok.site_url)
        self._pause(5)
        self._click("cookie_accept")

    def login(self, username: str, password: str) -> bool:
        """Fills the login form; returns False when the form is not on the page."""
        self._click("login_open")
        user_box, pass_box = self._first("login_user"), self._first("login_pass")
        if user_box is None or pass_box is None:
            return False
        user_box.send_keys(username)
        pass_box.send_keys(password)
        self._pause()
        clicked = self._click("login_submit")
        if clicked:
            self._click("login_start")
        return clicked

    def goto_lobby(self) -> None:
        """Switches to the game tab and selects the tournament room."""
        handles = self._browser.window_handles
        if len(handles) > 1:                                   # the game opens in a second tab
            self._browser.switch_to.window(handles[1])
        self._click("room_select")
        option = self._playok.selectors["room_option"].format(room=self._playok.lobby_room)
        found = self._browser.find_elements(_CSS, option)
        if found:
            found[0].click()
        self._pause()

    def check_for_invitation(self) -> Optional[str]:
        """Returns the nickname of the player who invited the bot, or None."""
        invitation = self._parser.invitation(self._dom())
        return invitation.user if invitation else None

    def accept_invitation(self) -> bool:
        """Clicks accept on the pending invitation; returns False when there is none."""
        accepted = self._click("invitation_accept")
        if accepted:
            self._reset_table()
        return accepted

    def get_players_name(self) -> Tuple[str, str]:
        """Returns the last readable pair of seat names, or two empty strings."""
        self._seats.update(self._parser.seat_names(self._dom()))
        return self._seats.names_for_result() or ("", "")

    def get_table_number(self) -> Optional[int]:
        """Returns the number of the table the bot sits at, or None when unreadable."""
        info = self._parser.table_info(self._dom())
        return info.number if info else None

    def lobby_tables(self) -> List[LobbyTable]:
        """Returns the tables currently listed in the lobby."""
        return self._parser.lobby_tables(self._dom())

    def join_table(self, number: int) -> bool:
        """Enters the lobby table with this number; returns False when it cannot be entered.

        A table with a free seat is entered with its join button. The button of a full table is
        hidden by the page (CSS `visibility`), so the row itself is clicked to watch it.
        """
        selectors = self._playok.selectors
        for row in self._browser.find_elements(_CSS, selectors["lobby_rows"]):
            labels = row.find_elements(_CSS, selectors["lobby_table_no"])
            if not labels or labels[0].text.strip().lstrip("#") != str(number):
                continue
            target = self._join_target(row)
            if target is None:
                return False
            target.click()
            self._reset_table()
            self._pause()
            return True
        return False

    def _join_target(self, row):
        """Returns the element to click to enter the table of this lobby row, or None."""
        if _SEAT_FREE_CLASS not in (row.get_attribute("class") or "").split():
            return row
        buttons = row.find_elements(_CSS, self._playok.selectors["lobby_join"])
        return buttons[0] if buttons else None

    def receive_messages(self) -> List[Tuple[str, str]]:
        """Returns the chat lines that appeared since the previous call, as (sender, text)."""
        dom = self._dom()
        # seats empty once a game ends: read them while it runs
        self._seats.update(self._parser.seat_names(dom))
        current = self._parser.chat_lines(dom)
        fresh, self._chat = new_lines(self._chat, current), current
        return [line.as_tuple() for line in fresh]

    def send_message(self, text: str) -> None:
        """Types the text into the table chat and sends it; does nothing without a chat box."""
        box = self._first("chat_input")
        if box is not None:
            box.send_keys(text + _ENTER)

    def leave_table(self) -> None:
        """Leaves the current table and forgets its chat and seats."""
        self._click("leave_table")
        self._reset_table()

    def quit(self) -> None:
        """Closes the browser."""
        self._reset_table()
        self._browser.quit()

    def _dom(self):
        return self._parser.parse(self._browser.page_source)

    def _first(self, selector_key: str):
        found = self._browser.find_elements(_CSS, self._playok.selectors[selector_key])
        return found[0] if found else None

    def _click(self, selector_key: str) -> bool:
        element = self._first(selector_key)
        if element is None:
            return False
        element.click()
        self._pause()
        return True

    def _pause(self, factor: float = 1.0) -> None:
        self._sleep(self._pause_seconds * factor)

    def _reset_table(self) -> None:
        self._chat = []
        self._seats.reset()


class SilentSeleniumDriver(SeleniumDriver):
    """A SeleniumDriver that reads the table chat but never writes to it (observer runs)."""

    def send_message(self, text: str) -> None:
        """Logs the text instead of typing it into the chat."""
        logger.info("Chat muted, not sent: %s", text)
