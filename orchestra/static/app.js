"use strict";

const TOKEN = new URLSearchParams(location.search).get("k") || "";
const POLL_MS = 2000;
// While the push stream is healthy, polling is only a safety net (it also
// catches time-based changes no file write announces, like a stall threshold).
const STREAM_POLL_MS = 15000;
const STREAM_DEBOUNCE_MS = 300;
const FLEET_POLL_MS = 4000;

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
  // agent_id -> { count }: how many of that agent's tool_calls are already
  // folded into `ticker`, so a re-fetched detail only contributes its tail.
  toolCache: {},
  // Merged tool-call feed across agents, newest first, capped below.
  ticker: [],
  // Free-text filter (matches description/id/type/model/objective) and a
  // status allow-set — empty means "no restriction," not "match nothing."
  filterText: "",
  filterStatuses: new Set(),
  // Which graph edges ("src>dst>kind") have already been drawn at least
  // once. The first render seeds this silently (nothing "just happened" on
  // page load); every edge key added after that is a real event — a file
  // handoff or message just detected between polls — and gets a one-time
  // traveling-dot animation instead of appearing as a plain static line.
  seenEdgeKeys: new Set(),
  graphSeeded: false,
  // Desktop notifications for "something happened while I wasn't watching."
  // notifySeeded guards the same way graphSeeded does: the first poll only
  // records what already exists (a pre-existing failure or an already-ended
  // session is not a new event), so opening the dashboard never fires a
  // burst of notifications for history.
  notifyEnabled: false,
  notifySeeded: false,
  fleet: null,           // last /api/fleet payload
  fleetSeeded: false,    // first fleet poll only records, never notifies
  fleetAttention: {},    // session_id -> kind@since already announced
  fleetTimer: null,
  liveRun: null,         // the newest run from the server; state.run may be a replay of it
  replay: { on: false, t: 0, playing: false, speed: 30, timer: null },
  faviconKey: "",
  soundEnabled: false,   // off by default; turning it on is the click autoplay needs
  soundMemo: null,       // per-session baseline so history never makes noise
  soundFleetMemo: null,
  lastSoundAt: 0,
  audio: null,
  pill: null,            // the open Picture-in-Picture window, if any
  stream: null,          // the open EventSource, if any
  streamLive: false,     // true only while that stream is connected
  refreshTimer: null,    // pending debounced refresh after a push
  knownAttention: "",   // kind@since of the last attention already announced
  knownBudget: "",      // last budget state announced (ok / warn / exceeded)
  knownFailedIds: new Set(),
  knownLoopIds: new Set(),
  lastSessionLive: null,
  // agent_id -> { toolCount, tokenTotal } as of the last render, so the Work
  // Floor view can tell "this agent did something since the last poll" apart
  // from "this is just what the run currently looks like." Seeded the same
  // way graphSeeded/notifySeeded are: the first render records history
  // silently, only later deltas earn the one-shot activity pulse.
  floorActivity: {},
  floorSeeded: false,
  // agent_id -> its last-seen status and a celebrate-until timestamp (ms),
  // so a live transition into "completed" gets a one-shot jump burst
  // instead of every render re-triggering it.
  agentPrevStatus: {},
  agentCelebrateUntil: {},
  // agent_id -> AgentSprite, the currently-mounted canvas animators. Always
  // stopped before the floor is rebuilt or the tab is left, so a detached
  // canvas never keeps a requestAnimationFrame loop running forever.
  agentSprites: {},
  // Set true by report.py's offline shim. A static report's "running" agent
  // was only running at export time — ticking its clock against the
  // viewer's real wall clock would make an old, safe-to-email snapshot claim
  // an agent has been running for however long it's sat on someone's disk.
  offline: false,
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

// A possible loop, in words, with its evidence. Never a verdict: the same
// pattern is also what legitimate polling looks like.
function loopText(loop) {
  if (!loop) return "";
  const call = (c) => c.tool + (c.target ? " " + c.target : "");
  if (loop.kind === "cycle") {
    return "alternating " + loop.calls.map(call).join(" \u21C4 ") + " over its last " +
      loop.count + " calls";
  }
  return call(loop.calls[0] || loop) + " \u00D7" + loop.count;
}

// Money in the price file's currency. Tiny amounts say "<", not "$0.00", which
// would read as free.
function fmtMoney(amount, currency) {
  if (amount === null || amount === undefined) return "—";
  const symbol = !currency || currency === "USD" ? "$" : currency + " ";
  if (amount > 0 && amount < 0.01) return "<" + symbol + "0.01";
  return symbol + (amount >= 100 ? amount.toFixed(0) : amount.toFixed(2));
}

function fmtCount(n) {
  if (n > 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n > 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

function fmtTokens(tokens) {
  return fmtCount(Object.values(tokens || {}).reduce((a, b) => a + b, 0));
}

// Only input-side tokens are cacheable; output is never served from cache,
// so it stays out of the denominator or every ratio would be diluted by
// however chatty the agent's replies happened to be.
function cacheHitRatio(tokens) {
  const t = tokens || {};
  const input = t.input || 0;
  const cacheRead = t.cache_read || 0;
  const cacheCreate = t.cache_create || 0;
  const denom = input + cacheRead + cacheCreate;
  return denom ? cacheRead / denom : null;
}

function fmtPct(ratio) {
  return ratio === null || ratio === undefined ? "—" : Math.round(ratio * 100) + "%";
}

function fmtTokenMix(tokens) {
  const t = tokens || {};
  return "input " + fmtCount(t.input || 0) +
    " · cache read " + fmtCount(t.cache_read || 0) +
    " · cache write " + fmtCount(t.cache_create || 0) +
    " · output " + fmtCount(t.output || 0);
}

// Shared by the drawer's tool-mix bar and the activity ticker: a fixed,
// small taxonomy rather than one swatch per literal tool name (which would
// run to dozens of mcp__* names and make the fingerprint unreadable).
const TOOL_BUCKETS = [
  ["Read", "tool-read", ["Read", "Grep", "Glob", "NotebookRead", "WebFetch", "WebSearch"]],
  ["Edit", "tool-edit", ["Edit", "Write", "NotebookEdit"]],
  ["Bash", "tool-bash", ["Bash"]],
  ["Task", "tool-task", ["Task", "Agent"]],
];
function toolBucket(name) {
  for (const [label, cssVar, names] of TOOL_BUCKETS) {
    if (names.includes(name)) return [label, cssVar];
  }
  return ["Other", "tool-other"];
}

function fmtClock(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
}

// The full model string ("claude-haiku-4-5-20251001") is the ground truth —
// shown as-is wherever there's room (the drawer). Compact spaces (Timeline
// rows, Graph nodes) get this shortened form instead: strip the "claude-"
// prefix and a trailing release-date suffix, so "sonnet-5" stays a real
// version identifier rather than collapsing back to the bare family name.
function fmtModelShort(model) {
  if (!model) return "?";
  return model.replace(/^claude-/, "").replace(/-\d{8}$/, "");
}

function renderToolMix(toolCalls) {
  if (!toolCalls || !toolCalls.length) return "";
  const counts = { Read: 0, Edit: 0, Bash: 0, Task: 0, Other: 0 };
  for (const call of toolCalls) counts[toolBucket(call.name)[0]] += 1;
  const order = [["Read", "tool-read"], ["Edit", "tool-edit"], ["Bash", "tool-bash"],
    ["Task", "tool-task"], ["Other", "tool-other"]].filter(([label]) => counts[label] > 0);
  const total = toolCalls.length;
  const bars = order.map(([label, cssVar]) =>
    '<span style="width:' + (counts[label] / total * 100) + '%;background:var(--' + cssVar + ')" ' +
    'title="' + esc(label) + " " + counts[label] + '"></span>').join("");
  const legend = order.map(([label, cssVar]) =>
    '<span><span class="swatch" style="background:var(--' + cssVar + ')"></span>' +
    esc(label) + " " + counts[label] + "</span>").join("");
  return '<div class="tool-mix"><div class="tool-mix-bar">' + bars + "</div>" +
    '<div class="tool-mix-legend">' + legend + "</div></div>";
}

// Diffs `toolCalls` against what's already been folded into the ticker for
// this agent, so re-fetching a running agent's growing detail only appends
// its new tail instead of duplicating everything seen so far.
function ingestToolCalls(agentId, label, toolCalls) {
  const calls = toolCalls || [];
  const cached = state.toolCache[agentId] || { count: 0 };
  if (calls.length > cached.count) {
    for (const call of calls.slice(cached.count)) {
      state.ticker.push({ ts: call.timestamp, agentId, label, name: call.name, target: call.target });
    }
    state.ticker.sort((a, b) => (b.ts || 0) - (a.ts || 0));
    if (state.ticker.length > 300) state.ticker.length = 300;
  }
  state.toolCache[agentId] = { count: calls.length };
}

async function refreshTicker(run) {
  const running = run.agents.filter((a) => a.status === "running");
  for (const agent of running) {
    let detail;
    try {
      detail = await api("/api/agent/" + encodeURIComponent(agent.agent_id));
    } catch (err) {
      continue;  // transient poll failure; the next cycle tries again
    }
    ingestToolCalls(agent.agent_id, agent.description || agent.agent_id, detail.tool_calls);
  }
  renderTicker();
}

function agentById(run) {
  const map = {};
  for (const a of run.agents) map[a.agent_id] = a;
  return map;
}

// Empty text and an empty status set both mean "no restriction" — a filter
// bar that starts out hiding everything would look like the dashboard broke.
function agentMatchesFilter(agent) {
  if (state.filterStatuses.size && !state.filterStatuses.has(agent.status)) return false;
  const q = state.filterText.trim().toLowerCase();
  if (!q) return true;
  const haystack = [agent.description, agent.agent_id, agent.agent_type,
    agent.model, agent.objective].filter(Boolean).join(" ").toLowerCase();
  return haystack.includes(q);
}

function renderFilterChips() {
  const box = $("filter-status");
  if (!box || !state.run) return;
  const order = ["running", "waiting", "completed", "failed", "stalled", "orphaned", "unknown"];
  const present = new Set(state.run.agents.map((a) => a.status));
  const statuses = order.filter((s) => present.has(s));
  box.innerHTML = statuses.map((s) =>
    '<button type="button" class="chip' + (state.filterStatuses.has(s) ? " active" : "") +
    '" data-status="' + s + '">' + esc(s) + "</button>").join("");
  for (const chip of box.querySelectorAll(".chip")) {
    chip.onclick = () => {
      const s = chip.dataset.status;
      if (state.filterStatuses.has(s)) state.filterStatuses.delete(s);
      else state.filterStatuses.add(s);
      renderFilterChips();
      render();
    };
  }
}

// Only shown while a filter is actually narrowing the view — at rest it
// would just duplicate the "N agents" the header already shows.
function renderFilterCount(run) {
  const el = $("filter-count");
  if (!el) return;
  const active = state.filterText.trim() || state.filterStatuses.size;
  if (!active) {
    el.textContent = "";
    return;
  }
  const shown = run.agents.filter(agentMatchesFilter).length;
  el.textContent = shown + " of " + run.agents.length;
}

// ------------------------------------------------------------ notifications
//
// The Notification global doesn't exist in every context this file runs in
// (this project's own headless-node report-render check among them) — every
// access is guarded so a missing API degrades to "no notifications," never
// a thrown error that blanks the page.

function notificationsSupported() {
  return typeof Notification !== "undefined";
}

function notify(title, body) {
  if (!notificationsSupported() || Notification.permission !== "granted") return;
  try {
    const n = new Notification(title, { body: body });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (err) { /* best-effort; never fatal to the dashboard */ }
}

function updateNotifyButton() {
  const btn = $("notify-toggle");
  if (!btn) return;
  btn.setAttribute("aria-pressed", String(state.notifyEnabled));
  btn.textContent = state.notifyEnabled ? "Notify: on" : "Notify";
}

function setStoredNotifyPref(enabled) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("orchestra-notify", enabled ? "1" : "0");
    }
  } catch (err) { /* private-browsing/blocked storage; not worth failing over */ }
}

