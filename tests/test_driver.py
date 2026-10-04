import unittest

from referee.driver_port      import DriverError
from referee.driver           import SeleniumDriver, SilentSeleniumDriver, new_lines
from referee.html_dom         import parse_html
from referee.page_parser      import ChatLine
from tests.test_page_parser import SETTINGS, page


def line(text: str, sender: str = "") -> ChatLine:
    return ChatLine(sender or None, text, system=not sender)


class FakeElement:
    def __init__(self, log: list, selector: str, node=None):
        self.log, self.selector, self.node = log, selector, node

    @property
    def text(self) -> str:
        return self.node.get_text()

    def find_elements(self, by, selector):
        return [FakeElement(self.log, selector, n) for n in self.node.select(selector)]

    def get_attribute(self, name):
        return " ".join(self.node.classes) if name == "class" else None

    def click(self):
        self.log.append(("click", self.selector))

    def send_keys(self, text):
        self.log.append(("keys", self.selector, text))


class FakeBrowser:
    """Serves one HTML page; `find_elements` finds what the page contains."""
    def __init__(self, **page_args):
        self.page_args     = page_args
        self.log           = []
        self.window_handles = ["main"]
        self.switch_to     = self

    @property
    def page_source(self) -> str:
        html = page(**self.page_args)
        return html.replace('<div class="tcrdpan">', '<form><input></form><div class="tcrdpan">')   # the chat box

    def window(self, handle):
        self.log.append(("window", handle))

    def get(self, url):
        self.log.append(("get", url))

    def find_elements(self, by, selector):
        found = parse_html(self.page_source).select(selector)
        return [FakeElement(self.log, selector, n) for n in found]

    def quit(self):
        self.log.append(("quit",))


class NewLinesTests(unittest.TestCase):
    def test_appended_lines_are_new(self):
        self.assertEqual([line("c")], new_lines([line("a"), line("b")], [line("a"), line("b"), line("c")]))

    def test_nothing_new(self):
        self.assertEqual([], new_lines([line("a")], [line("a")]))

    def test_identical_consecutive_lines_are_not_lost(self):
        win = line("player #1 wins")
        self.assertEqual([win], new_lines([line("x"), win], [line("x"), win, win]))

    def test_dropped_oldest_lines(self):
        self.assertEqual([line("d")], new_lines([line("a"), line("b"), line("c")], [line("b"), line("c"), line("d")]))

    def test_first_read_and_unrelated_snapshot_return_everything(self):
        self.assertEqual([line("a")], new_lines([], [line("a")]))
        self.assertEqual([line("z")], new_lines([line("a")], [line("z")]))


# stands in for selenium's StaleElementReferenceException without importing selenium
StaleElement = type("StaleElementReferenceException", (Exception,), {"__module__": "selenium.common.exceptions"})


class BrokenBrowser(FakeBrowser):
    def __init__(self, error: Exception):
        super().__init__()
        self.error = error

    def find_elements(self, by, selector):
        raise self.error


