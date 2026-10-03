import json
import os
import tempfile
import unittest
from unittest import mock

from orchestra import constants as C
from orchestra.build import RunBuilder
from orchestra.parent import parse_timestamp
from orchestra.pricing import (PriceError, PriceSource, PriceTable,
                               default_prices_path)
from tests.fixtures import agent_entry, build_session, ts, write_jsonl

TABLE = {"currency": "USD", "models": {
    "claude-opus-*": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_create": 12.5},
    "claude-haiku-*": {"input": 1.0, "output": 5.0},
}}


class TestParse(unittest.TestCase):
    def test_valid_table(self):
        t = PriceTable.parse(TABLE)
        self.assertEqual(t.currency, "USD")
        self.assertEqual(len(t.models), 2)

    def test_missing_cache_prices_default_to_the_input_price(self):
        # Overstates a cache read; never understates it.
        p = PriceTable.parse(TABLE).price_for("claude-haiku-4-5")
        self.assertEqual((p["cache_read"], p["cache_create"]), (1.0, 1.0))

    def test_bad_shapes_are_rejected_with_a_reason(self):
        bad = [None, [], {}, {"models": []},
               {"models": {"a": 5}},
               {"models": {"a": {"input": 1}}},                       # no output
               {"models": {"a": {"input": -1, "output": 1}}},
               {"models": {"a": {"input": "x", "output": 1}}},
               {"models": {"a": {"input": True, "output": 1}}},
               {"models": {"a": {"input": float("nan"), "output": 1}}},
               {"currency": "", "models": {"a": {"input": 1, "output": 1}}}]
        for raw in bad:
            with self.assertRaises(PriceError, msg=repr(raw)):
                PriceTable.parse(raw)

    def test_unknown_top_level_keys_are_ignored(self):
        PriceTable.parse(dict(TABLE, _comment="hi"))

    def test_matching_is_case_insensitive_and_first_match_wins(self):
        t = PriceTable.parse({"models": {
            "claude-opus-5*": {"input": 1, "output": 1},
            "claude-*": {"input": 9, "output": 9}}})
        self.assertEqual(t.price_for("CLAUDE-OPUS-5-5")["input"], 1.0)
        self.assertEqual(t.price_for("claude-haiku-4")["input"], 9.0)
        self.assertIsNone(t.price_for("gpt-x"))


class TestCost(unittest.TestCase):
    def test_arithmetic_is_per_million_tokens(self):
        t = PriceTable.parse(TABLE)
        cost, unpriced = t.cost({"claude-opus-5": {
            "input": 1_000_000, "output": 2_000_000,
            "cache_read": 10_000_000, "cache_create": 1_000_000}})
        # 10 + 100 + 10 + 12.5
        self.assertAlmostEqual(cost, 132.5)
        self.assertEqual(unpriced, [])

    def test_each_model_is_priced_separately(self):
        t = PriceTable.parse(TABLE)
        cost, _ = t.cost({"claude-opus-5": {"output": 1_000_000},
                          "claude-haiku-4-5": {"output": 1_000_000}})
        self.assertAlmostEqual(cost, 55.0)

    def test_an_unpriced_model_is_reported_not_counted_as_free_silently(self):
        t = PriceTable.parse(TABLE)
        cost, unpriced = t.cost({"claude-opus-5": {"output": 1_000_000},
                                 "mystery-9": {"output": 5_000_000}})
        self.assertAlmostEqual(cost, 50.0)
        self.assertEqual(unpriced, ["mystery-9"])

    def test_zero_usage_of_an_unknown_model_is_not_flagged(self):
        self.assertEqual(PriceTable.parse(TABLE).cost({"mystery": {}})[1], [])


