# Web Dashboard for the Server Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** The server serves a read-only web page for the organizer: live standings, recent games, referee bots with the tables they hold, open problems from `validate`, and the latest audit entries.

**Architecture:** A new `webui/` package runs a stdlib `ThreadingHTTPServer` inside the server process, started and stopped by `Server`. It reads through `TournamentStore` (the games table stays the single source of truth; nothing is cached or copied) and through a new domain port `IBotStatusSource` for live bot and table state, which `serverapp` implements from `SessionRegistry` and `ClaimRegistry`. One JSON endpoint (`/api/state`) feeds one static HTML page that polls it. No write path exists, so the dashboard cannot corrupt results.

**Tech Stack:** Python 3.11+ stdlib only (`http.server`, `json`), SQLite, unittest (`python3 -m unittest discover -s tests -t .`).

**Decisions (from the user, 2026-10-05):**
- Web dashboard, part of the server process.

**Status (2026-10-05):** Tasks 1 to 7 done, then two follow-ups on request, both done. 494 tests pass.
- Edit actions (opt-in `[dashboard] allow_edit`): `webui/actions.py` and `POST /api/action`. Void or restore a game, set a pair's score (like `!set`), record or remove a sudden-death decider. Actor `dashboard`, CSRF header plus `Origin` check, confirmation on the page, standings re-broadcast to the bots through `ActionOptions.on_changed`.
- Busy-port handling: `StartupError` in `server.py`, exit code 3, for the bot port and the dashboard port. `DashboardServer.stop()` no longer fails when the thread never started.
- Not done: per-person names in the audit log (every edit shows actor `dashboard`); fixtures and cross-table views.

**Defaults taken (change before building if wrong):**
- View-only in v1. Edit actions came as a follow-up (see Status).
- stdlib only, no Flask: the page needs one JSON route and one HTML route.
- Off by default (`[dashboard] enabled = false`), binds `127.0.0.1`. When enabled, a `DASHBOARD_TOKEN` secret is required (sent as `?token=` once, then kept in a cookie), because the page shows real nicknames. The token is compared with `hmac.compare_digest`.
- The page shows nicknames and teams only, never chat text.

**Knowledge used:** `principle-layering-dependency-rule` (webui depends inward on `domain`, `config`, `storage` only; the server-only live state crosses a domain port), `data-consistency-models` (derived standings, no second copy), `secrets-management` (token from `.env`, constant-time compare, off by default), `appsec-logging-monitoring` (no secrets in logs; request log at DEBUG, rejected tokens at WARNING).

