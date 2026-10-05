"""The static dashboard page: inline CSS and JS that poll /api/state and draw the tables."""

PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Referee Dashboard</title>
<style>
  :root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --text:#1c2128; --muted:#6b7480; --line:#e3e6ea;
          --accent:#2557d6; --warn:#b4540a; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171c; --card:#1d2128; --text:#e6e9ee; --muted:#8b94a1; --line:#2d333c;
            --accent:#6e9bff; --warn:#f0a35e; } }
  body { margin:0; background:var(--bg); color:var(--text);
         font:15px/1.45 system-ui, sans-serif; }
  header { padding:14px 16px; display:flex; justify-content:space-between; gap:12px;
           flex-wrap:wrap; align-items:baseline; }
  h1 { font-size:18px; margin:0; }
  #status { color:var(--muted); font-size:13px; }
  main { display:grid; gap:14px; padding:0 16px 24px;
         grid-template-columns:repeat(auto-fit, minmax(min(440px, 100%), 1fr)); }
  section { background:var(--card); border:1px solid var(--line); border-radius:8px;
            padding:12px 14px; overflow-x:auto; }
  h2 { font-size:14px; margin:0 0 8px; text-transform:uppercase; letter-spacing:.04em;
       color:var(--muted); }
  table { border-collapse:collapse; width:100%; }
  th, td { text-align:left; padding:4px 8px 4px 0; border-bottom:1px solid var(--line);
           white-space:nowrap; }
  th { color:var(--muted); font-weight:500; font-size:13px; }
  td.num, th.num { text-align:right; }
  .warn { color:var(--warn); }
  .empty { color:var(--muted); }
  tr.voided td:not(.act) { text-decoration:line-through; opacity:.55; }
  form { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:8px; }
  input, select, button { font:inherit; color:var(--text); background:var(--bg);
    border:1px solid var(--line); border-radius:6px; padding:4px 8px; }
  input[type=text], input:not([type]) { width:9em; }
  input[type=number] { width:5.5em; }
  section.wide { grid-column:1 / -1; }
  button { cursor:pointer; background:var(--card); }
  button:hover { border-color:var(--accent); }
  .hint { color:var(--muted); font-size:13px; }
  #flash { padding:0 16px 10px; min-height:1.2em; font-size:14px; }
  .error { color:var(--warn); }
</style>
</head>
<body>
<header><h1 id="title">Referee Dashboard</h1><span id="status">loading...</span></header>
<div id="flash" role="status"></div>
<main>
  <section><h2>Standings</h2><div id="standings"></div></section>
  <section><h2>Tables and bots</h2><div id="bots"></div></section>
  <section class="wide"><h2>Recent games</h2><div id="games"></div></section>
  <section id="problems-box" hidden><h2>Problems</h2><div id="problems"></div></section>
  <section id="pair-box" hidden><h2>Correct a pair's score</h2>
    <form id="pair-form">
      <input id="pair-p1" placeholder="nickname 1" required>
      <input id="pair-p2" placeholder="nickname 2" required>
      <input id="pair-pts1" type="number" min="0" step="0.5" placeholder="pts 1" required>
      <input id="pair-pts2" type="number" min="0" step="0.5" placeholder="pts 2" required>
      <button>Set score</button>
    </form>
    <div class="hint">Makes the pair's total score equal these points by adding corrective games.</div>
  </section>
  <section id="sd-box" hidden><h2>Sudden death</h2>
    <form id="sd-form">
      <select id="sd-winner"></select> beat <select id="sd-loser"></select>
      <button>Record</button>
    </form>
    <div id="sd-list"></div>
  </section>
  <section><h2>Latest changes</h2><div id="audit"></div></section>
</main>
<script>
"use strict";
const $ = (id) => document.getElementById(id);

function cell(text, cls) {
  const td = document.createElement("td");
  if (text instanceof Node) td.append(text);
  else td.textContent = text === null || text === undefined ? "" : String(text);
  if (cls) td.className = cls;
  return td;
}

function fill(box, headers, rows, empty) {
  box.replaceChildren();
  if (!rows.length) {
    const p = document.createElement("div");
    p.className = "empty";
    p.textContent = empty;
    box.append(p);
    return;
  }
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h.label;
    if (h.num) th.className = "num";
    head.append(th);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    if (row.cls) tr.className = row.cls;
    headers.forEach((h, i) => tr.append(cell(row.cells[i], h.num ? "num" : (h.act ? "act" : ""))));
  }
  box.append(table);
}

