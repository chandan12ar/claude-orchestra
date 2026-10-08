"""Where tokens were wasted: cache rebuilds, the biggest results, unchanged re-reads, and how they are shown."""

import json
import tempfile
import time
import unittest

from orchestra import waste as W
from orchestra.livestate import ANSWERED, Wait
from orchestra.pricing import PriceTable
from tests import fake_secrets as fake
from tests.fixtures import ts

PRICES = PriceTable.parse({"currency": "USD", "models": {
    "claude-sonnet*": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_create": 3.75}}})


def call(mid, at, created, read, model="claude-sonnet-5-5"):
    return {"type": "assistant", "timestamp": ts(at), "message": {
        "id": mid, "role": "assistant", "model": model, "content": [{"type": "text", "text": "x"}],
        "usage": {"input_tokens": 5, "output_tokens": 50,
                  "cache_creation_input_tokens": created, "cache_read_input_tokens": read}}}


def use(uid, at, name, **params):
    return {"type": "assistant", "timestamp": ts(at), "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": name, "input": params}]}}


def result(uid, at, content, is_error=False):
    return {"type": "user", "timestamp": ts(at), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": content, "is_error": is_error}]}}


def compaction(at):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": ts(at),
            "compactMetadata": {"trigger": "auto", "preTokens": 500000, "durationMs": 40000}}


def log(*entries):
    out = W.WasteLog()
    out.ingest(list(entries))
    return out


class TestRebuilds(unittest.TestCase):
    def test_each_cause(self):
        l = log(call("m1", 0, 80000, 0),                     # the first call builds the cache: not a rebuild
                call("m2", 30, 2000, 80000),                  # a normal turn reads it back
                call("m3", 30 + 400, 82000, 0),               # 6m40s idle
                call("m4", 430 + 4000, 84000, 1000),          # over an hour idle
                call("m5", 4440, 85000, 0, model="claude-opus-5-5"),   # the model changed
                compaction(4450), call("m6", 4500, 40000, 0, model="claude-opus-5-5"),
                call("m7", 4510, 41000, 500, model="claude-opus-5-5"))  # ten seconds later, no reason seen
        self.assertEqual([r["cause"] for r in l.rebuilds()],
                         [W.IDLE_SHORT, W.IDLE_LONG, W.MODEL, W.COMPACTION, W.UNKNOWN])
        self.assertEqual(l.rebuilds()[1]["gap_s"], 4000)

    def test_a_big_turn_that_also_reads_the_cache_is_not_a_rebuild(self):
        self.assertEqual(log(call("m1", 0, 50000, 0), call("m2", 900, 30000, 60000),
                             call("m3", 950, 9000, 0)).rebuilds(), [])

    def test_streamed_copies_of_one_message_count_once_with_the_latest_usage(self):
        l = log(call("m1", 0, 50000, 0), call("m2", 600, 100, 0), call("m2", 601, 60000, 0))
        self.assertEqual(len(l.calls), 2)
        [r] = l.rebuilds()
        self.assertEqual((r["tokens"], r["at"]), (60000, l.calls["m2"][0]))