**Rejected:** reading the SQLite file from a separate process (no live bot or table state, and a second writer-capable connection); a desktop GUI (only works on the server's machine, harder to test).

---

## Files at a glance

| Path | Action | Responsibility |
|---|---|---|
| `domain/ports.py` | modify | `BotStatus` dataclass, `IBotStatusSource` port |
| `serverapp/claims.py` | modify | `tables_of(addr)` |
| `serverapp/sessions.py` | modify | `snapshot()` of authenticated sessions |
| `serverapp/bot_status.py` | create | `BotStatusProvider(IBotStatusSource)` |
| `config/settings.py`, `config/config.example.toml` | modify | `[dashboard]` table, optional `DASHBOARD_TOKEN` secret |
| `webui/__init__.py` | create | empty |
| `webui/state.py` | create | `DashboardState`: builds the JSON-ready dict (pure, no HTTP) |
| `webui/page.py` | create | `PAGE_HTML` constant (static page, polls `/api/state`) |
| `webui/http_server.py` | create | `DashboardServer`: routes, token check, start/stop |
| `server.py` | modify | build and start/stop the dashboard |
| `tests/test_architecture.py` | modify | add `"webui": {"domain", "config", "storage"}`, add `webui` to `server` |
| `tests/test_bot_status.py`, `tests/test_dashboard_state.py`, `tests/test_dashboard_http.py`, `tests/test_dashboard_config.py` | create | tests |
| `README.md`, `CLAUDE.md` | modify | document, add package to the architecture list |

Each module stays well under 300 lines. Run the full suite after every task: `python3 -m unittest discover -s tests -t .` (currently green; the architecture test is the guard for layering).

---

### Task 1: Domain port for live bot state

**Files:**
- Modify: `domain/ports.py`
- Test: `tests/test_bot_status.py` (created in Task 2; this task is covered there)

**Step 1: Write the failing test** (in `tests/test_bot_status.py`)

```python
from domain.ports import BotStatus

class BotStatusTypeTests(unittest.TestCase):
    def test_bot_status_is_immutable_and_lists_tables_sorted(self):
        status = BotStatus("bot-1", "10.0.0.5:5000", (3, 1), 2.5)
        self.assertEqual("bot-1", status.name)
        with self.assertRaises(Exception):
            status.name = "x"
```

**Step 2:** `python3 -m unittest tests.test_bot_status` → FAIL (`ImportError: BotStatus`).

**Step 3: Implement** in `domain/ports.py`:

```python
@dataclass(frozen=True)
class BotStatus:
    """A connected referee bot as the organizer sees it.

    Attributes:
        name: Bot name from its AUTH packet.
        address: "host:port" of the connection.
        tables: Table numbers the bot holds, ascending.
        idle_seconds: Seconds since the last packet from the bot.
    """
    name        : str
    address     : str
    tables      : Tuple[int, ...]
    idle_seconds: float


class IBotStatusSource(ABC):
    """Read-only view of the referee bots currently connected."""

    @abstractmethod
    def bots(self) -> List[BotStatus]:
        """Returns the authenticated bots, ordered by name."""
```

(Use the file's existing imports; add `Tuple`/`List` if missing.)

**Step 4:** rerun → PASS. **Step 5:** `git add domain/ports.py tests/test_bot_status.py && git commit -m "feat: add IBotStatusSource port"`

---

### Task 2: Server-side bot status provider

**Files:**
- Modify: `serverapp/claims.py` (add `tables_of`), `serverapp/sessions.py` (add `snapshot`)
- Create: `serverapp/bot_status.py`
- Test: `tests/test_bot_status.py`

Contract: `SessionRegistry.snapshot() -> List[Tuple[Addr, str, float]]` returns (addr, bot name, idle seconds) for authenticated sessions only, taken under the lock. `ClaimRegistry.tables_of(addr) -> Tuple[int, ...]` ascending. `BotStatusProvider(sessions, claims)` joins them.

**Step 1: Write the failing tests**

```python
class FakeClock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now

class BotStatusProviderTests(unittest.TestCase):
    def setUp(self):
        self.clock    = FakeClock()
        self.sessions = SessionRegistry(self.clock, 45, 5)
        self.claims   = ClaimRegistry()
        self.provider = BotStatusProvider(self.sessions, self.claims)

    def test_lists_only_authenticated_bots_with_their_tables(self):
        a, b = ("1.1.1.1", 1), ("2.2.2.2", 2)
        for addr in (a, b):
            self.sessions.connect(addr)
        self.sessions.authenticate(a, "bot-a")          # b never authenticates
        self.claims.claim(7, a); self.claims.claim(2, a)
        self.clock.now = 4.0
        self.assertEqual([BotStatus("bot-a", "1.1.1.1:1", (2, 7), 4.0)], self.provider.bots())

    def test_empty_when_nobody_is_connected(self):
        self.assertEqual([], self.provider.bots())

    def test_table_released_on_disconnect_disappears(self):
        a = ("1.1.1.1", 1)
        self.sessions.connect(a); self.sessions.authenticate(a, "bot-a"); self.claims.claim(1, a)
        self.claims.release_all_for(a)
        self.assertEqual((), self.provider.bots()[0].tables)
```

**Step 2:** run → FAIL (`ImportError: serverapp.bot_status`).

**Step 3: Implement**

`serverapp/claims.py`:
```python
    def tables_of(self, addr: Addr) -> Tuple[int, ...]:
        """Returns the tables held by a connection, ascending."""
        with self._lock:
            return tuple(sorted(t for t, owner in self._owners.items() if owner == addr))
```
`serverapp/sessions.py` (in `SessionRegistry`):
```python
    def snapshot(self) -> List[Tuple[Addr, str, float]]:
        """Lists (address, bot name, idle seconds) of the authenticated connections."""
        now = self._clock()
        with self._lock:
            return [(a, s.name, now - s.last_seen)
                    for a, s in self._sessions.items() if s.name is not None]
```
`serverapp/bot_status.py`:
```python
"""Live bot and table state for the dashboard, built from the server's registries."""
from typing import List

from domain.ports        import BotStatus, IBotStatusSource
from serverapp.claims    import ClaimRegistry
from serverapp.sessions  import SessionRegistry


class BotStatusProvider(IBotStatusSource):
    """Joins the session and claim registries into BotStatus rows."""

    def __init__(self, sessions: SessionRegistry, claims: ClaimRegistry) -> None:
        self._sessions = sessions
        self._claims   = claims

    def bots(self) -> List[BotStatus]:
        """Returns the authenticated bots, ordered by name."""
        rows = [BotStatus(name, f"{addr[0]}:{addr[1]}", self._claims.tables_of(addr), idle)
                for addr, name, idle in self._sessions.snapshot()]
        return sorted(rows, key=lambda bot: bot.name)
```

**Step 4:** run suite → PASS. **Step 5:** commit `feat: bot status provider for the dashboard`.

---

### Task 3: Dashboard config

**Files:**
- Modify: `config/settings.py` (new `DashboardConfig`, `[dashboard]` parsing, optional secret), `config/config.example.toml`
- Test: `tests/test_dashboard_config.py`

First read `config/settings.py:45` (`SECRET_KEYS`) and `:340-420` (`_build`, how `Section` validates and how secrets are read) and mirror them. Do not invent a second parsing style.

New keys:
```toml
[dashboard]
enabled         = false        # serve the organizer web page from the server process
host            = "127.0.0.1"  # 0.0.0.0 only behind a trusted network; the page lists nicknames
port            = 8080
refresh_seconds = 5            # how often the page polls the server
recent_games    = 20           # rows in "recent games"
audit_entries   = 20           # rows in "latest changes"
```
Secret `DASHBOARD_TOKEN` in `.env`: required only when `enabled = true` (a missing token then aborts start-up with a `ConfigError`, as for `BOT_TOKEN`). Unknown or missing keys already abort, so add the table to `config/config.example.toml` and to every test config that is built by hand (grep `\[server\]` in `tests/`).

**Step 1: Failing tests:** (a) the example config loads and `settings.dashboard.enabled is False`; (b) `enabled = true` without `DASHBOARD_TOKEN` raises `ConfigError` naming the token; (c) `port = 0` or `refresh_seconds = 0` raises `ConfigError`; (d) an unknown key in `[dashboard]` raises `ConfigError`.
**Step 2:** run → FAIL. **Step 3:** implement `DashboardConfig(enabled, host, port, refresh_seconds, recent_games, audit_entries)` (frozen dataclass, Google docstring with `Attributes:`), add `dashboard` to `Settings` and the token to `Secrets`. **Step 4:** whole suite PASS (fix hand-built configs that now miss the table). **Step 5:** commit `feat: [dashboard] config table`.

---

### Task 4: Dashboard state (pure, no HTTP)

**Files:**
- Create: `webui/__init__.py` (empty), `webui/state.py`
- Modify: `tests/test_architecture.py` (`"webui": {"domain", "config", "storage"}`, and add `"webui"` to the `server` set)
- Test: `tests/test_dashboard_state.py`

Contract: `DashboardState(store, tournament_id, rules, bots_source, options).snapshot() -> dict` with keys `tournament`, `generated_at` (ISO UTC), `standings`, `recent_games`, `bots`, `problems`, `audit`. Everything JSON-serialisable. Reads only; a failing sub-query is not swallowed (the HTTP layer returns 500 and logs it).

Group parameters to stay at three: `DashboardOptions(scoring, ranking, recent_games, audit_entries)` as a frozen dataclass; constructor `(store, tournament_id, bots_source, options)`.

**Step 1: Failing tests**, using a real in-memory `TournamentStore` seeded like `tests/test_server.py` (`ROSTER`, two teams, one recorded game) and a fake `IBotStatusSource`:

```python
def test_standings_rows_follow_the_store_order(self): ...
def test_recent_games_are_newest_first_and_capped(self): ...      # record 3 games, recent_games=2
def test_bots_come_from_the_status_source(self): ...              # name, address, tables, idle_seconds
def test_problems_list_comes_from_validate(self): ...             # empty roster team -> a problem string
def test_snapshot_is_json_serialisable(self): json.dumps(state.snapshot())
def test_empty_tournament_gives_empty_lists_not_errors(self): ...
```
**Step 2:** run → FAIL. **Step 3:** implement. Map `StandingRow` fields `rank, tied, name, country, games, wins, draws, losses, points, match_points`; map each `list_games` dict to `{id, time, table_no, bot, p1, p2, result}` (open `storage/games.py:161` and `storage/schema.sql` first to confirm column names; adapt the mapping, not the tests' intent); use `store.validate`, `store.audit_log(limit)`. One small private method per section keeps each under 40 lines. **Step 4:** suite PASS including the architecture test. **Step 5:** commit `feat: dashboard state snapshot`.

---

### Task 5: HTTP server and page

**Files:**
- Create: `webui/page.py`, `webui/http_server.py`
- Test: `tests/test_dashboard_http.py`

Contract: `DashboardServer(config, token, state)`; `start()` binds and serves on a daemon thread, `stop()` shuts down and joins, `port` property (supports `port = 0` in tests). Routes:
- `GET /` returns `PAGE_HTML`.
- `GET /api/state` returns `state.snapshot()` as JSON.
- Anything else: 404. Non-GET: 405.
- Auth: token from the `?token=` query or the `dashboard_token` cookie, compared with `hmac.compare_digest`; missing or wrong → 401 and a WARNING log without the submitted value. A correct `?token=` on `/` answers with `Set-Cookie: dashboard_token=...; HttpOnly; SameSite=Strict`, then the page fetches `/api/state` with the cookie.
- Headers: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'`.
- Handler exceptions: log with `logger.exception`, answer 500 with a generic body.

The page (`PAGE_HTML`): one file, inline CSS and JS, polls every `refresh_seconds` (injected into the JSON as `refresh_seconds`, so the page needs no templating), builds the tables with `textContent` only (never `innerHTML`, since nicknames are user-controlled). Sections: Standings, Tables and bots (a bot idle over 2x heartbeat shown dimmed), Recent games, Problems (hidden when empty), Latest changes. Follow `artifact-design` guidance for a clean, light/dark-aware layout if the Artifact skill is loaded; otherwise plain system fonts and `prefers-color-scheme`.

**Step 1: Failing tests** (start on port 0 with a fake `state`, use `urllib.request`):
```python
def test_api_state_returns_json_with_valid_token(self): ...
def test_missing_or_wrong_token_is_401_and_body_has_no_data(self): ...
def test_token_in_query_sets_httponly_cookie_then_cookie_alone_works(self): ...
def test_index_serves_html(self): ...
def test_unknown_path_is_404_and_post_is_405(self): ...
def test_state_exception_is_500_without_traceback_in_body(self): ...
def test_security_headers_present(self): ...
def test_stop_releases_the_port(self): ...
```
**Step 2:** run → FAIL. **Step 3:** implement with `http.server.ThreadingHTTPServer` and a handler factory that closes over config/token/state (no module globals). Silence `log_message` to DEBUG. **Step 4:** PASS. **Step 5:** commit `feat: dashboard HTTP server and page`.

---

### Task 6: Wire into `Server`

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py` (add a class), or a new `tests/test_server_dashboard.py` if `test_server.py` is near 500 lines

In `Server.__init__`, after the registries exist: if `settings.dashboard.enabled`, build `BotStatusProvider(self._sessions, self._claims)`, `DashboardState(...)`, `DashboardServer(...)`; else `self._dashboard = None`. `start()` starts it after the socket; `stop()` stops it before the database closes. Keep `__init__` growth in a private `_build_dashboard()` method.

**Step 1: Failing tests:** (a) config with `enabled = true`, `port = 0`: after `server.start(block=False)`, `GET /api/state?token=...` shows the standings and, after a fake bot authenticates and claims table 3, the bot with `tables == [3]`; (b) a recorded game appears in `recent_games`; (c) with `enabled = false` no extra port is opened (`server.dashboard_port is None`); (d) `stop()` shuts both down. Expose `dashboard_port: Optional[int]` as a property for tests.
**Step 2:** FAIL. **Step 3:** implement. **Step 4:** suite PASS. **Step 5:** commit `feat: serve the dashboard from the server`.

---

### Task 7: Documentation and live check

**Files:** `README.md` (Configuration + Operations: enable, token, bind address, what it shows, "view-only"), `CLAUDE.md` (add `webui/` to the Architecture list and the architecture-test note), `.env` example if the repo has one (`DASHBOARD_TOKEN=`).

**Step 1:** write the docs. **Step 2:** run the app: set `enabled = true`, `DASHBOARD_TOKEN` in `.env`, `python3 server.py`, open `http://127.0.0.1:8080/?token=...`, run `python3 -m tools.admin_db seed_demo` data or a fake bot to see all sections fill. Report honestly what was checked in a real browser and what only by tests. **Step 3:** full suite `python3 -m unittest discover -s tests -t .` (expect all green, plus the new tests). **Step 4:** commit `docs: web dashboard`.

Do not run `/align`; the user triggers it.

---

## Later (not in this plan)

- Edit actions: void or correct a game, record sudden death, import roster. Each needs a CSRF-safe POST, the token, and a call to the existing `TournamentStore` method with actor `dashboard`.
- Fixtures view and a cross-table page.
- Server-sent events instead of polling, if 5-second polling is not live enough.
- HTTPS or a reverse proxy, if the page is exposed beyond a trusted network.

## Unverified until built

- Exact column names of `list_games` rows (Task 4 confirms them against `storage/games.py` and `schema.sql`).
- How `Secrets` and `SECRET_KEYS` handle an optional key (Task 3 reads `config/settings.py` first).
- Browser rendering of the page.
