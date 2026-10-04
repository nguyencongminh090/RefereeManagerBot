# Chat Commands and Score Display Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** After every game the bot writes the result in the table chat; `!set`, `!break` (admin) and `!cheer N` (everyone) work; seats are double-checked so a player who changes seat is scored correctly.

**Architecture:** The games table stays the single source of truth ([[data-consistency-models]]: no second copy of a score). `!set L-R` is sent to the server as a new `SET_SCORE` request; the server compares the pair's recorded game points with `L-R` and records corrective games (same fixture, audit-logged). `MATCH_ACK` is extended with the team-match score so the session can print the result line. Chat texts live in `[messages]` of the config; the session stays testable with fakes (`IDriver`, `IClientSocket`).

**Tech Stack:** Python 3.11+, SQLite, unittest (`python3 -m unittest discover -s tests -t .`).

**Decisions (from the user, 2026-10-04):**
- `!rules` stays admin-only.
- `!set L-R`: `L` = score of the player in the left seat (#1) now, `R` = score of the right seat (#2) now. Totals of the pair (micro-match). Numbers are game points: win 1, draw 0.5 (`!set 2.5-1.5` is valid). Used after a bot reconnect that lost counts. Method: corrective games.
- Team format: `!set` also updates the team score, because the corrective games belong to the pair's fixture and team totals are derived from games.
- Result line after each game: individual `Alice : Bob = 3-2`; team `TeamA : TeamB = 7 : 5`, plus a second line with the pair score (`Alice : Bob = 3-2`).
- Seats are read as they are at the moment a game ends, never from a cached #1/#2.
- `!break`: announces a break and the time to come back; length = `tournament.break_minutes` (5), optional argument `!break 10`.
- `!cheer N` (N = 1 or 2): random cheering sentence for the player in seat N now.

**Rule (user, 2026-10-04): no hard-coded values in new code.** The admin configures the bot for each year's tournament, so game count, break length and interval, `!break` maximum, cheer cooldown, languages and every chat text come from `config.toml` (new keys: `[commands] cheer_cooldown_seconds`, `break_max_minutes`), validated at start-up.

**Status:** Tasks 1 to 6 done (README updated) (2026-10-04, 321 tests pass). Tasks 5 and 5b: `referee/info_text.py` (pure), keys `result_team`, `last_game`, `final`, `break_hint` in every language table, lines written by `MatchSession._announce` after a fresh ACK and after `!set`. Task 4b: no `default_language` key and no fallback (YAGNI): `tournament.language` must have a table, so every automatic text exists; messages live in `config/messages.py`. Task 4 put the new chat texts flat in `[messages]` (`set_usage`, `break_usage`, `cheer_usage`, `seats_unreadable`, `set_failed`, `result_pair`, `cheers`); Task 4b moves them into per-language tables. The `!set` answer already prints the pair line (Task 5 adds the team line and the line after every game). Task 3 uses the existing error codes (`BAD_RESULT` for an unreachable score, `MICROMATCH_FULL` for too many games) plus `NOT_ADMIN` and `BAD_PACKET`; there is no separate `SET_SCORE_IMPOSSIBLE`. The two limits `max_pending_results` and `unreadable_polls_before_alert` are config keys.

**Knowledge used:** `data-consistency-models` (derived standings, idempotence), `principle-layering-dependency-rule` (domain/storage know nothing of chat), `cross-error-handling-strategy` (bad `!set` text is answered in chat, never crashes the session).

---

## Functions taken from `example.py`

| `example.py` | Decision | Where it lands |
|---|---|---|
| `show_info_message` | **Add** (config instead of 15/14/10) | Task 5b `referee/info_text.py` |
| `show_score` (`{a:g}-{b:g}`) | **Add** as the pair line format | Task 5 `referee/result_text.py` |
| `set_score` / `!set {h:g}-{a:g}` | **Add**, but server-side with corrective games, not a local counter | Tasks 3 and 4 |
| `get_prague_time(offset)` + `BREAK_TEXT` for `!break` | **Add** as a clock in the session (already there: `_clock`, `break_text`) | Task 4 |
| `LANG_ALT` + `!rules {lang}` + `RULES_TEXT` | **Add**, as config: language tables and aliases (world tournament) | Task 4b |
| `add_game` (local score list) | **Skip**: score lives on the server | n/a |
| `!show` alias of `!score` | **Skip** unless you want it | n/a |

## Open points to confirm with the user (defaults chosen)
1. Corrective games for `!set` carry bot name `chat:!set` and a fresh `game_uid`; if the target is *lower* than the recorded points the extra games are voided (newest first) instead of deleted.
2. `!set` is rejected (chat message) when the target would need more games than `games_per_pair` allows.
3. If one pair's points do not fit exactly (e.g. wanted 3-2 but recorded 2.5-0.5 with draws), the server answers `SET_SCORE_IMPOSSIBLE` and the bot says so in chat.

---

### Task 1: Seat double-check in the session

**Files:** Modify `referee/session.py` (`PendingResult`, `_handle_system_line`, `_flush_pending`, `_result_packet`); Test `tests/test_session.py`.

Problem: `_result_packet` uses `self._context.p1_name/p2_name` read at flush time; if a player moved seats between the win line and the flush, the win goes to the wrong player.

**Step 1: failing test** (`FakeDriver` in the test file already supports `get_players_name`; make it return a different pair on the second call):
```python
def test_result_uses_the_seats_read_when_the_game_ended(self):
    driver = self.driver_with_players(("alice", "bob"))
    session = self.make_session(driver)
    driver.players = ("bob", "alice")          # they swap seats after the win line
    session.process_message("+", WIN_P1)       # seat #1 won; seat #1 was alice when the line came
    self.assertEqual(["alice", "bob"], self.sent_packet()["data"]["players"])
```
Adjust: read names first (`_refresh_names`) *inside* `_handle_system_line`, store `names=(p1, p2)` in `PendingResult` (new field `players: tuple`), and `_result_packet` uses `result.players`. If names are unreadable the result stays pending **without** names and takes the names of the first successful read *only while the seats have not changed since the last good read* (keep a `SeatTracker` snapshot: `last_seen = (p1, p2)` updated on every `poll()`); this is the "double check".

**Step 2:** run `python3 -m unittest tests.test_session -v` → FAIL.
**Step 3:** implement as above (one new field, `_snapshot_names()` helper, ≤ 40 lines each).
**Step 4:** run → PASS; full suite → OK.
**Step 5:** `git commit -m "fix: score a game with the seat names read when it ended"`

---

### Task 2: Server computes the team-match score

**Files:** Modify `storage/standings_query.py` (`pair_score_for_game` adds `team_points`), `domain/ports.py` (docstring only, same method); Test `tests/test_standings.py`.

`pair_score_for_game(game_id, scoring)` returns, in addition to today's keys:
`"teams": [name_a, name_b]` and `"team_points": [x, y]` — game points of all non-voided games of the same fixture, grouped by team of each side (`participants.entrant_id`). Individual tournaments: `teams` is the two player names and `team_points` equals `points` (entrant = player).

**Step 1: failing test** — two teams A(a1,a2) vs B(b1,b2), games a1>b1, a2<b2, a1>b2 in one fixture; ACK dict has `teams == ["A","B"]`, `team_points == [2.0, 1.0]`.
**Step 2:** run `python3 -m unittest tests.test_standings -v` → FAIL (KeyError).
**Step 3:** one extra query per call: `SELECT entrant_id, ...` over `games` joined to `participants` for `fixture_id IS ?`. Games without a fixture: team points fall back to the pair totals (documented in the docstring).
**Step 4:** PASS. **Step 5:** commit `feat: MATCH_ACK carries the team-match score`.

---

### Task 3: `SET_SCORE` request on the server

**Files:** Modify `network/messages.py` (`RequestType.SET_SCORE = 8`, `ResponseType.SCORE_SET = 109`), `domain/ports.py` (`ITeamRepository.set_pair_score`), `storage/games.py` (`GameLedger.adjust_pair`), `storage/tournament_store.py`, `storage/sqlite_repository.py`, `server.py` (`_on_set_score`, handler table), `serverapp/match_request.py` (`parse_set_score`); Tests `tests/test_storage.py`, `tests/test_server.py`, `tests/test_serverapp.py`.

Contract: `set_pair_score(p1, p2, target1, target2, actor) -> dict` returns the same dict as an ACK (`games, players, points, teams, team_points, complete`). Rules:
- Current points `c1, c2` of the pair in its open fixture. Needed change `d1 = target1 - c1`, `d2 = target2 - c2`.
- Positive deltas become corrective games: each full point = one win for that player; a remaining 0.5 = one draw (only when both deltas have the 0.5). Negative deltas void the pair's newest games that produced those points; if the exact points cannot be reached → `ValidationError("cannot reach 3-2 ...")` mapped to code `SET_SCORE_IMPOSSIBLE`.
- Total games after the change must be ≤ `games_per_pair` else `MicroMatchFullError`.
- Everything in one transaction; one audit entry per game (`bot_name = "chat:!set"`, actor = admin nickname).
- Idempotent: sending the same target twice changes nothing (deltas are 0).

**Step 1: failing tests**
```python
def test_set_pair_score_adds_corrective_games(self):
    ...  # pair has 1-0 recorded; set 3-2 -> 5 games total, points [3.0, 2.0]
def test_set_pair_score_is_idempotent(self): ...
def test_set_pair_score_voids_extra_games_when_lowered(self): ...
def test_set_pair_score_rejects_over_games_per_pair(self): ...
def test_set_score_requires_admin_name_on_the_wire(self): ...   # server checks `data.sender` is in tournament.admins
```
**Step 2:** run each module → FAIL. **Step 3:** implement; keep `adjust_pair` ≤ 40 lines by splitting `_plan_changes(current, target)` (pure function, unit-tested alone) from the DB writes.
**Step 4:** full suite → OK. **Step 5:** commit `feat: SET_SCORE adjusts a pair's score with audited corrective games`.

Server-side admin check: the packet carries `sender`; the server verifies it against `settings.tournament.admins` (second check besides the bot's own `admin_only`), because a bot could be misconfigured. Unauthorised → `ERROR NOT_ADMIN`.

---

### Task 4: `!set`, `!break`, `!cheer` handlers

**Files:** Create `referee/commands/cheer.py` (pure: `pick_cheer(templates, name, rng)`), Modify `referee/commands/handlers.py`, `referee/session.py` (`!break` needs the session state, like `!leave`), `config/settings.py` (`MessagesConfig.cheers`, `messages.break_manual`), `config/config.example.toml`, `config/config.toml`; Tests `tests/test_commands.py` (new), `tests/test_session.py`, `tests/test_config.py`.

- `!set L-R`: parse with a regex `^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$` (halves only: value*2 must be an integer). Bad text → chat `Usage: !set 3-2`. Sends `SET_SCORE` with the *current* seat names (`get_players_name()` at the command), `sender`, and targets. Answer arrives as `SCORE_SET` or `ERROR`; session prints it with the result-line formatter (Task 5) or the error text.
- `!break [minutes]`: session sets `BREAK_TIME`, `resume_at = now + minutes` (default `break_minutes`), sends `messages.break_text` (already has `{curr_time}`/`{resume_time}`). Bad minutes (≤ 0 or > 60) → usage message. Reuses `_start_break` extracted from `_start_break_if_due` (no duplicated code).
- `!cheer N`: `N` in {1, 2}; name of the player in seat N now; random template from `[messages] cheers = ["Go, {name}!", ...]` (at least 5, each must contain `{name}`; validated at start-up). Randomness injected (`random.Random`) so the test is deterministic. Not admin-only; remove nothing from `admin_only` (`cheer` is simply not in the list). Cooldown: one cheer per 10 s per table to avoid chat spam (constant `CHEER_COOLDOWN_SEC`).
- `config.toml` `[commands] admin_only` becomes `["start", "leave", "break", "set", "rules"]` unchanged; `start` stays unimplemented and is removed from the list (it is not in the user's list) — **confirm**.

**Steps (per command):** failing test → run → minimal code → run → commit (`feat: !set command`, `feat: !break command`, `feat: !cheer command`).

---

### Task 4b: Multi-language chat texts (world tournament)

**Files:** Modify `config/settings.py` (`MessagesConfig`), `config/reader.py` if needed, `config/config.example.toml`, `config/config.toml`, `referee/commands/handlers.py` (`rules`), `referee/session.py` (text lookups); Tests `tests/test_config.py`, `tests/test_commands.py`.

Texts move from flat keys to one table per language, so every language has the same set and a missing key is a start-up error (no half-translated bot):
```toml
[messages]
default_language = "en"        # used when the tournament language has no table or a key is missing
[messages.aliases]             # what players may type after !rules
eng = "en"
hun = "hu"
cs  = "cz"
cze = "cz"

[messages.en]
rules      = "To remind, you play a total of 12 games. ..."
break_text = "Break! It is {curr_time} now, please continue the match not later than {resume_time}."
bye        = "bye"
last_game  = "Last game"
final      = "Ggs"
break_hint = "You may take a break after the next game."
cheers     = ["Go, {name}!", "Show them, {name}!", ...]
[messages.hu]  # same keys
[messages.cz]  # same keys
```
- `MessagesConfig.text(language, key)` returns the language's text, else the default language's (the fallback is logged once at WARNING, not silent).
- Validation at start-up: every language table has all keys; `{curr_time}/{resume_time}` only in `break_text`; `{name}` in every cheer; aliases point to an existing language.
- `!rules [lang]`: no argument -> tournament language; argument is lower-cased, mapped by `aliases`, unknown language -> chat reply in the tournament language: `Languages: en, hu, cz` (the list comes from the config).
- Automatic messages (result line, `Last game`, `Ggs`, break, cheer, bye) use the tournament language. The result line itself is numbers and names only, so it needs no translation. A per-table language switch is not planned (YAGNI); only `!rules` takes a language.
- The language texts for `hu` and `cz` start from the strings already in `example.py`; the other messages need a translator: the user supplies them or the bot falls back to English.

**Steps:** failing config tests (missing key in `hu`, bad alias, cheer without `{name}`) -> run -> implement -> run -> `!rules hun` test -> commit `feat: per-language chat texts and !rules <lang>`.

---

### Task 5: Result line after each game

**Files:** Create `referee/result_text.py` (pure formatter), Modify `referee/session.py` (`on_ack`), `config/settings.py` + both toml files (`[messages] result_individual`, `result_team`, `result_pair`), Test `tests/test_result_text.py` (new), `tests/test_session.py`.

Formats (configurable, defaults):
- individual: `{p1} : {p2} = {s1:g}-{s2:g}`
- team, line 1: `{team1} : {team2} = {t1:g} : {t2:g}`
- team, line 2: `{p1} : {p2} = {s1:g}-{s2:g}`

`on_ack` formats from ACK data (`players`, `points`, `teams`, `team_points`) and sends via `driver.send_message` — only for **new** results (`duplicate` false) so a re-sent outbox result does not repeat the line. Players come from the ACK (server order of that game), which is also a second seat check.

**Steps:** failing formatter tests (individual, team, halves printed as `2.5`) → run → implement → run → session test with a fake ACK → commit `feat: chat result line after every game`.

---

### Task 5b: Game-count info message (from `example.py: show_info_message`)

**Files:** Create `referee/info_text.py` (pure: `info_message(games_played, total, break_after, texts) -> Optional[str]`), Modify `referee/session.py` (`on_ack`, after the result line), `config/settings.py` + both toml files (`[messages] last_game`, `final`, `break_hint`), Test `tests/test_info_text.py` (new), `tests/test_session.py`.

Port of the example's rule, with its hard-coded 15 / 14 / `% 10 == 9` replaced by config:
- `games >= total` -> `final` (default `Ggs`)
- `games == total - 1` -> `last_game` (default `Last game`)
- `break_after` set and `(games + 1) % break_after == 0` and not the last game -> `break_hint` (default `You may take a break after the next game.`)
- otherwise nothing.
`games_played` is the **server's** count from the ACK, not the local count, so it stays right after a reconnect or `!set`. It is sent only once per ACK (skipped when `duplicate` is true) and also after `SCORE_SET`.

Failing tests first: totals 12/break 0, totals 15/break 10 (same numbers as the example: 9 -> break hint, 14 -> last game, 15 -> Ggs), break_after 0 never prints the hint.

---

### Task 6: Docs, config, architecture test

- `README.md`: command table (admin: `!leave`, `!rules`, `!set`, `!break`; user: `!score`, `!cheer`), new messages keys, `SET_SCORE`.
- `tests/test_architecture.py`: allow `referee -> config` edges already present; add new modules only if a new package appears (none planned). Max 500 lines per module: `referee/session.py` is 294 lines; if it passes ~420, move break logic to `referee/breaks.py`.
- `.claude/` untouched. Update the project memory (`project-status`).

### Task 7: Live checks (user)
Run one bot with chat on (no `--no-chat`): finish a game → result line; `!cheer 1`; `!break`; reconnect bot then `!set`.
