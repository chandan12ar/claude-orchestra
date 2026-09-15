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
  knownFailedIds: new Set(),
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
  const order = ["running", "completed", "failed", "stalled", "orphaned", "unknown"];
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
      notify("Orchestra session ended",
        (t.completed || 0) + " completed, " + ((t.failed || 0) + (t.orphaned || 0)) + " failed");
    }
  }

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
    "## Orchestra summary — " + (run.session_id || "session"),
    (t.agents || 0) + " agents · " + (t.completed || 0) + " completed · " +
      failedCount + " failed · " + (t.running || 0) + " running · " +
      fmtTokens(t.tokens) + " tokens (" + fmtPct(cacheHitRatio(t.tokens)) + " cached) · " +
      fmtDuration(t.wall_time_s) + " wall",
  ];

  const trouble = run.agents.filter((a) => ["stalled", "failed", "orphaned"].includes(a.status));
  if (trouble.length) {
    lines.push("", "### Needs attention");
    for (const agent of trouble) {
      lines.push("- " + agent.status.toUpperCase() + " — " + (agent.description || agent.agent_id));
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
  const text = buildSummaryMarkdown(state.run);
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
    ["done", t.completed],
    ["failed", t.failed + t.orphaned],
    ["tokens", fmtTokens(t.tokens)],
    ["cached", fmtPct(cacheHitRatio(t.tokens))],
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

const ROW_H = 32;
const LEFT = 200;
const PAD = 16;
const DOT_R = 4;
const LABEL_X = 14;

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
  renderHealth(state.run);
  renderConflicts(state.run);
  renderFilterChips();
  renderFilterCount(state.run);
  renderDiagnostics(state.run);
  if (state.view === "timeline") renderTimeline(state.run);
  else if (state.view === "graph") renderGraph(state.run);
  else if (state.view === "workfloor") renderWorkfloor(state.run);
  else renderTicker();
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
  checkNotifications(run);
  render();
  // Only actively poll agent detail while the tab showing it is open, so
  // watching Timeline/Graph never costs N extra per-agent fetches.
  if (state.view === "activity") refreshTicker(run);
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
    // A different session's edges are all pre-existing history to us, not
    // events happening live — reseed instead of flashing every one of them.
    state.seenEdgeKeys = new Set();
    state.graphSeeded = false;
    // Same reasoning for notifications: a different session's existing
    // failures/end-state are history, not something to alert on.
    state.notifySeeded = false;
    state.knownFailedIds = new Set();
    state.lastSessionLive = null;
    // A different session's agents are all pre-existing history — reseed so
    // switching sessions doesn't read as a burst of simultaneous activity.
    state.floorActivity = {};
    state.floorSeeded = false;
    state.agentPrevStatus = {};
    state.agentCelebrateUntil = {};
    startPolling();
  };
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
