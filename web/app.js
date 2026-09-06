/* No framework and no bundler: the page is small enough that a build step would
   cost more than it returns, and a static file deploys identically to Vercel and
   GitHub Pages. Charts are drawn as SVG by hand to avoid a charting dependency
   that would be larger than the data it renders. */

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};
const SVG_NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
};

const pct = (value, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const money = (value) => {
  if (value == null) return "—";
  if (value >= 1e6) return `€${(value / 1e6).toFixed(1)}M`;
  if (value >= 1e3) return `€${Math.round(value / 1e3)}k`;
  return `€${Math.round(value)}`;
};
const OUTCOME = { H: "home win", D: "draw", A: "away win" };

const state = {
  meta: null, ledger: null, analytics: null, valueModel: null,
  leagueFilter: "ALL", players: null, columns: null, similar: {},
  selected: null, valueSort: "over",
};

async function getJSON(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`${path} → ${response.status}`);
  return response.json();
}

/* Inlined by tools/bundle_preview.py for the single-file preview build. */
const embedded = window.__BIGFIVE__ || null;
const load = (name) => (embedded ? Promise.resolve(embedded[name]) : getJSON(`data/${name}.json`));

/* ------------------------------------------------------------------ chrome */

function renderMeta(meta) {
  if (meta.mode === "demo") {
    const badge = $("mode-badge");
    badge.hidden = false;
    badge.textContent = "Synthetic data";
    badge.title = "Generated offline so the project runs without credentials. Not real results.";
  }
  const generated = new Date(meta.generated_at);
  const days = Math.floor((Date.now() - generated) / 86400000);
  const freshness = $("freshness");
  freshness.textContent = days <= 0 ? "Updated today" : `Updated ${days} day${days === 1 ? "" : "s"} ago`;
  // A pipeline that quietly stops is the real failure mode, so staleness is
  // shown in the interface rather than left for someone to notice.
  if (days > 9) {
    freshness.classList.add("stale");
    freshness.textContent += " — the pipeline may be stuck";
  }
  $("footer-meta").textContent =
    `Model ${meta.model_version} · ${meta.leagues.length} leagues · season ${meta.current_season} · ` +
    `source join rate ${pct(meta.join_rate, 0)} · generated ${meta.generated_at.replace("T", " ").replace("+00:00", " UTC")}`;
}

/* -------------------------------------------------------------- the record */

function renderHero(ledger) {
  if (!ledger.n) {
    $("hero-line").textContent = "No predictions scored yet.";
    return;
  }
  $("hero-line").textContent = `${ledger.n.toLocaleString()} predictions. ${Math.round(ledger.accuracy * 100)}% right.`;
}

function renderChips(ledger) {
  const container = $("league-chips");
  container.replaceChildren();
  const leagues = ["ALL", ...Object.keys(ledger.by_league)];
  for (const league of leagues) {
    const chip = el("button", "chip", league === "ALL" ? "All leagues" : leagueName(league));
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.leagueFilter === league));
    chip.addEventListener("click", () => {
      state.leagueFilter = league;
      renderChips(state.ledger);
      renderWall(state.ledger);
    });
    container.append(chip);
  }
}

function leagueName(id) {
  const found = state.meta?.leagues.find((league) => league.id === id);
  return found ? found.name : id;
}

