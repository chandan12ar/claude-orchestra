"""Spend over the run: a running total from every API call, main session and agents apart."""

import unittest

from orchestra import spend
from orchestra.parent import parse_timestamp
from orchestra.pricing import PriceTable
from orchestra.waste import WasteLog
from tests.fixtures import ts

T0 = parse_timestamp(ts(0))
PRICES = PriceTable.parse({"currency": "USD", "models": {
    "claude-sonnet-*": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_create": 3.75},
    "claude-haiku-*": {"input": 1.0, "output": 5.0}}})


def call(mid, at, model="claude-sonnet-5-5", inp=0, out=0, create=0, read=0):
    return {"type": "assistant", "timestamp": ts(at), "message": {
        "id": mid, "model": model, "content": [],
        "usage": {"input_tokens": inp, "output_tokens": out,
                  "cache_creation_input_tokens": create, "cache_read_input_tokens": read}}}


def log(*entries):
    w = WasteLog()
    w.ingest(list(entries))
    return w


class SpendTestCase(unittest.TestCase):
    def setUp(self):
        # Main: 1M fresh input at 0s (= 3.00 on sonnet), 100k output at 600s (= 1.50).
        # Agent a1 (sonnet): 1M cache read at 250s (= 0.30) and 200k cache writes at 310s (= 0.75).
        # Agent a2 (haiku): 1M input at 320s (= 1.00), the same message again (counted once).
        self.main = log(call("m1", 0, inp=1_000_000), call("m2", 600, out=100_000))
        self.a1 = log(call("x1", 250, read=1_000_000), call("x2", 310, create=200_000))
        self.a2 = log(call("y1", 320, "claude-haiku-4-5", inp=1_000_000),
                      call("y1", 320, "claude-haiku-4-5", inp=1_000_000))
        self.sources = [("", "Main session", self.main), ("a1", "Agent one", self.a1), ("a2", "Agent two", self.a2)]

    def money(self, **kw):
        kw.setdefault("now", T0 + 900)
        kw.setdefault("live", False)
        return spend.summary(self.sources, PRICES, **kw)


class TestTotals(SpendTestCase):
    def test_money_split_into_main_and_agents(self):
        s = self.money()
        self.assertEqual((s["unit"], s["currency"]), ("money", "USD"))
        self.assertAlmostEqual(s["main_total"], 4.50)
        self.assertAlmostEqual(s["agents_total"], 0.30 + 0.75 + 1.00)
        self.assertAlmostEqual(s["total"], 6.55)

    def test_without_prices_it_counts_fresh_tokens(self):
        s = spend.summary(self.sources, None, now=T0 + 900, live=False)
        self.assertEqual((s["unit"], s["currency"]), ("tokens", None))
        # input + output + cache writes; cache reads are not fresh
        self.assertEqual(s["main_total"], 1_100_000)
        self.assertEqual(s["agents_total"], 200_000 + 1_000_000)

    def test_an_unpriced_model_adds_nothing_and_says_so(self):
        self.sources.append(("a3", "Agent three", log(call("z1", 400, "gpt-oss", inp=5_000_000))))
        s = self.money()
        self.assertAlmostEqual(s["total"], 6.55)
        self.assertEqual(s["unpriced_models"], ["gpt-oss"])

    def test_nothing_recorded_is_none(self):
        self.assertIsNone(spend.summary([("", "Main session", WasteLog())], PRICES, now=T0, live=False))


class TestSeries(SpendTestCase):
    def test_running_totals_end_at_the_totals_and_never_fall(self):
        s = self.money(bins=10)
        self.assertEqual(len(s["t"]), 10)
        for name in ("main", "agents"):
            values = s[name]
            self.assertEqual(len(values), 10)
            self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(s["main"][-1], s["main_total"])
        self.assertAlmostEqual(s["agents"][-1], s["agents_total"])

    def test_the_axis_runs_from_the_first_call_to_now_when_live(self):
        s = self.money(live=True, now=T0 + 1200, bins=12)
        self.assertEqual(s["start"], T0)
        self.assertEqual(s["end"], T0 + 1200)
        self.assertEqual(s["t"][-1], T0 + 1200)
        ended = self.money(live=False, now=T0 + 99_999)
        self.assertEqual(ended["end"], T0 + 600)          # the last call, not the time you looked

    def test_by_model_adds_up_to_the_same_total(self):
        s = self.money(bins=6)
        names = [m["model"] for m in s["models"]]
        self.assertEqual(names, ["sonnet-5-5", "haiku-4-5"])     # biggest first, short names
        self.assertAlmostEqual(sum(m["values"][-1] for m in s["models"]), s["total"])

    def test_at_most_four_models_then_other(self):
        for i in range(6):
            self.sources.append(("b%d" % i, "B", log(call("w%d" % i, 100 + i, "claude-sonnet-x%d" % i, inp=1000))))
        names = [m["model"] for m in self.money()["models"]]
        self.assertEqual(len(names), 5)
        self.assertEqual(names[-1], "other")


class TestBudgetAndPace(SpendTestCase):
    def test_when_the_warning_and_the_limit_were_crossed(self):
        s = self.money(budget=5.0, warn_ratio=0.8)
        b = s["budget"]
        self.assertEqual((b["limit"], b["warn_at"]), (5.0, 4.0))
        self.assertEqual(b["warn_t"], T0 + 310)      # 3.00 + 0.30 + 0.75 = 4.05 passes 4.00
        self.assertEqual(b["limit_t"], T0 + 320)     # + 1.00 = 5.05 passes 5.00
        s = self.money(budget=100.0)
        self.assertIsNone(s["budget"]["warn_t"])
        self.assertIsNone(s["budget"]["limit_t"])

    def test_no_budget_without_prices(self):
        self.assertIsNone(spend.summary(self.sources, None, now=T0 + 900, live=False, budget=5.0)["budget"])

    def test_pace_now_is_the_last_five_minutes_while_live(self):
        s = self.money(live=True, now=T0 + 620)
        self.assertAlmostEqual(s["rate_now"], 1.50 / 5)      # only m2 (at 600s) is in the last 5 minutes
        self.assertIsNone(self.money(live=False)["rate_now"])

    def test_the_most_expensive_five_minutes_and_who_spent_it(self):
        peak = self.money()["peak"]
        self.assertEqual(peak["start"], T0)
        self.assertEqual(peak["end"], T0 + 300)
        self.assertAlmostEqual(peak["value"], 3.00 + 0.30)
        self.assertEqual([w["label"] for w in peak["who"]], ["Main session", "Agent one"])


class TestInARun(unittest.TestCase):
    """The curve and the cost block are counted from different records; they must agree."""

    def test_the_curve_ends_at_the_runs_cost(self):
        from tests import test_insights_ui as ui
        s = ui.demo_run()
        curve = s["insights"]["spend"]
        self.assertEqual(curve["unit"], "money")
        self.assertAlmostEqual(curve["total"], s["cost"]["total"], places=6)
        self.assertAlmostEqual(curve["main_total"], s["cost"]["orchestrator"], places=6)
        self.assertAlmostEqual(curve["agents_total"], s["cost"]["agents"], places=6)


if __name__ == "__main__":
    unittest.main()