class TestResults(unittest.TestCase):
    def test_big_results_keep_tool_target_and_size_and_secrets_are_scrubbed(self):
        big = "line\n" * 6000
        l = log(use("t1", 1, "Read", file_path="/p/" + fake.GITHUB_TOKEN + "/app.js"), result("t1", 2, big),
                use("t2", 3, "Bash", command="ls"), result("t2", 4, "small"),
                call("m1", 5, 100, 1000), call("m2", 6, 100, 1000))
        [b] = l.big
        self.assertEqual((b["tool"], b["chars"]), ("Read", len(big)))
        self.assertNotIn("ABCDEFGHIJKLMNOP", b["target"])
        d = l.to_dict()["big"][0]
        self.assertEqual((d["tokens"], d["carried"]), (len(big) // 4, 2))

    def test_rereads_count_only_identical_content_of_the_same_range(self):
        l = log(use("a", 1, "Read", file_path="/p/x.py"), result("a", 2, "same text"),
                use("b", 3, "Read", file_path="/p/x.py"), result("b", 4, "same text"),
                use("c", 5, "Read", file_path="/p/x.py", offset=10), result("c", 6, "same text"),
                use("d", 7, "Read", file_path="/p/x.py"), result("d", 8, "edited text"),
                use("e", 9, "Read", file_path="/p/y.png"), result("e", 10, [{"type": "image", "source": {"data": "AAA"}}]),
                use("f", 11, "Read", file_path="/p/y.png"), result("f", 12, [{"type": "image", "source": {"data": "AAA"}}]),
                use("g", 13, "Read", file_path="/p/z"), result("g", 14, "boom", is_error=True),
                use("h", 15, "Read", file_path="/p/z"), result("h", 16, "boom", is_error=True))
        self.assertEqual((l.rereads, l.reread_chars), (2, len("same text")))

    def test_snapshot_is_frozen(self):
        l = log(call("m1", 0, 50000, 0))
        snap = l.snapshot()
        l.ingest([call("m2", 600, 60000, 0)])
        self.assertEqual((len(snap.calls), len(l.calls)), (1, 2))


class TestSummary(unittest.TestCase):
    def idle(self):
        return log(call("m1", 0, 50000, 0), call("m2", 10, 500, 50000), call("m3", 1210, 52000, 0))

    def test_extra_cost_is_above_the_cache_read_price(self):
        s = W.summary([("", "Main session", self.idle())], PRICES, [], now=2000)
        self.assertEqual((s["rebuilds"], s["rebuilt_tokens"]), (1, 52000))
        self.assertAlmostEqual(s["extra_cost"], 52000 * (3.75 - 0.3) / 1e6)
        self.assertEqual(s["currency"], "USD")
        self.assertAlmostEqual(s["share_of_cache_writes"], 52000 / 102500)
        none = W.summary([("", "Main session", self.idle())], None, [], now=2000)
        self.assertEqual((none["extra_cost"], none["rows"][0]["extra_cost"]), (None, None))

    def test_a_rebuild_after_a_long_approval_names_the_wait(self):
        start = W.parse_timestamp(ts(20))
        waits = [Wait(agent_id="a1", kind="permission", start=start, end=start + 1100, state=ANSWERED),
                 Wait(agent_id="other", kind="permission", start=start, end=start + 1150, state=ANSWERED)]
        s = W.summary([("a1", "Build it", self.idle())], PRICES, waits, now=start + 5000)
        row = s["rows"][0]
        self.assertEqual((row["wait_s"], row["wait_kind"], row["label"], s["while_waiting"]),
                         (1100, "permission", "Build it", 1))

    def test_unpriced_models_are_said_not_guessed(self):
        l = log(call("m1", 0, 50000, 0, model="mystery"), call("m2", 900, 50000, 0, model="mystery"))
        s = W.summary([("", "Main", l)], PRICES, [], now=1000)
        self.assertEqual((s["extra_cost"], s["unpriced"]), (None, True))

    def test_nothing_recorded_is_none(self):
        self.assertIsNone(W.summary([("", "Main", W.WasteLog()), ("a", "A", None)], PRICES, [], 0))


class TestBuiltAndShown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        from orchestra.pricing import PriceSource
        import os
        cls.now = time.time()
        root = tempfile.mkdtemp()
        cls.paths, cls.agents = demo.build_demo(root, now=cls.now)
        prices = os.path.join(root, "p.json")
        demo.write_prices(prices)
        cls.builder = RunBuilder(cls.paths, now_fn=lambda: cls.now + 10, prices=PriceSource(prices))
        cls.built = cls.builder.refresh()

    def test_the_demo_main_session_rebuilt_after_its_compaction(self):
        w = self.built.insights["waste"]
        main = [r for r in w["rows"] if r["agent_id"] == ""]
        self.assertEqual([r["cause"] for r in main], [W.COMPACTION])
        self.assertIsNotNone(w["extra_cost"])
        self.assertEqual(w["big"][0]["tool"], "Read")

    def test_what_the_main_session_recorded_survives_later_reads(self):
        from orchestra import demo
        demo.Simulator(self.paths, self.agents).tick(now=self.now + 5)
        again = self.builder.refresh().insights["waste"]
        self.assertIn(W.COMPACTION, [r["cause"] for r in again["rows"] if r["agent_id"] == ""])

    def test_the_agent_panel_carries_its_own(self):
        design = [a for a in self.built.agents if a.description == "Design the checkout v2 architecture"][0]
        d = design.to_detail_dict()["waste"]
        self.assertEqual(d["big"][0]["tool"], "Read")
        self.assertEqual(design.to_light_dict()["waste"], {"rebuilds": 0, "rebuilt_tokens": 0})

    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    def test_the_card(self):
        ui = self.ui()
        html = ui.card(ui.render_insights(ui.demo_run()), "Where tokens were wasted")
        self.assertIn("after a compaction", html)
        self.assertIn("rewritten to the cache", html)
        self.assertIn("Biggest things pulled into context", html)
        for bad in ("NaN", "undefined", "null"):
            self.assertNotIn(bad, html)

    def test_a_wait_and_idle_rows_say_why(self):
        ui = self.ui()
        w = {"rebuilds": 2, "rebuilt_tokens": 600000, "share_of_cache_writes": 0.33, "extra_cost": 2.07,
             "currency": "USD", "unpriced": False, "by_cause": {"idle_long": 400000, "idle_short": 200000},
             "while_waiting": 1, "rereads": 3, "reread_tokens": 1200, "big": [],
             "rows": [{"agent_id": "", "label": "Main session", "cause": "idle_long", "gap_s": 6862, "tokens": 400000,
                       "at": 1, "extra_cost": 1.38, "wait_s": None, "wait_kind": None, "model": "m"},
                      {"agent_id": "a1", "label": "Build it", "cause": "idle_short", "gap_s": 720, "tokens": 200000,
                       "at": 2, "extra_cost": 0.69, "wait_s": 700, "wait_kind": "permission", "model": "m"}]}
        out = ui.run_js(ui.INSIGHT_FNS, [],
                        "console.log(JSON.stringify(insWaste({waste: %s})));" % json.dumps(w))
        self.assertIn("idle 1h 54m", out)
        self.assertIn("while waiting for your approval (11m 40s)", out)
        self.assertIn('data-agent="a1"', out)
        self.assertIn("3 re-reads of unchanged files added about 1.2k tokens.", out)
        w["rereads"], w["reread_tokens"] = 1, 40
        tiny = ui.run_js(ui.INSIGHT_FNS, [], "console.log(JSON.stringify(insWaste({waste: %s})));" % json.dumps(w))
        self.assertIn("1 re-read of an unchanged file added next to nothing.", tiny)

    def test_hostile_values_are_text(self):
        ui = self.ui()
        evil = '"><img src=x onerror=alert(1)>'

        def hit(summary):
            w = summary["insights"]["waste"]
            for r in w["rows"]:
                r["label"], r["cause"], r["model"] = evil, evil, evil
            for b in w["big"]:
                b["label"], b["tool"], b["target"] = evil, evil, evil
        html = ui.card(ui.render_insights(ui.demo_run(hit)), "Where tokens were wasted")
        self.assertNotIn("<img", html)

    def test_the_panel_line(self):
        ui = self.ui()
        d = {"rebuilds": [{"cause": "idle_long", "gap_s": 4400, "tokens": 500000},
                          {"cause": "compaction", "gap_s": 30, "tokens": 340000}],
             "rebuilt_tokens": 840000, "big": [], "rereads": 0, "reread_tokens": 0}
        out = ui.run_js(("esc", "fmtDuration", "fmtCount", "wasteCause", "wasteRow"), [],
                        "console.log(JSON.stringify([wasteRow(%s), wasteRow(null),"
                        " wasteRow({rebuilds: [], rebuilt_tokens: 0, big: [], rereads: 0})]));" % json.dumps(d))
        self.assertIn("Cache rebuilt 2× · 840.0k tokens · idle 1h 13m, after a compaction", out[0])
        self.assertEqual(out[1:], ["", ""])
        self.assertIn("wasteRow(agent.waste)", ui.fn(ui.read("app.js"), "openDrawer"))


if __name__ == "__main__":
    unittest.main()
