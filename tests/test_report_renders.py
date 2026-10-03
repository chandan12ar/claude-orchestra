"""Execute the generated report's JavaScript and assert it actually renders.

Every other UI assertion in this suite is a regex over app.js source text.
That is exactly how a blank report shipped: report.py carries its own copy of
the page shell, and one missing `id` made app.js throw before it drew
anything, while every text-based assertion still passed.

This test runs the real report through node with a minimal DOM stub. It needs
node on PATH and skips cleanly without it.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from orchestra.build import RunBuilder
from orchestra.parent import parse_timestamp
from orchestra.report import render_report
from tests.fixtures import build_session, ts

NODE = shutil.which("node")

# A DOM stub with just enough surface for app.js: element lookup by id,
# createElement/createElementNS, textContent/innerHTML, and the handful of
# properties the renderers touch. Any access to a missing element returns
# undefined, so a bad id throws exactly as it would in a browser.
HARNESS = r"""
const ids = {};
function makeEl(tag) {
  return {
    tagName: tag, children: [], attributes: {}, dataset: {}, style: {},
    _text: "", _html: "", hidden: false, clientWidth: 900,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    set textContent(v) { this._text = String(v); this.children = []; },
    get textContent() { return this._text; },
    set innerHTML(v) { this._html = String(v); this.children = []; },
    get innerHTML() { return this._html; },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k]; },
    appendChild(c) { this.children.push(c); return c; },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
}
for (const id of __IDS__) ids[id] = makeEl("div");
const realSetTimeout = setTimeout;
let onReady = null;
global.document = {
  getElementById: (id) => ids[id],
  createElement: makeEl,
  createElementNS: (ns, tag) => makeEl(tag),
  querySelectorAll: () => [],
  // app.js boots from DOMContentLoaded; capture it so we can fire it.
  addEventListener: (evt, fn) => { if (evt === "DOMContentLoaded") onReady = fn; },
};
global.window = { ORCHESTRA_RUN: null, ORCHESTRA_DETAILS: null,
                  addEventListener: () => {} };
global.location = { search: "" };
global.fetch = () => Promise.reject(new Error("offline"));
global.setTimeout = (fn) => 0;
let failure = null;
process.on("unhandledRejection", (e) => { failure = String(e && e.message || e); });
"""

FOOTER = r"""
if (typeof onReady === "function") onReady();
realSetTimeout(() => {
  console.log(JSON.stringify({
    failure: failure,
    totals: ids["totals"].children.length,
    timeline: ids["timeline"].children.length,
    conn: ids["conn"] ? ids["conn"]._text : null,
  }));
}, 200);
"""


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestReportActuallyRenders(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        paths = build_session(self.root)
        builder = RunBuilder(paths, now_fn=lambda: parse_timestamp(ts(150)))
        run = builder.refresh()
        details = {a.agent_id: a.to_detail_dict() for a in run.agents}
        self.html = render_report(run, details)

    def _run_report_js(self):
        """Extract the report's inline scripts and execute them under node."""
        import re
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.DOTALL)
        self.assertGreaterEqual(len(scripts), 2, "report lost its inline scripts")

        ids = re.findall(r'id="([^"]+)"', self.html)
        harness = HARNESS.replace("__IDS__", json.dumps(sorted(set(ids))))
        program = harness + "\n" + "\n".join(scripts) + "\n" + FOOTER

        path = os.path.join(self.root, "harness.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(program)
        proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8",
                              timeout=60)
        self.assertEqual(proc.returncode, 0,
                         "report JS crashed:\n" + proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_the_report_renders_content_not_a_blank_page(self):
        out = self._run_report_js()
        self.assertIsNone(out["failure"],
                          "report threw while rendering: {}".format(out["failure"]))
        self.assertGreater(out["totals"], 0, "header totals never rendered")
        self.assertGreater(out["timeline"], 0, "timeline drew nothing")

    def test_the_shell_carries_every_id_app_js_touches(self):
        import re
        js = self.html
        referenced = set(re.findall(r'\$\("([^"]+)"\)', js))
        present = set(re.findall(r'id="([^"]+)"', js))
        # Created dynamically: drawer-close by openDrawer, transport-time by renderHeader.
        missing = referenced - present - {"drawer-close", "transport-time"}
        self.assertEqual(missing, set(),
                         "report shell is missing ids app.js uses: {}".format(missing))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestReportPayloadCannotBreakOutOfScript(unittest.TestCase):
    def test_a_brief_containing_a_closing_script_tag_is_escaped(self):
        from orchestra.model import Agent, Run
        hostile = "before </script><script>alert(1)</script> after"
        run = Run(session_id="s1", agents=[Agent(agent_id="a1", brief=hostile)])
        html = render_report(run, {"a1": run.agents[0].to_detail_dict()})
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("\\u003c/script", html)

    def test_the_escaped_payload_still_parses_as_json(self):
        import re
        from orchestra.model import Agent, Run
        run = Run(session_id="s1",
                  agents=[Agent(agent_id="a1", brief="a < b and </script>")])
        html = render_report(run, {})
        m = re.search(r"window\.ORCHESTRA_RUN\s*=\s*(\{.*?\});", html, re.DOTALL)
        payload = json.loads(m.group(1))
        self.assertEqual(payload["agents"][0]["agent_id"], "a1")


if __name__ == "__main__":
    unittest.main()