function render(state) {
  $("title").textContent = state.tournament.name + " (" + state.tournament.format + ")";
  const team = state.tournament.format === "team";
  fill($("standings"),
    [{label:"#"}, {label:"Name"}, {label:"P", num:true}, {label:"W", num:true},
     {label:"D", num:true}, {label:"L", num:true}, {label:"Pts", num:true}]
      .concat(team ? [{label:"MP", num:true}] : []),
    state.standings.map((r) => ({cells: [r.rank + (r.tied ? "=" : ""), r.name, r.games, r.wins,
      r.draws, r.losses, r.points].concat(team ? [r.match_points] : [])})),
    "No entrants yet.");
  fill($("bots"), [{label:"Bot"}, {label:"Tables"}, {label:"Idle s", num:true}],
    state.bots.map((b) => ({cells: [b.name, b.tables.join(", ") || "-", b.idle_seconds.toFixed(0)]})),
    "No bot connected.");
  const gameHeaders = [{label:"Time"}, {label:"Table", num:true}, {label:"Player 1"},
      {label:"Player 2"}, {label:"Result"}, {label:"Bot"}];
  if (state.can_edit) gameHeaders.push({label:"", act:true});
  fill($("games"), gameHeaders,
    state.recent_games.map((g) => ({cells: [g.at.slice(11, 19), g.table_no, g.p1, g.p2,
      g.result, g.bot].concat(state.can_edit ? [voidButton(g)] : []),
      cls: g.voided ? "voided" : ""})), "No games yet.");
  renderEditing(state);
  $("problems-box").hidden = state.problems.length === 0;
  fill($("problems"), [{label:"To fix before the start"}],
    state.problems.map((p) => ({cells: [p], cls: "warn"})), "");
  fill($("audit"), [{label:"Time"}, {label:"Who"}, {label:"Action"}, {label:"What"}],
    state.audit.map((a) => ({cells: [a.at.slice(11, 19), a.actor, a.action,
      a.entity + (a.entity_id === null ? "" : " #" + a.entity_id)]})), "No changes yet.");
  $("status").className = "";
  $("status").textContent = "updated " + state.generated_at.slice(11, 19) + " UTC";
}

function button(label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

function voidButton(g) {
  const label = g.voided ? "Restore" : "Void";
  return button(label, () => act({action: "void_game", game_id: g.id, voided: !g.voided},
    label + " game #" + g.id + " (" + g.p1 + " vs " + g.p2 + ")?"));
}

function flash(message, isError) {
  $("flash").className = isError ? "error" : "";
  $("flash").textContent = message;
}

async function act(body, question) {
  if (question && !confirm(question)) return;
  flash("Saving...", false);
  try {
    const response = await fetch("/api/action" + location.search, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-Dashboard-Action": "1"},
      body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
    flash("Saved.", false);
    poll();
  } catch (error) {
    flash(error.message, true);
  }
}

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

function renderEditing(state) {
  $("pair-box").hidden = !state.can_edit;
  $("sd-box").hidden = !state.can_edit;
  if (!state.can_edit) return;
  fillEntrantSelects(state.entrants);
  fill($("sd-list"), [{label:"Winner"}, {label:"Loser"}, {label:"", act:true}],
    state.sudden_death.map((d) => ({cells: [d.winner, d.loser,
      button("Remove", () => act({action: "delete_sudden_death", winner_id: d.winner_id,
        loser_id: d.loser_id}, "Remove the decider " + d.winner + " beat " + d.loser + "?"))]})),
    "No deciders recorded.");
}

$("pair-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const p1 = $("pair-p1").value.trim(), p2 = $("pair-p2").value.trim();
  const points1 = parseFloat($("pair-pts1").value), points2 = parseFloat($("pair-pts2").value);
  act({action: "set_pair_score", p1, p2, points1, points2},
    "Set " + p1 + " vs " + p2 + " to " + points1 + " : " + points2 + "?");
});

$("sd-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const winner = $("sd-winner"), loser = $("sd-loser");
  act({action: "record_sudden_death", winner_id: Number(winner.value),
       loser_id: Number(loser.value)},
    "Record " + winner.selectedOptions[0].text + " as the sudden-death winner against " +
    loser.selectedOptions[0].text + "?");
});

let delaySeconds = 5;
let timer = null;
async function poll() {
  clearTimeout(timer);
  try {
    const response = await fetch("/api/state" + location.search, {credentials: "same-origin"});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const state = await response.json();
    delaySeconds = state.refresh_seconds;
    render(state);
  } catch (error) {
    $("status").className = "error";
    $("status").textContent = "cannot reach the server (" + error.message + ")";
  }
  timer = setTimeout(poll, delaySeconds * 1000);
}
poll();
</script>
</body>
</html>
"""
