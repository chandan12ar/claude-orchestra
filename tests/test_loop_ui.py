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
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")

FOOTER = r"""
if (typeof onReady === "function") onReady();
realSetTimeout(() => {
  const h = ids["health"];
  console.log(JSON.stringify({failure: failure, hidden: h.hidden, head: h._html,
    items: h.children.map((ul) => (ul.children || []).map((li) => li._text))}));
}, 200);
"""


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def fn(js, name):
    start = js.index("function {}(".format(name))
    return js[start:js.index("\n}\n", start) + 3]


def loop_text(loop):
    js = read("app.js")
    program = fn(js, "loopText") + "\nconsole.log(JSON.stringify(loopText(%s)));" % json.dumps(loop)
    path = os.path.join(tempfile.mkdtemp(), "l.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


REPEAT = {"kind": "repeat", "count": 9, "tool": "Bash", "target": "npm test",
          "calls": [{"tool": "Bash", "target": "npm test"}]}
CYCLE = {"kind": "cycle", "count": 16, "tool": "Edit", "target": "a.py",
         "calls": [{"tool": "Edit", "target": "a.py"}, {"tool": "Bash", "target": "pytest"}]}


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestLoopText(unittest.TestCase):
    def test_repeat(self):
        self.assertEqual(loop_text(REPEAT), "Bash npm test ×9")

    def test_cycle(self):
        self.assertEqual(loop_text(CYCLE),
                         "alternating Edit a.py ⇄ Bash pytest over its last 16 calls")

    def test_none_is_empty(self):
        self.assertEqual(loop_text(None), "")

    def test_a_call_with_no_target_has_no_stray_space(self):
        text = loop_text({"kind": "repeat", "count": 7, "tool": "Glob",
                          "calls": [{"tool": "Glob", "target": ""}]})
        self.assertEqual(text, "Glob ×7")


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestHealthBox(unittest.TestCase):
    def render(self, agents):
        html = render_report(Run(session_id="s", agents=agents), {})
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        ids = sorted(set(re.findall(r'id="([^"]+)"', html)))
        program = (HARNESS.replace("__IDS__", json.dumps(ids)) + "\n" +
                   "\n".join(scripts) + "\n" + FOOTER)
        path = os.path.join(tempfile.mkdtemp(), "h.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(program)
        proc = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIsNone(out["failure"], out["failure"])
        return out

    def agent(self, aid, status="running", loop=None, desc="work"):
        return Agent(agent_id=aid, description=desc, status=status, loop=loop,
                     rounds=[Round(started_at=1.0)])

    def test_a_looping_running_agent_appears_even_though_it_is_healthy_otherwise(self):
        out = self.render([self.agent("a1", loop=REPEAT, desc="fix tests")])
        self.assertFalse(out["hidden"])
        self.assertEqual(out["items"][0], ["POSSIBLE LOOP — fix tests: Bash npm test ×9"])

    def test_no_loop_and_healthy_means_the_box_stays_hidden(self):
        self.assertTrue(self.render([self.agent("a1")])["hidden"])

    def test_a_stalled_looping_agent_is_listed_twice_but_counted_once(self):
        out = self.render([self.agent("a1", "stalled", REPEAT)])
        self.assertEqual(len(out["items"][0]), 2)
        self.assertIn("1 agent(s)", out["head"])


class TestWiring(unittest.TestCase):
    def test_drawer_summary_and_notification_all_use_loop_text(self):
        js = read("app.js")
        self.assertGreaterEqual(js.count("loopText("), 5)
        self.assertIn('notify("Possible loop"', js)

    def test_switching_sessions_reseeds_loop_notifications(self):
        js = read("app.js")
        body = js[js.index("function switchSession("):]
        self.assertIn("state.knownLoopIds = new Set()", body[:body.index("\n}\n")])


if __name__ == "__main__":
    unittest.main()
