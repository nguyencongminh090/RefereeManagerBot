# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Referee bots for PlayOK Gomoku tournaments (TGWBC 2026; rules in `TGWBC2026-ENG.pdf`). Selenium bots sit at tables, count games
from table chat and send results over TCP to one server that stores them in SQLite. The code is complete with tests but has
never been run live. See `README.md` for configuration, operations and the admin CLI.

## Commands

```
python3 -m unittest discover -s tests -t .                        # all tests (no selenium needed)
python3 -m unittest tests.test_server                             # one module
python3 -m unittest tests.test_server.SomeClass.test_name         # one test
python3 server.py [--config config/config.toml] [--env .env]      # server
python3 client.py [--headless]                                    # referee bot (needs selenium, Firefox + geckodriver)
python3 -m tools.admin_db --db data/tournament.db <group> -h      # admin CLI (run from repo root)
```

Config: copy `config/config.example.toml` to `config/config.toml`; secrets (`PLAYOK_USER`, `PLAYOK_PASS`, `BOT_TOKEN`) go in
`.env`. Unknown or missing config keys abort start-up. Python >= 3.11 (tomllib); use the workspace `.venv`.

## Architecture

Flow: `client.py` (bot) --TCP--> `server.py` --> SQLite. Packages and the layering between them are enforced by
`tests/test_architecture.py` (allowed-import table, max 500 lines per module, no `utils`/`helpers`/`common` module names).
Update `ALLOWED_IMPORTS` there if you add a package or a dependency edge.

- `domain/`: pure types and ports (`ITeamRepository`); stdlib only, imports nothing internal.
- `network/`: wire protocol, messages, socket ports and TCP implementations. Client socket handles auth, reconnect with
  backoff, heartbeat and re-sends results from a JSONL outbox until `MATCH_ACK`.
- `storage/`: SQLite schema/migrations, `TournamentStore`, `SqliteTeamRepository`. The `games` table is the source of truth;
  standings are derived. Idempotent by game id.
- `serverapp/`: server-side sessions, table claims, message router, backup.
- `referee/`: client side. Page parsing (`html_dom.py`, `page_parser.py`) is stdlib-only and testable against saved PlayOK
  pages; Selenium is isolated behind `driver_port.py`/`driver.py`. `session.py` runs a match, `commands/` handles chat commands.
- `tools/`: admin CLI over `storage` (every change audit-logged).

Dependencies are injected through ports so server and bot logic can be tested with fakes instead of sockets/browsers.
The server decides when a micro-match is complete (`MATCH_ACK.complete`).

## Skills and knowledge (use them when deciding)

- **Knowledge, before design decisions:** `.claude/knowledge/` (`software-architecture/`, `be-realtime.md`, `secrets-management.md`,
  `appsec-logging-monitoring.md`). Before choosing a structure, protocol/reconnect/delivery behaviour, error strategy, storage
  consistency, logging or secret handling, read the matching note (for example `cross-error-handling-strategy.md`,
  `data-consistency-models.md`, `principle-layering-dependency-rule.md`, `process-decision-making.md`, `evolve-adr.md`) and
  state which one informed the choice. Search by file name; do not read the whole folder.
- **Reason with the knowledge, do not just cite it:** when weighing options, apply the note's principles, trade-offs and
  checklists to this project's concrete facts (two-process TCP bot, SQLite source of truth, unreliable PlayOK page). Compare
  the options against the note's criteria, and give a recommendation with the reason. If the code or a note disagrees with
  your first instinct, say so and follow the note unless a project rule overrides it. If no note applies, say that too.
- **Skills, by task** (`.claude/skills/`, index in `.claude/skills/README.md`): invoke the matching skill before starting,
  not after. `tdd-test-first` for new logic and bugfixes, `bug-hunter` for tracing a bug, `find-bugs` for reviewing branch
  changes, `writing-plans` for multi-step work, `hexagonal-*` / `solid-*` / `oop-encapsulation` when adding or reviewing
  ports, packages or class boundaries.
- **Missing something:** search the wider library with
  `/run/media/ngmint/Data/Programming/Programming/SKILLS/SKILLS_TREE/tools/find "<need>"` before writing a workflow or
  checklist from scratch. Skills dropped from `.claude/skills/` (and why) are listed in its README.
- Project rules and the existing code win over generic advice in a skill or note.

## Project conventions

- `.claude/rules/code-writing.md` and `coding-style.md` apply: functions ~40 lines, at most 3 parameters, depth <= 3, Google-style
  docstrings on public items, no magic values. Bugfixes get a regression test first.
- No column alignment while writing code; only run the alignment pass when the user types `/align`.
- Never commit saved PlayOK pages, rosters, databases or `.env` (real names, chat, secrets).
