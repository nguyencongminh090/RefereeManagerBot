"use strict";

/**
 * Public audience page: standings, latest results and the tables in play. Read-only; shared
 * helpers live in common.js, which is loaded first.
 */

function renderLiveTables(state) {
  const box = $("live-tables");
  box.replaceChildren();
  if (!state.live_tables.length) {
    box.append(h("p", "empty", "No tables in play right now."));
    return;
  }
  const list = h("ul", "tables", state.live_tables.map((t) => h("li", "tag", "Table " + t)));
  box.append(list);
}

function render(state) {
  $("title").textContent = state.tournament.name;
  $("format").textContent = state.tournament.format + " tournament";
  document.title = state.tournament.name + " - results";
  renderLiveTables(state);
  renderStandings(state);
  renderTable($("games"), gameColumns(), state.recent_games, "No results yet.");
}

function start() {
  applyTheme(currentTheme());
  $("theme-toggle").addEventListener("click", toggleTheme);
  startPolling(render);
}

start();