// Fires for events that happen WHILE the dashboard is open — an agent that
// fails, a session that ends — not for state that already existed when the
// tab was opened (the Health box already shows that at a glance).
function checkNotifications(run) {
  const seeding = !state.notifySeeded;
  const currentlyFailed = new Set(
    run.agents.filter((a) => a.status === "failed").map((a) => a.agent_id));

  if (!seeding && state.notifyEnabled) {
    for (const agent of run.agents) {
      if (agent.status === "failed" && !state.knownFailedIds.has(agent.agent_id)) {
        notify("Agent failed", agent.description || agent.agent_id);
      }
    }
    if (state.lastSessionLive === true && !run.session_live) {
      const t = run.totals || {};
      notify("Workflow session ended",
        (t.completed || 0) + " completed, " + ((t.failed || 0) + (t.orphaned || 0)) + " failed");
    }
  }

  // A prompt that appears while the tab is open is exactly what a notification
  // is for; one already pending at page load is shown by the banner instead.
  const att = run.live && run.live.attention;
  const attKey = att ? att.kind + "@" + att.since : "";
  if (!seeding && state.notifyEnabled && att && attKey !== state.knownAttention &&
      att.kind !== "idle") {
    notify(attentionTitle(att), att.message || "");
  }
  state.knownAttention = attKey;

  // A newly suspected loop is worth a nudge while the tab is open: it is
  // burning tokens right now. Seeded like failures, so history stays quiet.
  const loopingNow = new Set(run.agents.filter((a) => a.loop).map((a) => a.agent_id));
  if (!seeding && state.notifyEnabled) {
    for (const agent of run.agents) {
      if (agent.loop && !state.knownLoopIds.has(agent.agent_id)) {
        notify("Possible loop", (agent.description || agent.agent_id) + ": " + loopText(agent.loop));
      }
    }
  }
  state.knownLoopIds = loopingNow;

  // Budget: announce crossing INTO warn/exceeded while the tab is open.
  const budget = run.cost && run.cost.budget ? run.cost.budget.state : "";
  if (!seeding && state.notifyEnabled && budget !== state.knownBudget &&
      (budget === "warn" || budget === "exceeded")) {
    notify(budget === "exceeded" ? "Budget exceeded" : "Budget warning",
      costText(run.cost) + " (" + Math.round(run.cost.budget.ratio * 100) + "%)");
  }
  state.knownBudget = budget;

  state.knownFailedIds = currentlyFailed;
  state.lastSessionLive = run.session_live;
  state.notifySeeded = true;
}

// ------------------------------------------------------------- copy summary

// Plain markdown, meant to be pasted straight into a PR description or a
// Slack update — no HTML, no dashboard-specific jargon a reader wouldn't
// already have from opening the linked session.
function buildSummaryMarkdown(run) {
  const t = run.totals || {};
  const failedCount = (t.failed || 0) + (t.orphaned || 0);
  const lines = [
    "## Workflow summary — " + (run.session_id || "session"),
    (t.agents || 0) + " agents · " + (t.completed || 0) + " completed · " +
      failedCount + " failed · " + (t.running || 0) + " running · " +
      fmtTokens(t.tokens) + " tokens (" + fmtPct(cacheHitRatio(t.tokens)) + " cached) · " +
      fmtDuration(t.wall_time_s) + " wall",
  ];
  if (run.cost && run.cost.enabled) {
    lines.push("Cost: " + costText(run.cost) + (run.cost.partial ? " (partial — no price for " +
      run.cost.unpriced_models.join(", ") + ")" : ""));
  }

  const trouble = run.agents.filter((a) =>
    ["waiting", "stalled", "failed", "orphaned"].includes(a.status));
  const att = run.live && run.live.attention;
  const loops = run.agents.filter((a) => a.loop);
  if (trouble.length || att || loops.length) {
    lines.push("", "### Needs attention");
    if (att) lines.push("- " + attentionTitle(att).toUpperCase() +
      (att.message ? " — " + att.message : ""));
    for (const agent of trouble) {
      lines.push("- " + agent.status.toUpperCase() + " — " + (agent.description || agent.agent_id));
    }
    for (const agent of loops) {
      lines.push("- POSSIBLE LOOP — " + (agent.description || agent.agent_id) + ": " +
        loopText(agent.loop));
    }
  }

  const conflicts = run.write_conflicts || [];
  if (conflicts.length) {
    lines.push("", "### Write conflicts");
    const byId = agentById(run);
    for (const c of conflicts) {
      const labels = c.writer_ids.map((id) => (byId[id] && byId[id].description) || id);
      lines.push("- `" + c.path + "` — written by " + labels.join(", "));
    }
  }

  return lines.join("\n");
}

async function copySummary() {
  const btn = $("copy-summary");
  if (!state.run || !btn) return;
  const text = buildSummaryMarkdown(state.liveRun || state.run);
  let ok = false;
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (err) { /* ok stays false; the button reports it below */ }
  const original = btn.textContent;
  btn.textContent = ok ? "Copied!" : "Copy failed";
  setTimeout(() => { btn.textContent = original; }, 1500);
}

function renderConflicts(run) {
  const box = $("conflicts");
  if (!box) return;
  const conflicts = run.write_conflicts || [];
  if (!conflicts.length) { box.hidden = true; return; }
  box.hidden = false;
  const byId = agentById(run);
  box.innerHTML = "<strong>" + conflicts.length +
    " file(s) written by more than one agent</strong>";
  const list = document.createElement("ul");
  for (const c of conflicts) {
    const item = document.createElement("li");
    const labels = c.writer_ids.map((id) => (byId[id] && byId[id].description) || id);
    item.textContent = c.path + " — written by " + labels.join(", ");
    item.style.cursor = "pointer";
    item.onclick = () => openDrawer(c.writer_ids[0]);
    list.appendChild(item);
  }
  box.appendChild(list);
}

function renderTicker() {
  const box = $("ticker");
  if (!box) return;
  const byId = state.run ? agentById(state.run) : {};
  const rows = state.ticker.filter((e) => {
    const agent = byId[e.agentId];
    return !agent || agentMatchesFilter(agent);
  });
  if (!rows.length) {
    box.innerHTML = '<div class="ticker-empty">' + (state.ticker.length
      ? "No activity matches the current filter."
      : "No live tool-call activity yet — this fills in while agents are running.") +
      "</div>";
    return;
  }
  box.innerHTML = rows.map((e) => {
    const [, cssVar] = toolBucket(e.name);
    return '<div class="ticker-row" data-agent="' + esc(e.agentId) + '">' +
      '<span class="ticker-time">' + esc(fmtClock(e.ts)) + "</span>" +
      '<span class="ticker-dot" style="background:var(--' + cssVar + ')"></span>' +
      '<span class="ticker-agent">' + esc(e.label) + "</span>" +
      '<span class="ticker-tool">' + esc(e.name) + "</span>" +
      '<span class="ticker-target">' + esc(e.target || "") + "</span>" +
      "</div>";
  }).join("");
  for (const row of box.querySelectorAll(".ticker-row")) {
    row.onclick = () => openDrawer(row.dataset.agent);
  }
}

// ------------------------------------------------------------ agent sprite
//
// Sprite slicing + animation is a JS port of the canvas pet engine from
// ntd4996/agentpet (MIT License — https://github.com/ntd4996/agentpet),
// including this file's own copy of that license:
//
//   MIT License
//   Copyright (c) 2026 Nguyễn Thành Đạt
//   Permission is hereby granted, free of charge, to any person obtaining a
//   copy of this software and associated documentation files (the
//   "Software"), to deal in the Software without restriction, including
//   without limitation the rights to use, copy, modify, merge, publish,
//   distribute, sublicense, and/or sell copies of the Software, and to
//   permit persons to whom the Software is furnished to do so, subject to
//   the following conditions: the above copyright notice and this
//   permission notice shall be included in all copies or substantial
//   portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT
//   WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
//   THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
//   NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
//   LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
//   OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
//   WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
// static/agent-sprite.png is that repo's own bundled sample character sheet
// (landing/sample-pet.png), reused here under the same license — served
// locally, never fetched from their CDN, so the dashboard's offline
// guarantee holds. report.py embeds it as a data: URI for the same reason
// a static report can't reference a sibling file it isn't shipped with.
//
// It's one character sheet, not a gallery — agentpet's full character
// variety lives on their CDN, which a fully offline dashboard can't reach.
// Every agent instead gets its own hue-rotated tint of the same sheet (see
// agentHue/AGENT_TINT below), so agents still read as visually distinct
// without needing more art.
const AGENT_SPRITE_URL = (typeof window !== "undefined" && window.ORCHESTRA_AGENT_SPRITE) ||
  "agent-sprite.png";
const SPRITE_COLS = 8, SPRITE_ROWS = 9, SPRITE_ALPHA_THRESHOLD = 16;
const SPRITE_REDUCED_MOTION = typeof window !== "undefined" && window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Contiguous runs of `true` in an occupancy array → [start, end) pairs.
function spriteSegments(occ) {
  const out = [];
  let start = -1;
  for (let i = 0; i < occ.length; i++) {
    if (occ[i] && start < 0) start = i;
    else if (!occ[i] && start >= 0) { out.push([start, i]); start = -1; }
  }
  if (start >= 0) out.push([start, occ.length]);
  return out;
}

// Alpha-gutter slice: rows by transparent bands, then frames within each
// row, so a sheet with a different frame count per row (this one: 6, 8, 8,
// 4, 5, 8, 6, 6, 6) still slices correctly instead of assuming a fixed grid.
function sliceSpriteSheet(img) {
  const w = img.naturalWidth, h = img.naturalHeight;
  if (!w || !h) return [];
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d", { willReadFrequently: true });
  if (!ctx) return [];
  ctx.drawImage(img, 0, 0);
  let data;
  try {
    data = ctx.getImageData(0, 0, w, h).data;
  } catch (err) {
    return []; // caller falls back to a fixed SPRITE_COLS x SPRITE_ROWS grid
  }
  const rowHas = new Uint8Array(h);
  for (let y = 0; y < h; y++) {
    const off = y * w * 4;
    for (let x = 0; x < w; x++) {
      if (data[off + x * 4 + 3] > SPRITE_ALPHA_THRESHOLD) { rowHas[y] = 1; break; }
    }
  }
  const clips = [];
  for (const [y0, y1] of spriteSegments(rowHas)) {
    const colHas = new Uint8Array(w);
    for (let y = y0; y < y1; y++) {
      const off = y * w * 4;
      for (let x = 0; x < w; x++) {
        if (data[off + x * 4 + 3] > SPRITE_ALPHA_THRESHOLD) colHas[x] = 1;
      }
    }
    const clip = spriteSegments(colHas).map(([x0, x1]) => ({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 }));
    if (clip.length) clips.push(clip);
  }
  return clips;
}

