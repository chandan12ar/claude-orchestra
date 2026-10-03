"""The live pulse strip: its pure chart maths under node, and the wiring that keeps it safe and calm."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests import test_contrast as palette

NODE = shutil.which("node")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "orchestra", "static")


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
    js = read("app.js")
    prelude = "\n".join([extract_const(js, "PULSE_W"), extract_const(js, "PULSE_H"), extract_const(js, "PULSE_PAD")]
                        + [extract_function(js, n) for n in
                           ("fmtCount", "sparkGeometry", "stepGeometry", "levelAt", "pulseDelta", "pulseModel")])
    path = os.path.join(tempfile.mkdtemp(), "p.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prelude + "\nconsole.log(JSON.stringify(" + expression + "));")
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


def run_js(**over):
    """A run payload with a pulse block, as JS source; `over` replaces top-level keys."""
    pulse = {"start": 0, "end": 600, "now": 600, "live": True, "buckets": 4, "calls": [0, 4, 2, 6],
             "tokens": [0, 100, 400, 1200],
             "markers": [{"t": i * 10, "kind": "start", "agent_id": "a%d" % i, "label": "L%d" % i} for i in range(12)],
             "rate": {"window_s": 60, "calls_last": 6, "calls_prev": 2, "tokens_last": 800, "tokens_prev": 300}}
    run = {"totals": {"running": 2, "waiting": 1, "failed": 1, "orphaned": 0, "stalled": 1},
           "insights": {"pulse": pulse, "parallelism": {"series": [[0, 1], [100, 3], [500, 2]]}}}
    run.update(over)
    return json.dumps(run)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestChartMaths(unittest.TestCase):
    def test_spark_starts_at_zero_and_peaks_at_the_top(self):
        g = evaluate("sparkGeometry([0, 5, 10], 100, 40, 2)")
        self.assertTrue(g["line"].startswith("M0.0 38.0"))               # zero sits on the baseline
        self.assertEqual(g["last"], [100, 2])                            # the maximum sits at the top
        self.assertTrue(g["area"].endswith("Z"))

    def test_a_flat_zero_series_does_not_divide_by_zero(self):
        g = evaluate("sparkGeometry([0, 0, 0], 100, 40, 2)")
        self.assertEqual(g["last"], [100, 38])
        self.assertNotIn("NaN", g["line"])

    def test_empty_and_single_point_series(self):
        self.assertIsNone(evaluate("sparkGeometry([], 100, 40, 2)"))
        self.assertNotIn("NaN", evaluate("sparkGeometry([3], 100, 40, 2)")["line"])

    def test_the_y_axis_does_not_rescale_to_hide_a_small_change(self):
        # Values 9 and 10 must look almost level, not like a collapse from top to bottom.
        g = evaluate("sparkGeometry([10, 9], 100, 40, 0)")
        self.assertAlmostEqual(g["last"][1], 4.0, places=1)

    def test_step_series_holds_each_level_until_the_next_change_and_ends_at_the_right_edge(self):
        g = evaluate("stepGeometry([[0, 1], [10, 2]], 0, 20, 100, 40, 0)")
        self.assertEqual(g["last"], [100, 0])                            # level 2 is the peak, top of the chart
        self.assertIn("L100 0.0", g["line"])
        self.assertIn("L50.0 20.0L50.0 0.0", g["line"])                  # holds level 1 until t=10, then steps up to 2

    def test_level_at(self):
        series = "[[10, 1], [20, 3], [30, 0]]"
        self.assertEqual(evaluate("levelAt(%s, 5)" % series), 0)
        self.assertEqual(evaluate("levelAt(%s, 20)" % series), 3)
        self.assertEqual(evaluate("levelAt(%s, 99)" % series), 0)
        self.assertEqual(evaluate("levelAt(null, 5)"), 0)

    def test_delta_words(self):
        self.assertEqual(evaluate("pulseDelta(5, 3)"), {"dir": "up", "text": "+2"})
        self.assertEqual(evaluate("pulseDelta(1, 4)"), {"dir": "down", "text": "−3"})
        self.assertEqual(evaluate("pulseDelta(2, 2)"), {"dir": "flat", "text": "no change"})
        self.assertEqual(evaluate("pulseDelta(3000, 1000, fmtCount)")["text"], "+2.0k")


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestModel(unittest.TestCase):
    def model(self, **over):
        return evaluate("pulseModel(%s)" % run_js(**over))

    def test_nothing_to_chart_without_a_pulse_or_during_a_replay(self):
        self.assertIsNone(evaluate("pulseModel({totals: {}, insights: {}})"))
        self.assertIsNone(evaluate("pulseModel(null)"))
        self.assertIsNone(self.model(replay_at=5))

    def test_three_chart_tiles_in_order_with_their_values(self):
        m = self.model()
        self.assertEqual([t["key"] for t in m["tiles"]], ["running", "calls", "tokens"])
        self.assertEqual([t["value"] for t in m["tiles"]], ["3", "6", "1.2k"])    # running counts waiting agents too

    def test_running_compares_with_a_minute_ago(self):
        running = self.model()["tiles"][0]
        # now = 600, a minute ago = 540: the series stepped down to 2 at t=500, and 3 are running now.
        self.assertEqual(running["delta"], {"dir": "up", "text": "+1"})

    def test_calls_and_tokens_compare_with_the_window_before(self):
        m = self.model()
        self.assertEqual(m["tiles"][1]["delta"], {"dir": "up", "text": "+4"})
        self.assertEqual(m["tiles"][2]["delta"], {"dir": "up", "text": "+500"})

    def test_health_adds_up_what_needs_the_user(self):
        h = self.model()["health"]
        self.assertEqual((h["issues"], h["failed"], h["stalled"], h["waiting"]), (3, 1, 1, 1))

    def test_the_tape_shows_the_newest_eight_newest_first(self):
        marks = self.model()["marks"]
        self.assertEqual(len(marks), 8)
        self.assertEqual(marks[0]["label"], "L11")

    def test_an_ended_session_is_not_live(self):
        pulse = json.loads(run_js())["insights"]["pulse"]
        pulse["live"] = False
        run = json.loads(run_js())
        run["insights"]["pulse"] = pulse
        self.assertFalse(evaluate("pulseModel(%s)" % json.dumps(run))["live"])


class TestWiring(unittest.TestCase):
    def test_the_section_exists_in_the_page_and_the_static_report(self):
        from orchestra import report
        for html in (read("index.html"), report._SHELL):
            self.assertRegex(html, r'<section id="pulse"[^>]*\bhidden\b')

    def test_it_renders_with_the_run_and_ticks_between_polls(self):
        js = read("app.js")
        self.assertIn("renderPulse(state.run);", js)
        self.assertIn("tickPulse();", extract_function(js, "tickAgentClocks"))
        self.assertIn("setupPulse();", js)

    def test_text_from_transcripts_is_escaped_before_it_reaches_markup(self):
        js = read("app.js")
        self.assertIn("esc(m.label)", extract_function(js, "pulseTapeHtml"))
        self.assertIn("esc(tile.label)", extract_function(js, "pulseTileHtml"))

    def test_charts_update_in_place_so_the_live_dot_keeps_pulsing(self):
        body = extract_function(read("app.js"), "renderPulse")
        self.assertIn("updatePulseTile", body)
        self.assertIn("state.pulseShape", body)

    def test_the_collapsed_choice_survives_a_blocked_storage(self):
        js = read("app.js")
        for name in ("pulseCollapsed", "setPulseCollapsed"):
            self.assertIn("try {", extract_function(js, name))

    def test_motion_stops_under_reduced_motion(self):
        css = read("style.css")
        block = css[css.index("@media (prefers-reduced-motion: reduce) {\n  .pulse"):]
        self.assertIn("animation: none !important", block)

    def test_chip_glyphs_and_the_tooltip_are_readable_in_both_themes(self):
        css = palette.read_css()
        light = palette.tokens(palette.block(css, "\n:root {"))
        dark = dict(light)
        dark.update(palette.tokens(palette.block(css, ':root[data-theme="dark"]')))
        for name, t in (("light", light), ("dark", dark)):
            for status in ("running", "completed", "failed", "stalled"):
                self.assertGreaterEqual(palette.contrast(t["bar-ink"], t[status]), 3.0, "%s %s glyph" % (name, status))
            self.assertGreaterEqual(palette.contrast(t["panel"], t["ink"]), 4.5, name + " tooltip")
            self.assertGreaterEqual(palette.contrast(t["completed"], t["panel-2"]), 4.5, name + " all clear")


if __name__ == "__main__":
    unittest.main()
