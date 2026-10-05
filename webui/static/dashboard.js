"use strict";

/**
 * Organizer dashboard: draws the views and sends the organizer's edit actions.
 * Shared helpers live in common.js, which is loaded first.
 */

const IDLE_QUIET_SECONDS = 45;  // three missed heartbeats (config heartbeat_seconds = 15)
const IDLE_STALE_SECONDS = 90;
const TAB_NAMES = ["overview", "organizer"];

// ------------------------------------------------------------------ views

function idleLevel(seconds) {
  if (seconds >= IDLE_STALE_SECONDS) return "stale";
  return seconds >= IDLE_QUIET_SECONDS ? "quiet" : "online";
}

function formatIdle(seconds) {
  const whole = Math.round(seconds);
  return whole < 60 ? whole + " s ago" : Math.floor(whole / 60) + " min ago";
}

function healthCell(bot) {
  const level = idleLevel(bot.idle_seconds);
  const label = {online: "Online", quiet: "Quiet", stale: "No signal"}[level];
  const dot = h("span", "health__dot");
  dot.setAttribute("aria-hidden", "true");
  return h("span", "health health--" + level, [dot, label + ", " + formatIdle(bot.idle_seconds)]);
}

function renderBots(state) {
  const columns = [
    {label: "Bot", cell: (b) => b.name},
    {label: "Tables", cell: (b) => b.tables.length
      ? b.tables.map((t) => h("span", "tag", t)) : "none yet"},
    {label: "Last signal", cell: healthCell},
  ];
  renderTable($("bots"), columns, state.bots, "No bot connected.");
}

function renderGames(state) {
  const columns = gameColumns().concat([{label: "Bot", cell: (g) => g.bot}]);
  if (state.can_edit) columns.push({label: "Action", act: true, cell: voidButton});
  renderTable($("games"), columns, state.recent_games, "No games yet.");
}

function renderAudit(state) {
  const columns = [
    {label: "Time (UTC)", cell: (a) => clock(a.at)},
    {label: "Who", cell: (a) => a.actor},
    {label: "Action", cell: (a) => a.action},
    {label: "What", cell: (a) => a.entity + (a.entity_id === null ? "" : " #" + a.entity_id)},
  ];
  renderTable($("audit"), columns, state.audit, "No changes yet.");
}

function renderProblems(state) {
  $("problems-box").hidden = state.problems.length === 0;
  $("problems").replaceChildren(...state.problems.map((p) => h("li", "", p)));
}

// ------------------------------------------------------------------ summary tiles

function setTile(name, value, note, level) {
  $("kpi-" + name + "-value").textContent = value;
  $("kpi-" + name + "-note").textContent = note;
  $("kpi-" + name).dataset.level = level || "";
}

function renderTiles(state) {
  const staleBots = state.bots.filter((b) => idleLevel(b.idle_seconds) === "stale").length;
  const tableCount = state.bots.reduce((sum, b) => sum + b.tables.length, 0);
  const responding = state.bots.length - staleBots;
  const botNote = state.bots.length === 0 ? "No bot connected"
    : staleBots ? staleBots + " of " + state.bots.length + " without signal" : "All responding";
  setTile("bots", responding, botNote, state.bots.length === 0 || staleBots ? "warn" : "ok");
  setTile("tables", tableCount, "claimed by bots", "");
  const last = state.recent_games.find((g) => !g.voided);
  if (last) setTile("last", clock(last.at), "Table " + last.table_no + ": " + last.p1 + " vs " + last.p2, "");
  else setTile("last", "none", "No games yet", "");
  const problems = state.problems.length;
  setTile("problems", problems, problems ? "Listed above" : "All clear", problems ? "warn" : "ok");
}

// ------------------------------------------------------------------ confirm and actions

/** Asks the organizer to confirm; resolves true on Confirm. Falls back to confirm(). */
function askConfirm(text) {
  const dialog = $("confirm");
  if (typeof dialog.showModal !== "function") return Promise.resolve(window.confirm(text));
  $("confirm-text").textContent = text;
  dialog.returnValue = "";   // Escape keeps the old value otherwise
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), {once: true});
    dialog.showModal();
  });
}

function setFlash(message, isError) {
  const flash = $("flash");
  flash.className = isError ? "flash flash--error" : "flash";
  flash.textContent = message;
}

