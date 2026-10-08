"""Context pressure: how full each context got, every compaction, and how it is shown."""

import json
import tempfile
import time
import unittest
from types import SimpleNamespace

from orchestra import pressure as P
from orchestra import waste as W
from orchestra.parent import parse_timestamp
from tests.fixtures import ts


def call(mid, at, read=0, created=0, fresh=0, model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts(at), "message": {
        "id": mid, "role": "assistant", "model": model, "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": fresh, "output_tokens": 50,
                  "cache_creation_input_tokens": created, "cache_read_input_tokens": read}}}


def compaction(at, pre=None, post=None, trigger="auto", uuid=None):
    meta = {"trigger": trigger, "durationMs": 21400}
    if pre is not None:
        meta["preTokens"] = pre
    if post is not None:
        meta["postTokens"] = post
    entry = {"type": "system", "subtype": "compact_boundary", "timestamp": ts(at), "compactMetadata": meta}
    if uuid:
        entry["uuid"] = uuid
    return entry


def log(*entries):
    out = W.WasteLog()
    out.ingest(list(entries))
    return out


def agent(agent_id, waste, status="completed", description="Build it"):
    return SimpleNamespace(agent_id=agent_id, description=description, status=status, waste=waste)


def run(main=None, agents=(), live=False):
    return SimpleNamespace(main_waste=main, agents=list(agents), session_live=live)


class TestLimits(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(P.limit_for("claude-haiku-4-5", ""), 200_000)
        self.assertEqual(P.limit_for("claude-opus-5-5", ""), 1_000_000)
        self.assertEqual(P.limit_for("claude-sonnet-5", ""), 1_000_000)
        self.assertEqual(P.limit_for("", ""), 1_000_000)

    def test_an_override_wins_and_bad_parts_are_skipped(self):
        raw = "opus=500000, sonnet=abc, =5, haiku=0, HAIKU=150000"
        self.assertEqual(P.limit_for("claude-opus-5-5", raw), 500_000)
        self.assertEqual(P.limit_for("claude-sonnet-5", raw), 1_000_000)
        self.assertEqual(P.limit_for("claude-haiku-4-5", raw), 150_000)

    def test_the_environment_is_read(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"ORCHESTRA_CONTEXT_LIMITS": "sonnet=400000"}):
            self.assertEqual(P.limit_for("claude-sonnet-5"), 400_000)


class TestCurve(unittest.TestCase):
    def test_context_is_input_plus_cache_read_plus_cache_write_once_per_message(self):
        l = log(call("m1", 0, read=1000, created=500, fresh=20),
                call("m1", 1, read=1000, created=500, fresh=30),       # the same message again: latest wins
                call("m2", 10, read=4000, created=100, fresh=10),
                call("m3", 20))                                        # no tokens: skipped
        self.assertEqual([p[1] for p in P.series(l)], [1530, 4110])

    def test_a_compaction_adds_the_size_recorded_before_it_and_now_comes_from_calls(self):
        l = log(call("m1", 0, read=90000), call("m2", 60, read=120000),
                compaction(4000, pre=180000, post=30000), call("m3", 4010, created=30000))
        self.assertEqual([p[1] for p in P.series(l)], [90000, 120000, 180000, 30000])
        peak = P.peak_of(l)
        self.assertEqual((peak["tokens"], peak["model"], peak["limit"]), (180000, "claude-opus-5-5", 1_000_000))
        self.assertAlmostEqual(peak["fill"], 0.18)
        self.assertEqual((peak["now"], peak["now_fill"]), (30000, 0.03))

    def test_a_compaction_as_the_last_thing_does_not_become_now(self):
        peak = P.peak_of(log(call("m1", 0, read=90000), compaction(50, pre=95000)))
        self.assertEqual((peak["tokens"], peak["now"]), (95000, 90000))

    def test_haiku_fills_against_its_own_window(self):
        peak = P.peak_of(log(call("m1", 0, read=170000, model="claude-haiku-4-5")))
        self.assertEqual(peak["limit"], 200_000)
        self.assertAlmostEqual(peak["fill"], 0.85)

    def test_nothing_recorded(self):
        self.assertIsNone(P.peak_of(None))
        self.assertIsNone(P.peak_of(log(call("m1", 0))))
        self.assertIsNone(P.summary(run(log(), [agent("a", None), agent("b", log())])))

    def test_thinning_keeps_the_peak(self):
        points = [(float(i), 1000 + i, "m") for i in range(1000)]
        points[777] = (777.0, 999_999, "m")
        thin = P._thin(points)
        self.assertLessEqual(len(thin), P.MAX_POINTS)
        self.assertIn([777.0, 999_999], thin)
        self.assertEqual(P._thin(points[:5]), [[float(i), 1000 + i] for i in range(5)])


