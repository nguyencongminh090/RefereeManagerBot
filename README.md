# Referee Manager Bot

Referee bots for PlayOK Gomoku team/individual tournaments (built for TGWBC). Bots sit at tables, count the games
from the table chat and send results to one server, which stores them in SQLite and keeps the standings.

## Architecture

```
referee bot (client.py, Selenium on PlayOK)  --TCP-->  server.py  -->  SQLite (data/tournament.db)
referee bot ...                              --TCP-->
```

- `server.py`: authenticates clients with a token, validates and stores `MATCH_RESULT` messages (idempotent by game
  id), answers `MATCH_ACK` (with the pair and team-match score) or `ERROR <code>`, handles roster queries, table claims,
  admin score corrections (`SET_SCORE` -> `SCORE_SET`), heartbeats and periodic `.bak` backups.
- `domain/`: pure types (`GameResult`, `Scoring`) and ports (`ITeamRepository`, `MatchRecord`); imports only the stdlib.
- `network/`: wire protocol and messages, socket ports, `TcpServerSocket`, `TcpClientSocket` (auth, reconnect with
  backoff, heartbeat, results re-sent until acknowledged).
- `storage/`: `schema.sql`, `Database`, `TournamentStore`, `SqliteTeamRepository`. `games` is the source of truth.
- `referee/`: the client side. `html_dom.py` and `page_parser.py` (stdlib HTML parsing of chat, seats, lobby),
  `driver.py` (`SeleniumDriver`, port in `driver_port.py`), `browser.py` (Firefox factory), `lobby.py`, `session.py`
  (`MatchSession`), `info_text.py` (which game-count text to write), `commands/` (chat commands).
- `serverapp/`: server-side parts (sessions, claims, router, backup).
- `config/`: `ConfigLoader`, `messages.py` (chat texts per language) and `config.example.toml`.
- `tools/admin_db.py`: admin CLI for the database.

## Configuration

1. Python >= 3.11. `pip install -r requirements.txt` (only selenium, needed by the client; the server and tests do not need it).
2. `cp config/config.example.toml config/config.toml` and edit it (rules, selectors, texts, ports). Unknown or missing keys stop start-up with a message.
3. Create `.env` (git-ignored) with the secrets: `PLAYOK_USER`, `PLAYOK_PASS`, `BOT_TOKEN`.
   Real environment variables override `.env`. Use `--config` or `REFEREE_CONFIG` for another config file.

## Chat commands

The prefix is `[commands] prefix` (default `!`). Commands listed in `[commands] admin_only` work only for the
nicknames in `[tournament] admins` (several are allowed, case is ignored); everyone else is ignored silently.
Admin-only: `leave`, `rules`, `set`, `break`, `sync`.

| Command | Who | What it does |
|---|---|---|
| `!score` | everyone | Writes the standings in the table chat. |
| `!cheer 1` / `!cheer 2` | everyone | Cheers the player in that seat with a random sentence from `cheers`. One per `cheer_cooldown_seconds` per table. |
| `!rules [language]` | admin | Writes the rules reminder: in the tournament language, or in another one (a language name or alias such as `eng`). An unknown language is answered with the configured ones. |
| `!set L-R` | admin | Corrects the pair's score after a bot restart or a lost count: `L` = points of the player in the left seat (#1) now, `R` = right seat (#2). Points are game points, a draw is half (`!set 2.5-1.5`). |
| `!sync total` | admin | Like `!sync`, but takes the all-time record of the two players from the opponents tab (no round start, no time zone). Right only when they never played each other before this match. |
| `!sync [[date] time]` | admin | Typed at a table, it does what `!set L-R` does but finds the score itself: counts the games of the two seated players on PlayOK's games list (`[stats]`) since midnight today (a WBC match is played in one day), or since the time given (`!sync 18:30`, `!sync 2026-10-04 18:30`), or since `tournament.round_start`, and sends the result as `!set` does. Nothing changes when no game, or more than `total_matches`, are found. |
| `!break [minutes]` | admin | Announces a break and the time to come back. Default length `tournament.break_minutes`, at most `commands.break_max_minutes`. |
| `!leave` | admin | The bot says goodbye and leaves the table. |

**`!set` in detail.** The bot reads the seat names when the command arrives and asks the server. The server compares
the pair's counted games with the numbers, then voids the newest games while a player is above the target and records
the fewest corrective games (wins, then draws) that reach it, in the pair's fixture. Everything is one transaction
and every game is audit-logged under the admin's nickname. Setting the same score twice changes nothing. A team
event updates the team score by itself, because team totals are derived from the games. The server refuses (and the
bot says why in the chat) when the sender is not an admin, when more games than `total_matches` would be needed, or
when no mix of wins and draws gives the score.

