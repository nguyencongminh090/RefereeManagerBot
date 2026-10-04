import os
import unittest
from pathlib import Path

from config.settings import ConfigLoader
from referee.html_dom   import parse_html
from referee.page_parser import (ChatLine, GameOutcome, LobbyTable, PageParser, Seat, SeatTracker, eligible_tables,
                              table_rule_problems)
from domain.types      import GameResult

ROOT    = Path(__file__).resolve().parent.parent
SETTINGS = ConfigLoader.load(str(ROOT / "config" / "config.example.toml"), env_file="/nonexistent", environ={})
PARSER  = PageParser(SETTINGS.playok)

SEAT = ('<div><div class="f12">#{n}</div><div><button class="butsit">#{n}</button>'
        '<div><button>X</button><div class="nowrel">{name}</div></div></div></div>')


def page(seat1="alice", seat2="bob", title="table #116 &nbsp; 1m+1s, sw, x", chat=()):
    lines = "".join(f'<div class="tind">{c}</div>' for c in chat)
    return f"""<html><body>
    <div class="alrt dcpd"><div class="mbsp">zed [1093] invites you to table #104 (1m); accept?</div><button class="minw">yes</button></div>
    <div class="chpan"><div class="btlbr"><div class="tind">+ gomoku</div><div class="tind"><b>lobby</b>: hello</div></div></div>
    <div class="tlst usno">
      <a class="awrap bbsep dcpd"><div class="tmaxw"><div class="tnum">#106</div><div class="tpar1">3m</div>
        <div class="tplbl"><div class="tplnorm"><div class="r2"></div>alice<span class="tplrn snum">1291</span></div>
        <div class="tplnorm"><div class="r1"></div>bob<span class="tplrn snum">1182</span></div></div>
        <div class="tjoin"><button>&gt;&gt;</button></div></div></a>
      <a class="awrap bbsep dcpd tavail"><div class="tmaxw"><div class="tnum">#103</div><div class="tpar1">1m+1s, x</div>
        <div class="tplbl"><div class="tplnorm"><div class="r2"></div>carol<span class="tplrn snum">-</span></div>
        <div class="tplemp"><div class="rnone"></div>&ndash;<span class="tplrn snum">-</span></div></div>
        <div class="tjoin"><button>&gt;&gt;</button></div></div></a>
      <a class="awrap"><div class="tmaxw"><div class="tpar1">no number here</div></div></a>
    </div>
    <div class="imvfrm"><div class="imtx"><div class="tind"><b>dave</b>: private message</div></div></div>
    <div class="bsbb tsb"><div class="bsbb tsbinner">
      <div class="ttlcont"><div class="ttlnav"><button>-</button><button>X</button></div><div>{title}</div></div>
      <div><div class="tplcont">{SEAT.format(n=1, name=seat1)}{SEAT.format(n=2, name=seat2)}</div></div>
      <div class="tcrdpan"><div class="bsbb mb1s">{lines}</div></div>
    </div></div></body></html>"""


class DomTests(unittest.TestCase):
    def setUp(self):
        self.root = parse_html('<div id="a" class="x y"><p class="x">one<b>bold</b></p><p>two</p>'
                               '<span data-k="v">s</span><ul><li>1<li>2<li>3</ul></div><script>var a="<b>";</script>')

    def test_selectors(self):
        r = self.root
        self.assertEqual(["one", "two"], [n.own_text() for n in r.select("div p")])
        self.assertEqual(1, len(r.select("p.x")))
        self.assertEqual(2, len(r.select("div.x.y > p")))
        self.assertEqual(0, len(r.select("body > p")))
        self.assertEqual("two", r.select_one("p:nth-child(2)").own_text())
        self.assertEqual("span", r.select_one("span:nth-of-type(1)").tag)
        self.assertEqual("s", r.select_one('[data-k="v"]').get_text())
        self.assertEqual("s", r.select_one("[data-k]").get_text())
        self.assertEqual("a", r.select_one("#a").attrs["id"])
        self.assertEqual(["1", "2", "3"], [n.get_text() for n in r.select("li")])   # unclosed <li> tolerated
        self.assertEqual("3", r.select_one("li:last-child").get_text())
        self.assertEqual("1", r.select_one("li:first-child").get_text())
        self.assertEqual("bold", r.select_one("p > b").get_text())

    def test_text_helpers_and_scripts(self):
        p = self.root.select_one("p")
        self.assertEqual("onebold", p.get_text())
        self.assertEqual("one", p.own_text())
        self.assertNotIn("var a", self.root.get_text())               # script content is dropped

    def test_bad_selectors(self):
        for css in ("a, b", "p:nth-child", "p:hover", ""):
            with self.assertRaises(ValueError):
                self.root.select(css)