class TestCompactions(unittest.TestCase):
    def test_the_same_boundary_written_twice_counts_once(self):
        l = log(compaction(100, pre=500000, uuid="c1"), compaction(100, pre=500000, uuid="c1"),
                compaction(900, pre=400000, trigger="manual", uuid="c2"))
        self.assertEqual([(c["trigger"], c["pre_tokens"]) for c in l.compactions],
                         [("auto", 500000), ("manual", 400000)])
        again = l.snapshot()
        again.ingest([compaction(100, pre=500000, uuid="c1")])
        self.assertEqual(len(again.compactions), 2)

    def test_odd_metadata_is_dropped_not_trusted(self):
        entry = compaction(100, uuid="c1")
        entry["compactMetadata"].update({"trigger": "<b>", "preTokens": True, "postTokens": -4, "durationMs": "x"})
        c = log(entry).compactions[0]
        self.assertEqual((c["trigger"], c["pre_tokens"], c["post_tokens"], c["duration_s"]), ("", None, None, None))

    def test_after_falls_back_to_the_next_call_when_not_recorded(self):
        main = log(call("m1", 0, read=200000), compaction(100, pre=210000), call("m2", 130, created=42000))
        c = P.summary(run(main))["main"]["compactions"][0]
        self.assertEqual((c["pre_tokens"], c["post_tokens"], c["duration_s"]), (210000, 42000, 21.4))
        recorded = log(call("m1", 0, read=200000), compaction(100, pre=210000, post=39000), call("m2", 130, created=42000))
        self.assertEqual(P.summary(run(recorded))["main"]["compactions"][0]["post_tokens"], 39000)


class TestSummary(unittest.TestCase):
    def test_agents_ranked_by_peak_and_capped(self):
        agents = [agent("a%d" % i, log(call("m", 0, read=1000 * (i + 1))), description="Agent %d" % i)
                  for i in range(14)]
        s = P.summary(run(None, agents))
        self.assertIsNone(s["main"])
        self.assertEqual([a["agent_id"] for a in s["agents"][:2]], ["a13", "a12"])
        self.assertEqual((len(s["agents"]), s["agent_count"]), (P.SHOWN_AGENTS, 14))

    def test_near_the_window_only_while_the_session_is_live_and_the_agent_still_runs(self):
        full = lambda: log(call("m", 0, read=180000, model="claude-haiku-4-5"))
        main = log(call("m", 0, read=850000))
        agents = [agent("run", full(), "running", "Still going"), agent("done", full(), "completed"),
                  agent("calm", log(call("m", 0, read=1000)), "running")]
        live = P.summary(run(main, agents, live=True))
        self.assertEqual([(n["agent_id"], n["label"]) for n in live["near"]],
                         [("", "Main session"), ("run", "Still going")])
        self.assertAlmostEqual(live["near"][0]["fill"], 0.85)
        self.assertEqual(P.summary(run(main, agents, live=False))["near"], [])

    def test_agent_labels_are_scrubbed(self):
        from tests import fake_secrets as fake
        s = P.summary(run(None, [agent("a", log(call("m", 0, read=10)), description="use " + fake.GITHUB_PAT)]))
        self.assertNotIn(fake.GITHUB_PAT, json.dumps(s))


class TestBuiltFromTheDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        cls.now = time.time()
        cls.paths, cls.agents = demo.build_demo(tempfile.mkdtemp(), now=cls.now)
        cls.builder = RunBuilder(cls.paths, now_fn=lambda: cls.now + 10)
        cls.built = cls.builder.refresh()

    def test_the_main_session_filled_up_then_compacted(self):
        m = self.built.insights["pressure"]["main"]
        self.assertEqual(m["tokens"], 186000)
        self.assertEqual([(c["trigger"], c["pre_tokens"]) for c in m["compactions"]], [("auto", 186000)])
        self.assertLess(m["now"], m["tokens"])
        self.assertEqual(max(p[1] for p in m["points"]), 186000)

    def test_each_agent_carries_its_peak(self):
        design = [a for a in self.built.agents if a.description == "Design the checkout v2 architecture"][0]
        peak = design.to_light_dict()["context_peak"]
        self.assertEqual(peak["limit"], 1_000_000)
        self.assertGreater(peak["tokens"], 10000)
        self.assertEqual(peak["compactions"], 0)
        self.assertEqual(design.to_detail_dict()["context_peak"], peak)

    def test_it_survives_later_reads(self):
        from orchestra import demo
        demo.Simulator(self.paths, self.agents).tick(now=self.now + 5)
        again = self.builder.refresh().insights["pressure"]["main"]
        self.assertEqual(len(again["compactions"]), 1)
        self.assertEqual(again["tokens"], 186000)