// One decode + slice, shared by every agent card instead of one per agent.
class AgentSpriteSheet {
  constructor(url) {
    this.img = null;
    this.clips = [];
    this.clipMaxW = [];
    this._onReady = [];
    // No Image constructor (e.g. the report-renderer's headless DOM stub in
    // tests, which deliberately exposes only what it wires up): stay blank
    // forever rather than throw during module load, same as a real failed
    // image load would leave `img` unset below.
    if (typeof Image === "undefined") return;
    const img = new Image();
    img.onload = () => {
      this.img = img;
      try { this.clips = sliceSpriteSheet(img); } catch (err) { this.clips = []; }
      this.clipMaxW = this.clips.map((clip) => Math.max(...clip.map((r) => r.w)));
      for (const cb of this._onReady) cb();
      this._onReady = [];
    };
    img.onerror = () => { this._onReady = []; }; // stays blank; draw() no-ops
    img.src = url;
  }
  onReady(cb) {
    if (this.img) cb(); else this._onReady.push(cb);
  }
  clipFor(row) {
    return this.clips.length ? this.clips[Math.min(row, this.clips.length - 1)] : null;
  }
}

const agentSpriteSheet = new AgentSpriteSheet(AGENT_SPRITE_URL);

// Row + fps per Orchestra status, the same idea as agentpet's STATE_ROW /
// STATE_FPS mapped onto the six statuses Orchestra actually has. Sheet rows,
// top to bottom: 0 Idle, 1 RunRight, 2 RunLeft, 3 Waving, 4 Jumping,
// 5 Failed, 6 Waiting, 7 Running, 8 Review.
const AGENT_SPRITE_STATE = {
  running: { row: 7, fps: 8 },
  completed: { row: 3, fps: 3 },
  failed: { row: 5, fps: 3 },
  stalled: { row: 6, fps: 4 },
  waiting: { row: 6, fps: 3 },
  orphaned: { row: 0, fps: 2 },
  unknown: { row: 0, fps: 2 },
};
// A brief jumping burst the moment an agent finishes — agentpet's "celebrate"
// mood, fired once on the transition into "completed," not on every render.
const AGENT_CELEBRATE_STATE = { row: 4, fps: 8 };
const AGENT_CELEBRATE_MS = 3000;

// A stable hue per agent, purely a function of its id so the same agent
// keeps the same tint across every re-render instead of flickering colors.
function agentHue(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h % 360;
}

class AgentSprite {
  constructor(canvas, sheet) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.ctx.imageSmoothingEnabled = false;
    this.sheet = sheet;
    this.row = 0;
    this.fps = 3;
    this.frame = 0;
    this.lastTick = 0;
    this.stopped = false;
    sheet.onReady(() => { if (!this.stopped) this.draw(); });
    this._raf = requestAnimationFrame((t) => this.loop(t));
  }
  setState(row, fps) {
    if (row !== this.row) { this.row = row; this.frame = 0; }
    this.fps = fps;
    if (this.sheet.img) this.draw();
  }
  stop() {
    this.stopped = true;
    cancelAnimationFrame(this._raf);
  }
  loop(t) {
    if (this.stopped) return;
    // Reduced motion: hold the current frame (still the right pose for the
    // status) instead of cycling — motion stops, the state stays legible.
    if (!SPRITE_REDUCED_MOTION && this.sheet.img && t - this.lastTick > 1000 / this.fps) {
      this.lastTick = t;
      this.frame++;
      this.draw();
    }
    this._raf = requestAnimationFrame((n) => this.loop(n));
  }
  draw() {
    const ctx = this.ctx;
    const W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0, 0, W, H);
    const img = this.sheet.img;
    if (!img) return;
    const clip = this.sheet.clipFor(this.row);
    let r, scaleW;
    if (clip) {
      r = clip[this.frame % clip.length];
      scaleW = this.sheet.clipMaxW[Math.min(this.row, this.sheet.clips.length - 1)] || r.w;
    } else {
      const fw = img.naturalWidth / SPRITE_COLS, fh = img.naturalHeight / SPRITE_ROWS;
      if (!fw || !fh) return;
      r = { x: (this.frame % SPRITE_COLS) * fw, y: Math.min(this.row, SPRITE_ROWS - 1) * fh, w: fw, h: fh };
      scaleW = fw;
    }
    // Integer scale keeps pixel art crisp; anchored bottom-center so every
    // frame's feet stay put even as frame widths vary within a row.
    const fit = Math.min(W / scaleW, H / r.h);
    const s = fit >= 1 ? Math.floor(fit) : fit;
    const dw = r.w * s, dh = r.h * s;
    ctx.drawImage(img, r.x, r.y, r.w, r.h, (W - dw) / 2, H - dh, dw, dh);
  }
}

function agentTokenTotal(tokens) {
  return Object.values(tokens || {}).reduce((a, b) => a + b, 0);
}

function stopAgentSprites() {
  for (const sprite of Object.values(state.agentSprites)) sprite.stop();
  state.agentSprites = {};
}

// The agent's own subagent type ("general-purpose", "code-reviewer",
// "Explore", "honeycomb:honeycomb-investigator", ...), turned into a section
// label: last segment of a namespaced type, hyphens/underscores as spaces,
// title case. Falls back to "Ungrouped" for an inline launch with no type.
function humanizeAgentType(agentType) {
  const raw = (agentType || "").trim();
  if (!raw) return "Ungrouped";
  const last = raw.includes(":") ? raw.slice(raw.lastIndexOf(":") + 1) : raw;
  return last.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function renderWorkfloor(run) {
  const box = $("workfloor");
  if (!box) return;
  stopAgentSprites();
  if (!run.agents.length) {
    box.innerHTML = '<div class="ticker-empty">No agents in this session yet.</div>';
    return;
  }
  const now = Date.now() / 1000;
  const seeded = state.floorSeeded;
  const nextActivity = {};
  const spriteStates = {}; // agent_id -> {row, fps}, resolved here so the DOM pass below just wires canvases

  // Group by role (agent_type), each group its own floor section, sorted by
  // label — "Ungrouped" (inline launches with no declared type) always last,
  // since it's a catch-all rather than a real role.
  const groups = new Map(); // label -> agents[]
  for (const agent of run.agents) {
    const label = humanizeAgentType(agent.agent_type);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(agent);
  }
  const groupLabels = Array.from(groups.keys()).sort((a, b) => {
    if (a === "Ungrouped") return 1;
    if (b === "Ungrouped") return -1;
    return a.localeCompare(b);
  });

  function renderAgentCard(agent) {
    const id = agent.agent_id;
    const status = agent.status || "unknown";
    const toolCount = agent.tool_call_count || 0;
    const tokenTotal = agentTokenTotal(agent.tokens);
    const prev = state.floorActivity[id];
    // Only a real increase since a *previous* render counts as activity —
    // an agent's first appearance (tab just opened, or it just spawned) is
    // history/creation, not something that "just happened."
    const pulse = seeded && prev &&
      (toolCount > prev.toolCount || tokenTotal > prev.tokenTotal);
    nextActivity[id] = { toolCount, tokenTotal };

    // A live transition into "completed" (not just being completed already
    // when the tab opens) earns a one-shot celebrate burst.
    const prevStatus = state.agentPrevStatus[id];
    if (seeded && prevStatus && prevStatus !== "completed" && status === "completed") {
      state.agentCelebrateUntil[id] = Date.now() + AGENT_CELEBRATE_MS;
    }
    state.agentPrevStatus[id] = status;
    const celebrating = (state.agentCelebrateUntil[id] || 0) > Date.now();
    spriteStates[id] = celebrating ? AGENT_CELEBRATE_STATE : (AGENT_SPRITE_STATE[status] || AGENT_SPRITE_STATE.unknown);

    // Same "open round" test Timeline uses for its dashed bar-open bars:
    // stalled and orphaned agents have gone quiet, but their round never
    // formally ended, so they still deserve a ticking clock, not a "—".
    const live = agent.started_at !== null && agent.ended_at === null;
    // A static report has no "now" — ticking against the viewer's wall
    // clock would make an old snapshot claim a stale run is still live.
    // last_activity_at is the report's own idea of "as of," already baked
    // into the payload, same fallback Timeline uses for an open bar's end.
    const asOf = state.offline ? (agent.last_activity_at || agent.started_at) : now;
    const label = agent.description || agent.agent_id;
    const clockText = live ? fmtDuration(asOf - agent.started_at) : fmtDuration(agent.duration_s);
    const ariaLabel = label + ", " + status + ", " + fmtTokens(agent.tokens) + " tokens, " +
      (live ? clockText + " so far" : clockText + " total");
    const quiet = status === "orphaned" || status === "unknown";

    return '<div class="agent-card' + (agentMatchesFilter(agent) ? "" : " agent-dim") +
      (quiet ? " agent-quiet" : "") + '" data-agent="' + esc(id) + '"' +
      (live && !state.offline ? ' data-live="1" data-started="' + agent.started_at + '"' : "") +
      ' role="group" tabindex="0" aria-label="' + esc(ariaLabel) + '">' +
      '<div class="agent-stage' + (pulse ? " pulse" : "") + '">' +
        '<canvas class="agent-canvas bob" data-agent="' + esc(id) + '" width="60" height="68"></canvas>' +
      '</div>' +
      '<div class="agent-name" title="' + esc(label) + '">' + esc(label) + '</div>' +
      '<div class="agent-meta">' + esc(agent.agent_type + " · " + fmtModelShort(agent.model)) + '</div>' +
      '<div class="agent-status s-' + status + '">' + esc(status) + '</div>' +
      '<div class="agent-clock">' + esc(clockText) + '</div>' +
      '<div class="agent-tokens" title="' + esc(fmtTokenMix(agent.tokens)) + '">' +
        esc(fmtTokens(agent.tokens)) + ' tok</div>' +
    '</div>';
  }

  box.innerHTML = groupLabels.map((label) => {
    const agents = groups.get(label);
    const groupTokens = agents.reduce((sum, a) => sum + agentTokenTotal(a.tokens), 0);
    return '<section class="floor-group">' +
      '<div class="floor-group-header"><h3>' + esc(label) + '</h3>' +
        '<span class="floor-group-meta">' + agents.length +
        (agents.length === 1 ? " agent · " : " agents · ") +
        esc(fmtCount(groupTokens)) + ' tok</span></div>' +
      '<div class="floor-group-grid">' + agents.map(renderAgentCard).join("") + '</div>' +
    '</section>';
  }).join("");

  state.floorActivity = nextActivity;
  state.floorSeeded = true;

  for (const canvas of box.querySelectorAll(".agent-canvas")) {
    const id = canvas.dataset.agent;
    const sprite = new AgentSprite(canvas, agentSpriteSheet);
    sprite.setState(spriteStates[id].row, spriteStates[id].fps);
    // Same character, a unique per-agent hue so a crowded floor still reads
    // as individuals rather than one sprite copy-pasted everywhere.
    canvas.style.filter = "hue-rotate(" + agentHue(id) + "deg)";
    state.agentSprites[id] = sprite;
  }
  for (const card of box.querySelectorAll(".agent-card")) {
    card.onclick = () => openDrawer(card.dataset.agent);
    card.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDrawer(card.dataset.agent);
      }
    };
  }
}