class TestPriceSource(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "prices.json")

    def write(self, data):
        with open(self.path, "w") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))

    def test_missing_file_means_cost_is_simply_off(self):
        src = PriceSource(self.path)
        self.assertIsNone(src.get())
        self.assertEqual(src.error, "")

    def test_loads_and_reloads_when_the_file_changes(self):
        self.write(TABLE)
        src = PriceSource(self.path)
        self.assertEqual(src.get().price_for("claude-opus-5")["output"], 50.0)
        changed = json.loads(json.dumps(TABLE))
        changed["models"]["claude-opus-*"]["output"] = 99.0
        self.write(changed)
        # "50.0" -> "99.0" is the same size, so the change is only visible through
        # the mtime. Windows timestamps are coarse (a rewrite within one tick can
        # keep the old value), so move it explicitly instead of trusting "now".
        before = os.stat(self.path).st_mtime
        os.utime(self.path, (before + 10, before + 10))
        self.assertEqual(src.get().price_for("claude-opus-5")["output"], 99.0)

    def test_a_broken_file_is_reported_and_never_raises(self):
        self.write("{not json")
        src = PriceSource(self.path)
        self.assertIsNone(src.get())
        self.assertIn("unusable", src.error)

    def test_a_semantically_invalid_file_is_reported_with_the_reason(self):
        self.write({"models": {"a": {"input": 1}}})
        src = PriceSource(self.path)
        self.assertIsNone(src.get())
        self.assertIn("output", src.error)

    def test_fixing_the_file_clears_the_error(self):
        self.write("{bad")
        src = PriceSource(self.path)
        src.get()
        self.write(TABLE)
        os.utime(self.path, (1, 1))
        self.assertIsNotNone(src.get())
        self.assertEqual(src.error, "")

    def test_default_path_honours_the_override_and_is_not_under_dot_claude(self):
        with mock.patch.dict(os.environ, {"ORCHESTRA_PRICES": "/x/p.json"}):
            self.assertEqual(default_prices_path(), "/x/p.json")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORCHESTRA_PRICES", None)
            self.assertNotIn(os.sep + ".claude" + os.sep, default_prices_path())

    def test_the_shipped_example_is_valid(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "prices.example.json")
        with open(path, encoding="utf-8") as fh:
            PriceTable.parse(json.load(fh))