class ChatTests(unittest.TestCase):
    def test_only_the_table_chat_is_read(self):
        dom = PARSER.parse(page(chat=["<b>alice</b>: good luck &amp; have fun", "+ player #1 wins"]))
        lines = PARSER.chat_lines(dom)
        self.assertEqual([ChatLine("alice", "good luck & have fun", False), ChatLine(None, "player #1 wins", True)], lines)
        self.assertEqual([("alice", "good luck & have fun"), ("+", "player #1 wins")], [l.as_tuple() for l in lines])

    def test_line_shapes(self):
        dom = PARSER.parse(page(chat=["<b>x</b>: a: b", "<b>y</b>:", "<b>emo</b>: <span class=\"emo\">😀</span>", "plain: text", "no colon at all"]))
        lines = PARSER.chat_lines(dom)
        self.assertEqual(("x", "a: b"), lines[0].as_tuple())
        self.assertEqual(("y", ""), lines[1].as_tuple())
        self.assertEqual(("emo", "😀"), lines[2].as_tuple())
        self.assertEqual(("plain", "text"), lines[3].as_tuple())
        self.assertEqual(("", "no colon at all"), lines[4].as_tuple())

    def test_result_tracker(self):
        dom = PARSER.parse(page(chat=["<b>alice</b>: player #1 wins", "+ #2 exceeded time for game", "+ player #1 wins",
                                      "+ player #2 wins", "+ #draw", "+ bob [1182] joins", "+ bob leaves", "+ #1 asks to undo the turn"]))
        tracker = PARSER.result_tracker()
        outcomes = [o for o in (tracker.feed(l) for l in PARSER.chat_lines(dom)) if o]
        self.assertEqual([GameOutcome(1, False, 2), GameOutcome(2, False, None), GameOutcome(None, True, None)], outcomes)
        self.assertEqual((GameResult.WIN, GameResult.LOSS), outcomes[0].results())
        self.assertEqual((GameResult.LOSS, GameResult.WIN), outcomes[1].results())
        self.assertEqual((GameResult.DRAW, GameResult.DRAW), outcomes[2].results())

    def test_player_chat_cannot_fake_a_result(self):
        tracker = PARSER.result_tracker()
        self.assertIsNone(tracker.feed(ChatLine("mallory", "player #1 wins", False)))


class TableTests(unittest.TestCase):
    def test_seat_names(self):
        self.assertEqual(("alice", "bob"), PARSER.seat_names(PARSER.parse(page())))
        self.assertEqual((None, None), PARSER.seat_names(PARSER.parse(page("-", "-"))))
        self.assertEqual(("alice", None), PARSER.seat_names(PARSER.parse(page("alice", "-"))))

    def test_seat_tracker_keeps_last_full_reading(self):
        t = SeatTracker()
        self.assertIsNone(t.names_for_result())
        t.update(("a", None))
        self.assertIsNone(t.names_for_result())
        t.update(("a", "b"))
        t.update((None, None))                      # game over: seats emptied
        self.assertEqual(("a", "b"), t.names_for_result())
        t.reset()
        self.assertIsNone(t.names_for_result())

    def test_table_info_and_rules(self):
        info = PARSER.table_info(PARSER.parse(page()))
        self.assertEqual((116, 1, 1, frozenset({"sw", "x"})), (info.number, info.base_minutes, info.increment_seconds, info.flags))
        rules = SETTINGS.tournament.table_rules
        self.assertEqual(["the game is not rated, expected rated"], table_rule_problems(info, rules))
        good = PARSER.table_info(PARSER.parse(page(title="table #7 &nbsp; 1m+1s, sw")))
        self.assertEqual([], table_rule_problems(good, rules))
        bad = PARSER.table_info(PARSER.parse(page(title="table #8 &nbsp; 3m")))
        self.assertEqual(2, len(table_rule_problems(bad, rules)))           # wrong time and no swap2
        self.assertEqual(0, PARSER.table_info(PARSER.parse(page(title="table #9 &nbsp; 5m"))).increment_seconds)

    def test_unreadable_title_gives_none(self):
        self.assertIsNone(PARSER.table_info(PARSER.parse(page(title="something else"))))
        self.assertIsNone(PARSER.table_info(PARSER.parse("<html></html>")))