// Elapsed time for a still-running agent should visibly tick like Claude
// Code's own live timer, without waiting on the 2s data poll for it — a
// cheap local interval that only touches DOM text, never re-renders.
function tickAgentClocks() {
  if (state.view !== "workfloor" || state.offline) return;
  const now = Date.now() / 1000;
  for (const card of document.querySelectorAll('.agent-card[data-live="1"]')) {
    const started = parseFloat(card.dataset.started);
    if (isNaN(started)) continue;
    const clockEl = card.querySelector(".agent-clock");
    if (clockEl) clockEl.textContent = fmtDuration(now - started);
  }
}

function svgEl(name, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

// ------------------------------------------------------------ text fitting
//
// SVG has no text-overflow: ellipsis, and character-count truncation lies as
// soon as a label mixes wide and narrow glyphs. Measuring against a real
// canvas context is the only way to guarantee a label never crosses the
// boundary it's drawn against.

let measureCtx = null;
function bodyFont(px) {
  // getComputedStyle is absent in some minimal JS execution contexts (this
  // project's own headless-node report-render check among them) — fall back
  // to a generic stack rather than throw and blank the whole report.
  const family = typeof getComputedStyle === "function"
    ? getComputedStyle(document.body).fontFamily
    : "sans-serif";
  return px + "px " + family;
}
function textWidth(text, font) {
  if (measureCtx === null) {
    const canvas = document.createElement("canvas");
    measureCtx = (canvas.getContext && canvas.getContext("2d")) || false;
  }
  if (!measureCtx) {
    // No canvas 2D context (the project's own headless-node report-render
    // check, e.g.) — a rough per-character estimate beats throwing.
    const px = parseInt(font, 10) || 11;
    return text.length * px * 0.55;
  }
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}
function fitText(text, maxWidth, font) {
  if (textWidth(text, font) <= maxWidth) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (textWidth(text.slice(0, mid) + "…", font) <= maxWidth) lo = mid;
    else hi = mid - 1;
  }
  return lo > 0 ? text.slice(0, lo) + "…" : "…";
}

// ---------------------------------------------------------------- header

function renderHeader(run) {
  const t = run.totals;
  $("totals").innerHTML = "";
  const parts = [
    ["agents", t.agents],
    ["running", t.running],
    ...(t.waiting ? [["waiting", t.waiting]] : []),
    ["done", t.completed],
    ["failed", t.failed + t.orphaned],
    ["tokens", run.replay_at !== undefined ? "\u2014" : fmtTokens(t.tokens)],
    ["cached", run.replay_at !== undefined ? "\u2014" : fmtPct(cacheHitRatio(t.tokens))],
    ["wall", fmtDuration(t.wall_time_s)],
  ];
  if (run.orchestrator) {
    // The per-agent "tokens" above exclude the orchestrator; say so, don't hide it.
    parts.splice(parts.length - 2, 0, ["orchestrator", fmtTokens(run.orchestrator.tokens)]);
  }
  for (const [label, value] of parts) {
    const span = document.createElement("span");
    span.innerHTML = "<strong>" + value + "</strong> " + label;
    $("totals").appendChild(span);
  }
  renderCostPart(run);
  $("conn").textContent = run.session_live ? "" : "session ended";
}

function costText(cost) {
  const total = fmtMoney(cost.total, cost.currency);
  const b = cost.budget;
  return total + (b ? " / " + fmtMoney(b.limit, cost.currency) : "");
}

function renderCostPart(run) {
  const cost = run.cost;
  if (!cost) return;
  const span = document.createElement("span");
  if (cost.enabled) {
    const strong = document.createElement("strong");
    strong.textContent = costText(cost);
    span.appendChild(strong);
    const label = document.createElement("span");
    label.textContent = cost.partial ? " cost (partial)" : " cost";
    span.appendChild(label);
    if (cost.budget && cost.budget.state !== "ok") span.className = "cost-" + cost.budget.state;
    const notes = [];
    if (cost.partial) notes.push("No price for: " + cost.unpriced_models.join(", "));
    if (cost.budget) notes.push(Math.round(cost.budget.ratio * 100) + "% of budget");
    notes.push("agents " + fmtMoney(cost.agents, cost.currency) +
      " + orchestrator " + fmtMoney(cost.orchestrator, cost.currency));
    span.title = notes.join(" · ");
  } else if (cost.error) {
    span.className = "cost-warn";
    span.textContent = cost.error;
  } else {
    return;     // no price file: tokens only, as documented
  }
  $("totals").appendChild(span);
}

function renderHealth(run) {
  const items = [];
  for (const agent of run.agents) {
    const label = agent.description || agent.agent_id;
    if (["waiting", "stalled", "failed", "orphaned"].includes(agent.status)) {
      items.push({ id: agent.agent_id, text: agent.status.toUpperCase() + " — " + label });
    }
    if (agent.loop) {
      items.push({ id: agent.agent_id,
        text: "POSSIBLE LOOP — " + label + ": " + loopText(agent.loop) });
    }
  }
  const box = $("health");
  if (!items.length) { box.hidden = true; return; }
  box.hidden = false;
  const distinct = new Set(items.map((i) => i.id)).size;
  box.innerHTML = "<strong>" + distinct + " agent(s) need attention</strong>";
  const list = document.createElement("ul");
  for (const entry of items) {
    const item = document.createElement("li");
    item.textContent = entry.text;
    item.style.cursor = "pointer";
    item.onclick = () => openDrawer(entry.id);
    list.appendChild(item);
  }
  box.appendChild(list);
}

function attentionTitle(att) {
  if (att.kind === "permission") return "Waiting for your permission";
  if (att.kind === "input") return "Waiting for your input";
  if (att.kind === "idle") return "Idle — waiting for your next prompt";
  if (att.kind === "error") {
    return "API error" + (att.error_type ? ": " + att.error_type : "");
  }
  return att.kind;
}

// What the session is blocked on, straight from hook events. Built with
// textContent, never innerHTML: the message is text from outside this page.
function renderAttention(run) {
  const box = $("attention");
  if (!box) return;
  const live = run.live;
  let kind = "";
  let title = "";
  let detail = "";
  let since = null;
  if (live && live.attention) {
    kind = live.attention.kind;
    title = attentionTitle(live.attention);
    detail = live.attention.message || "";
    since = live.attention.since;
  } else if (live && live.ended && !run.session_live) {
    kind = "ended";
    title = "Session ended" + (live.ended.reason ? " (" + live.ended.reason + ")" : "");
    since = live.ended.at;
  }
  if (!kind) { box.hidden = true; return; }
  box.hidden = false;
  box.setAttribute("data-kind", kind);
  box.setAttribute("role", kind === "permission" || kind === "error" ? "alert" : "status");
  box.textContent = "";
  const add = (cls, text) => {
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    box.appendChild(span);
  };
  add("att-title", title);
  if (detail) add("att-detail", detail);
  if (since !== null && !state.offline) {
    add("att-since", fmtDuration(Math.max(0, Date.now() / 1000 - since)) + " ago");
  }
}

function renderDiagnostics(run) {
  const d = run.diagnostics || {};
  const bad = (d.unparsable_lines || 0);
  $("diagnostics").textContent = bad
    ? bad + " transcript line(s) could not be parsed; the view may be incomplete."
    : "";
}

// -------------------------------------------------------------- timeline

const ROW_H = 32;
const LEFT = 200;
const PAD = 16;
const DOT_R = 4;
const LABEL_X = 14;

function timeWindow(run) {
  // A replay scrubs along a FIXED axis (the whole run), so bars grow across a
  // stable scale instead of the scale itself rescaling on every frame.
  if (run.replay_window) return run.replay_window;
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
  const labelFont = bodyFont(11);
  const metaFont = bodyFont(10);
  const labelMax = LEFT - LABEL_X - DOT_R * 2 - 14;

  // Zebra striping first, fully behind everything, so long rows stay easy to
  // track from the label to the bar without counting rows.
  agents.forEach((agent, i) => {
    if (i % 2 === 0) return;
    svg.appendChild(svgEl("rect", {
      x: 0, y: PAD + i * ROW_H, width, height: ROW_H, class: "row-alt",
    }));
  });

  // Batch bands sit above the stripes: they are what shows a parallel wave.
  (run.batches || []).forEach((batch, bi) => {
    const rows = batch.agent_ids.map((id) => rowOf[id]).filter((r) => r !== undefined);
    if (rows.length < 2) return;
    const top = PAD + Math.min(...rows) * ROW_H;
    const tall = (Math.max(...rows) - Math.min(...rows) + 1) * ROW_H;
    svg.appendChild(svgEl("rect", {
      x: LEFT - 4, y: top, width: plot + 8, height: tall, class: "batch-band",
      rx: 4,
    }));
    svg.appendChild(svgEl("text", {
      x: LEFT + 2, y: top - 3, class: "batch-label",
    }, "batch " + (bi + 1)));
  });

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
    const row = svgEl("g", { class: "row" + (agentMatchesFilter(agent) ? "" : " row-dim") });

    // A full-width hit target behind the row content: hover feedback should
    // not depend on landing the cursor on a 3px-wide bar.
    row.appendChild(svgEl("rect", {
      x: 0, y, width, height: ROW_H, class: "row-bg",
    }));

    row.appendChild(svgEl("circle", {
      cx: LABEL_X + DOT_R, cy: y + ROW_H / 2, r: DOT_R,
      class: "row-dot s-" + agent.status,
    }));
    if (agent.status === "running") {
      row.appendChild(svgEl("circle", {
        cx: LABEL_X + DOT_R, cy: y + ROW_H / 2, r: DOT_R, class: "row-dot-ping",
      }));
    }
    row.appendChild(svgEl("title", {}, agent.status));

    const labelX = LABEL_X + DOT_R * 2 + 6;
    row.appendChild(svgEl("text", { x: labelX, y: y + 14, class: "row-label" },
      fitText(label, labelMax, labelFont)));
    row.appendChild(svgEl("text", { x: labelX, y: y + 25, class: "row-meta" },
      fitText(agent.agent_type + " · " + fmtModelShort(agent.model), labelMax, metaFont)));

    const rounds = agent.rounds.length ? agent.rounds
      : [{ started_at: agent.started_at, ended_at: agent.ended_at }];
    for (const round of rounds) {
      const start = round.started_at !== null ? round.started_at : t0;
      const end = round.ended_at !== null ? round.ended_at
        : (agent.last_activity_at || t1);
      const bx = x(start);
      const bw = Math.max(4, x(Math.max(end, start)) - bx);
      const bar = svgEl("rect", {
        x: bx, y: y + 8, width: bw, height: ROW_H - 16, rx: 3,
        class: "bar s-" + agent.status + (round.ended_at === null ? " bar-open" : ""),
      });
      bar.appendChild(svgEl("title", {},
        label + " — " + agent.status + " — " + fmtDuration(agent.duration_s)));
      row.appendChild(bar);
    }
    row.onclick = () => openDrawer(agent.agent_id);
    svg.appendChild(row);
  });
}

