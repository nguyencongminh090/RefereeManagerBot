"""HTML of the fake PlayOK site, shaped like the saved pages so the production selectors match it.

`render_fragment` is the part that changes (lobby, invitation, table panel); `render_page` wraps it
in a shell whose script polls `/version` and swaps the fragment in, like the live page redraws itself.
Clicks carry `data-act` attributes that the script posts to `/_act/<name>`.
"""
from html import escape
from typing import Optional

from tests.fake_playok.world import FakeTable, FakeWorld

ROOMS = ("#100... bieniasze", "#200... chwalecin", "#300... dobrocin", "#400... turznica")
POLL_MS = 200
CHURN_MS = 15

_SCRIPT = """
const root = document.getElementById('root');
let shown = %(version)d;
let churning = false;
async function act(name, fields) {
  await fetch('/_act/' + name, {method: 'POST', body: new URLSearchParams(fields || {})});
}
async function refresh() {
  const info = await (await fetch('/version')).json();
  churning = info.churn;
  if (info.version === shown) return;
  const box = root.querySelector('form input');
  const typed = box ? box.value : '';
  root.innerHTML = await (await fetch('/fragment')).text();
  shown = info.version;
  const fresh = root.querySelector('form input');
  if (fresh) fresh.value = typed;
}
root.addEventListener('click', (event) => {
  const target = event.target.closest('[data-act]');
  if (target) act(target.dataset.act, {table: target.dataset.table || ''});
});
root.addEventListener('submit', (event) => {
  event.preventDefault();
  const box = event.target.querySelector('input');
  act('say', {text: box.value});
  box.value = '';
});
// churn: rebuild every element without any change, so references held by the driver go stale
setInterval(() => {
  if (!churning) return;
  const box = root.querySelector('form input');
  const typed = box ? box.value : '';
  root.innerHTML = root.innerHTML;
  const fresh = root.querySelector('form input');
  if (fresh) fresh.value = typed;
}, %(churn_ms)d);
setInterval(() => refresh().catch(() => {}), %(poll_ms)d);
"""


def render_page(world: FakeWorld) -> str:
    """Returns the whole page, with the current fragment already inside."""
    script = _SCRIPT % {"version": world.version, "poll_ms": POLL_MS, "churn_ms": CHURN_MS}
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Gomoku</title></head><body>"
            f"<div id=\"root\">{render_fragment(world)}</div><script>{script}</script></body></html>")


def render_fragment(world: FakeWorld) -> str:
    """Returns the changing part of the page: header, invitation, lobby chat, lobby list, table."""
    parts = [_header(), _invitation(world), _lobby_chat(), _lobby(world)]
    if world.bot_table is not None:
        parts.append(_table_panel(world, world.bot_table))
    return "".join(parts)


def _header() -> str:
    options = "".join(f"<option>{escape(room)} (0)</option>" for room in ROOMS)
    return f'<div class="selcwr"><select class="selcsl">{options}</select></div>'


def _invitation(world: FakeWorld) -> str:
    invite = world.invitation
    if invite is None:
        return ""
    text = f"{invite.user} [{invite.rating}] invites you to table #{invite.table} ({invite.info}); accept?"
    return ('<div><div class="alrt dcpd">'
            f'<div class="mbsp">{escape(text)}</div>'
            '<button class="minw" data-act="accept">yes</button> '
            '<button class="minw" data-act="decline">no</button></div></div>')


def _lobby_chat() -> str:
    return '<div class="chpan bbsep dcpd"><div class="btlbr"><div class="tind">+ gomoku</div></div></div>'


def _lobby(world: FakeWorld) -> str:
    rows = "".join(_lobby_row(table) for table in world.tables())
    return f'<div class="tlst usno">{rows}</div>'


def _lobby_row(table: FakeTable) -> str:
    free = len([name for name in table.players if name]) < 2
    classes = "awrap bbsep dcpd tavail" if free else "awrap bbsep dcpd"
    players = "".join(f'<div class="tplnorm"><div class="r2"></div>{escape(name)}'
                      '<span class="tplrn snum">1200</span></div>'
                      for name in table.players if name)
    return (f'<a class="{classes}" data-act="join" data-table="{table.number}"><div class="tmaxw">'
            f'<div class="tnum">#{table.number}</div><div class="tpar1">{escape(table.time)}</div>'
            f'<div class="tplbl">{players}</div>'
            '<div class="tjoin"><button class="butbl">&gt;&gt;</button></div></div></a>')


def _table_panel(world: FakeWorld, number: int) -> str:
    table = next(t for t in world.tables() if t.number == number)
    chat = "".join(_chat_line(line) for line in table.chat)
    return ('<div class="bsbb tsb"><div class="tsbinner bsbb">'
            '<div class="ttlcont"><div class="ttlnav">'
            '<button class="butsys butlh">&ndash;</button>'
            '<button class="butsys butlh" data-act="leave">X</button></div>'
            f'<div>table #{table.number} &nbsp; {escape(table.time)}</div></div>'
            f'<div><div class="tplcont">{_seat(1, table)}{_seat(2, table)}</div></div>'
            f'<div class="tcrdpan"><div class="mb1s">{chat}</div>'
            '<form><input class="bsbb" name="somename" type="text" autocomplete="off"></form></div>'
            '</div></div>')


def _seat(seat_no: int, table: FakeTable) -> str:
    name = table.players[seat_no - 1] if len(table.players) >= seat_no else ""
    return (f'<div><div class="f12">#{seat_no}</div><div><button class="butsys butsit">#{seat_no}</button>'
            f'<div><button>X</button><div class="nowrel">{escape(name) or "-"}</div></div></div></div>')


def _chat_line(line: str) -> str:
    return f'<div class="tind">{escape(line)}</div>'