class LobbyTests(unittest.TestCase):
    def test_invitation(self):
        inv = PARSER.invitation(PARSER.parse(page()))
        self.assertEqual(("zed", 1093, 104, "1m"), (inv.user, inv.elo, inv.table, inv.info))
        self.assertIsNone(PARSER.invitation(PARSER.parse("<html></html>")))

    def test_lobby_tables(self):
        tables = PARSER.lobby_tables(PARSER.parse(page()))
        self.assertEqual([106, 103], [t.number for t in tables])          # the row without a number is skipped
        self.assertEqual((Seat("alice", 1291), Seat("bob", 1182)), tables[0].seats)
        self.assertEqual("3m", tables[0].time_control)
        self.assertFalse(tables[0].joinable)
        self.assertEqual((Seat("carol", None),), tables[1].seats)           # one seat free, rating shown as '-'
        self.assertTrue(tables[1].joinable)

    def test_eligible_tables(self):
        tables = PARSER.lobby_tables(PARSER.parse(page()))
        self.assertEqual([106], [t.number for t in eligible_tables(tables, {"ALICE": "Red", "bob": "Blue", "carol": "Red"})])
        self.assertEqual([], eligible_tables(tables, {"alice": "Red", "bob": "Red"}))        # same team
        self.assertEqual([], eligible_tables(tables, {"alice": "Red"}))                       # bob unknown
        self.assertEqual([], eligible_tables(tables, {"carol": "Red", "alice": "Blue", "bob": "Blue"}))   # carol's table has one seat


@unittest.skipUnless((ROOT / "Gomoku2.txt").exists() and (ROOT / "web.txt").exists() and (ROOT / "Gomoku.html").exists(),
                     "saved PlayOK pages not present")
class SavedPageTests(unittest.TestCase):
    """Structure only (counts and shapes), so no personal data is asserted. The pages are not in git."""

    @staticmethod
    def load(name):
        return PARSER.parse((ROOT / name).read_text(encoding="utf-8"))

    def test_gomoku2_chat_and_results(self):
        dom = self.load("Gomoku2.txt")
        lines = PARSER.chat_lines(dom)
        self.assertEqual(227, len(lines))                  # 274 `.tind` in all: 46 are private messages, 1 lobby
        tracker = PARSER.result_tracker()
        outcomes = [o for o in (tracker.feed(l) for l in lines) if o]
        self.assertEqual(18, len(outcomes))
        self.assertEqual(7, len([o for o in outcomes if o.timed_out_seat]))
        info = PARSER.table_info(dom)
        self.assertEqual((116, 1, 1, frozenset({"sw", "x"})), (info.number, info.base_minutes, info.increment_seconds, info.flags))
        self.assertEqual((None, None), PARSER.seat_names(dom))          # saved after the games ended

    def test_web_page_is_a_live_game(self):
        dom = self.load("web.txt")
        self.assertEqual(2, len(PARSER.chat_lines(dom)))                 # 57 `.tind` in all: 54 private messages, 1 lobby
        first, second = PARSER.seat_names(dom)
        self.assertTrue(first and second and first != second)
        self.assertEqual((106, 3), (PARSER.table_info(dom).number, PARSER.table_info(dom).base_minutes))

    def test_private_messages_and_lobby_chat_are_never_table_chat(self):
        for name, private in (("Gomoku2.txt", 46), ("web.txt", 54), ("lobby.txt", 54)):
            if not (ROOT / name).exists():
                continue
            dom = self.load(name)
            every = len(dom.select(".tind"))
            self.assertEqual(private, len(dom.select(".imtx .tind")))
            self.assertEqual(every, len(PARSER.chat_lines(dom)) + private + len(dom.select(".chpan .tind")))

    def test_gomoku_html_lobby_and_invitation(self):
        dom = self.load("Gomoku.html")
        self.assertEqual(9, len(PARSER.chat_lines(dom)))                 # 10 `.tind` in all: 1 is the lobby line
        tables = PARSER.lobby_tables(dom)
        self.assertGreater(len(tables), 10)
        self.assertTrue(all(len(t.seats) <= 2 and t.time_control for t in tables))
        self.assertTrue(any(len(t.seats) == 2 for t in tables))
        inv = PARSER.invitation(dom)
        self.assertEqual((104, 1093), (inv.table, inv.elo))


if __name__ == "__main__":
    unittest.main()
