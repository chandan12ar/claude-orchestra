"use strict";

const TOKEN = new URLSearchParams(location.search).get("k") || "";
const POLL_MS = 2000;

const state = {
  run: null,
  sessionId: new URLSearchParams(location.search).get("session") || "",
  live: true,
  view: "timeline",
  backoff: POLL_MS,
  selected: null,
  // Bumped whenever a new poll loop starts. A loop whose generation is stale
  // discards its result and stops rescheduling, so toggling Live or switching
  // sessions cannot leave two loops running on independent cadences.
  generation: 0,
};

const $ = (id) => document.getElementById(id);

function api(path) {
  const join = path.includes("?") ? "&" : "?";
  const session = state.sessionId ? "&session=" + encodeURIComponent(state.sessionId) : "";
  return fetch(path + join + "k=" + encodeURIComponent(TOKEN) + session)
    .then((response) => {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    });
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return seconds.toFixed(0) + "s";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m + "m " + (s < 10 ? "0" : "") + s + "s";
}

function fmtTokens(tokens) {
  const total = Object.values(tokens || {}).reduce((a, b) => a + b, 0);
  if (total > 1000000) return (total / 1000000).toFixed(1) + "M";
  if (total > 1000) return (total / 1000).toFixed(1) + "k";
  return String(total);
}

function svgEl(name, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------------------------------------------------------------- header

function renderHeader(run) {
  const t = run.totals;
  $("totals").innerHTML = "";
  const parts = [
    ["agents", t.agents],
    ["running", t.running],
    ["done", t.completed],
    ["failed", t.failed + t.orphaned],
    ["tokens", fmtTokens(t.tokens)],
    ["wall", fmtDuration(t.wall_time_s)],
  ];
  for (const [label, value] of parts) {
    const span = document.createElement("span");
    span.innerHTML = "<strong>" + value + "</strong> " + label;
    $("totals").appendChild(span);
  }
  $("conn").textContent = run.session_live ? "" : "session ended";
}

function renderHealth(run) {
  const trouble = run.agents.filter((a) =>
    ["stalled", "failed", "orphaned"].includes(a.status));
  const box = $("health");
  if (!trouble.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = "<strong>" + trouble.length + " agent(s) need attention</strong>";
  const list = document.createElement("ul");
  for (const agent of trouble) {
    const item = document.createElement("li");
    item.textContent = agent.status.toUpperCase() + " — " +
      (agent.description || agent.agent_id);
    item.style.cursor = "pointer";
    item.onclick = () => openDrawer(agent.agent_id);
    list.appendChild(item);
  }
  box.appendChild(list);
}

function renderDiagnostics(run) {
  const d = run.diagnostics || {};
  const bad = (d.unparsable_lines || 0);
  $("diagnostics").textContent = bad
    ? bad + " transcript line(s) could not be parsed; the view may be incomplete."
    : "";
}

// -------------------------------------------------------------- timeline

const ROW_H = 26;
const LEFT = 190;
const PAD = 16;

function timeWindow(run) {
  let min = Infinity;
  let max = -Infinity;
  for (const agent of run.agents) {
    if (agent.started_at !== null) min = Math.min(min, agent.started_at);
    const end = agent.ended_at !== null ? agent.ended_at : agent.last_activity_at;
    if (end) max = Math.max(max, end);
  }
  if (!isFinite(min)) return [0, 1];
  if (!isFinite(max) || max <= min) max = min + 1;
  return [min, max];
}

function renderTimeline(run) {
  const svg = $("timeline");
  svg.innerHTML = "";
  const agents = run.agents;
  const width = svg.clientWidth || 900;
  const height = PAD * 2 + Math.max(1, agents.length) * ROW_H + 20;
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);

  const [t0, t1] = timeWindow(run);
  const plot = width - LEFT - PAD;
  const x = (t) => LEFT + ((t - t0) / (t1 - t0)) * plot;
  const rowOf = {};
  agents.forEach((agent, i) => { rowOf[agent.agent_id] = i; });

  // Batch bands sit behind the bars: they are what shows a parallel wave.
  for (const batch of run.batches || []) {
    const rows = batch.agent_ids.map((id) => rowOf[id]).filter((r) => r !== undefined);
    if (rows.length < 2) continue;
    const top = PAD + Math.min(...rows) * ROW_H;
    const tall = (Math.max(...rows) - Math.min(...rows) + 1) * ROW_H;
    svg.appendChild(svgEl("rect", {
      x: LEFT - 4, y: top, width: plot + 8, height: tall, class: "batch-band",
      rx: 4,
    }));
  }

  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4;
    svg.appendChild(svgEl("line", {
      x1: x(t), y1: PAD - 6, x2: x(t), y2: height - PAD, class: "axis",
    }));
    svg.appendChild(svgEl("text", {
      x: x(t) + 3, y: height - PAD + 12, class: "axis-text",
    }, fmtDuration(t - t0)));
  }

  agents.forEach((agent, i) => {
    const y = PAD + i * ROW_H;
    const label = agent.description || agent.agent_id;
    svg.appendChild(svgEl("text", { x: 0, y: y + 12, class: "row-label" },
      label.length > 28 ? label.slice(0, 27) + "…" : label));
    svg.appendChild(svgEl("text", { x: 0, y: y + 22, class: "row-meta" },
      agent.agent_type + " · " + (agent.model || "?")));

    const rounds = agent.rounds.length ? agent.rounds
      : [{ started_at: agent.started_at, ended_at: agent.ended_at }];
    for (const round of rounds) {
      const start = round.started_at !== null ? round.started_at : t0;
      const end = round.ended_at !== null ? round.ended_at
        : (agent.last_activity_at || t1);
      const bx = x(start);
      const bw = Math.max(3, x(Math.max(end, start)) - bx);
      const bar = svgEl("rect", {
        x: bx, y: y + 4, width: bw, height: ROW_H - 12, rx: 3,
        class: "bar s-" + agent.status + (round.ended_at === null ? " bar-open" : ""),
      });
      bar.appendChild(svgEl("title", {},
        label + " — " + agent.status + " — " + fmtDuration(agent.duration_s)));
      bar.onclick = () => openDrawer(agent.agent_id);
      svg.appendChild(bar);
    }
    // The status word is drawn, not only coloured.
    svg.appendChild(svgEl("text", {
      x: x(agent.started_at !== null ? agent.started_at : t0) + 4,
      y: y + 16, class: "row-meta",
    }, agent.status));
  });
}

