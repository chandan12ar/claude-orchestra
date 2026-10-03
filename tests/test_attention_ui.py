"""The attention banner and the `waiting` status, executed for real under node."""

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
  const box = ids["attention"];
  console.log(JSON.stringify({
    failure: failure,
    hidden: box.hidden,
    kind: box.attributes["data-kind"] || null,
    role: box.attributes["role"] || null,
    spans: box.children.map((c) => ({cls: c.className, text: c._text})),
    html: box._html,
    totals: ids["totals"].children.map((c) => c._html),
  }));
}, 200);
"""


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def run_with(live, session_live=True, agents=None):
    agents = agents if agents is not None else [
        Agent(agent_id="a1", status="waiting", rounds=[Round(started_at=1.0)])]
    return Run(session_id="s1", session_live=session_live, agents=agents, live=live)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestAttentionBanner(unittest.TestCase):
    def render(self, run):
        html = render_report(run, {})
        scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        ids = sorted(set(re.findall(r'id="([^"]+)"', html)))
        program = (HARNESS.replace("__IDS__", json.dumps(ids)) + "\n" +
                   "\n".join(scripts) + "\n" + FOOTER)
        path = os.path.join(tempfile.mkdtemp(), "h.js")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(program)
        proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8",
                              timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIsNone(out["failure"], out["failure"])
        return out

    def live(self, kind="permission", message="Claude needs permission", **kw):
        attention = {"kind": kind, "since": 100.0, "message": message,
                     "error_type": kw.get("error_type", ""), "agent_id": ""}
        return {"has_events": True, "last_event_at": 100.0,
                "attention": attention, "ended": None}

    def test_hidden_when_there_are_no_hook_events(self):
        self.assertTrue(self.render(run_with(None))["hidden"])

    def test_permission_prompt_shows_loudly_with_its_message(self):
        out = self.render(run_with(self.live()))
        self.assertFalse(out["hidden"])
        self.assertEqual((out["kind"], out["role"]), ("permission", "alert"))
        self.assertEqual(out["spans"][0]["text"], "Waiting for your permission")
        self.assertEqual(out["spans"][1]["text"], "Claude needs permission")

    def test_api_error_names_its_type(self):
        out = self.render(run_with(self.live("error", "", error_type="rate_limit")))
        self.assertEqual(out["spans"][0]["text"], "API error: rate_limit")
        self.assertEqual(out["role"], "alert")

    def test_idle_is_quiet(self):
        out = self.render(run_with(self.live("idle", "")))
        self.assertEqual(out["role"], "status")

    def test_session_end_shows_when_nothing_else_is_pending(self):
        live = {"has_events": True, "last_event_at": 5.0, "attention": None,
                "ended": {"at": 5.0, "reason": "logout"}}
        out = self.render(run_with(live, session_live=False))
        self.assertEqual(out["kind"], "ended")
        self.assertEqual(out["spans"][0]["text"], "Session ended (logout)")

    def test_a_live_session_with_a_stale_end_event_shows_nothing(self):
        live = {"has_events": True, "last_event_at": 5.0, "attention": None,
                "ended": {"at": 5.0, "reason": "x"}}
        self.assertTrue(self.render(run_with(live, session_live=True))["hidden"])

    def test_a_hostile_message_is_text_not_markup(self):
        hostile = '<img src=x onerror=alert(1)><script>alert(2)</script>'
        out = self.render(run_with(self.live(message=hostile)))
        self.assertEqual(out["spans"][1]["text"], hostile)   # shown verbatim
        self.assertEqual(out["html"], "")                    # never via innerHTML

    def test_static_report_shows_no_relative_time(self):
        out = self.render(run_with(self.live()))
        self.assertFalse(any(s["cls"] == "att-since" for s in out["spans"]))

    def test_waiting_agents_are_counted_in_the_header(self):
        out = self.render(run_with(None))
        self.assertTrue(any("waiting" in t for t in out["totals"]))


class TestStaticAssetsKnowAboutWaiting(unittest.TestCase):
    def test_page_and_report_shell_both_have_the_mount_point(self):
        self.assertIn('id="attention"', read("index.html"))
        from orchestra import report
        self.assertIn('id="attention"', report._SHELL)

    def test_waiting_has_a_colour_and_a_class(self):
        css = read("style.css")
        self.assertIn("--waiting:", css)
        self.assertIn(".s-waiting", css)

    def test_waiting_is_a_filter_chip_and_a_sprite_state(self):
        js = read("app.js")
        self.assertIn('"waiting"', js[js.index("function renderFilterChips"):][:400])
        self.assertIn("waiting: {", js)

    def test_hidden_really_hides_even_for_elements_that_set_their_own_display(self):
        # `.attention { display: flex }` and `.badge { display: inline-block }`
        # override the browser's default [hidden] rule, leaving an empty bar and
        # an empty pill on screen. A global !important rule must win.
        css = read("style.css")
        self.assertRegex(css, r"\[hidden\]\s*\{\s*display:\s*none\s*!important")

    def test_attention_text_never_goes_through_inner_html(self):
        js = read("app.js")
        body = js[js.index("function renderAttention("):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("innerHTML", body)


if __name__ == "__main__":
    unittest.main()
