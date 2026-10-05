"use strict";

/**
 * Pieces shared by the organizer dashboard and the public page: DOM helpers, tables, standings,
 * game rows, theme and polling. Everything goes in through textContent, never as markup.
 */

const TOP_RANKS               = 3;
const TIME_START              = 11;   // "YYYY-MM-DDTHH:MM:SSZ" -> "HH:MM:SS"
const TIME_END                = 19;
const THEME_KEY               = "dashboard-theme";
const RESULT_P1_WINS          = "p1 wins";
const RESULT_P2_WINS          = "p2 wins";
const RESULT_DRAW             = "draw";
const DEFAULT_REFRESH_SECONDS = 5;

const $ = (id) => document.getElementById(id);

/** Builds an element; `content` is text, a Node or an array of them. */
function h(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const part of [].concat(content === undefined ? [] : content)) {
    node.append(part instanceof Node ? part : String(part));
  }
  return node;
}

function clock(isoText) {
  return isoText.slice(TIME_START, TIME_END);
}

// ------------------------------------------------------------------ tables

/**
 * Replaces the box content with a table.
 * Each column is {label, num?, act?, cell(row)}; cell returns text or a Node.
 */
function renderTable(box, columns, rows, emptyText) {
  box.replaceChildren();
  if (!rows.length) {
    box.append(h("p", "empty", emptyText));
    return;
  }
  const table = h("table", "data");
  const head = table.createTHead().insertRow();
  for (const column of columns) {
    const th = h("th", column.num ? "num" : "", column.label);
    th.scope = "col";
    head.append(th);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    if (row.voided) tr.className = "is-voided";
    for (const column of columns) {
      const td = h("td", [column.num ? "num" : "", column.act ? "act" : ""].join(" ").trim(),
                   column.cell(row));
      tr.append(td);
    }
  }
  box.append(table);
}

// ------------------------------------------------------------------ standings and games

function renderStandings(state) {
  const isTeam = state.tournament.format === "team";
  const columns = [
    {label: "Rank", cell: rankBadge},
    {label: "Name", cell: nameWithCountry},
    {label: "Played", num: true, cell: (r) => r.games},
    {label: "Won", num: true, cell: (r) => r.wins},
    {label: "Drawn", num: true, cell: (r) => r.draws},
    {label: "Lost", num: true, cell: (r) => r.losses},
    {label: "Points", num: true, cell: (r) => h("span", "points", r.points)},
  ];
  if (isTeam) columns.push({label: "Match points", num: true, cell: (r) => r.match_points});
  renderTable($("standings"), columns, state.standings, "No entrants yet.");
}

function rankBadge(row) {
  const badge = h("span", row.rank <= TOP_RANKS ? "rank rank--top" : "rank",
                  row.rank + (row.tied ? "=" : ""));
  if (row.tied) badge.title = "Tied";
  return badge;
}

function nameWithCountry(row) {
  return row.country ? [row.name, h("span", "country", row.country)] : row.name;
}

function playerCell(game, side) {
  const wonBySide = game.result === (side === 1 ? RESULT_P1_WINS : RESULT_P2_WINS);
  const isDraw = game.result === RESULT_DRAW;
  const stone = h("span", "stone " + (wonBySide ? "stone--win" : isDraw ? "stone--draw" : "stone--none"));
  stone.setAttribute("aria-hidden", "true");
  return h("span", wonBySide ? "player player--winner" : "player",
           [stone, side === 1 ? game.p1 : game.p2]);
}

function resultText(game) {
  const text = {[RESULT_P1_WINS]: "Player 1 won", [RESULT_P2_WINS]: "Player 2 won",
                [RESULT_DRAW]: "Draw"}[game.result] || game.result;
  return game.voided ? text + " (voided)" : text;
}

/** The columns every audience sees; the organizer adds the bot and an action. */
function gameColumns() {
  return [
    {label: "Time (UTC)", cell: (g) => clock(g.at)},
    {label: "Table", num: true, cell: (g) => g.table_no},
    {label: "Player 1", cell: (g) => playerCell(g, 1)},
    {label: "Player 2", cell: (g) => playerCell(g, 2)},
    {label: "Result", cell: resultText},
  ];
}

// ------------------------------------------------------------------ theme

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch (error) {
    return null;     // storage blocked: the system theme applies
  }
}

function currentTheme() {
  const stored = storedTheme();
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme-toggle").textContent = theme === "dark" ? "Light theme" : "Dark theme";
}

function toggleTheme() {
  const next = currentTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (error) {
    // not remembered; the choice still applies until reload
  }
}

// ------------------------------------------------------------------ status and polling

function setStatus(kind, text) {
  $("status").className = "pill pill--" + kind;
  $("status-text").textContent = text;
}

/**
 * Starts polling /api/state and calls render(state) with each answer.
 * Returns a function that polls again at once (used after an organizer action).
 */
function startPolling(render) {
  let delaySeconds = DEFAULT_REFRESH_SECONDS;
  let timer = null;
  async function poll() {
    clearTimeout(timer);
    try {
      const response = await fetch("/api/state" + location.search, {credentials: "same-origin"});
      if (!response.ok) throw new Error("HTTP " + response.status);
      const state = await response.json();
      delaySeconds = state.refresh_seconds;
      render(state);
      setStatus("live", "Live, updated " + clock(state.generated_at) + " UTC");
    } catch (error) {
      setStatus("down", "Offline (" + error.message + "), retrying");
    }
    timer = setTimeout(poll, delaySeconds * 1000);
  }
  poll();
  return poll;
}