// ------------------------------------------------------------ view state

function setView(view) {
  // Leaving Work Floor: stop every mounted sprite's rAF loop rather than let
  // it keep animating an off-screen, hidden canvas indefinitely.
  if (state.view === "workfloor" && view !== "workfloor") stopAgentSprites();
  state.view = view;
  $("view-timeline").hidden = view !== "timeline";
  $("view-graph").hidden = view !== "graph";
  $("view-activity").hidden = view !== "activity";
  $("view-workfloor").hidden = view !== "workfloor";
  $("view-fleet").hidden = view !== "fleet";
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  render();
  // Refresh immediately on switching in, rather than waiting up to POLL_MS
  // for the next cycle to notice the tab is now visible.
  if (view === "activity" && state.run) refreshTicker(state.run);
}

function render() {
  if (!state.run) return;
  renderHeader(state.run);
  updateChrome();
  renderAttention(state.run);
  renderHealth(state.run);
  renderConflicts(state.run);
  renderFilterChips();
  renderFilterCount(state.run);
  renderDiagnostics(state.run);
  if (state.view === "timeline") renderTimeline(state.run);
  else if (state.view === "graph") renderGraph(state.run);
  else if (state.view === "workfloor") renderWorkfloor(state.run);
  else if (state.view === "fleet") renderFleet();
  else renderTicker();
}

function startPolling() {
  state.generation += 1;
  poll(state.generation);
}

async function poll(generation) {
  // A timer scheduled by a loop that has since been superseded must not fetch.
  if (generation !== state.generation) return;
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
  state.liveRun = run;
  state.run = state.replay.on ? deriveRunAt(run, replayClamp(state.replay.t, run)) : run;
  state.backoff = state.streamLive ? STREAM_POLL_MS : POLL_MS;
  $("conn").textContent = run.session_live ? "" : "session ended";
  checkNotifications(run);
  checkSounds(run);
  render();
  // Only actively poll agent detail while the tab showing it is open, so
  // watching Timeline/Graph never costs N extra per-agent fetches.
  if (state.view === "activity") refreshTicker(run);
  if (state.live) setTimeout(() => poll(generation), state.backoff);
}

// ------------------------------------------------------------------ sounds
//
// Synthesized with Web Audio: no audio files, so nothing to fetch (the no-egress
// guarantee stands) and nothing to license. Off until the user clicks Sound,
// which is also the user gesture browsers require before a page may make noise.
//
// Three sounds, by what a person should do about it:
//   fail   something broke (API error, an agent failed)   -> look now
//   alert  Claude is waiting on you                       -> act now
//   done   everything that was running has finished       -> optional
// At most one plays per update (the most important), and never twice in 1.5 s.

const SOUND_PRIORITY = { fail: 3, alert: 2, done: 1 };
const SOUND_NOTES = {            // [frequency Hz, start offset s]
  alert: [[660, 0], [880, 0.14]],
  fail: [[330, 0], [220, 0.18]],
  done: [[523, 0], [659, 0.11], [784, 0.22]],
};

function topSound(names) {
  let best = null;
  for (const name of names) {
    if (!best || SOUND_PRIORITY[name] > SOUND_PRIORITY[best]) best = name;
  }
  return best;
}

// What should sound, given this poll of the viewed session? `memo` is the
// baseline from the previous poll; the first call only records it.
function computeSounds(run, memo) {
  const att = run.live && run.live.attention;
  const attKey = att && att.kind !== "idle" ? att.kind + "@" + att.since : "";
  const failed = new Set(run.agents
    .filter((a) => a.status === "failed").map((a) => a.agent_id));
  const running = run.totals ? run.totals.running + (run.totals.waiting || 0) : 0;
  const budget = run.cost && run.cost.budget ? run.cost.budget.state : "";
  const out = [];
  if (memo.seeded) {
    if (budget === "exceeded" && memo.budget !== "exceeded") out.push("alert");
    if (attKey && attKey !== memo.attKey) out.push(att.kind === "error" ? "fail" : "alert");
    for (const id of failed) if (!memo.failed.has(id)) out.push("fail");
    if (memo.running > 0 && running === 0 && failed.size === 0) out.push("done");
  }
  memo.seeded = true;
  memo.attKey = attKey;
  memo.failed = failed;
  memo.running = running;
  memo.budget = budget;
  return out;
}

// Other sessions: a prompt or error in one you are not looking at.
function computeFleetSounds(fleet, viewing, memo) {
  const next = {};
  const out = [];
  for (const s of fleet.sessions) {
    const att = s.attention && s.session_live && s.urgency > 0 ? s.attention : null;
    if (!att) continue;
    const key = att.kind + "@" + att.since;
    next[s.session_id] = key;
    if (memo.seeded && s.session_id !== viewing && memo.keys[s.session_id] !== key) {
      out.push(att.kind === "error" ? "fail" : "alert");
    }
  }
  memo.seeded = true;
  memo.keys = next;
  return out;
}

function audioContext() {
  if (state.audio) return state.audio;
  const Ctor = typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext);
  if (!Ctor) return null;
  try { state.audio = new Ctor(); } catch (err) { return null; }
  return state.audio;
}

function playSound(name) {
  const ctx = state.soundEnabled ? audioContext() : null;
  const notes = SOUND_NOTES[name];
  if (!ctx || !notes) return;
  const now = Date.now();
  if (now - state.lastSoundAt < 1500) return;
  state.lastSoundAt = now;
  if (ctx.state === "suspended" && typeof ctx.resume === "function") ctx.resume();
  for (const [freq, offset] of notes) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    const t = ctx.currentTime + offset;
    osc.type = "sine";
    osc.frequency.value = freq;
    // A short attack and release: a bare square edge clicks.
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(t);
    osc.stop(t + 0.25);
  }
}

function checkSounds(run) {
  if (!state.soundMemo) state.soundMemo = { seeded: false, attKey: "", failed: new Set(), running: 0 };
  const names = computeSounds(run, state.soundMemo);
  if (state.soundEnabled && names.length) playSound(topSound(names));
}

function checkFleetSounds(fleet) {
  if (!state.soundFleetMemo) state.soundFleetMemo = { seeded: false, keys: {} };
  const viewing = state.run && state.run.session_id;
  const names = computeFleetSounds(fleet, viewing, state.soundFleetMemo);
  if (state.soundEnabled && names.length) playSound(topSound(names));
}

function updateSoundButton() {
  const btn = $("sound-toggle");
  if (!btn) return;
  btn.setAttribute("aria-pressed", String(state.soundEnabled));
  btn.textContent = state.soundEnabled ? "Sound: on" : "Sound";
}

// Exports come from the server (one implementation of the format, the same
// scrubbed data the dashboard shows), so a static report has nothing to call.
function downloadExport(format) {
  const session = state.sessionId ? "&session=" + encodeURIComponent(state.sessionId) : "";
  const link = document.createElement("a");
  link.href = "/api/export?format=" + encodeURIComponent(format) +
    "&k=" + encodeURIComponent(TOKEN) + session;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function setStoredSoundPref(enabled) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("orchestra-sound", enabled ? "1" : "0");
    }
  } catch (err) { /* blocked storage; not worth failing over */ }
}

// ------------------------------------------------------------------ replay
//
// "What did this run look like at 02:13?" Rebuilt purely from the round start
// and end times already in the payload, so it works live AND in a static report
// shared after the fact (a post-mortem needs no server).
//
// What it can know: who had launched, who was still in a round, who had ended
// and how. What it cannot know: stalled / waiting / orphaned (those depend on
// the clock and on hook events of that moment) and token totals at that moment.
// So an open round replays as "running", and tokens are left out rather than
// shown as final numbers that would be wrong.

function replayBounds(run) {
  if (!run || run.started_at === null || run.started_at === undefined) return null;
  const times = [];
  for (const a of run.agents) {
    if (a.ended_at !== null && a.ended_at !== undefined) times.push(a.ended_at);
    if (a.last_activity_at) times.push(a.last_activity_at);
  }
  if (run.ended_at) times.push(run.ended_at);
  const end = times.length ? Math.max.apply(null, times) : run.started_at;
  return { start: run.started_at, end: Math.max(end, run.started_at) };
}

function agentAt(agent, t) {
  const rounds = (agent.rounds || []).filter((r) => r.started_at !== null &&
    r.started_at !== undefined && r.started_at <= t);
  if (!rounds.length) return null;                     // not launched yet
  const open = rounds.some((r) => r.ended_at === null || r.ended_at === undefined ||
    r.ended_at > t);
  const closed = rounds.filter((r) => r.ended_at !== null && r.ended_at !== undefined &&
    r.ended_at <= t);
  let status = "running";
  if (!open) {
    status = closed.length ? closed[closed.length - 1].status : "unknown";
  }
  const ended = open ? null : Math.max.apply(null, closed.map((r) => r.ended_at));
  const started = Math.min.apply(null, rounds.map((r) => r.started_at));
  return Object.assign({}, agent, {
    status: status,
    started_at: started,
    ended_at: ended,
    duration_s: ended === null ? null : ended - started,
    rounds: rounds.map((r) => ({
      started_at: r.started_at,
      ended_at: r.ended_at !== null && r.ended_at !== undefined && r.ended_at <= t ? r.ended_at : null,
      status: r.ended_at !== null && r.ended_at !== undefined && r.ended_at <= t ? r.status : "running",
    })),
    // An open agent has been going at least until t; this is what the bar is
    // drawn to. A finished one keeps its own last activity.
    last_activity_at: open ? t : Math.min(agent.last_activity_at || ended, t),
    tokens: {}, cost: null, loop: null, tool_call_count: 0, files_written_count: 0,
  });
}

function deriveRunAt(run, t) {
  const agents = run.agents.map((a) => agentAt(a, t)).filter((a) => a !== null);
  const present = new Set(agents.map((a) => a.agent_id));
  present.add("main");
  const counts = { agents: agents.length, running: 0, waiting: 0, completed: 0,
    failed: 0, stalled: 0, orphaned: 0, unknown: 0, tokens: {}, wall_time_s: null };
  for (const a of agents) counts[a.status] = (counts[a.status] || 0) + 1;
  const ends = agents.map((a) => a.ended_at).filter((e) => e !== null);
  const starts = agents.map((a) => a.started_at);
  if (starts.length) {
    counts.wall_time_s = (ends.length ? Math.max.apply(null, ends) : t) - Math.min.apply(null, starts);
  }
  return Object.assign({}, run, {
    agents: agents,
    totals: counts,
    edges: run.edges.filter((e) => present.has(e.src) && present.has(e.dst)),
    batches: run.batches.map((b) => Object.assign({}, b, {
      agent_ids: b.agent_ids.filter((id) => present.has(id)) })).filter((b) => b.agent_ids.length),
    hub_files: [], write_conflicts: [],
    live: null, cost: null, orchestrator: null,
    session_live: t < replayBounds(run).end,
    ended_at: t >= replayBounds(run).end ? run.ended_at : null,
    replay_at: t,
    replay_window: [replayBounds(run).start,
      Math.max(replayBounds(run).end, replayBounds(run).start + 1)],
  });
}