function renderWall(ledger) {
  const wall = $("wall");
  wall.replaceChildren();
  const cells = ledger.cells.filter(
    (cell) => state.leagueFilter === "ALL" || cell.league === state.leagueFilter);

  cells.forEach((cell, index) => {
    const node = el("button", `cell${cell.correct ? "" : " wrong"}`);
    node.type = "button";
    // Confidence rides on opacity; correctness on fill and hue together, so the
    // grid still reads without colour vision.
    node.style.opacity = String(0.45 + Math.min(cell.confidence, 0.85) * 0.65);
    node.style.animationDelay = `${Math.min(index * 3, 900)}ms`;
    node.setAttribute("aria-label",
      `${cell.label}. Picked ${OUTCOME[cell.pick]}, ${cell.correct ? "correct" : "wrong"}.`);
    const show = (event) => showTooltip(event, `
      <strong>${cell.label}</strong><br>
      ${cell.date} · ${leagueName(cell.league)}<br>
      Picked ${OUTCOME[cell.pick]} at ${pct(cell.confidence, 0)}<br>
      <span class="verdict${cell.correct ? "" : " wrong"}">${cell.correct ? "Correct" : `Actual: ${OUTCOME[cell.actual]}`}</span>`);
    node.addEventListener("mouseenter", show);
    node.addEventListener("focus", show);
    node.addEventListener("mousemove", moveTooltip);
    node.addEventListener("mouseleave", hideTooltip);
    node.addEventListener("blur", hideTooltip);
    wall.append(node);
  });

  $("wall-note").textContent =
    `${cells.length} most recent scored predictions${state.leagueFilter === "ALL" ? "" : ` in ${leagueName(state.leagueFilter)}`}. ` +
    `Brightness is how confident the model was.`;
}

function renderScoreboard(ledger, matchModel) {
  const board = $("scoreboard");
  board.replaceChildren();
  const entries = [
    ["Accuracy", pct(ledger.accuracy), `${ledger.n.toLocaleString()} scored`],
    ["Always pick home", pct(matchModel.baseline_home), "baseline to beat"],
    ["Better form wins", pct(matchModel.baseline_form), "baseline to beat"],
    ["Brier score", ledger.brier.toFixed(3), "lower is better"],
    ["Last 50", pct(ledger.accuracy_last_50), `since ${ledger.first_recorded}`],
  ];
  for (const [term, value, note] of entries) {
    const wrap = el("div");
    wrap.append(el("dt", null, term));
    const dd = el("dd", null, value);
    dd.append(el("small", null, ` ${note}`));
    wrap.append(dd);
    board.append(wrap);
  }
}