class TestShown(unittest.TestCase):
    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    FNS = ("esc", "fmtDuration", "fmtCount", "fmtPct", "fmtClock", "insMetric", "insCard", "insRank",
           "fmtWindow", "compactionCause", "insPressureChart", "insPressure")

    def card(self, p, width=900):
        ui = self.ui()
        return ui.run_js(self.FNS, [], "console.log(JSON.stringify(insPressure({pressure: %s}, %d)));"
                         % (json.dumps(p), width))

    def test_the_demo_card(self):
        ui = self.ui()
        html = ui.card(ui.render_insights(ui.demo_run()), "How full each context got")
        self.assertIn("peak in the main session", html)
        self.assertIn("186.0k → 61.1k", html)
        self.assertIn("Claude Code compacted it", html)
        self.assertIn('class="pressure-mark"', html)
        self.assertIn("The main session&#39;s context over time. Peak 186.0k tokens, 1 compaction.", html)
        self.assertIn("Agents by peak context", html)
        self.assertNotIn("dashed line", html)              # 186k of 1M: 80% is off the chart
        for bad in ("NaN", "undefined", "null", "Infinity"):
            self.assertNotIn(bad, html)

    def test_it_sits_after_waste(self):
        ui = self.ui()
        body = ui.fn(ui.read("app.js"), "renderInsights")
        self.assertIn("insWaste(ins) + insPressure(ins, width)", body)

    def test_near_and_manual_and_unknown_sizes(self):
        p = {"main": {"tokens": 900000, "at": 50, "model": "m", "limit": 1000000, "fill": 0.9, "now": 870000,
                      "now_fill": 0.87, "points": [[0, 100000], [50, 900000], [60, 870000]],
                      "compactions": [{"at": 30, "trigger": "manual", "pre_tokens": None, "post_tokens": None,
                                       "duration_s": None}]},
             "agents": [{"agent_id": "a1", "label": "Haiku helper", "status": "running", "tokens": 190000,
                         "limit": 200000, "fill": 0.95, "now": 190000, "now_fill": 0.95, "compactions": 2}],
             "agent_count": 3, "agent_compactions": 2, "near_at": 0.8,
             "near": [{"agent_id": "", "label": "Main session", "fill": 0.87, "tokens": 870000}]}
        html = self.card(p)
        self.assertIn("Near the window now: Main session (87%).", html)
        self.assertIn("Agents were compacted 2 times.", html)
        self.assertIn("? → ?", html)
        self.assertIn("you ran /compact", html)
        self.assertIn(">/compact</text>", html)
        self.assertIn("The dashed line is 80% of the assumed 1M window.", html)
        self.assertIn("background:var(--stalled)", html)    # the near-full agent's bar
        self.assertIn("compacted 2×", html)
        self.assertIn("And 2 more.", html)
        self.assertIn('data-agent="a1"', html)
        self.assertNotIn("assumed window, so", html)
        p["agents"][0]["fill"] = 1.3
        self.assertIn("A context went past its assumed window, so that window is too small", self.card(p))

    def test_agents_only_and_a_single_point(self):
        p = {"main": None, "agents": [{"agent_id": "a1", "label": "Solo", "status": "completed", "tokens": 5000,
                                       "limit": 1000000, "fill": 0.005, "now": 5000, "now_fill": 0.005,
                                       "compactions": 0}],
             "agent_count": 1, "agent_compactions": 0, "near": [], "near_at": 0.8}
        html = self.card(p)
        self.assertNotIn("<svg", html)
        self.assertIn("fullest agent", html)
        self.assertEqual(self.card(None), "")
        for bad in ("NaN", "undefined", "null"):
            self.assertNotIn(bad, html)

    def test_hostile_values_are_text(self):
        ui = self.ui()
        evil = '"><img src=x onerror=alert(1)>'

        def hit(summary):
            p = summary["insights"]["pressure"]
            for a in p["agents"]:
                a["label"], a["agent_id"] = evil, evil
            for c in p["main"]["compactions"]:
                c["trigger"] = evil
        html = ui.card(ui.render_insights(ui.demo_run(hit)), "How full each context got")
        self.assertNotIn("<img", html)

    def test_the_panel_line(self):
        ui = self.ui()
        out = ui.run_js(("esc", "fmtCount", "fmtPct", "fmtWindow", "pressureRow"), [],
                        "console.log(JSON.stringify([pressureRow(%s), pressureRow(%s), pressureRow(null)]));" % (
                            json.dumps({"tokens": 41178, "limit": 1000000, "fill": 0.041, "compactions": 0}),
                            json.dumps({"tokens": 150000, "limit": 200000, "fill": 0.75, "compactions": 3})))
        self.assertEqual(out[0], "<dt>context</dt><dd>Peak 41.2k tokens · 4% of an assumed 1M window</dd>")
        self.assertIn("75% of an assumed 200k window · compacted 3×", out[1])
        self.assertEqual(out[2], "")
        self.assertIn("pressureRow(agent.context_peak)", ui.fn(ui.read("app.js"), "openDrawer"))

    def test_the_health_box(self):
        ui = self.ui()
        run_ = {"agents": [], "insights": {"pressure": {"near": [
            {"agent_id": "", "label": "Main session", "fill": 0.86, "tokens": 860000},
            {"agent_id": "a1", "label": "<b>Helper</b>", "fill": 0.91, "tokens": 182000}]}}}
        body = """
const opened = [];
function openDrawer(id) { opened.push("drawer:" + id); }
function openPressure() { opened.push("insights"); }
global.document = {createElement: () => ({children: [], attrs: {}, textContent: "",
  setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); }})};
box.appendChild = (c) => { box.list = c; };
renderHealth(%s);
const items = box.list.children;
items.forEach((i) => i.onclick());
console.log(JSON.stringify({head: box.innerHTML, items: items.map((i) => [i.textContent, i.attrs["data-kind"]]),
                            opened: opened, hidden: box.hidden}));
""" % json.dumps(run_)
        out = ui.run_js(("fmtCount", "fmtPct", "renderHealth"), [], body)
        self.assertEqual(out["head"], "<strong>The main session and 1 agent(s) need attention</strong>")
        self.assertEqual(out["items"], [["CONTEXT 86% FULL — Main session (860.0k tokens)", "context"],
                                        ["CONTEXT 91% FULL — <b>Helper</b> (182.0k tokens)", "context"]])
        self.assertEqual(out["opened"], ["insights", "drawer:a1"])
        self.assertFalse(out["hidden"])
        alone = ui.run_js(("fmtCount", "fmtPct", "renderHealth"), [], body.replace(
            json.dumps(run_), json.dumps({"agents": [], "insights": {"pressure": {"near": [run_["insights"]["pressure"]["near"][0]]}}})))
        self.assertEqual(alone["head"], "<strong>The main session needs attention</strong>")
        quiet = ui.run_js(("fmtCount", "fmtPct", "renderHealth"), [], body.replace(
            json.dumps(run_), json.dumps({"agents": [], "insights": None})).replace(
            "const items = box.list.children;", "const items = [];"))
        self.assertTrue(quiet["hidden"])


if __name__ == "__main__":
    unittest.main()