// Replay controls. state.run is what the views draw; during a replay it is
// deriveRunAt(state.liveRun, t), and everything else (notifications, sounds,
// the pill, copy-summary) keeps reading the true live run.

function replayClamp(t, run) {
  const b = replayBounds(run);
  if (!b) return t;
  return Math.min(Math.max(t, b.start), b.end);
}

function fmtOffset(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n) => (n < 10 ? "0" : "") + n;
  return (h ? h + ":" + pad(m) : m) + ":" + pad(s % 60);
}

function setReplayTime(t) {
  const base = state.liveRun;
  if (!base) return;
  state.replay.t = replayClamp(t, base);
  state.run = deriveRunAt(base, state.replay.t);
  updateReplayBar();
  render();
}

function stopReplayTimer() {
  if (state.replay.timer) { clearInterval(state.replay.timer); state.replay.timer = null; }
  state.replay.playing = false;
}

function playReplay() {
  const b = replayBounds(state.liveRun);
  if (!b) return;
  if (state.replay.t >= b.end) setReplayTime(b.start);      // play again from the top
  state.replay.playing = true;
  updateReplayBar();
  state.replay.timer = setInterval(() => {
    const bounds = replayBounds(state.liveRun);
    if (!bounds) { stopReplayTimer(); return; }
    const next = state.replay.t + state.replay.speed * 0.1;
    if (next >= bounds.end) {
      setReplayTime(bounds.end);
      stopReplayTimer();
      updateReplayBar();
    } else {
      setReplayTime(next);
    }
  }, 100);
}

function updateReplayBar() {
  const bar = $("replay-bar");
  const b = replayBounds(state.liveRun);
  if (!bar || !b || bar.hidden) return;
  const refs = state.replayRefs;
  if (!refs) return;
  refs.slider.min = String(b.start);
  refs.slider.max = String(b.end);
  refs.slider.value = String(state.replay.t);
  refs.play.textContent = state.replay.playing ? "Pause" : "Play";
  refs.time.textContent = fmtOffset(state.replay.t - b.start) + " / " + fmtOffset(b.end - b.start);
}

function buildReplayBar() {
  const bar = $("replay-bar");
  bar.textContent = "";
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const play = make("button", "", "Play");
  play.type = "button";
  play.onclick = () => (state.replay.playing ? (stopReplayTimer(), updateReplayBar()) : playReplay());
  const slider = make("input");
  slider.type = "range";
  slider.step = "1";
  slider.setAttribute("aria-label", "Replay position");
  slider.oninput = () => { stopReplayTimer(); setReplayTime(Number(slider.value)); };
  const time = make("span", "replay-time", "");
  const speed = make("select");
  speed.setAttribute("aria-label", "Replay speed");
  for (const x of [1, 10, 30, 60, 120]) {
    const option = make("option", "", x + "×");
    option.value = String(x);
    speed.appendChild(option);
  }
  speed.value = String(state.replay.speed);
  speed.onchange = () => { state.replay.speed = Number(speed.value); };
  const note = make("span", "replay-note",
    "Statuses are reconstructed from start/end times; token totals are not shown.");
  for (const el of [play, slider, time, speed, note]) bar.appendChild(el);
  state.replayRefs = { play: play, slider: slider, time: time };
}

function toggleReplay() {
  const bar = $("replay-bar");
  const btn = $("replay-toggle");
  if (!bar || !btn) return;
  if (state.replay.on) {
    stopReplayTimer();
    state.replay.on = false;
    bar.hidden = true;
    btn.setAttribute("aria-pressed", "false");
    state.run = state.liveRun;
    render();
    return;
  }
  const b = replayBounds(state.liveRun);
  if (!b) return;
  state.replay.on = true;
  bar.hidden = false;
  btn.setAttribute("aria-pressed", "true");
  buildReplayBar();
  setReplayTime(b.start);
}

// ------------------------------------------------------- pill + tab chrome
//
// The same few facts drive three surfaces: the browser tab's title, its
// favicon, and an optional always-on-top "pill" window. One pure model decides
// what to say; the surfaces only draw it.

const PILL_COLORS = { permission: "#7950f2", input: "#7950f2", error: "#e03131",
  running: "#1c7ed6", idle: "#868e96" };

function pillModel(run, fleet) {
  const live = fleet ? fleet.sessions.filter((s) => s.session_live) : [];
  let needing = live.filter((s) => s.urgency > 0 && s.attention);
  // Before the first fleet poll, fall back to the session on screen.
  if (!fleet && run && run.live && run.live.attention && run.session_live &&
      run.live.attention.kind !== "idle") {
    needing = [{ attention: run.live.attention, project_name: "", session_id: run.session_id }];
  }
  const totals = run ? run.totals : null;
  const running = fleet
    ? live.reduce((n, s) => n + ((s.totals && s.totals.running) || 0), 0)
    : (totals ? totals.running : 0);
  const top = needing[0] || null;
  let kind = "idle";
  if (top) kind = top.attention.kind;
  else if (running > 0) kind = "running";
  const where = top ? (top.project_name || (top.session_id || "").slice(0, 8)) : "";
  const more = needing.length - 1;
  return {
    kind: kind,
    count: needing.length,
    headline: top ? attentionTitle(top.attention)
      : (running > 0 ? running + " running" : "All quiet"),
    detail: top
      ? where + (more > 0 ? " · +" + more + " more" : "")
      : (totals ? totals.agents + " agents · " + totals.completed + " done" : ""),
    agents: run ? run.agents.slice(0, 16).map((a) => a.status) : [],
  };
}

function tabTitle(model) {
  if (model.count > 0) return "(" + model.count + ") Workflow";
  if (model.kind === "running") return "\u25B6 Workflow";
  return "Workflow";
}

function drawFavicon(model) {
  if (typeof document.createElement !== "function") return null;
  const canvas = document.createElement("canvas");
  if (!canvas || typeof canvas.getContext !== "function") return null;
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.beginPath();
  ctx.arc(32, 32, 28, 0, Math.PI * 2);
  ctx.fillStyle = PILL_COLORS[model.kind] || PILL_COLORS.idle;
  ctx.fill();
  if (model.count > 0) {
    ctx.fillStyle = "#fff";
    ctx.font = "bold 38px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(model.count > 9 ? "9+" : String(model.count), 32, 35);
  }
  try { return canvas.toDataURL("image/png"); } catch (err) { return null; }
}

function updateTabChrome(model) {
  document.title = tabTitle(model);
  // Redrawing a canvas on every poll is wasted work if nothing changed.
  const key = model.kind + ":" + model.count;
  if (key === state.faviconKey) return;
  state.faviconKey = key;
  const href = drawFavicon(model);
  if (!href || typeof document.querySelectorAll !== "function") return;
  let link = null;
  for (const el of document.querySelectorAll("link")) {
    if (el.rel === "icon") link = el;
  }
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = href;
}

const PILL_CSS = `
:root { color-scheme: light dark; --bg:#fbfbfa; --ink:#1a1a19; --muted:#6b6b66; --line:#e3e3df; }
@media (prefers-color-scheme: dark) { :root { --bg:#17171a; --ink:#e8e8e6; --muted:#9a9a95; --line:#32323a; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:13px system-ui,"Segoe UI",Roboto,sans-serif; }
.pill { display:flex; align-items:center; gap:10px; padding:10px 12px; height:100vh;
  border-left:5px solid var(--c, #868e96); }
.dot { width:12px; height:12px; border-radius:50%; background:var(--c, #868e96); flex:none; }
.pill[data-kind="running"] .dot, .pill[data-kind="permission"] .dot,
.pill[data-kind="input"] .dot, .pill[data-kind="error"] .dot { animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 0%,100% { box-shadow:0 0 0 0 color-mix(in srgb, var(--c) 55%, transparent); }
  50% { box-shadow:0 0 0 6px transparent; } }
@media (prefers-reduced-motion: reduce) { .dot { animation:none !important; } }
.txt { min-width:0; flex:1; }
.head { font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.sub { color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.agents { display:flex; flex-wrap:wrap; gap:3px; max-width:84px; justify-content:flex-end; }
.agents i { width:8px; height:8px; border-radius:2px; background:var(--muted); display:block; }
.agents i[data-s="running"] { background:#1c7ed6; } .agents i[data-s="completed"] { background:#2f9e44; }
.agents i[data-s="failed"], .agents i[data-s="orphaned"] { background:#e03131; }
.agents i[data-s="stalled"] { background:#e8950c; } .agents i[data-s="waiting"] { background:#7950f2; }
`;

function renderPill(model) {
  const win = state.pill;
  if (!win || win.closed) return;
  const doc = win.document;
  let root = doc.getElementById("pill-root");
  if (!root) {
    root = doc.createElement("div");
    root.id = "pill-root";
    doc.body.appendChild(root);
  }
  root.textContent = "";
  const make = (tag, cls, text) => {
    const el = doc.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const pill = make("div", "pill");
  pill.setAttribute("data-kind", model.kind);
  pill.style.setProperty("--c", PILL_COLORS[model.kind] || PILL_COLORS.idle);
  pill.appendChild(make("span", "dot"));
  const txt = make("div", "txt");
  txt.appendChild(make("div", "head", model.headline));
  txt.appendChild(make("div", "sub", model.detail));
  pill.appendChild(txt);
  const agents = make("div", "agents");
  for (const status of model.agents) {
    const cell = make("i");
    cell.setAttribute("data-s", status);
    agents.appendChild(cell);
  }
  pill.appendChild(agents);
  root.appendChild(pill);
}

function updatePillButton() {
  const btn = $("pill-toggle");
  if (!btn) return;
  btn.setAttribute("aria-pressed", String(!!state.pill));
}

async function togglePill() {
  if (state.pill) { state.pill.close(); return; }
  if (typeof window.documentPictureInPicture === "undefined") return;
  let win;
  try {
    win = await window.documentPictureInPicture.requestWindow({ width: 340, height: 96 });
  } catch (err) { return; }   // refused (no user gesture, or the user declined)
  const style = win.document.createElement("style");
  style.textContent = PILL_CSS;
  win.document.head.appendChild(style);
  win.document.title = "Workflow";
  win.addEventListener("pagehide", () => { state.pill = null; updatePillButton(); });
  state.pill = win;
  updatePillButton();
  updateChrome();
}

function updateChrome() {
  const model = pillModel(state.liveRun || state.run, state.fleet);
  updateTabChrome(model);
  renderPill(model);
}

// ------------------------------------------------------------------ fleet
//
// Every recently active session across every project, most urgent first: the
// answer to "which of my sessions needs me?" without opening each one.

function fleetAgo(modifiedAt) {
  return fmtDuration(Math.max(0, Date.now() / 1000 - modifiedAt)) + " ago";
}

function renderFleetBadge() {
  const badge = $("fleet-badge");
  if (!badge) return;
  const n = state.fleet ? state.fleet.attention_count : 0;
  badge.hidden = !n;
  badge.textContent = n ? String(n) : "";
}

function renderFleet() {
  const box = $("fleet");
  if (!box) return;
  const data = state.fleet;
  if (!data) { box.innerHTML = '<div class="fleet-empty">Loading sessions…</div>'; return; }
  if (!data.sessions.length) {
    box.innerHTML = '<div class="fleet-empty">No sessions active in the last ' +
      esc(fmtDuration(data.window_s)) + ".</div>";
    return;
  }
  const current = state.run && state.run.session_id;
  box.innerHTML = data.sessions.map((s) => {
    const att = s.attention && s.session_live ? s.attention : null;
    const t = s.totals;
    const sub = att
      ? attentionTitle(att) + (att.message ? " — " + att.message : "")
      : (!s.session_live ? "Ended" + (s.ended && s.ended.reason ? " (" + s.ended.reason + ")" : "")
        : (t && t.running ? t.running + " agent(s) running" : "Idle"));
    const meta = (t ? t.agents + " agents" +
      (t.waiting ? " · " + t.waiting + " waiting" : "") +
      (t.failed ? " · " + t.failed + " failed" : "") + " · " : "") + fleetAgo(s.modified_at);
    return '<div class="fleet-row' + (s.session_id === current ? " fleet-current" : "") +
      (s.session_live ? "" : " fleet-quiet") + '" data-session="' + esc(s.session_id) + '"' +
      (att ? ' data-kind="' + esc(att.kind) + '"' : "") +
      (s.session_live ? ' data-live="1"' : "") + ' role="button" tabindex="0">' +
      '<span class="fleet-dot"></span>' +
      '<div class="fleet-main"><div class="fleet-title">' +
        esc(s.project_name || "(unknown project)") +
        '<span class="fleet-id">' + esc(s.session_id.slice(0, 8)) + "</span></div>" +
        '<div class="fleet-sub">' + esc(sub) + "</div></div>" +
      '<div class="fleet-meta">' + esc(meta) + "</div></div>";
  }).join("");
  for (const row of box.querySelectorAll(".fleet-row")) {
    const open = () => { switchSession(row.dataset.session); setView("timeline"); };
    row.onclick = open;
    row.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
    };
  }
}