async function act(body, question) {
  if (question && !(await askConfirm(question))) return;
  setFlash("Saving...", false);
  try {
    const response = await fetch("/api/action" + location.search, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Dashboard-Action": "1"},
      body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
    setFlash("Saved.", false);
    poll();
  } catch (error) {
    setFlash("Not saved: " + error.message, true);
  }
}

function button(label, className, onClick) {
  const node = h("button", "btn " + className, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function voidButton(game) {
  const label = game.voided ? "Restore" : "Void";
  return button(label, "btn--small", () => act(
    {action: "void_game", game_id: game.id, voided: !game.voided},
    label + " game #" + game.id + " (" + game.p1 + " vs " + game.p2 + ")?"));
}

function removeDeciderButton(decider) {
  return button("Remove", "btn--small", () => act(
    {action: "delete_sudden_death", winner_id: decider.winner_id, loser_id: decider.loser_id},
    "Remove the decider " + decider.winner + " beat " + decider.loser + "?"));
}

// ------------------------------------------------------------------ organizer tab

let entrantsShown = "";

function fillEntrantSelects(entrants) {
  const signature = JSON.stringify(entrants);
  if (signature === entrantsShown) return;       // keep the organizer's choice while polling
  entrantsShown = signature;
  for (const id of ["sd-winner", "sd-loser"]) {
    const select = $(id);
    const chosen = select.value;
    select.replaceChildren(...entrants.map((e) => new Option(e.name, e.id)));
    if (chosen) select.value = chosen;
    else if (id === "sd-loser" && entrants.length > 1) select.selectedIndex = 1;
  }
}

function renderOrganizer(state) {
  $("tabs").hidden = !state.can_edit;
  if (!state.can_edit) {
    showTab("overview");
    return;
  }
  fillEntrantSelects(state.entrants);
  const columns = [
    {label: "Winner", cell: (d) => d.winner},
    {label: "Loser", cell: (d) => d.loser},
    {label: "Action", act: true, cell: removeDeciderButton},
  ];
  renderTable($("sd-list"), columns, state.sudden_death, "No deciders recorded.");
}

function submitPair(event) {
  event.preventDefault();
  const p1 = $("pair-p1").value.trim(), p2 = $("pair-p2").value.trim();
  const points1 = parseFloat($("pair-pts1").value), points2 = parseFloat($("pair-pts2").value);
  act({action: "set_pair_score", p1, p2, points1, points2},
      "Set " + p1 + " vs " + p2 + " to " + points1 + " : " + points2 + "?");
}

function submitDecider(event) {
  event.preventDefault();
  const winner = $("sd-winner"), loser = $("sd-loser");
  act({action: "record_sudden_death", winner_id: Number(winner.value),
       loser_id: Number(loser.value)},
      "Record " + winner.selectedOptions[0].text + " as the sudden-death winner against " +
      loser.selectedOptions[0].text + "?");
}

// ------------------------------------------------------------------ tabs and theme

function showTab(name) {
  for (const tab of TAB_NAMES) {
    const selected = tab === name;
    $("panel-" + tab).hidden = !selected;
    $("tab-" + tab).setAttribute("aria-selected", String(selected));
    $("tab-" + tab).tabIndex = selected ? 0 : -1;
  }
}

function onTabKey(event) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const step = event.key === "ArrowRight" ? 1 : -1;
  const index = TAB_NAMES.findIndex((t) => $("tab-" + t) === document.activeElement);
  const next = TAB_NAMES[(index + step + TAB_NAMES.length) % TAB_NAMES.length];
  showTab(next);
  $("tab-" + next).focus();
}

// ------------------------------------------------------------------ start

function render(state) {
  $("title").textContent = state.tournament.name;
  $("format").textContent = state.tournament.format + " tournament";
  document.title = state.tournament.name + " - Referee dashboard";
  renderProblems(state);
  renderTiles(state);
  renderStandings(state);
  renderBots(state);
  renderGames(state);
  renderAudit(state);
  renderOrganizer(state);
}

let poll = null;   // polls again at once; set by start()

function start() {
  applyTheme(currentTheme());
  $("theme-toggle").addEventListener("click", toggleTheme);
  for (const tab of TAB_NAMES) $("tab-" + tab).addEventListener("click", () => showTab(tab));
  $("tabs").addEventListener("keydown", onTabKey);
  $("pair-form").addEventListener("submit", submitPair);
  $("sd-form").addEventListener("submit", submitDecider);
  poll = startPolling(render);
}

start();
