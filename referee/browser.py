"""Creates the Selenium browser used by the referee client."""
import os

# Path of the real Firefox binary; needed when `firefox` is a wrapper script (snap installs)
FIREFOX_BINARY_ENV = "FIREFOX_BINARY"


def create_firefox(headless: bool = False):
    """Starts a Firefox WebDriver.

    Selenium is imported here so the rest of the project (server, tests) runs without it.

    Args:
        headless: Run Firefox without a visible window.

    Returns:
        A selenium Firefox WebDriver.

    Raises:
        RuntimeError: If selenium is not installed.
    """
    try:
        from selenium import webdriver
    except ImportError as error:
        raise RuntimeError("selenium is not installed: pip install -r requirements.txt") from error
    options = webdriver.FirefoxOptions()
    binary = os.environ.get(FIREFOX_BINARY_ENV)
    if binary:
        options.binary_location = binary
    if headless:
        options.add_argument("-headless")
    return webdriver.Firefox(options=options)