// Announce a prompt or failure in a session you are NOT looking at — the case
// the current-session notifications cannot cover. Seeded like every other alert:
// whatever is already pending when the page opens is shown, not announced.
function checkFleetNotifications(data) {
  const viewing = state.run && state.run.session_id;
  const next = {};
  for (const s of data.sessions) {
    const att = s.attention && s.session_live && s.urgency > 0 ? s.attention : null;
    if (!att) continue;
    const key = att.kind + "@" + att.since;
    next[s.session_id] = key;
    if (state.fleetSeeded && state.notifyEnabled && s.session_id !== viewing &&
        state.fleetAttention[s.session_id] !== key) {
      notify(attentionTitle(att) + " — " + (s.project_name || s.session_id.slice(0, 8)),
        att.message || "");
    }
  }
  state.fleetAttention = next;
  state.fleetSeeded = true;
}

async function pollFleet() {
  if (state.fleetTimer) { clearTimeout(state.fleetTimer); state.fleetTimer = null; }
  try {
    const data = await api("/api/fleet");
    state.fleet = data;
    checkFleetNotifications(data);
    checkFleetSounds(data);
    renderFleetBadge();
    updateChrome();
    if (state.view === "fleet") renderFleet();
  } catch (err) { /* the fleet is an extra; the current session still works */ }
  if (state.live && !state.offline) state.fleetTimer = setTimeout(pollFleet, FLEET_POLL_MS);
}

// One place that knows everything to reset when the viewed session changes.
function switchSession(sessionId) {
  state.sessionId = sessionId;
  const picker = $("session-picker");
  if (picker) {
    if (![...picker.options].some((o) => o.value === sessionId)) {
      const option = document.createElement("option");
      option.value = sessionId;
      option.textContent = sessionId.slice(0, 8);
      picker.appendChild(option);
    }
    picker.value = sessionId;
  }
  if (state.replay.on) toggleReplay();      // a replay belongs to one session
  state.run = null;
  state.liveRun = null;
  // A different session's edges are all pre-existing history to us, not
  // events happening live — reseed instead of flashing every one of them.
  state.seenEdgeKeys = new Set();
  state.graphSeeded = false;
  // Same reasoning for notifications: a different session's existing
  // failures/end-state are history, not something to alert on.
  state.notifySeeded = false;
  state.knownFailedIds = new Set();
  state.knownLoopIds = new Set();
  state.knownAttention = "";
  state.knownBudget = "";
  state.soundMemo = null;           // reseed: another session's history is silent
  state.lastSessionLive = null;
  // A different session's agents are all pre-existing history — reseed so
  // switching sessions doesn't read as a burst of simultaneous activity.
  state.floorActivity = {};
  state.floorSeeded = false;
  state.agentPrevStatus = {};
  state.agentCelebrateUntil = {};
  startPolling();
  startStream();
}

// Live push. The server only says *that* something changed; the data still
// comes from /api/run, so there is one place anything is redacted. Without
// EventSource, or if the stream drops, the 2-second poll simply carries on.
function stopStream() {
  if (state.stream) { state.stream.close(); state.stream = null; }
  state.streamLive = false;
}

function scheduleRefresh() {
  if (!state.live || state.refreshTimer) return;
  state.refreshTimer = setTimeout(() => {
    state.refreshTimer = null;
    if (state.live) startPolling();
  }, STREAM_DEBOUNCE_MS);
}

function startStream() {
  stopStream();
  if (state.offline || typeof EventSource === "undefined") return;
  const session = state.sessionId ? "&session=" + encodeURIComponent(state.sessionId) : "";
  let source;
  try {
    source = new EventSource("/api/stream?k=" + encodeURIComponent(TOKEN) + session);
  } catch (err) { return; }
  state.stream = source;
  source.addEventListener("hello", () => { state.streamLive = true; });
  source.addEventListener("tick", scheduleRefresh);
  source.onerror = () => {
    // EventSource retries by itself; meanwhile go back to fast polling.
    const wasLive = state.streamLive;
    state.streamLive = false;
    if (wasLive) scheduleRefresh();
  };
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
    if (state.live) { startPolling(); startStream(); pollFleet(); }
    else stopStream();
  };
  $("session-picker").onchange = (event) => switchSession(event.target.value);
  if (notificationsSupported()) {
    let stored = "0";
    try {
      if (typeof localStorage !== "undefined") {
        stored = localStorage.getItem("orchestra-notify") || "0";
      }
    } catch (err) { /* fall back to off */ }
    state.notifyEnabled = stored === "1" && Notification.permission === "granted";
    updateNotifyButton();
    $("notify-toggle").onclick = async () => {
      if (Notification.permission === "granted") {
        state.notifyEnabled = !state.notifyEnabled;
      } else {
        const permission = await Notification.requestPermission();
        state.notifyEnabled = permission === "granted";
      }
      setStoredNotifyPref(state.notifyEnabled);
      updateNotifyButton();
    };
  } else {
    $("notify-toggle").hidden = true;
  }
  $("copy-summary").onclick = copySummary;
  const replayBtn = $("replay-toggle");
  if (replayBtn) replayBtn.onclick = toggleReplay;
  const exportSelect = $("export-select");
  if (exportSelect && !state.offline) {
    exportSelect.hidden = false;
    exportSelect.onchange = () => {
      if (exportSelect.value) downloadExport(exportSelect.value);
      exportSelect.value = "";        // it is a menu, not a setting
    };
  }
  const soundBtn = $("sound-toggle");
  const hasAudio = typeof window !== "undefined" && (window.AudioContext || window.webkitAudioContext);
  if (soundBtn && !state.offline && hasAudio) {
    let stored = "0";
    try {
      if (typeof localStorage !== "undefined") stored = localStorage.getItem("orchestra-sound") || "0";
    } catch (err) { /* off */ }
    soundBtn.hidden = false;
    // A remembered "on" still needs a fresh click before the browser lets this
    // page make noise, so the first click after a reload re-arms it.
    state.soundEnabled = stored === "1";
    updateSoundButton();
    // Browsers keep an audio context silent until a gesture; the first click
    // anywhere on the page re-arms it, so a remembered "on" works after reload.
    document.addEventListener("click", () => {
      const ctx = state.soundEnabled ? audioContext() : null;
      if (ctx && ctx.state === "suspended" && typeof ctx.resume === "function") ctx.resume();
    }, { once: true });
    soundBtn.onclick = () => {
      state.soundEnabled = !state.soundEnabled;
      setStoredSoundPref(state.soundEnabled);
      updateSoundButton();
      if (state.soundEnabled) { state.lastSoundAt = 0; playSound("done"); }  // hear what you turned on
    };
  }
  // Only where the browser can do it (Chromium); a static report has no use.
  const pillBtn = $("pill-toggle");
  if (pillBtn && !state.offline && typeof window.documentPictureInPicture !== "undefined") {
    pillBtn.hidden = false;
    pillBtn.onclick = togglePill;
  }
  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => setView(tab.dataset.view);
  }
  $("filter-text").oninput = (event) => {
    state.filterText = event.target.value;
    $("filter-clear").hidden = !state.filterText;
    render();
  };
  $("filter-clear").onclick = () => {
    state.filterText = "";
    const input = $("filter-text");
    input.value = "";
    input.focus();
    $("filter-clear").hidden = true;
    render();
  };
  window.addEventListener("resize", () => render());
  // A static report has nothing to tick (see state.offline) and, more
  // concretely, must not leave a live timer running: Node's event loop
  // never exits with one pending, which is exactly what hung the
  // report-rendering test suite until this was scoped to the live dashboard.
  if (!state.offline) setInterval(tickAgentClocks, 500);
  loadSessions();
  startPolling();
  startStream();
  if (state.offline) {
    // A frozen snapshot has no other sessions to list.
    for (const tab of document.querySelectorAll(".tab")) {
      if (tab.dataset.view === "fleet") tab.hidden = true;
    }
  } else {
    pollFleet();
  }
  // A #agent=<id> link (pasted from a health-box item, a ticker row, or an
  // earlier session) opens straight to that agent's drawer. openDrawer fetches
  // independently of run state, so this doesn't need to wait for the first poll.
  const hash = location.hash || "";
  if (hash.indexOf("#agent=") === 0) {
    openDrawer(decodeURIComponent(hash.slice("#agent=".length)));
  }
}

document.addEventListener("DOMContentLoaded", init);

// ----------------------------------------------------------------- graph

const NODE_W = 200;
const NODE_H = 50;
const COL_GAP = 80;
const ROW_GAP = 24;
const ACCENT_W = 4;
const NODE_TEXT_X = 16;
const EXACT_KINDS = ["spawn", "artifact", "message"];

