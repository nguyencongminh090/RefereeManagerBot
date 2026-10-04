import random
import unittest
from dataclasses import replace

from config.messages import MessagesConfig
from network.messages import RequestType
from referee.commands.dispatcher import CommandDispatcher
from referee.commands.handlers import CommandHandlers
from tests.test_page_parser import SETTINGS
from tests.test_session import FakeDriver, FakeSocket

MESSAGES = SETTINGS.texts
COOLDOWN = SETTINGS.commands.cheer_cooldown_seconds


class HandlerTestCase(unittest.TestCase):
    def build(self, names=("alice", "bob"), settings=SETTINGS):
        self.driver, self.socket, self.now = FakeDriver(names), FakeSocket(), 1000.0
        handlers = CommandHandlers(settings, rng=random.Random(7), clock=lambda: self.now)
        self.dispatcher = CommandDispatcher(self.driver, self.socket)
        handlers.register_on(self.dispatcher)

    def run_command(self, text, sender="gtrate"):
        self.dispatcher.dispatch(sender, text)


class RulesCommandTests(HandlerTestCase):
    def build_two_languages(self):
        english = SETTINGS.texts
        messages = MessagesConfig({"en": english, "hu": replace(english, rules="Szabalyok.")},
                                  {"hun": "hu", "eng": "en"})
        self.build(settings=replace(SETTINGS, messages=messages))

    def test_no_argument_gives_the_tournament_language(self):
        self.build_two_languages()
        self.run_command("!rules")
        self.assertEqual([SETTINGS.texts.rules], self.driver.said)

    def test_a_language_or_its_alias_gives_that_language(self):
        self.build_two_languages()
        for text in ("!rules hu", "!rules HUN"):
            self.run_command(text)
        self.assertEqual(["Szabalyok."] * 2, self.driver.said)

    def test_an_unknown_language_lists_the_known_ones(self):
        self.build_two_languages()
        self.run_command("!rules klingon")
        self.assertEqual([SETTINGS.texts.rules_unknown.format(languages="en, hu")], self.driver.said)


class SetCommandTests(HandlerTestCase):
    def test_sends_the_score_of_the_seats_as_they_are_now(self):
        self.build()
        self.run_command("!set 3-2", sender="gtrate")
        packet = self.socket.packets[0]
        self.assertEqual(RequestType.SET_SCORE.value, packet["type"])
        self.assertEqual({"sender": "gtrate", "players": ["alice", "bob"], "scores": [3.0, 2.0]}, packet["data"])

    def test_accepts_spaces_halves_and_decimal_commas(self):
        self.build()
        self.run_command("!set 2,5 - 1.5")
        self.assertEqual([2.5, 1.5], self.socket.packets[0]["data"]["scores"])

    def test_malformed_score_is_answered_with_the_usage_text(self):
        self.build()
        for text in ("!set", "!set 3", "!set a-b", "!set 3-2-1", "!set -3-2"):
            self.run_command(text)
        self.assertEqual([], self.socket.packets)
        self.assertEqual([MESSAGES.set_usage] * 5, self.driver.said)

    def test_unreadable_seats_are_reported_and_nothing_is_sent(self):
        self.build(names=("alice", ""))
        self.run_command("!set 3-2")
        self.assertEqual([], self.socket.packets)
        self.assertEqual([MESSAGES.seats_unreadable], self.driver.said)


class CheerCommandTests(HandlerTestCase):
    def test_cheers_the_player_in_the_given_seat(self):
        self.build()
        self.run_command("!cheer 2", sender="carol")
        [said] = self.driver.said
        self.assertIn(said, [c.format(name="bob") for c in MESSAGES.cheers])

    def test_sentences_vary(self):
        self.build()
        seen = set()
        for _ in range(12):
            self.now += COOLDOWN
            self.run_command("!cheer 1")
        seen = set(self.driver.said)
        self.assertGreater(len(seen), 1)

    def test_bad_seat_gets_the_usage_text(self):
        self.build()
        for text in ("!cheer", "!cheer 3", "!cheer x", "!cheer 1 2"):
            self.run_command(text)
        self.assertEqual([MESSAGES.cheer_usage] * 4, self.driver.said)

    def test_unreadable_seat_is_reported(self):
        self.build(names=("", ""))
        self.run_command("!cheer 1")
        self.assertEqual([MESSAGES.seats_unreadable], self.driver.said)

    def test_cooldown_blocks_a_second_cheer_until_it_is_over(self):
        self.build()
        self.run_command("!cheer 1")
        self.now += COOLDOWN - 1
        self.run_command("!cheer 2")
        self.assertEqual(1, len(self.driver.said))
        self.now += 1
        self.run_command("!cheer 2")
        self.assertEqual(2, len(self.driver.said))


if __name__ == "__main__":
    unittest.main()