// ------------------------------------------------------------ view state

function setView(view) {
  state.view = view;
  $("view-timeline").hidden = view !== "timeline";
  $("view-graph").hidden = view !== "graph";
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  render();
}

function render() {
  if (!state.run) return;
  renderHeader(state.run);
  renderHealth(state.run);
  renderDiagnostics(state.run);
  if (state.view === "timeline") renderTimeline(state.run);
  else renderGraph(state.run);
}

function startPolling() {
  state.generation += 1;
  poll(state.generation);
}

async function poll(generation) {
  let run = null;
  try {
    run = await api("/api/run");
  } catch (err) {
    if (generation !== state.generation) return;
    $("conn").textContent = "reconnecting…";
    state.backoff = Math.min(state.backoff * 2, 30000);
    if (state.live) setTimeout(() => poll(generation), state.backoff);
    return;
  }
  // A response that arrived after the session changed, or after this loop was
  // superseded, must not overwrite the current view.
  if (generation !== state.generation) return;
  state.run = run;
  state.backoff = POLL_MS;
  $("conn").textContent = run.session_live ? "" : "session ended";
  render();
  if (state.live) setTimeout(() => poll(generation), state.backoff);
}

async function loadSessions() {
  try {
    const data = await api("/api/sessions");
    const picker = $("session-picker");
    picker.innerHTML = "";
    for (const session of data.sessions) {
      const option = document.createElement("option");
      option.value = session.session_id;
      option.textContent = session.session_id.slice(0, 8) + " · " +
        session.agent_count + " agents" +
        (session.session_id === data.current ? " (current)" : "");
      picker.appendChild(option);
    }
    picker.value = state.sessionId || data.current;
  } catch (err) { /* picker is optional; the run view still works */ }
}

function init() {
  $("live-toggle").onclick = (event) => {
    state.live = !state.live;
    event.target.setAttribute("aria-pressed", String(state.live));
    event.target.textContent = state.live ? "Live" : "Paused";
    if (state.live) startPolling();
  };
  $("session-picker").onchange = (event) => {
    state.sessionId = event.target.value;
    state.run = null;
    startPolling();
  };
  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => setView(tab.dataset.view);
  }
  window.addEventListener("resize", () => render());
  loadSessions();
  startPolling();
}

document.addEventListener("DOMContentLoaded", init);

// ----------------------------------------------------------------- graph

const NODE_W = 170;
const NODE_H = 40;
const COL_GAP = 90;
const ROW_GAP = 18;
const EXACT_KINDS = ["spawn", "artifact", "message"];

