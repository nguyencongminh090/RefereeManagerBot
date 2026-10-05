# Dashboard redesign Implementation Plan

**Goal:** Replace the inline-string dashboard page with a denser "control room" page: health strip, KPI tiles, richer standings/bots/games, and a separate Organizer tab.

**Architecture:** `webui/static/` holds `index.html`, `dashboard.css`, `dashboard.js`; `webui/assets.py` loads them once and `http_server.py` serves them from an allowlisted `/static/<name>` route, so the CSP can drop `'unsafe-inline'`. `/api/state` and `/api/action` keep their shape.

**Tech Stack:** stdlib `http.server`, plain HTML/CSS/JS (no build step, no CDN), `unittest`.

Knowledge used: `vpick-style-by-site-type` (dashboard: flat, dense, optional dark), `vbase-layout-spacing`, `vbase-typography` (tabular numerals), `vbase-color` (status never by colour alone), `vstyle-dark-mode`, `a11y-aria-keyboard-focus`, `fe-html-progressive-enhancement`, `cross-error-handling-strategy` (inline, specific errors).
Skills: @tdd-test-first @design-system @frontend-design-anthropic @google-style-html-css

## Design decisions
- Look: flat, cool grey-green neutrals, one deep-teal accent, stable semantic ok/warn/error. System font stack, `tabular-nums`. Sentence-case labels.
- One memorable element: Gomoku stones. A result shows a filled/hollow disc beside the winner and a half disc for a draw; the word (won/draw/void) is always present.
- Light default, follows the system, manual toggle kept in `localStorage` (try/catch).
- Bot idle thresholds follow `heartbeat_seconds = 15`: warn after 45 s (3 missed), stale after 90 s. Constants in `dashboard.js`.
- Confirmations use a native `<dialog>` naming exactly what changes (replaces `confirm()`).
- Out of scope: API changes, new config keys, new actions.

## Tasks
1. **Static assets loader** (`webui/assets.py`, test `tests/test_dashboard_assets.py`): allowlist, content types, unknown name -> None.
2. **HTTP route** (`webui/http_server.py`, test `tests/test_dashboard_http.py`): `/static/<name>` behind the token, `/` serves `index.html`, CSP without `'unsafe-inline'`, traversal is 404.
3. **Page** (`webui/static/*`, delete `webui/page.py`): build HTML/CSS/JS.
4. **Docs** (`README.md` dashboard section): describe layout and the static folder.
5. **Verify**: full test suite, architecture test, run the server against a seeded DB, browser check (light, dark, 360 px, keyboard), `ux-audit` pass.
