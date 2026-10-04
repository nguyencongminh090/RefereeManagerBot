import sys
import types
import unittest
from unittest.mock import patch

from referee.browser import create_firefox


class FakeOptions:
    def __init__(self):
        self.binary_location = ""
        self.arguments = []

    def add_argument(self, argument):
        self.arguments.append(argument)


def fake_selenium():
    """Returns a stand-in `selenium` package whose Firefox() hands back the options it got."""
    webdriver = types.SimpleNamespace(FirefoxOptions=FakeOptions, Firefox=lambda options: options)
    return {"selenium": types.SimpleNamespace(webdriver=webdriver)}


class CreateFirefoxTests(unittest.TestCase):
    def test_uses_the_binary_named_in_the_environment(self):
        with patch.dict(sys.modules, fake_selenium()), \
                patch.dict("os.environ", {"FIREFOX_BINARY": "/opt/firefox/firefox"}):
            self.assertEqual("/opt/firefox/firefox", create_firefox().binary_location)

    def test_keeps_the_default_binary_without_the_variable(self):
        with patch.dict(sys.modules, fake_selenium()), patch.dict("os.environ", clear=True):
            self.assertEqual("", create_firefox().binary_location)

    def test_headless_adds_the_headless_argument_only_when_asked(self):
        with patch.dict(sys.modules, fake_selenium()), patch.dict("os.environ", clear=True):
            self.assertIn("-headless", create_firefox(headless=True).arguments)
            self.assertNotIn("-headless", create_firefox().arguments)

    def test_missing_selenium_is_reported_as_a_runtime_error(self):
        with patch.dict(sys.modules, {"selenium": None}):
            with self.assertRaises(RuntimeError):
                create_firefox()


if __name__ == "__main__":
    unittest.main()