function layoutGraph(run) {
  const nodes = [{ id: "main", label: "orchestrator", status: "completed",
                   sub: "this session", isMain: true }];
  for (const agent of run.agents) {
    nodes.push({
      id: agent.agent_id,
      label: agent.description || agent.agent_id,
      sub: agent.agent_type + " · " + (agent.model || "?"),
      status: agent.status,
      startedAt: agent.started_at,
    });
  }
  const byId = {};
  nodes.forEach((n) => { byId[n.id] = n; });
  const edges = (run.edges || []).filter((e) => byId[e.src] && byId[e.dst]);

  // Rank: longest path over exact edges. An inferred edge never sets a rank,
  // so a bad guess cannot rearrange the whole picture.
  const rank = {};
  nodes.forEach((n) => { rank[n.id] = 0; });
  const structural = edges.filter((e) => EXACT_KINDS.includes(e.kind));
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const edge of structural) {
      const want = rank[edge.src] + 1;
      if (rank[edge.dst] < want) { rank[edge.dst] = want; moved = true; }
    }
    if (!moved) break;  // also the cycle guard: bounded by node count
  }

  const columns = {};
  for (const node of nodes) {
    node.rank = rank[node.id];
    (columns[node.rank] = columns[node.rank] || []).push(node);
  }
  for (const key in columns) {
    columns[key].sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0));
  }

  // Two barycenter sweeps: cheap, and enough for the fan-out shapes real
  // orchestrations produce.
  const ranks = Object.keys(columns).map(Number).sort((a, b) => a - b);
  for (let sweep = 0; sweep < 2; sweep++) {
    for (const r of ranks) {
      const index = {};
      (columns[r - 1] || []).forEach((n, i) => { index[n.id] = i; });
      for (const node of columns[r]) {
        const parents = structural
          .filter((e) => e.dst === node.id && index[e.src] !== undefined)
          .map((e) => index[e.src]);
        node.bary = parents.length
          ? parents.reduce((a, b) => a + b, 0) / parents.length
          : Number.MAX_SAFE_INTEGER;
      }
      columns[r].sort((a, b) => (a.bary - b.bary) || 0);
    }
  }

  // Place by COLUMN INDEX, not by raw rank value. Artifact edges can form a
  // cycle (two agents each reading what the other wrote), and the rank loop is
  // bounded by node count rather than convergence, so a raw rank can climb far
  // past the number of columns actually occupied — putting nodes outside the
  // viewBox with no error and no visible cue that anything is missing.
  ranks.forEach((r, column) => {
    columns[r].forEach((node, i) => {
      node.column = column;
      node.x = 20 + column * (NODE_W + COL_GAP);
      node.y = 20 + i * (NODE_H + ROW_GAP);
    });
  });
  const width = 40 + (ranks.length) * (NODE_W + COL_GAP);
  const height = 40 + Math.max(...ranks.map((r) => columns[r].length)) *
    (NODE_H + ROW_GAP);
  return { nodes, edges, byId, width, height };
}

function renderGraph(run) {
  const svg = $("graph");
  svg.innerHTML = "";
  $("edge-evidence").hidden = true;
  const layout = layoutGraph(run);
  const width = Math.max(svg.clientWidth || 900, layout.width);
  svg.setAttribute("height", Math.max(layout.height, 200));
  svg.setAttribute("viewBox", "0 0 " + width + " " + Math.max(layout.height, 200));

  for (const edge of layout.edges) {
    const a = layout.byId[edge.src];
    const b = layout.byId[edge.dst];
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    const path = svgEl("path", {
      d: "M" + x1 + "," + y1 + " C" + mid + "," + y1 + " " + mid + "," + y2 +
         " " + x2 + "," + y2,
      class: "edge" + (edge.confidence === "inferred" ? " edge-inferred" : ""),
    });
    path.appendChild(svgEl("title", {}, edge.kind + " (" + edge.confidence + ")"));
    path.onclick = () => showEvidence(edge);
    svg.appendChild(path);
  }

  for (const node of layout.nodes) {
    const group = svgEl("g", { class: "node" });
    group.appendChild(svgEl("rect", {
      x: node.x, y: node.y, width: NODE_W, height: NODE_H, rx: 6,
    }));
    group.appendChild(svgEl("circle", {
      cx: node.x + 12, cy: node.y + 14, r: 5,
      class: "dot s-" + node.status,
    }));
    const label = node.label.length > 22 ? node.label.slice(0, 21) + "…" : node.label;
    group.appendChild(svgEl("text", { x: node.x + 24, y: node.y + 18 }, label));
    group.appendChild(svgEl("text", {
      x: node.x + 24, y: node.y + 31, class: "edge-label",
    }, node.status + " · " + node.sub));
    if (!node.isMain) group.onclick = () => openDrawer(node.id);
    svg.appendChild(group);
  }

  svg.appendChild(svgEl("text", { x: 20, y: Math.max(layout.height, 200) - 8,
    class: "legend" }, "solid = exact  ·  dashed = inferred (click an edge for evidence)"));

  // Hub files are context, not dependencies, so they are listed rather than
  // drawn. They go to the footer, not #edge-evidence, which showEvidence()
  // overwrites wholesale.
  const hubs = (run.hub_files || []).map(
    (hub) => "shared context: " + hub.path + " (read by " +
             hub.reader_ids.length + " agents)");
  if (hubs.length) {
    // Appended, not assigned: renderDiagnostics ran first and may have put a
    // parse warning there that must not be thrown away.
    const footer = $("diagnostics");
    footer.textContent = [footer.textContent, hubs.join(" · ")]
      .filter(Boolean).join("  |  ");
  }
}