**`!sync` in detail.** It fetches `stat.phtml?u=<left>&g=gm&sk=2&oid=<right>` (one request, `stats.timeout_seconds`, no retry; a
failure is written in the chat and the admin tries again). Game times on that page are read in `stats.timezone`, `Etc/GMT-1` (UTC+1 all year): checked on 2026-10-04 against the server clock and the game records (Warsaw time, UTC+2 in summer); check again after the clocks change on 2026-10-25. `round_start`
and the command's time are in `tournament.timezone`. Only the newest page of the list is read, so keep `total_matches` below the
page length (about 15 games). A rematch of the same pair in a later round needs a later start. `!sync total` reads `stat.phtml?u=<left>&g=gm&sk=3&sid=<right>`, whose opponents table has the row of `<right>` as `wins-losses-draws` of the left player, and is refused above `total_matches` games.

**After every new result** the bot writes the score: in a team event `Alpha : Beta = 7 : 5` and then the pair line
`alice : bob = 3-2`; in an individual event only the pair line. Then, when it applies, one info text: `final`
when the match is over, `last_game` one game before, `break_hint` one game before a break (`break_after`). The game
count is the server's. A result the server already had (a re-sent one) writes nothing. Results are scored with the
seat names read when the game ended, so a player who changes seats cannot move a win to the other player.

## Chat texts and languages

All texts the bot writes are in `[messages]`, one table per language, all with the same keys (see
`config/config.example.toml`). A missing key, a wrong `{placeholder}`, a cheer without `{name}`, an alias to an unknown
language or a `tournament.language` without a table stops start-up and names the table.

- `tournament.language` selects the language of the bot's own messages (results, breaks, goodbye, cheers, usage).
- `!rules <language>` can write the rules in any configured language. `[messages.aliases]` lists other spellings,
  for example `eng = "en"`, `hun = "hu"`, `cze = "cz"`.
- To add a language: copy `[messages.en]`, rename it (lower case) and translate the values; add its aliases.
- Numbers that depend on the tournament are config keys, not code: `total_matches`, `break_after`, `break_minutes`,
  `commands.break_max_minutes`, `commands.cheer_cooldown_seconds`, `client.max_pending_results`,
  `client.unreadable_polls_before_alert`. The admin sets them per year.

## Security and operations

- **The token travels unencrypted.** `BOT_TOKEN` is a shared secret sent over plain TCP. Run server and bots on a private
  network, or tunnel the port (SSH or a VPN). Do not expose the server port to the internet.
- **Limits** (`[server]` in the config): `max_packet_bytes`, `max_clients`, `send_timeout_seconds`,
  `auth_timeout_seconds` (a connection that does not authenticate in time is closed), `auth_max_failures` and
  `auth_lockout_seconds` (an address with repeated wrong tokens is locked out).
- **Results survive a bot crash.** The client writes each unconfirmed result to `client.outbox_path` (default
  `data/outbox.jsonl`) and re-sends it after reconnecting; the server ignores repeats of the same `match_id`.
  An empty `outbox_path` keeps them in memory only.
- **The server decides when a micro-match is complete** (`MATCH_ACK.complete`); the bot only counts locally if no
  answer ever arrives.
- **Never commit** saved PlayOK pages, rosters or databases (they hold real names and chat). `.gitignore` lists the
  known ones. If a password was ever committed, rotate it: removing the file does not remove it from git history.

## Running

Server:

```
python3 server.py [--config config/config.toml] [--env .env] [--log-level INFO]
```

Run a referee bot (needs `pip install -r requirements.txt`, Firefox with geckodriver, and PLAYOK_USER, PLAYOK_PASS, BOT_TOKEN in `.env`):

```
python3 client.py [--config config/config.toml] [--env .env] [--headless] [--no-chat] [--log-level INFO]
```