function renderRolling(ledger) {
  const container = $("rolling");
  container.replaceChildren();
  if (ledger.rolling.length < 5) return;

  const width = 900, height = 130, pad = { l: 34, r: 12, t: 12, b: 22 };
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", role: "img" });
  const svgTitle = svgEl("title", {});
  svgTitle.textContent = "Rolling 40-match accuracy";
  svg.append(svgTitle);

  const values = ledger.rolling.map((point) => point.accuracy);
  const low = Math.min(0.35, Math.min(...values) - 0.03);
  const high = Math.max(0.7, Math.max(...values) + 0.03);
  const x = (i) => pad.l + (i / (values.length - 1)) * (width - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - low) / (high - low)) * (height - pad.t - pad.b);

  for (const tick of [low, (low + high) / 2, high]) {
    svg.append(svgEl("line", {
      x1: pad.l, x2: width - pad.r, y1: y(tick), y2: y(tick), class: "axis", "stroke-width": 1,
    }));
    const label = svgEl("text", { x: 4, y: y(tick) + 4 });
    label.textContent = pct(tick, 0);
    svg.append(label);
  }

  const path = values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(value).toFixed(1)}`).join(" ");
  svg.append(svgEl("path", { d: path, fill: "none", stroke: "var(--hit)", "stroke-width": 2 }));

  const caption = el("p", "fine", "Rolling accuracy over the last 40 scored predictions. The swings are what a 50-odd percent process looks like week to week.");
  container.append(svg, caption);
}

/* ----------------------------------------------------------------- fixtures */

function renderFixtures(payload) {
  const container = $("fixtures");
  container.replaceChildren();
  const fixtures = payload.fixtures.slice(0, 12);
  $("fixture-count").textContent = String(payload.fixtures.length);

  for (const fixture of fixtures) {
    const card = el("div", "fixture");
    const meta = el("div", "fixture-meta");
    meta.append(el("span", null, leagueName(fixture.league)));
    meta.append(el("span", null, new Date(fixture.utc_date).toLocaleDateString(undefined,
      { weekday: "short", day: "numeric", month: "short" })));
    const teams = el("div", "fixture-teams");
    teams.append(el("span", null, fixture.home_team));
    teams.append(el("span", "away", fixture.away_team));

    const bar = el("div", "prob");
    for (const [key, value] of [["h", fixture.p_home], ["d", fixture.p_draw], ["a", fixture.p_away]]) {
      const part = el("i", key);
      part.style.width = `${value * 100}%`;
      bar.append(part);
    }
    const labels = el("div", "prob-labels");
    labels.append(el("span", null, `${pct(fixture.p_home, 0)} home`));
    labels.append(el("span", null, `${pct(fixture.p_draw, 0)} draw`));
    labels.append(el("span", null, `${pct(fixture.p_away, 0)} away`));

    card.append(meta, teams, bar, labels);
    container.append(card);
  }
}

/* ----------------------------------------------------------- model weakness */

function bar(label, value, max, { warn = false, format = pct } = {}) {
  const row = el("div", "bar-row");
  row.append(el("span", null, label));
  const track = el("div", "bar-track");
  const fill = el("div", `bar-fill${warn ? " warn" : ""}`);
  fill.style.width = `${Math.max(1, (value / max) * 100)}%`;
  track.append(fill);
  row.append(track, el("span", "bar-value", format(value)));
  return row;
}

function renderPerClass(matchModel) {
  const container = $("per-class");
  container.replaceChildren();
  const names = { H: "Home win", D: "Draw", A: "Away win" };
  for (const key of ["H", "D", "A"]) {
    const stats = matchModel.per_class[key];
    container.append(bar(names[key], stats.recall, 1, { warn: key === "D" }));
  }
  const draw = matchModel.per_class.D;
  $("draw-note").textContent =
    `Share of real results the model actually catches. It finds ${pct(draw.recall, 0)} of the ` +
    `${draw.support} draws, because a draw is rarely the single most likely outcome even when it is ` +
    `the most common one. Every published football model has this problem; most do not show it.`;
}

function renderCalibration(matchModel) {
  const container = $("calibration");
  container.replaceChildren();
  const points = matchModel.calibration || [];
  if (!points.length) return;

  const size = 260, pad = 34;
  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, width: "100%", role: "img" });
  const scale = (value) => pad + (value - 0.3) / 0.7 * (size - pad * 2);
  const flip = (value) => size - scale(value);

  svg.append(svgEl("line", {
    x1: scale(0.3), y1: flip(0.3), x2: scale(1), y2: flip(1),
    class: "axis", "stroke-dasharray": "3 3", "stroke-width": 1,
  }));
  for (const tick of [0.4, 0.6, 0.8]) {
    const label = svgEl("text", { x: scale(tick) - 8, y: size - 8 });
    label.textContent = pct(tick, 0);
    svg.append(label);
    const side = svgEl("text", { x: 2, y: flip(tick) + 4 });
    side.textContent = pct(tick, 0);
    svg.append(side);
  }
  for (const point of points) {
    svg.append(svgEl("circle", {
      cx: scale(point.stated), cy: flip(point.observed),
      r: Math.max(3, Math.sqrt(point.n) * 0.55),
      fill: "var(--hit)", "fill-opacity": 0.55, stroke: "var(--hit)",
    }));
  }
  const xLabel = svgEl("text", { x: size / 2 - 30, y: size - 22 });
  xLabel.textContent = "stated confidence";
  const yLabel = svgEl("text", { x: 6, y: 14 });
  yLabel.textContent = "actually right";
  svg.append(xLabel, yLabel);
  container.append(svg);
}

/* ------------------------------------------------------------ value section */

function renderPremium(analytics) {
  const container = $("premium-chart");
  container.replaceChildren();
  const rows = [...analytics.leagues].sort((a, b) => (b.value_premium ?? 0) - (a.value_premium ?? 0));
  const max = Math.max(...rows.map((row) => row.value_premium ?? 1));
  for (const row of rows) {
    container.append(bar(leagueName(row.league), row.value_premium ?? 1, max,
      { format: (value) => `${value.toFixed(2)}×` }));
  }
  const spread = rows[0];
  container.append(el("p", "fine",
    `Same output, different price tag: ${leagueName(spread.league)} carries a ${spread.value_premium.toFixed(2)}× ` +
    `premium over Ligue 1 once age, minutes and production are held equal.`));
}

function renderValueToggle() {
  const container = $("value-toggle");
  container.replaceChildren();
  for (const [key, label] of [["over", "Costliest vs model"], ["under", "Cheapest vs model"]]) {
    const chip = el("button", "chip", label);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.valueSort === key));
    chip.addEventListener("click", () => {
      state.valueSort = key;
      renderValueToggle();
      renderValueList(state.analytics);
    });
    container.append(chip);
  }
}

function renderValueList(analytics) {
  const list = $("value-list");
  list.replaceChildren();
  const rows = state.valueSort === "over" ? analytics.overvalued : analytics.undervalued;
  for (const row of rows.slice(0, 8)) {
    const item = el("li");
    const who = el("div");
    who.append(el("div", "who", row.name));
    who.append(el("div", "where",
      `${row.team} · ${leagueName(row.league)} · ${row.position}, ${row.age}`));
    // Below 1.0 a single decimal collapses everything to "0.0×", which reads as
    // a bug rather than a cheap player.
    const ratio = row.value_ratio;
    const gap = el("div", `gap ${state.valueSort === "over" ? "over" : "under"}`,
      `${ratio >= 1 ? ratio.toFixed(1) : ratio.toFixed(2)}×`);
    gap.title = `${money(row.market_value_eur)} market vs ${money(row.predicted_value_eur)} modelled`;
    item.append(who, gap);
    list.append(item);
  }
}

/* ------------------------------------------------- client-side value model */

const WHATIF = [
  { key: "age", label: "Age", min: 17, max: 38, step: 1, value: 24 },
  { key: "goals_p90", label: "Goals per 90", min: 0, max: 1.2, step: 0.01, value: 0.35 },
  { key: "assists_p90", label: "Assists per 90", min: 0, max: 0.8, step: 0.01, value: 0.2 },
  { key: "key_passes_p90", label: "Key passes per 90", min: 0, max: 4, step: 0.05, value: 1.4 },
  { key: "minutes_share", label: "Share of available minutes", min: 0.1, max: 1.2, step: 0.01, value: 0.8 },
];

function renderWhatIf(model, meta) {
  const container = $("whatif-controls");
  container.replaceChildren();
  const inputs = {};

  for (const spec of WHATIF) {
    const wrap = el("div", "control");
    const label = el("label");
    label.htmlFor = `whatif-${spec.key}`;
    label.append(el("span", null, spec.label));
    const readout = el("span", null, String(spec.value));
    label.append(readout);
    const input = document.createElement("input");
    Object.assign(input, { type: "range", id: `whatif-${spec.key}`, min: spec.min, max: spec.max, step: spec.step, value: spec.value });
    input.addEventListener("input", () => {
      readout.textContent = input.value;
      update();
    });
    inputs[spec.key] = input;
    wrap.append(label, input);
    container.append(wrap);
  }

  const leagueWrap = el("div", "control");
  const leagueLabel = el("label");
  leagueLabel.htmlFor = "whatif-league";
  leagueLabel.append(el("span", null, "League"));
  const select = document.createElement("select");
  select.id = "whatif-league";
  for (const league of meta.leagues) {
    const option = document.createElement("option");
    option.value = league.id;
    option.textContent = league.name;
    select.append(option);
  }
  select.addEventListener("change", update);
  leagueWrap.append(leagueLabel, select);
  container.append(leagueWrap);

  function update() {
    const values = Object.fromEntries(
      Object.entries(inputs).map(([key, input]) => [key, Number(input.value)]));
    // The two expected-goal inputs are not exposed as sliders; they track
    // finishing at roughly league-average conversion so the panel stays legible.
    values.xg_p90 = values.goals_p90 * 0.95;
    values.xa_p90 = values.assists_p90 * 0.95;
    values.age_sq = values.age ** 2;
    for (const league of meta.leagues) values[`league_${league.id}`] = league.id === select.value ? 1 : 0;

    let total = model.intercept;
    model.columns.forEach((column, index) => {
      const raw = values[column] ?? 0;
      total += model.coef[index] * ((raw - model.mean[index]) / model.scale[index]);
    });
    const predicted = Math.exp(total);

    const out = $("whatif-out");
    out.replaceChildren(document.createTextNode(money(predicted)));
    out.append(el("small", null,
      `What the market has historically paid for this profile in ${leagueName(select.value)}. ` +
      `Typical error is around ${Math.round(model.median_abs_error_pct)}%, so read it as an order of ` +
      `magnitude, not a price. Move the league selector to see the premium in action.`));
  }
  update();
}

/* -------------------------------------------------------------- style map */

async function initScout(meta) {
  const select = $("league-select");
  for (const league of meta.leagues) {
    const option = document.createElement("option");
    option.value = league.id;
    option.textContent = league.name;
    select.append(option);
  }
  select.addEventListener("change", () => drawScatter());
  $("player-search").addEventListener("input", () => drawScatter());

  const payload = await load("players");
  state.columns = payload.columns;
  state.players = payload.rows.map((row) =>
    Object.fromEntries(row.map((value, index) => [payload.columns[index], value])));
  drawScatter();
}

async function neighboursFor(league) {
  if (!state.similar[league]) {
    state.similar[league] = embedded
      ? (embedded.similar?.[league] ?? {})
      : await getJSON(`data/similar/${league}.json`);
  }
  return state.similar[league];
}

const CLUSTER_COLOURS = ["#37D9B0", "#E8913C", "#7FB2E8", "#C79BE0", "#88C46A", "#E0C662", "#E0798E", "#6FD3D8"];

function drawScatter() {
  const container = $("scatter");
  container.replaceChildren();
  if (!state.players) return;

  const league = $("league-select").value;
  const query = $("player-search").value.trim().toLowerCase();
  const rows = state.players.filter((player) =>
    player.league === league && (!query || player.name.toLowerCase().includes(query)));

  if (!rows.length) {
    container.append(el("p", "empty", "No player matches that search in this league."));
    return;
  }

  const width = 620, height = 420, pad = 26;
  const xs = state.players.map((player) => player.style_x);
  const ys = state.players.map((player) => player.style_y);
  const scaleX = (value) => pad + (value - Math.min(...xs)) / (Math.max(...xs) - Math.min(...xs)) * (width - pad * 2);
  const scaleY = (value) => height - pad - (value - Math.min(...ys)) / (Math.max(...ys) - Math.min(...ys)) * (height - pad * 2);

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", role: "img" });
  const title = svgEl("title", {});
  title.textContent = `Playing styles in ${leagueName(league)}`;
  svg.append(title);

  for (const player of rows) {
    const dot = svgEl("circle", {
      cx: scaleX(player.style_x).toFixed(1),
      cy: scaleY(player.style_y).toFixed(1),
      r: 3.4 + Math.min(player.minutes / 1400, 2.6),
      fill: CLUSTER_COLOURS[player.style_cluster % CLUSTER_COLOURS.length],
      "fill-opacity": 0.62,
      class: `dot${state.selected?.player_id === player.player_id ? " selected" : ""}`,
      tabindex: 0,
      role: "button",
      "aria-label": `${player.name}, ${player.team}`,
    });
    const show = (event) => showTooltip(event,
      `<strong>${player.name}</strong><br>${player.team}<br>${player.goals} goals, ${player.assists} assists`);
    dot.addEventListener("mouseenter", show);
    dot.addEventListener("mousemove", moveTooltip);
    dot.addEventListener("mouseleave", hideTooltip);
    dot.addEventListener("click", () => selectPlayer(player));
    dot.addEventListener("keydown", (event) => { if (event.key === "Enter") selectPlayer(player); });
    if (state.selected?.player_id === player.player_id) svg.append(dot);
    svg.append(dot);
  }
  container.append(svg);

  // Narrowing the search to a single player selects them, but only when that is
  // a change: selectPlayer redraws the scatter, so an unguarded call recurses.
  if (query && rows.length === 1 && state.selected?.player_id !== rows[0].player_id) {
    selectPlayer(rows[0]);
  }
}

async function selectPlayer(player) {
  const changed = state.selected?.player_id !== player.player_id;
  state.selected = player;
  if (changed) drawScatter();

  const panel = $("player-panel");
  panel.replaceChildren();

  const head = el("div", "player-head");
  head.append(el("h3", null, player.name));
  head.append(el("div", "stat-line",
    `${player.team} · ${leagueName(player.league)} · ${player.position}, age ${player.age}`));
  head.append(el("div", "stat-line",
    `${player.goals} goals, ${player.assists} assists in ${player.minutes.toLocaleString()} minutes`));
  head.append(el("div", "stat-line",
    `${money(player.market_value_eur)} market · ${money(player.predicted_value_eur)} modelled`));
  panel.append(head);

  panel.append(el("h3", null, "Closest profiles"));
  const lookup = await neighboursFor(player.league);
  const neighbours = lookup[String(player.player_id)] ?? [];
  const index = new Map(state.players.map((row) => [row.player_id, row]));

  if (!neighbours.length) {
    panel.append(el("p", "empty", "No neighbours stored for this player."));
    return;
  }
  for (const [id, similarity] of neighbours.slice(0, 8)) {
    const other = index.get(id);
    if (!other) continue;
    const row = el("div", "neighbour");
    const who = el("div");
    who.append(el("div", null, other.name));
    who.append(el("div", "where", `${other.team} · ${leagueName(other.league)}`));
    row.append(who, el("div", "sim", similarity.toFixed(2)));
    panel.append(row);
  }
  panel.append(el("p", "fine",
    "Cosine similarity on eight per-90 measures, z-scored within each league so a Ligue 1 profile can match a Serie A one."));
}

/* -------------------------------------------------------------- tooltip */

function showTooltip(event, html) {
  const tip = $("tooltip");
  tip.innerHTML = html;
  tip.hidden = false;
  moveTooltip(event);
}
function moveTooltip(event) {
  const tip = $("tooltip");
  const x = (event.clientX ?? 0) + 14;
  const y = (event.clientY ?? 0) + 14;
  tip.style.left = `${Math.min(x, window.innerWidth - tip.offsetWidth - 12)}px`;
  tip.style.top = `${Math.min(y, window.innerHeight - tip.offsetHeight - 12)}px`;
}
function hideTooltip() { $("tooltip").hidden = true; }

/* ----------------------------------------------------------------- start */

async function main() {
  try {
    const [meta, ledgerData, fixtures, matchModel, valueModel, analytics] = await Promise.all(
      ["meta", "ledger", "fixtures", "model_match", "model_value", "analytics"].map(load));

    Object.assign(state, { meta, ledger: ledgerData, analytics, valueModel });

    renderMeta(meta);
    renderHero(ledgerData);
    renderChips(ledgerData);
    renderWall(ledgerData);
    renderScoreboard(ledgerData, matchModel);
    renderRolling(ledgerData);
    renderFixtures(fixtures);
    renderPerClass(matchModel);
    renderCalibration(matchModel);
    renderPremium(analytics);
    renderValueToggle();
    renderValueList(analytics);
    renderWhatIf(valueModel, meta);
    await initScout(meta);
  } catch (error) {
    console.error(error);
    document.querySelector(".lede").textContent =
      "The data files could not be loaded. Run `python -m pipeline.run` to generate them, then serve this folder.";
  }
}

main();
