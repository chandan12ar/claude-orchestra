"""A single-file HTML snapshot: no server, no network, safe to email."""

import base64
import json
import os
from typing import Any, Dict

from orchestra.build import RunBuilder
from orchestra.http import STATIC_DIR
from orchestra.model import Run

_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workflow report — {session}</title>
<style>
{css}
</style>
</head>
<body>
<header>
  <div class="bar">
    <h1>Workflow</h1>
    <button id="pill-toggle" type="button" aria-pressed="false" hidden>Pill</button>
    <button id="sound-toggle" type="button" aria-pressed="false" hidden>Sound</button>
    <button id="copy-summary" type="button">Copy summary</button>
    <!-- id is load-bearing: app.js sets $("conn").textContent inside poll(),
         before render(). Without it the whole page throws and stays blank. -->
    <span id="conn" class="conn">static report · session {session}</span>
  </div>
  <div id="totals" class="totals"></div>
</header>
<div id="attention" class="attention" hidden></div>
<div id="health" class="health" hidden></div>
<div id="conflicts" class="health" hidden></div>
<div id="filter-bar" class="filter-bar">
  <div class="search-wrap">
    <svg class="search-icon" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="9" cy="9" r="6" fill="none" stroke="currentColor" stroke-width="1.6"/>
      <path d="M13.4 13.4 L18 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
    </svg>
    <input id="filter-text" type="search" placeholder="Filter agents…" aria-label="Filter agents">
    <button type="button" id="filter-clear" class="filter-clear" aria-label="Clear filter" hidden>&times;</button>
  </div>
  <span id="filter-count" class="filter-count"></span>
  <div id="filter-status" class="filter-chips"></div>
</div>
<nav class="tabs" role="tablist">
  <button type="button" class="tab active" data-view="timeline" role="tab">Timeline</button>
  <button type="button" class="tab" data-view="graph" role="tab">Graph</button>
  <button type="button" class="tab" data-view="activity" role="tab">Activity</button>
  <button type="button" class="tab" data-view="workfloor" role="tab">Work Floor</button>
  <button type="button" hidden class="tab" data-view="fleet" role="tab">Fleet <span id="fleet-badge" class="badge" hidden></span></button>
</nav>
<main>
  <section id="view-timeline" class="view">
    <svg id="timeline" role="img" aria-label="Agent timeline"></svg>
  </section>
  <section id="view-graph" class="view" hidden>
    <svg id="graph" role="img" aria-label="Agent dependency graph"></svg>
    <div id="edge-evidence" class="evidence" hidden></div>
  </section>
  <section id="view-activity" class="view" hidden>
    <div id="ticker" class="ticker"></div>
  </section>
  <section id="view-fleet" class="view" hidden>
    <div id="fleet" class="fleet"></div>
  </section>
  <section id="view-workfloor" class="view" hidden>
    <div id="workfloor" class="workfloor"></div>
  </section>
</main>
<div id="scrim" class="scrim" hidden></div>
<aside id="drawer" class="drawer" hidden aria-label="Agent detail"></aside>
<footer id="diagnostics" class="diagnostics"></footer>
<select id="session-picker" hidden></select>
<button id="live-toggle" hidden></button>
<button id="notify-toggle" hidden></button>
<script>
window.ORCHESTRA_RUN = {run_json};
window.ORCHESTRA_DETAILS = {details_json};
window.ORCHESTRA_AGENT_SPRITE = {agent_sprite_json};
</script>
<script>
{js}
</script>
</body>
</html>
"""


def _script_safe(payload: Any) -> str:
    """JSON for embedding inside an inline <script>.

    json.dumps does not escape "<", so a brief containing "</script>" would
    close the script element early: a benign case (an agent discussing HTML)
    truncates the payload into a syntax error and kills the report, and a
    hostile one executes. Escaping "<" is enough, and leaves the JSON valid.
    """
    return json.dumps(payload).replace("<", "\\u003c")


def _read_static(name: str) -> str:
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _agent_sprite_data_uri() -> str:
    """The character sheet as a data: URI — a report is one file with no
    server to fetch a sibling image from, so the sprite has to travel inside
    it."""
    with open(os.path.join(STATIC_DIR, "agent-sprite.png"), "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    return "data:image/png;base64," + encoded


def _offline_shim(js: str) -> str:
    """Serve the baked-in payload instead of polling, and never schedule a poll."""
    shim = """
api = function (path) {
  if (path.indexOf("/api/agent/") === 0) {
    var id = decodeURIComponent(path.slice("/api/agent/".length));
    var detail = window.ORCHESTRA_DETAILS[id];
    return detail ? Promise.resolve(detail) : Promise.reject(new Error("no detail"));
  }
  if (path.indexOf("/api/run") === 0) return Promise.resolve(window.ORCHESTRA_RUN);
  return Promise.reject(new Error("offline"));
};
state.live = false;
state.offline = true;
"""
    # `api` and `state` are declared with const/let in app.js; rebind via window
    # after the definitions rather than before them.
    return js.replace("const TOKEN =", "var TOKEN =") \
             .replace("function api(path) {", "var api = function (path) {") \
             .replace("\n// ---------------------------------------------------------------- header",
                      "\n" + shim +
                      "\n// ---------------------------------------------------------------- header")


def render_report(run: Run, details: Dict[str, Dict[str, Any]]) -> str:
    return _SHELL.format(
        session=run.session_id,
        css=_read_static("style.css"),
        js=_offline_shim(_read_static("app.js")),
        run_json=_script_safe(run.to_summary_dict()),
        details_json=_script_safe(details),
        agent_sprite_json=_script_safe(_agent_sprite_data_uri()),
    )


def write_report(builder: RunBuilder, path: str) -> str:
    """Render and write. An empty or directory path gets a session-named default."""
    run = builder.refresh()
    details = {a.agent_id: a.to_detail_dict() for a in run.agents}
    if not path or os.path.isdir(path) or path.endswith((os.sep, "/")):
        directory = path or os.getcwd()
        path = os.path.join(directory,
                            "orchestra-report-{}.html".format(run.session_id))
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_report(run, details))
    return path
