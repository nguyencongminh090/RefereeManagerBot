# Saved PlayOK pages (private)

The `*.html` files here are real PlayOK pages saved from the browser. They hold real nicknames and chat, so they are
git-ignored (see `.gitignore`) and must never be committed. `tests/test_page_parser.py` uses them and skips those tests
when they are absent.

| File | What the page shows |
|---|---|
| `lobby_with_invitation.html` | lobby with about 17 tables, one invitation to table #104, one table panel |
| `table_finished_games.html` | table #116 after its games ended (18 results, empty seats), plus private messages |
| `table_live_game.html` | table #106 while a game runs (both seats readable) |
| `lobby_private_messages.html` | lobby with private message threads, used to check they are not read as table chat |
