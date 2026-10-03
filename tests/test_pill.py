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
        self.assertEqual(self.title("permission", 2), "(2) Cuelight")

    def test_running_gets_a_play_marker(self):
        self.assertEqual(self.title("running", 0), "▶ Cuelight")

    def test_idle_is_plain(self):
        self.assertEqual(self.title("idle", 0), "Cuelight")


RECORDER = """
const calls = [];
const ctx = new Proxy({}, {
  get: (t, k) => (k in t ? t[k] : (...a) => {
    calls.push(k + ':' + a.join(','));
    return k === 'createRadialGradient' ? { addColorStop() {} } : undefined;
  }),
  set: (t, k, v) => { t[k] = v; if (k === 'fillStyle') calls.push('fill=' + v); return true; },
});
"""


def draw(kind, count):
    js = read("app.js")
    prelude = "\n".join([extract_const(js, "PILL_COLORS"), extract_function(js, "mixHex"),
                         extract_function(js, "drawCue"), RECORDER])
    program = prelude + ("\ndrawCue(ctx, {kind: '%s', count: %d});"
                         "\nconsole.log(JSON.stringify(calls));" % (kind, count))
    path = os.path.join(tempfile.mkdtemp(), "f.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestTabIconFace(unittest.TestCase):
    def test_every_state_draws_without_throwing(self):
        for kind in ("idle", "running", "permission", "input", "error"):
            self.assertTrue(draw(kind, 0), kind)

    def test_count_badge_only_when_something_waits(self):
        self.assertFalse([c for c in draw("idle", 0) if c.startswith("fillText")])
        self.assertTrue([c for c in draw("permission", 2) if c == "fillText:2,51,51"])

    def test_big_counts_are_capped(self):
        self.assertIn("fillText:9+,51,51", draw("error", 12))

    def test_the_face_changes_with_the_state(self):
        self.assertNotEqual(draw("idle", 0), draw("error", 0))
        self.assertNotEqual(draw("running", 0), draw("permission", 0))


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

    def test_every_state_has_a_face_and_a_motion(self):
        css = read("app.js")[read("app.js").index("const PILL_CSS"):]
        for kind in ("idle", "running", "permission", "input", "error"):
            self.assertIn('.pill[data-kind="%s"] .body' % kind, css)
        for part in ("m-idle", "m-run", "m-ask", "m-err", "eyes-sleep", "brows-err"):
            self.assertIn(part, css)

    def test_motion_is_switched_off_for_reduced_motion(self):
        css = read("app.js")[read("app.js").index("const PILL_CSS"):]
        block = css[css.index("prefers-reduced-motion"):]
        self.assertIn("animation:none !important", block)

    def test_window_is_built_once_and_then_only_updated(self):
        # Rebuilding on every poll would restart the mascot's animations.
        js = read("app.js")
        body = extract_function(js, "renderPill")
        self.assertIn("state.pillUi", body)
        self.assertIn("ui.sig", body)
        self.assertNotIn('root.textContent = ""', body)
        self.assertIn("state.pillUi = null", js[js.index("async function togglePill"):])

    def test_avatar_is_drawn_with_dom_calls_not_markup_strings(self):
        self.assertNotIn("innerHTML", extract_function(read("app.js"), "buildPillAvatar"))

    def test_pill_text_colours_are_readable_on_both_backgrounds(self):
        css = read("app.js")[read("app.js").index("const PILL_CSS"):]

        def lum(hex_colour):
            rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
            return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        def token(name, section):
            return re.search(name + r":(#[0-9a-f]{6})", section).group(1)

        light = css[css.index(":root"):css.index("@media")]
        dark = css[css.index("@media (prefers-color-scheme: dark)"):css.index("* {")]
        for section in (light, dark):
            self.assertGreaterEqual(ratio(token("--ink", section), token("--bg", section)), 7)
            self.assertGreaterEqual(ratio(token("--muted", section), token("--bg", section)), 4.5)
            for seg in ("--run", "--done", "--bad", "--stall", "--wait"):
                self.assertGreaterEqual(ratio(token(seg, section), token("--bg", section)), 3, seg)

    def test_chrome_updates_on_every_render_and_fleet_poll(self):
        js = read("app.js")
        self.assertGreaterEqual(js.count("updateChrome();"), 3)   # render, fleet, open


if __name__ == "__main__":
    unittest.main()