function layoutGraph(run) {
  const nodes = [{ id: "main", label: "orchestrator", status: "completed",
                   sub: "this session", isMain: true, matches: true }];
  for (const agent of run.agents) {
    nodes.push({
      id: agent.agent_id,
      label: agent.description || agent.agent_id,
      sub: agent.agent_type + " · " + fmtModelShort(agent.model),
      status: agent.status,
      startedAt: agent.started_at,
      duration: agent.duration_s || 0,
      matches: agentMatchesFilter(agent),
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

  // Critical path: the duration-weighted longest chain, not the hop-count
  // rank above. A 5-second agent and a 5-minute agent are equally "one hop,"
  // but only one of them can be why the run took as long as it did. Same
  // bounded-relaxation shape as the rank loop for the same cycle-safety.
  const pathDuration = {};
  const critPrev = {};
  nodes.forEach((n) => { pathDuration[n.id] = n.duration || 0; critPrev[n.id] = null; });
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false;
    for (const edge of structural) {
      const candidate = pathDuration[edge.src] + (byId[edge.dst].duration || 0);
      if (candidate > pathDuration[edge.dst] + 1e-9) {
        pathDuration[edge.dst] = candidate;
        critPrev[edge.dst] = edge.src;
        moved = true;
      } else if (critPrev[edge.dst] === null && candidate >= pathDuration[edge.dst] - 1e-9) {
        // A tie against the node's own-duration base case (its most common
        // predecessor is "main", whose duration is always 0) must still
        // record a predecessor, or the chain silently ends one hop short.
        critPrev[edge.dst] = edge.src;
      }
    }
    if (!moved) break;
  }
  let critEnd = nodes[0].id;
  for (const n of nodes) {
    if (pathDuration[n.id] > pathDuration[critEnd]) critEnd = n.id;
  }
  const criticalNodes = new Set();
  const criticalEdges = new Set();
  for (let cur = critEnd; cur; cur = critPrev[cur]) {
    criticalNodes.add(cur);
    if (critPrev[cur]) criticalEdges.add(critPrev[cur] + "→" + cur);
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
  return { nodes, edges, byId, width, height, criticalNodes, criticalEdges };
}

function renderGraph(run) {
  const svg = $("graph");
  svg.innerHTML = "";
  $("edge-evidence").hidden = true;
  // The first render of a session just seeds what's already-existing history;
  // nothing on it "just happened," so nothing should flash. Only edges that
  // appear on a LATER render — after real polls have run — are live events.
  const firstRender = !state.graphSeeded;
  state.graphSeeded = true;
  const reducedMotion = typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const layout = layoutGraph(run);
  const width = Math.max(svg.clientWidth || 900, layout.width);
  const height = Math.max(layout.height, 200);
  svg.setAttribute("height", height);
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);

  const defs = svgEl("defs");
  defs.appendChild(svgEl("marker", {
    id: "arrow", viewBox: "0 0 8 8", refX: 7, refY: 4,
    markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse",
  }, "")).appendChild(svgEl("path", { d: "M0,0 L8,4 L0,8 z", class: "edge-arrow" }));
  svg.appendChild(defs);

  const labelFont = bodyFont(11);
  const subFont = bodyFont(9);
  const textMax = NODE_W - NODE_TEXT_X - ACCENT_W - 12;

  const edgesByNode = {};
  const edgeEls = [];
  for (const edge of layout.edges) {
    const a = layout.byId[edge.src];
    const b = layout.byId[edge.dst];
    const x1 = a.x + NODE_W;
    const y1 = a.y + NODE_H / 2;
    const x2 = b.x;
    const y2 = b.y + NODE_H / 2;
    const mid = (x1 + x2) / 2;
    const isCritical = layout.criticalEdges.has(edge.src + "→" + edge.dst);
    const d = "M" + x1 + "," + y1 + " C" + mid + "," + y1 + " " + mid + "," + y2 +
      " " + (x2 - 6) + "," + y2;
    const path = svgEl("path", {
      d: d,
      class: "edge" + (edge.confidence === "inferred" ? " edge-inferred" : "") +
        (isCritical ? " edge-critical" : ""),
      "marker-end": "url(#arrow)",
    });
    path.appendChild(svgEl("title", {}, edge.kind + " (" + edge.confidence + ")"));
    path.onclick = () => showEvidence(edge);
    svg.appendChild(path);
    edgeEls.push(path);
    (edgesByNode[edge.src] = edgesByNode[edge.src] || []).push(path);
    (edgesByNode[edge.dst] = edgesByNode[edge.dst] || []).push(path);

    // A brand-new edge key is a real event: this handoff was JUST detected
    // between polls. Flash a dot traveling the same path once, then let it
    // settle into an ordinary static line for good.
    const edgeKey = edge.src + ">" + edge.dst + ">" + edge.kind;
    const isNewEdge = !firstRender && !state.seenEdgeKeys.has(edgeKey);
    state.seenEdgeKeys.add(edgeKey);
    if (isNewEdge && !reducedMotion) {
      const packet = svgEl("circle", { r: 4, class: "packet" });
      packet.appendChild(svgEl("animateMotion", {
        dur: "1s", begin: "0s", fill: "freeze", path: d,
      }));
      packet.appendChild(svgEl("animate", {
        attributeName: "opacity", from: "1", to: "0",
        begin: "0.7s", dur: "0.3s", fill: "freeze",
      }));
      svg.appendChild(packet);
    }
  }

  for (const node of layout.nodes) {
    const isCritical = layout.criticalNodes.has(node.id);
    const group = svgEl("g", { class: "node" + (node.isMain ? " main" : "") +
      (isCritical ? " critical" : "") + (node.matches ? "" : " dim") });
    group.appendChild(svgEl("rect", {
      x: node.x, y: node.y, width: NODE_W, height: NODE_H, rx: 8, class: "card",
    }));
    if (!node.isMain) {
      group.appendChild(svgEl("rect", {
        x: node.x, y: node.y, width: ACCENT_W, height: NODE_H,
        rx: 2, class: "accent s-" + node.status,
      }));
      if (node.status === "running") {
        group.appendChild(svgEl("rect", {
          x: node.x, y: node.y, width: NODE_W, height: NODE_H, rx: 8,
          class: "node-ping",
        }));
      }
    }
    const tx = node.x + NODE_TEXT_X;
    group.appendChild(svgEl("text", { x: tx, y: node.y + 21 },
      fitText(node.label, textMax, labelFont)));
    group.appendChild(svgEl("text", { x: tx, y: node.y + 35, class: "sub" },
      fitText(node.status + " · " + node.sub, textMax, subFont)));
    group.appendChild(svgEl("title", {}, node.label + " — " + node.status));
    const connected = edgesByNode[node.id] || [];
    group.onmouseenter = () => {
      if (!connected.length) return;
      const keep = new Set(connected);
      for (const el of edgeEls) el.classList.toggle("edge-dim", !keep.has(el));
    };
    group.onmouseleave = () => {
      for (const el of edgeEls) el.classList.remove("edge-dim");
    };
    if (!node.isMain) group.onclick = () => openDrawer(node.id);
    svg.appendChild(group);
  }

  const legend = "solid = exact  ·  dashed = inferred (click an edge for evidence, hover a node to trace it)" +
    (layout.criticalNodes.size > 1 ? "  ·  accent = critical path (longest dependency chain by duration)" : "");
  svg.appendChild(svgEl("text", { x: 20, y: height - 8, class: "legend" }, legend));

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

// Deep-linkable: #agent=<id> is set while the drawer is open (replaceState,
// not location.hash=, so opening agents one after another doesn't spam back
// history) so a health-box, ticker, or conflict-list link is pasteable.
function setAgentHash(agentId) {
  if (typeof history === "undefined" || !history.replaceState) return;
  const hash = agentId ? "#agent=" + encodeURIComponent(agentId) : "";
  history.replaceState(null, "", (location.pathname || "") + (location.search || "") + hash);
}

const DRAWER_TRANSITION_MS = 220;

function closeDrawer() {
  if (state.selected === null) return;
  const drawer = $("drawer");
  const scrim = $("scrim");
  drawer.classList.remove("show");
  scrim.classList.remove("show");
  state.selected = null;
  setAgentHash(null);
  // A reopen within this window (closing one agent, immediately picking
  // another) must not have this timeout hide the drawer out from under it —
  // only finish hiding if nothing else got selected in the meantime.
  setTimeout(() => {
    if (state.selected === null) {
      drawer.hidden = true;
      scrim.hidden = true;
    }
  }, DRAWER_TRANSITION_MS);
}

async function openDrawer(agentId) {
  const drawer = $("drawer");
  const scrim = $("scrim");
  drawer.hidden = false;
  scrim.hidden = false;
  scrim.onclick = closeDrawer;
  // Force a layout flush so the browser registers the off-screen starting
  // position before .show is added on the next line — added in the same
  // tick, the transform and its transition would both apply at once and
  // nothing would visibly slide.
  void drawer.offsetWidth;
  drawer.classList.add("show");
  scrim.classList.add("show");
  drawer.innerHTML = "<p>Loading…</p>";
  let agent;
  try {
    agent = await api("/api/agent/" + encodeURIComponent(agentId));
  } catch (err) {
    drawer.innerHTML = "<p>Could not load this agent.</p>";
    return;
  }
  state.selected = agentId;
  setAgentHash(agentId);
  ingestToolCalls(agentId, agent.description || agent.agent_id, agent.tool_calls);
  if (state.view === "activity") renderTicker();

  const rows = [
    ["status", agent.status],
    ["type", agent.agent_type],
    ["model", fmtModelShort(agent.model) +
      (agent.model && agent.model !== fmtModelShort(agent.model) ? "  (" + agent.model + ")" : "")],
    ["launch", agent.launch_mode],
    ["duration", fmtDuration(agent.duration_s)],
    ["tokens", fmtTokens(agent.tokens)],
    ...(agent.loop ? [["possible loop", loopText(agent.loop)]] : []),
    ...(agent.cost !== null && agent.cost !== undefined && state.run && state.run.cost
      ? [["cost", fmtMoney(agent.cost, state.run.cost.currency)]] : []),
    ["cache hit", fmtPct(cacheHitRatio(agent.tokens)) +
      (cacheHitRatio(agent.tokens) !== null ? "  (" + fmtTokenMix(agent.tokens) + ")" : "")],
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
    "<h3>Tool mix</h3>" + (renderToolMix(agent.tool_calls) || '<p class="source-note">no tool calls yet</p>') +
    "<h3>Objective</h3><pre>" + esc(agent.objective || "—") + "</pre>" +
    '<div class="source-note">' + esc(agent.objective_source) + "</div>" +
    "<h3>Expected output</h3><pre>" + esc(agent.expected_output || "—") + "</pre>" +
    '<div class="source-note">' + esc(agent.expected_output_source) + "</div>" +
    "<h3>Returned result</h3><pre>" + esc(agent.result || "(still running)") + "</pre>" +
    "<details><summary>Full brief</summary><pre>" + esc(agent.brief) + "</pre></details>" +
    "<details><summary>Tool calls (last 40)</summary><pre>" + tools + "</pre></details>" +
    "<h3>Files written</h3><pre>" + esc(agent.files_written.join("\n") || "—") + "</pre>" +
    "<h3>Files read</h3><pre>" + esc(agent.files_read.join("\n") || "—") + "</pre>";

  $("drawer-close").onclick = closeDrawer;
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});
