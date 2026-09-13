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
    tab.classList.toggle("active", tab.dataset.view === view);
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

async function poll() {
  try {
    state.run = await api("/api/run");
    state.backoff = POLL_MS;
    $("conn").textContent = state.run.session_live ? "" : "session ended";
    render();
  } catch (err) {
    $("conn").textContent = "reconnecting…";
    state.backoff = Math.min(state.backoff * 2, 30000);
  }
  if (state.live) setTimeout(poll, state.backoff);
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
    if (state.live) poll();
  };
  $("session-picker").onchange = (event) => {
    state.sessionId = event.target.value;
    state.run = null;
    poll();
  };
  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => setView(tab.dataset.view);
  }
  window.addEventListener("resize", () => render());
  loadSessions();
  poll();
}

document.addEventListener("DOMContentLoaded", init);