`--no-chat` reads the table chat but never writes to it (observer runs); the text the bot would have sent is logged
instead. If `firefox` on your system is a wrapper script (snap installs), geckodriver refuses it ("binary is not a
Firefox executable"): set `FIREFOX_BINARY` to the real binary, for example
`FIREFOX_BINARY=/snap/firefox/current/usr/lib/firefox/firefox python3 client.py --no-chat`.

Admin CLI (run from the repo root):

```
python3 -m tools.admin_db --db data/tournament.db <group> <command> [options]
```

Groups: `init`, `tournament`, `team`, `individual`, `player`, `fixture`, `game`, `standings`, `cross`, `roster`,
`validate`, `import` (roster CSV), `export`, `audit`, `backup`. Use `-h` after any group for its commands
(for example `player -h` lists add, list, edit, rename, captain, role, substitute, deactivate, activate, move, remove).
Every change is recorded in the audit log (`--actor`). `python3 -m tools.seed_demo` fills a database with fake demo data.

## Tests

```
python3 -m unittest discover -s tests -t .
```

Tests that use saved PlayOK pages (`tests/fixtures/playok/`, git-ignored, see the README there) are skipped when those files are absent. Selenium is not needed.

**Fake PlayOK site (`tests/fake_playok/`).** A small local web server that imitates the lobby and table pages (markup shaped
like the saved pages, anonymised content) so that the real `SeleniumDriver`, `Client`, TCP socket, server and SQLite can run
together without a tournament. The tests parse the fake pages with the production `PageParser` and the selectors of
`config.example.toml`, so a selector change fails them too. They need selenium, Firefox and geckodriver and are skipped
otherwise:

```
FIREFOX_BINARY=/snap/firefox/current/usr/lib/firefox/firefox \
  .venv/bin/python -m unittest tests.test_fake_playok tests.test_fake_playok_browser tests.test_fake_playok_match
```

(`FIREFOX_BINARY` is only needed when `firefox` is a snap wrapper; about 70 s for the browser tests.) `test_fake_playok` needs
no browser. Scenarios: driver actions, a whole 12-game micro-match, two bots on two tables, a slow site, a page that rebuilds
its elements on every poll (stale elements), a player leaving mid-match, admin and non-admin `!set`, server restart with outbox
replay, and a second bot trying to take a table across a restart. Test code controls the site through `FakeWorld` (one bot's
view: seat, invitation, `latency_sec`, `churn`) on a shared `FakeBoard` (tables and chat).

The fake is only as accurate as the saved pages and our reading of them. It does not reproduce PlayOK's real timing, hidden
elements, login form, cookie banner or anti-bot checks; the live trial remains the ground truth, and when the live site
differs the fake should be corrected and a test added.

## Layout

```
server.py  client.py
domain/    types.py, ports.py                      (stdlib only)
config/    settings.py, messages.py, reader.py, config.example.toml  (-> domain)
network/   messages.py, ports.py, protocol.py, options.py, outbox.py, server_socket.py, client_socket.py
storage/   schema.sql, database.py, tournament_store.py, sqlite_repository.py, pair_adjust.py, models.py, errors.py, ...  (-> domain)
referee/   html_dom.py, page_parser.py, lobby.py, driver.py, driver_port.py, browser.py, session.py, info_text.py, commands/
serverapp/ claims.py, sessions.py, router.py, backup.py, match_request.py (MATCH_RESULT and SET_SCORE packets)
tools/     admin_db.py, seed_demo.py
tests/     fake_playok/ (fake site for browser tests), test_*.py
data/      *.db is git-ignored
```

## Status

Done: server, storage and tie-breaks, config, client socket, page parser, `MatchSession`, chat commands (`!score`,
`!rules [language]`, `!leave`, `!set`, `!break`, `!cheer`), result lines and game-count texts, per-language chat texts,
`LobbyWatcher` auto-join, `Client` wiring, admin CLI. All of it is covered by unit tests with fakes.

Live trial, 2026-10-04 (one bot, `--no-chat`, individual format, two tables with real players): login, lobby scan,
table claim, joining a full table, reading the table chat, parsing `player #1/#2 wins`, sending results, server
storage and standings all worked; 4 games were recorded and the referee confirmed every result. Findings fixed
afterwards: the `>>` join button is hidden by the page on a full table, so the bot clicks the lobby row instead;
a page error no longer kills the bot (`DriverError`, retried, stops after 30 failed passes in a row).

After the trial, tested against the fake PlayOK site (see Tests): a join that fails on a page error is retried on a later
scan (the table is no longer skipped); start-up (`open_site`, `goto_lobby`) retries 5 times, 3 s apart; the bot picks a
random eligible table and rescans at once after a denied claim; a page error while leaving or while writing the score lines
no longer leaves the bot at the table; after every (re)connection the bot re-sends its table claim (the server forgets the
claims of a closed link), and a new link of the same bot name takes over its table.

Not yet run on the live site (reconnect and outbox re-send after a server restart are only tested against the fake site): invite mode, every `!` command and the result lines with chat enabled (the trial ran with
`--no-chat`), `!set` after a reconnect, `--headless`, the `draw` line pattern, table-rule warnings. Expect to adjust selectors and patterns
in `config.toml` if PlayOK changes its page.

Design notes: `proposal.md` (Vietnamese), `text-processing.md`.