class RunCostCase(unittest.TestCase):
    """Shared setup: a real session built with a real price file."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)
        self.prices = os.path.join(tempfile.mkdtemp(), "p.json")
        with open(self.prices, "w") as fh:
            json.dump({"currency": "USD", "models": {
                "claude-haiku-*": {"input": 1.0, "output": 5.0, "cache_read": 0.1,
                                   "cache_create": 1.25},
                "claude-opus-*": {"input": 10.0, "output": 50.0}}}, fh)
        # The fixture's a1 reports 100 in / 50 out, a2 200 in / 20 out, a3 210 in.

    def build(self, prices=True):
        now = parse_timestamp(ts(150))
        os.utime(self.paths.session_jsonl, (now, now))
        src = PriceSource(self.prices) if prices else None
        return RunBuilder(self.paths, now_fn=lambda: now, prices=src).refresh()


class TestRunCost(RunCostCase):
    """Cost through the real builder, including the orchestrator's own usage."""

    def test_no_prices_means_no_cost_anywhere(self):
        run = self.build(prices=False)
        self.assertEqual(run.cost, {"enabled": False})
        self.assertTrue(all(a.cost is None for a in run.agents))

    def test_each_agent_is_priced_from_its_own_tokens(self):
        run = self.build()
        # a1: 100 in * $1/M + 50 out * $5/M
        self.assertAlmostEqual(run.agent("a1").cost, (100 * 1 + 50 * 5) / 1e6)
        self.assertAlmostEqual(run.agent("a2").cost, (200 * 1 + 20 * 5) / 1e6)

    def test_run_total_is_agents_plus_orchestrator(self):
        main = [{"type": "assistant", "timestamp": ts(5), "message": {
            "id": "m1", "model": "claude-opus-5", "content": [],
            "usage": {"input_tokens": 1000, "output_tokens": 2000}}}]
        with open(self.paths.session_jsonl, "a", encoding="utf-8") as fh:
            for e in main:
                fh.write(json.dumps(e) + "\n")
        run = self.build()
        orchestrator = (1000 * 10 + 2000 * 50) / 1e6
        agents = sum(a.cost for a in run.agents)
        self.assertAlmostEqual(run.cost["orchestrator"], orchestrator)
        self.assertAlmostEqual(run.cost["total"], agents + orchestrator)
        self.assertEqual(run.orchestrator["model"], "claude-opus-5")
        self.assertFalse(run.cost["partial"])

    def test_an_unpriced_model_marks_the_total_partial(self):
        with open(self.paths.session_jsonl, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {
                "id": "m", "model": "mystery-1", "content": [],
                "usage": {"output_tokens": 500}}}) + "\n")
        run = self.build()
        self.assertTrue(run.cost["partial"])
        self.assertEqual(run.cost["unpriced_models"], ["mystery-1"])

    def test_a_broken_price_file_surfaces_its_error(self):
        with open(self.prices, "w") as fh:
            fh.write("{oops")
        run = self.build()
        self.assertFalse(run.cost["enabled"])
        self.assertIn("unusable", run.cost["error"])

    def test_the_orchestrator_is_counted_once_per_message(self):
        entry = {"type": "assistant", "message": {
            "id": "m1", "model": "claude-opus-5", "content": [],
            "usage": {"output_tokens": 100}}}
        with open(self.paths.session_jsonl, "a", encoding="utf-8") as fh:
            for _ in range(4):               # four content blocks, one message
                fh.write(json.dumps(entry) + "\n")
        self.assertEqual(self.build().orchestrator["tokens"], {"output": 100})

    def test_orchestrator_tokens_survive_incremental_refreshes(self):
        now = parse_timestamp(ts(150))
        os.utime(self.paths.session_jsonl, (now, now))
        builder = RunBuilder(self.paths, now_fn=lambda: now,
                             prices=PriceSource(self.prices))
        entry = lambda i: json.dumps({"type": "assistant", "message": {
            "id": "m%d" % i, "model": "claude-opus-5", "content": [],
            "usage": {"output_tokens": 10}}}) + "\n"
        with open(self.paths.session_jsonl, "a") as fh:
            fh.write(entry(1))
        self.assertEqual(builder.refresh().orchestrator["tokens"], {"output": 10})
        with open(self.paths.session_jsonl, "a") as fh:
            fh.write(entry(2))
        self.assertEqual(builder.refresh().orchestrator["tokens"], {"output": 20})
        self.assertEqual(builder.refresh().orchestrator["tokens"], {"output": 20})


class TestBudget(RunCostCase):
    def run_with_budget(self, limit, warn=0.8):
        with mock.patch.object(C, "BUDGET", limit), \
                mock.patch.object(C, "BUDGET_WARN_RATIO", warn):
            return self.build()

    def spend(self):
        return self.build().cost["total"]

    def test_no_budget_means_no_budget_block(self):
        self.assertIsNone(self.build().cost["budget"])

    def test_states_ok_warn_exceeded(self):
        total = self.spend()
        self.assertEqual(self.run_with_budget(total * 10).cost["budget"]["state"], "ok")
        self.assertEqual(self.run_with_budget(total * 1.1).cost["budget"]["state"], "warn")
        self.assertEqual(self.run_with_budget(total * 0.9).cost["budget"]["state"], "exceeded")

    def test_exactly_at_the_limit_counts_as_exceeded(self):
        total = self.spend()
        self.assertEqual(self.run_with_budget(total).cost["budget"]["state"], "exceeded")

    def test_ratio_is_reported(self):
        total = self.spend()
        b = self.run_with_budget(total * 2).cost["budget"]
        self.assertAlmostEqual(b["ratio"], 0.5)
        self.assertAlmostEqual(b["limit"], total * 2)


if __name__ == "__main__":
    unittest.main()
