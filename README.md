# Referee Manager Bot

Referee bots for PlayOK Gomoku team/individual tournaments (built for TGWBC). Bots sit at tables, count the games
from the table chat and send results to one server, which stores them in SQLite and keeps the standings.

## Architecture

```
referee bot (client.py, Selenium on PlayOK)  --TCP-->  server.py  -->  SQLite (data/tournament.db)
referee bot ...                              --TCP-->
```

- `server.py`: authenticates clients with a token, validates and stores `MATCH_RESULT` messages (idempotent by game
  id), answers `MATCH_ACK` or `ERROR <code>`, handles roster queries, table claims, heartbeats and periodic `.bak` backups.
- `domain/`: pure types (`GameResult`, `Scoring`) and ports (`ITeamRepository`, `MatchRecord`); imports only the stdlib.
- `network/`: wire protocol and messages, socket ports, `TcpServerSocket`, `TcpClientSocket` (auth, reconnect with
  backoff, heartbeat, results re-sent until acknowledged).
- `storage/`: `schema.sql`, `Database`, `TournamentStore`, `SqliteTeamRepository`. `games` is the source of truth.
- `referee/`: the client side. `html_dom.py` and `page_parser.py` (stdlib HTML parsing of chat, seats, lobby),
  `driver.py` (`SeleniumDriver`, port in `driver_port.py`), `browser.py` (Firefox factory), `lobby.py`, `session.py`
  (`MatchSession`), `commands/` (chat commands).
- `serverapp/`: server-side parts (sessions, claims, router, backup).
- `config/`: `ConfigLoader` and `config.example.toml`.
- `tools/admin_db.py`: admin CLI for the database.

## Configuration

1. Python >= 3.11. `pip install -r requirements.txt` (only selenium, needed by the client; the server and tests do not need it).
2. `cp config/config.example.toml config/config.toml` and edit it (rules, selectors, texts, ports). Unknown or missing keys stop start-up with a message.
3. Create `.env` (git-ignored) with the secrets: `PLAYOK_USER`, `PLAYOK_PASS`, `BOT_TOKEN`.
   Real environment variables override `.env`. Use `--config` or `REFEREE_CONFIG` for another config file.

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

Tests that use saved PlayOK pages in the repo root are skipped when those files are absent. Selenium is not needed.

## Layout

```
server.py  client.py
domain/    types.py, ports.py                      (stdlib only)
config/    settings.py, config.example.toml        (-> domain)
network/   messages.py, ports.py, protocol.py, options.py, outbox.py, server_socket.py, client_socket.py
storage/   schema.sql, database.py, tournament_store.py, sqlite_repository.py, models.py, errors.py, ...  (-> domain)
referee/   html_dom.py, page_parser.py, lobby.py, driver.py, driver_port.py, browser.py, session.py, commands/
serverapp/ claims.py, sessions.py, router.py, backup.py, match_request.py
tools/     admin_db.py, seed_demo.py
tests/
data/      *.db is git-ignored
```

## Status

Done: server, storage and tie-breaks, config, client socket, page parser, `MatchSession`, chat commands (`!score`,
`!rules`, `!leave`), `LobbyWatcher` auto-join, `Client` wiring, admin CLI. All of it is covered by unit tests with fakes.

Live trial, 2026-10-04 (one bot, `--no-chat`, individual format, two tables with real players): login, lobby scan,
table claim, joining a full table, reading the table chat, parsing `player #1/#2 wins`, sending results, server
storage and standings all worked; 4 games were recorded and the referee confirmed every result. Findings fixed
afterwards: the `>>` join button is hidden by the page on a full table, so the bot clicks the lobby row instead;
a page error no longer kills the bot (`DriverError`, retried, stops after 30 failed passes in a row).

Not yet run on the live site: invite mode, `!` commands with chat enabled, reconnect and outbox re-send after a
server restart, `--headless`, the `#draw` line pattern, table-rule warnings. Expect to adjust selectors and patterns
in `config.toml` if PlayOK changes its page.

Design notes: `proposal.md` (Vietnamese), `text-processing.md`.
