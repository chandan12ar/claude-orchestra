"""The pill / tab-title model is pure logic in app.js; run it under node."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def extract_function(js, name):
    start = js.index("function {}(".format(name))
    return js[start:js.index("\n}\n", start) + 3]


def extract_const(js, name):
    start = js.index("const {} =".format(name))
    return js[start:js.index(";\n", start) + 2]


def evaluate(expression):
    """Evaluate a JS expression with the model functions in scope; return JSON."""
    js = read("app.js")
    prelude = "\n".join([extract_const(js, "PILL_COLORS"),
                         extract_function(js, "attentionTitle"),
                         extract_function(js, "pillModel"),
                         extract_function(js, "tabTitle")])
    program = prelude + "\nconsole.log(JSON.stringify(" + expression + "));"
    path = os.path.join(tempfile.mkdtemp(), "m.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


RUN = ('{session_id: "s1", session_live: true, live: null, '
       'totals: {agents: 3, completed: 2, running: 1}, '
       'agents: [{status: "completed"}, {status: "completed"}, {status: "running"}]}')


def fleet(*sessions):
    return "{sessions: [" + ", ".join(sessions) + "]}"


def sess(sid, project, kind=None, urgency=0, live=True, running=0):
    att = ('{kind: "%s", message: "m", since: 1}' % kind) if kind else "null"
    return ('{session_id: "%s", project_name: "%s", session_live: %s, urgency: %d, '
            'attention: %s, totals: {running: %d, agents: 1}}'
            % (sid, project, str(live).lower(), urgency, att, running))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestPillModel(unittest.TestCase):
    def model(self, run, fl):
        return evaluate("pillModel({}, {})".format(run, fl))

    def test_quiet_when_nothing_runs_or_waits(self):
        m = self.model("null", fleet(sess("a", "api")))
        self.assertEqual((m["kind"], m["count"], m["headline"]), ("idle", 0, "All quiet"))

    def test_running_agents_are_summed_across_live_sessions(self):
        m = self.model(RUN, fleet(sess("a", "api", running=2), sess("b", "web", running=3)))
        self.assertEqual((m["kind"], m["headline"]), ("running", "5 running"))

    def test_ended_sessions_do_not_count_as_running_or_waiting(self):
        m = self.model(RUN, fleet(sess("a", "api", "permission", 4, live=False, running=9)))
        self.assertEqual((m["kind"], m["count"]), ("idle", 0))

    def test_a_blocked_session_wins_and_names_its_project(self):
        m = self.model(RUN, fleet(sess("a", "web", "permission", 4, running=2)))
        self.assertEqual((m["kind"], m["count"]), ("permission", 1))
        self.assertEqual(m["headline"], "Waiting for your permission")
        self.assertEqual(m["detail"], "web")

    def test_several_blocked_sessions_say_how_many_more(self):
        m = self.model(RUN, fleet(sess("a", "web", "permission", 4),
                                  sess("b", "api", "error", 3),
                                  sess("c", "docs", "input", 2)))
        self.assertEqual(m["count"], 3)
        self.assertEqual(m["detail"], "web · +2 more")

    def test_idle_attention_is_not_counted_as_needing_you(self):
        m = self.model(RUN, fleet(sess("a", "web", "idle", 0)))
        self.assertEqual(m["count"], 0)

    def test_falls_back_to_the_open_session_before_the_first_fleet_poll(self):
        run = RUN.replace("live: null", 'live: {attention: {kind: "permission"}}')
        m = self.model(run, "null")
        self.assertEqual((m["kind"], m["count"]), ("permission", 1))

    def test_fallback_ignores_an_idle_prompt(self):
        run = RUN.replace("live: null", 'live: {attention: {kind: "idle"}}')
        self.assertEqual(self.model(run, "null")["count"], 0)

    def test_agent_dots_come_from_the_open_run_and_are_capped(self):
        agents = ", ".join(['{status: "running"}'] * 40)
        run = RUN.replace(RUN[RUN.index("agents: ["):], "agents: [" + agents + "]}")
        self.assertEqual(len(self.model(run, "null")["agents"]), 16)

    def test_no_run_and_no_fleet_does_not_throw(self):
        m = self.model("null", "null")
        self.assertEqual((m["kind"], m["agents"]), ("idle", []))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestTabTitle(unittest.TestCase):
    def title(self, kind, count):
        return evaluate('tabTitle({kind: "%s", count: %d})' % (kind, count))

    def test_count_leads_when_something_needs_you(self):
        self.assertEqual(self.title("permission", 2), "(2) Workflow")

    def test_running_gets_a_play_marker(self):
        self.assertEqual(self.title("running", 0), "▶ Workflow")

    def test_idle_is_plain(self):
        self.assertEqual(self.title("idle", 0), "Workflow")


class TestPillWiring(unittest.TestCase):
    def test_button_exists_in_page_and_report_shell_and_starts_hidden(self):
        from orchestra import report
        for html in (read("index.html"), report._SHELL):
            self.assertRegex(html, r'<button id="pill-toggle"[^>]*hidden')

    def test_only_offered_where_the_browser_supports_it(self):
        js = read("app.js")
        self.assertIn('typeof window.documentPictureInPicture !== "undefined"', js)
        self.assertIn("!state.offline", js[js.index("const pillBtn"):][:200])

    def test_pill_text_never_goes_through_inner_html(self):
        js = read("app.js")
        body = extract_function(js, "renderPill")
        self.assertNotIn("innerHTML", body)

    def test_pill_is_styled_inline_not_by_fetching_anything(self):
        js = read("app.js")
        self.assertIn("const PILL_CSS", js)
        self.assertNotRegex(js[js.index("async function togglePill"):][:900],
                            r"(href|src)\s*=")

    def test_pill_respects_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", read("app.js")[read("app.js").index("const PILL_CSS"):])

    def test_chrome_updates_on_every_render_and_fleet_poll(self):
        js = read("app.js")
        self.assertGreaterEqual(js.count("updateChrome();"), 3)   # render, fleet, open


if __name__ == "__main__":
    unittest.main()
