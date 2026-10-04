"""Turns the games tab of a PlayOK profile (`stat.phtml?...&sk=2`) into `StatGame` objects.

Stdlib only; the input is the page HTML, so it is tested against a saved page without a network.
"""
import re
from datetime import datetime, tzinfo
from typing   import List, Optional

from domain.types       import GameResult
from referee.html_dom   import Node, parse_html
from referee.stats_port import PairTotals, StatGame, StatsError

ROW_SELECTOR = "table.ktb tr"
DATE_FORMAT = "%Y-%m-%d %H:%M"
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")   # the "(1)" duration is dropped
GAME_ID_PATTERN = re.compile(r"[?&]g=(gm\d+)")
RECORD_PATTERN = re.compile(r"(\d+)-(\d+)-(\d+)")                   # wins-losses-draws
RESULT_WORDS = {"win": GameResult.WIN, "loss": GameResult.LOSS, "draw": GameResult.DRAW}
CELLS_PER_TOTAL = 3                                         # user, rank, record
CELLS_PER_GAME = 3                                          # date, players, result (txt is extra)


class StatsParseError(StatsError):
    """The games list has a row this parser does not understand."""


def parse_pair_games(html: str, zone: tzinfo) -> List[StatGame]:
    """Reads every game row of the page, in page order (newest first).

    Args:
        html: The page source.
        zone: Time zone the page writes its dates in.

    Raises:
        StatsParseError: If a row has an unknown result word, no game link or an unreadable date.
    """
    rows = parse_html(html).select(ROW_SELECTOR)
    return [_parse_row(cells, zone) for cells in (r.select("td") for r in rows) if cells]


def parse_pair_totals(html: str, opponent: str) -> Optional[PairTotals]:
    """Reads the record against `opponent` from the opponents tab (`sk=3`).

    Args:
        html: The page source, normally fetched with `sid=<opponent>` so that the row is on it.
        opponent: Nickname to look for; case is ignored.

    Returns:
        The record, or None if the opponent has no row on the page.

    Raises:
        StatsParseError: If the opponent's record is not written as `wins-losses-draws`.
    """
    wanted = opponent.strip().lower()
    for row in parse_html(html).select(ROW_SELECTOR):
        cells = row.select("td")
        if len(cells) < CELLS_PER_TOTAL or cells[0].get_text().strip().lower() != wanted:
            continue
        found = RECORD_PATTERN.fullmatch(cells[2].get_text().strip())
        if found is None:
            raise StatsParseError(f"unreadable record {cells[2].get_text()!r} against {opponent}")
        return PairTotals(*(int(part) for part in found.groups()))
    return None


def _parse_row(cells: List[Node], zone: tzinfo) -> StatGame:
    if len(cells) < CELLS_PER_GAME:
        raise StatsParseError(f"game row with {len(cells)} cells, expected {CELLS_PER_GAME}")
    date_cell, players_cell, result_cell = cells[:CELLS_PER_GAME]
    names = [a.get_text().strip() for a in players_cell.select("a")]
    if len(names) != 2:
        raise StatsParseError(f"game row with {len(names)} player links: {players_cell.get_text()!r}")
    link = result_cell.select_one("a")
    link_match = GAME_ID_PATTERN.search(link.attrs.get("href", "")) if link else None
    if link_match is None:
        raise StatsParseError(f"game row without a game link: {result_cell.get_text()!r}")
    word = result_cell.get_text().strip().lower()
    if word not in RESULT_WORDS:
        raise StatsParseError(f"unknown game result {word!r} in {link_match.group(1)}")
    return StatGame(link_match.group(1), _parse_date(date_cell.get_text(), zone), names[0], names[1],
                    RESULT_WORDS[word])


def _parse_date(text: str, zone: tzinfo) -> datetime:
    found = DATE_PATTERN.search(text.replace("\xa0", " "))
    if found is None:
        raise StatsParseError(f"unreadable game date {text!r}")
    return datetime.strptime(found.group(0), DATE_FORMAT).replace(tzinfo=zone)
