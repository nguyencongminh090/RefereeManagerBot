import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from domain.types import GameResult
from referee.stats_parser import StatsParseError, parse_pair_games, parse_pair_totals

WARSAW = ZoneInfo("Europe/Warsaw")

# Same structure as the games tab of a PlayOK profile (sk=2); invented names.
PAGE = """<html><body><table cellspacing="2" cellpadding="2" border="0" class="ktb">
<tr class="kbl"><th class="glcoldt">date (duration)</th><th>players</th><th class="glcolres" colspan="2">result</th></tr>
<tr><td>2026-10-04 18:48&nbsp;(1)</td><td valign="top">
<a href="/en/stat.phtml?u=bob&amp;g=gm">bob</a> - <a href="/en/stat.phtml?u=alice&amp;g=gm">alice</a></td>
<td valign="top"><a target="_blank" href="/p/?g=gm172668589"><b>win</b></a></td><td valign="top"><span class="gr"><a href="/p/?g=gm172668589.txt">txt</a></span></td></tr>
<tr><td>2026-10-04 18:46&nbsp;(12)</td><td valign="top">
<a href="/en/stat.phtml?u=alice&amp;g=gm">alice</a> - <a href="/en/stat.phtml?u=bob&amp;g=gm">bob</a></td>
<td valign="top"><a target="_blank" href="/p/?g=gm172668571"><b>loss</b></a></td><td valign="top"><span class="gr"></span></td></tr>
<tr><td>2026-10-04 18:40&nbsp;(3)</td><td valign="top">
<a href="/en/stat.phtml?u=bob&amp;g=gm">bob</a> - <a href="/en/stat.phtml?u=alice&amp;g=gm">alice</a></td>
<td valign="top"><a target="_blank" href="/p/?g=gm172668500"><b>draw</b></a></td><td></td></tr>
</table><h3><a href="/en/stat.phtml?u=alice&amp;g=gm&amp;sk=2&amp;page=2">next page&nbsp;&gt;</a></h3></body></html>"""


class ParsePairGamesTests(unittest.TestCase):
    def test_reads_id_time_players_and_result_of_every_row(self):
        games = parse_pair_games(PAGE, WARSAW)
        self.assertEqual(["gm172668589", "gm172668571", "gm172668500"], [g.game_id for g in games])
        first = games[0]
        self.assertEqual(datetime(2026, 10, 4, 18, 48, tzinfo=WARSAW), first.played_at)
        self.assertEqual(("bob", "alice"), (first.black, first.white))
        self.assertEqual([GameResult.WIN, GameResult.LOSS, GameResult.DRAW], [g.result for g in games])

    def test_times_are_read_in_the_given_zone(self):
        [first, *_] = parse_pair_games(PAGE, timezone.utc)
        self.assertEqual(datetime(2026, 10, 4, 18, 48, tzinfo=timezone.utc), first.played_at)

    def test_a_page_without_games_gives_an_empty_list(self):
        self.assertEqual([], parse_pair_games("<html><body><p>no games</p></body></html>", WARSAW))

    def test_an_unknown_result_word_is_an_error_not_a_guess(self):
        page = PAGE.replace("<b>draw</b>", "<b>abandoned</b>")
        with self.assertRaises(StatsParseError) as ctx:
            parse_pair_games(page, WARSAW)
        self.assertIn("abandoned", str(ctx.exception))

    def test_a_row_with_an_unreadable_date_is_an_error(self):
        with self.assertRaises(StatsParseError):
            parse_pair_games(PAGE.replace("2026-10-04 18:40", "yesterday"), WARSAW)


# Same structure as the opponents tab (sk=3); the searched opponent's row is highlighted.
TOTALS_PAGE = """<html><body><table class="ktb"><tr class="kbl"><th>user</th><th>rank.</th><th>wn-ls-dr</th>
<th>opponent<br />wn-ls-dr (ab)</th><th>idle</th></tr>
<tr><td><a href="/en/stat.phtml?u=carol&amp;g=gm">carol</a></td><td>1863&nbsp;</td>
<td nowrap="nowrap"><a href="/en/stat.phtml?u=alice&amp;g=gm&amp;sk=2&amp;oid=carol">411-348-1</a></td>
<td nowrap="nowrap">14194-7711-40 (0%)</td><td>02h</td></tr>
<tr style="background: rgba(128,128,128,.15);"><td><a href="/en/stat.phtml?u=Bob&amp;g=gm">Bob</a></td><td>1765&nbsp;</td>
<td nowrap="nowrap"><a href="/en/stat.phtml?u=alice&amp;g=gm&amp;sk=2&amp;oid=Bob">17-9-2</a></td>
<td nowrap="nowrap">6471-12163-52 (0%)</td><td>00h</td></tr></table></body></html>"""


class ParsePairTotalsTests(unittest.TestCase):
    def test_reads_wins_losses_draws_of_the_named_opponent_ignoring_case(self):
        totals = parse_pair_totals(TOTALS_PAGE, "bob")
        self.assertEqual((17, 9, 2), (totals.wins, totals.losses, totals.draws))

    def test_an_opponent_who_is_not_listed_gives_none(self):
        self.assertIsNone(parse_pair_totals(TOTALS_PAGE, "dave"))

    def test_an_unreadable_record_is_an_error(self):
        with self.assertRaises(StatsParseError):
            parse_pair_totals(TOTALS_PAGE.replace("17-9-2", "17-9"), "bob")


if __name__ == "__main__":
    unittest.main()
