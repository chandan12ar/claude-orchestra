"""The Work Floor: grouping, the "doing now" line, safety and rebuild-avoidance (node)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")

SETUP = """
global.document = {createElement: () => {
  let text = "";
  return {set textContent(v) { text = String(v); },
          get innerHTML() { return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
                                       .replace(/>/g, "&gt;"); }};
}};
const box = {innerHTML: "", querySelectorAll: () => []};
const $ = () => box;
const state = {offline: false, floorGroup: "type", floorSig: "", floorSeeded: false, floorActivity: {},
  agentPrevStatus: {}, agentCelebrateUntil: {}, agentSprites: {}, filterText: "", filterStatuses: new Set()};
const agentMatchesFilter = () => true;
const stopAgentSprites = () => { state.stops = (state.stops || 0) + 1; };
const fmtTokens = () => "1k";
const fmtTokenMix = () => "";
const fmtCount = (n) => String(n);
const fmtModelShort = (m) => String(m || "?");
const agentHue = () => 0;
class AgentSprite { constructor() {} setState() {} stop() {} }
const agentSpriteSheet = {};
"""

CONSTS = ("AGENT_SPRITE_STATE", "AGENT_CELEBRATE_STATE", "AGENT_CELEBRATE_MS", "FLOOR_STATUS_ORDER",
          "FLOOR_STATUS_LABEL", "FOCUS_NAMES")
FNS = ("esc", "fmtDuration", "agentTokenTotal", "humanizeAgentType", "baseName", "floorRank",
       "toolTargetLabel", "sparkHtml", "floorSignature", "focusMatches", "noteFocus", "restoreFocus",
       "renderWorkfloor")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def fn(js, name):
    start = js.index("function {}(".format(name))
    return js[start:js.index("\n}\n", start) + 3]


def const(js, name):
    start = js.index("const {} =".format(name))
    return js[start:js.index(";\n", start) + 2]


def run_js(body):
    js = read("app.js")
    prelude = "\n".join([SETUP] + [const(js, c) for c in CONSTS] + [fn(js, n) for n in FNS])
    path = os.path.join(tempfile.mkdtemp(), "w.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prelude + "\n" + body)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


def agent(aid, status="completed", kind="general-purpose", **extra):
    base = {"agent_id": aid, "description": "Agent " + aid, "status": status, "agent_type": kind,
            "model": "m", "started_at": 100.0, "ended_at": None if status == "running" else 160.0,
            "duration_s": None if status == "running" else 60.0, "tokens": {"output": 5},
            "tool_call_count": 2, "last_tool": None, "activity": []}
    base.update(extra)
    return base


def render(agents, group="type", then=""):
    body = ("state.floorGroup = %s; const run = {agents: %s}; renderWorkfloor(run); %s"
            "console.log(JSON.stringify({html: box.innerHTML, stops: state.stops || 0}));"
            % (json.dumps(group), json.dumps(agents), then))
    return run_js(body)


def headings(html):
    import re
    return re.findall(r"<h3>(.*?)</h3>", html)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestGrouping(unittest.TestCase):
    AGENTS = [agent("a", "completed", "Plan"), agent("b", "failed", "Explore"),
              agent("c", "running", "Explore"), agent("d", "stalled", "")]

    def test_by_role_sorts_labels_and_puts_ungrouped_last(self):
        self.assertEqual(headings(render(self.AGENTS)["html"]), ["Explore", "Plan", "Ungrouped"])

    def test_by_status_puts_what_needs_a_look_first(self):
        self.assertEqual(headings(render(self.AGENTS, "status")["html"]),
                         ["Failed", "Stalled", "Running", "Completed"])

    def test_inside_a_group_attention_comes_first(self):
        html = render([agent("ok", "completed"), agent("bad", "failed")])["html"]
        self.assertLess(html.index('data-agent="bad"'), html.index('data-agent="ok"'))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestCardContent(unittest.TestCase):
    def test_shows_what_the_agent_is_doing_with_files_by_name(self):
        html = render([agent("a", last_tool={"name": "Edit", "target": "/p/src/ui/Checkout.tsx"})])["html"]
        self.assertIn("<b>Edit</b> Checkout.tsx", html)

    def test_commands_are_shown_as_written_not_cut_at_a_slash(self):
        html = render([agent("a", last_tool={"name": "Bash", "target": "cd /x/y && npm test"})])["html"]
        self.assertIn("cd /x/y &amp;&amp; npm test", html)

    def test_an_agent_with_no_calls_says_so(self):
        self.assertIn("no tool calls yet", render([agent("a")])["html"])

    def test_sparkline_scales_to_the_busiest_slice(self):
        html = render([agent("a", activity=[0, 1, 4, 2])])["html"]
        self.assertIn('class="z" style="height:8%"', html)
        self.assertIn("height:100%", html)

    def test_hostile_text_cannot_inject_markup(self):
        evil = '"><img src=x onerror=alert(1)>'
        html = render([agent("a", description=evil, last_tool={"name": "Bash", "target": evil})])["html"]
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestRebuildAvoidance(unittest.TestCase):
    def test_an_unchanged_floor_is_not_rebuilt_and_sprites_keep_running(self):
        out = render([agent("a", "running")], then="const first = box.innerHTML; box.marker = 1; "
                     "renderWorkfloor(run); renderWorkfloor(run); ")
        self.assertEqual(out["stops"], 1)          # sprites were stopped only for the first build

    def test_a_new_tool_call_rebuilds(self):
        body = ("const run = {agents: [%s]}; renderWorkfloor(run); "
                "run.agents[0].tool_call_count = 3; run.agents[0].last_tool = {name: 'Read', target: '/a/b.ts'};"
                "renderWorkfloor(run); console.log(JSON.stringify({stops: state.stops, html: box.innerHTML}));"
                % json.dumps(agent("a", "running")))
        out = run_js(body)
        self.assertEqual(out["stops"], 2)
        self.assertIn("<b>Read</b> b.ts", out["html"])

    def test_changing_the_grouping_rebuilds(self):
        body = ("const run = {agents: [%s]}; renderWorkfloor(run); state.floorGroup = 'status';"
                "renderWorkfloor(run); console.log(JSON.stringify({stops: state.stops, html: box.innerHTML}));"
                % json.dumps(agent("a", "failed")))
        out = run_js(body)
        self.assertEqual(out["stops"], 2)
        self.assertIn("<h3>Failed</h3>", out["html"])

    def test_the_empty_floor_says_so(self):
        out = run_js("renderWorkfloor({agents: []}); console.log(JSON.stringify({html: box.innerHTML}));")
        self.assertIn("No agents in this session yet", out["html"])


if __name__ == "__main__":
    unittest.main()