class SeleniumDriverTests(unittest.TestCase):
    def test_browser_errors_become_driver_errors(self):
        driver = SeleniumDriver(SETTINGS.playok, BrokenBrowser(StaleElement("gone")), pause_seconds=0.0, sleep=lambda _: None)
        with self.assertRaises(DriverError):
            driver.join_table(106)

    def test_other_errors_are_not_hidden(self):
        driver = SeleniumDriver(SETTINGS.playok, BrokenBrowser(ValueError("bug")), pause_seconds=0.0, sleep=lambda _: None)
        with self.assertRaises(ValueError):
            driver.join_table(106)


    def make(self, **page_args):
        browser = FakeBrowser(**page_args)
        driver  = SeleniumDriver(SETTINGS.playok, browser, pause_seconds=0.0, sleep=lambda _: None)
        return driver, browser

    def test_open_site_loads_url_and_ignores_missing_cookie_banner(self):
        driver, browser = self.make()
        driver.open_site()
        self.assertEqual(("get", SETTINGS.playok.site_url), browser.log[0])

    def test_login_fails_when_form_is_missing(self):
        driver, browser = self.make()
        self.assertFalse(driver.login("u", "p"))

    def test_invitation_is_reported_and_accepted(self):
        driver, browser = self.make()
        self.assertEqual("zed", driver.check_for_invitation())
        self.assertTrue(driver.accept_invitation())
        self.assertIn(("click", SETTINGS.playok.selectors["invitation_accept"]), browser.log)

    def test_chat_lines_are_reported_once_including_repeats(self):
        driver, browser = self.make(chat=["+ player #1 wins"])
        self.assertEqual([("+", "player #1 wins")], driver.receive_messages())
        self.assertEqual([], driver.receive_messages())
        browser.page_args["chat"] = ["+ player #1 wins", "+ player #1 wins", "<b>alice</b>: gg"]
        self.assertEqual([("+", "player #1 wins"), ("alice", "gg")], driver.receive_messages())

    def test_names_are_remembered_after_seats_empty(self):
        driver, browser = self.make(seat1="alice", seat2="bob")
        driver.receive_messages()
        browser.page_args.update(seat1="-", seat2="-")
        self.assertEqual(("alice", "bob"), driver.get_players_name())

    def test_unknown_names_come_back_empty(self):
        driver, browser = self.make(seat1="-", seat2="-")
        self.assertEqual(("", ""), driver.get_players_name())

    def test_leave_table_forgets_names_and_chat(self):
        driver, browser = self.make(chat=["+ x"])
        driver.receive_messages()
        driver.leave_table()
        browser.page_args.update(seat1="-", seat2="-")
        self.assertEqual(("", ""), driver.get_players_name())
        self.assertEqual([("+", "x")], driver.receive_messages())

    def test_send_message_presses_enter(self):
        driver, browser = self.make()
        driver.send_message("hello")
        self.assertEqual(("keys", SETTINGS.playok.selectors["chat_input"], "hello"), browser.log[-1])

    def test_silent_driver_never_types_into_the_chat(self):
        browser = FakeBrowser()
        driver  = SilentSeleniumDriver(SETTINGS.playok, browser, pause_seconds=0.0, sleep=lambda _: None)
        driver.send_message("hello")
        self.assertEqual([], [entry for entry in browser.log if entry[0] == "keys"])

    def test_silent_driver_still_reads_the_chat(self):
        browser = FakeBrowser(chat=["+ player #1 wins"])
        driver  = SilentSeleniumDriver(SETTINGS.playok, browser, pause_seconds=0.0, sleep=lambda _: None)
        self.assertEqual([("+", "player #1 wins")], driver.receive_messages())

    def test_table_number_comes_from_the_table_title(self):
        driver, browser = self.make()
        self.assertEqual(116, driver.get_table_number())

    def test_lobby_tables_are_listed(self):
        driver, browser = self.make()
        self.assertEqual([106, 103], [t.number for t in driver.lobby_tables()])

    def test_join_clicks_the_join_button_of_a_table_with_a_free_seat(self):
        driver, browser = self.make()
        self.assertTrue(driver.join_table(103))
        self.assertEqual([("click", SETTINGS.playok.selectors["lobby_join"])], browser.log)

    def test_join_clicks_the_row_of_a_full_table_because_its_button_is_hidden(self):
        driver, browser = self.make()
        self.assertTrue(driver.join_table(106))
        self.assertEqual([("click", SETTINGS.playok.selectors["lobby_rows"])], browser.log)

    def test_join_reports_false_for_an_unknown_table(self):
        driver, browser = self.make()
        self.assertFalse(driver.join_table(999))
        self.assertEqual([], browser.log)

    def test_goto_lobby_uses_second_tab_and_room_option(self):
        driver, browser = self.make()
        browser.window_handles = ["a", "b"]
        driver.goto_lobby()
        self.assertEqual(("window", "b"), browser.log[0])


if __name__ == "__main__":
    unittest.main()