function showEvidence(edge) {
  const box = $("edge-evidence");
  box.hidden = false;
  // Everything here is transcript-derived and untrusted. e.path in particular
  // is a tool call's literal file_path argument, and < > " are all legal in a
  // filename — so this must be escaped exactly as openDrawer escapes the same
  // data. scrub() redacts credentials; it does not HTML-escape.
  const bits = ["<strong>" + esc(edge.kind) + "</strong> — " + esc(edge.confidence),
                esc(edge.src) + " → " + esc(edge.dst)];
  const e = edge.evidence || {};
  if (e.path) bits.push("file: <code>" + esc(e.path) + "</code>");
  if (e.score !== undefined) {
    bits.push("overlap score: " + esc(e.score) + " · longest match: " +
              esc(e.run_words) + " words");
  }
  if (e.snippet) bits.push("<pre>" + esc(e.snippet) + "</pre>");
  if (e.handoff) {
    bits.push("<em>result text also reused:</em> " + esc(e.handoff.run_words) +
              " word match");
  }
  box.innerHTML = bits.join("<br>");
}

// ---------------------------------------------------------------- drawer

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text === null || text === undefined ? "" : String(text);
  return div.innerHTML;
}

async function openDrawer(agentId) {
  const drawer = $("drawer");
  drawer.hidden = false;
  drawer.innerHTML = "<p>Loading…</p>";
  let agent;
  try {
    agent = await api("/api/agent/" + encodeURIComponent(agentId));
  } catch (err) {
    drawer.innerHTML = "<p>Could not load this agent.</p>";
    return;
  }
  state.selected = agentId;

  const rows = [
    ["status", agent.status],
    ["type", agent.agent_type],
    ["model", agent.model],
    ["launch", agent.launch_mode],
    ["duration", fmtDuration(agent.duration_s)],
    ["tokens", fmtTokens(agent.tokens)],
    ["rounds", agent.rounds.length],
    ["tool calls", agent.tool_calls.length],
  ];

  const tools = agent.tool_calls.slice(-40)
    .map((t) => esc(t.name) + "  " + esc(t.target)).join("\n");

  drawer.innerHTML =
    '<button class="close" type="button" id="drawer-close">Close</button>' +
    "<h2>" + esc(agent.description || agent.agent_id) + "</h2>" +
    '<div class="source-note">' + esc(agent.agent_id) + "</div>" +
    "<dl>" + rows.map(([k, v]) =>
      "<dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd>").join("") + "</dl>" +
    "<h3>Objective</h3><pre>" + esc(agent.objective || "—") + "</pre>" +
    '<div class="source-note">' + esc(agent.objective_source) + "</div>" +
    "<h3>Expected output</h3><pre>" + esc(agent.expected_output || "—") + "</pre>" +
    '<div class="source-note">' + esc(agent.expected_output_source) + "</div>" +
    "<h3>Returned result</h3><pre>" + esc(agent.result || "(still running)") + "</pre>" +
    "<details><summary>Full brief</summary><pre>" + esc(agent.brief) + "</pre></details>" +
    "<details><summary>Tool calls (last 40)</summary><pre>" + tools + "</pre></details>" +
    "<h3>Files written</h3><pre>" + esc(agent.files_written.join("\n") || "—") + "</pre>" +
    "<h3>Files read</h3><pre>" + esc(agent.files_read.join("\n") || "—") + "</pre>";

  $("drawer-close").onclick = () => { drawer.hidden = true; state.selected = null; };
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("drawer").hidden = true;
});
