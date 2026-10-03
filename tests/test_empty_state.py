"""Empty and loading states: the first thing a person sees when nothing has happened yet."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from orchestra.model import Agent, Round, Run
from orchestra.report import render_report
from tests.test_report_renders import HARNESS

NODE = shutil.which("node")

FOOTER = r"""
if (typeof onReady === "function") onReady();
realSetTimeout(() => {
  const v = (id) => ids[id] ? ids[id].hidden : null;
  console.log(JSON.stringify({failure: failure, boot: v("boot"), empty: v("empty-state"),
    timeline: v("view-timeline"), insights: v("view-insights"), drawn: ids["timeline"].children.length}));
}, 200);
"""


def render_state(run):
    html = render_report(run, {})
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    ids = sorted(set(re.findall(r'id="([^"]+)"', html)))
    program = HARNESS.replace("__IDS__", json.dumps(ids)) + "\n" + "\n".join(scripts) + "\n" + FOOTER
    path = os.path.join(tempfile.mkdtemp(), "e.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestEmptyAndLoading(unittest.TestCase):
    def test_a_run_with_no_agents_explains_itself_instead_of_drawing_empty_panels(self):
        out = render_state(Run(session_id="s", agents=[]))
        self.assertIsNone(out["failure"])
        self.assertFalse(out["empty"])            # the explanation is shown
        self.assertTrue(out["timeline"])          # the empty timeline panel is not
        self.assertEqual(out["drawn"], 0)

    def test_the_loading_notice_goes_away_once_a_run_has_rendered(self):
        out = render_state(Run(session_id="s", agents=[]))
        self.assertTrue(out["boot"])

    def test_a_run_with_agents_shows_the_timeline_and_no_explanation(self):
        agent = Agent(agent_id="a1", description="x", status="completed",
                      rounds=[Round(started_at=1.0, ended_at=2.0)])
        out = render_state(Run(session_id="s", agents=[agent]))
        self.assertTrue(out["empty"])
        self.assertFalse(out["timeline"])
        self.assertGreater(out["drawn"], 0)


if __name__ == "__main__":
    unittest.main()
