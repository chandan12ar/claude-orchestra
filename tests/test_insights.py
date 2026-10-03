"""Run-level analytics, against hand-computed expectations."""

import unittest

from orchestra import insights
from orchestra.model import Agent, Edge, Round, Run, ToolCall
from orchestra.pricing import PriceTable


def agent(aid, start, end, **kw):
    a = Agent(agent_id=aid, description=kw.pop("description", aid))
    a.rounds = [Round(started_at=start, ended_at=end)]
    for key, value in kw.items():
        setattr(a, key, value)
    return a


def run_of(*agents, edges=()):
    return Run(session_id="s", agents=list(agents), edges=list(edges))


class TestParallelism(unittest.TestCase):
    def test_overlap_peak_and_average(self):
        # a: 0-10, b: 5-15, c: 5-10  -> peak 3 at t=5..10; busy 10+10+5 = 25; wall 15
        r = run_of(agent("a", 0, 10), agent("b", 5, 15), agent("c", 5, 10))
        p = insights.compute(r, now=100)["parallelism"]
        self.assertEqual(p["peak"], 3)
        self.assertAlmostEqual(p["busy_s"], 25.0)
        self.assertAlmostEqual(p["wall_s"], 15.0)
        self.assertAlmostEqual(p["average"], 25.0 / 15.0)
        self.assertAlmostEqual(p["solo_s"], 10.0)      # 0-5 (a alone) + 10-15 (b alone)
        self.assertAlmostEqual(p["idle_s"], 0.0)

    def test_back_to_back_agents_do_not_overlap(self):
        r = run_of(agent("a", 0, 10), agent("b", 10, 20))
        p = insights.compute(r, now=100)["parallelism"]
        self.assertEqual(p["peak"], 1)

    def test_gap_counts_as_idle(self):
        r = run_of(agent("a", 0, 10), agent("b", 20, 30))
        p = insights.compute(r, now=100)["parallelism"]
        self.assertAlmostEqual(p["idle_s"], 10.0)

    def test_open_round_runs_until_now(self):
        a = Agent(agent_id="a")
        a.rounds = [Round(started_at=0, ended_at=None)]
        p = insights.compute(run_of(a), now=50)["parallelism"]
        self.assertAlmostEqual(p["busy_s"], 50.0)

    def test_empty_run(self):
        out = insights.compute(run_of(), now=1)
        self.assertIsNone(out["parallelism"])
        self.assertIsNone(out["critical_path"])

    def test_series_is_bounded(self):
        many = [agent("a%d" % i, i, i + 0.5) for i in range(1000)]
        series = insights.compute(run_of(*many), now=2000)["parallelism"]["series"]
        self.assertLessEqual(len(series), insights.MAX_SERIES + 1)


class TestCriticalPath(unittest.TestCase):
    def test_longest_chain_wins_over_longest_agent(self):
        # a(0-10) -> b(10-30)  is 30s of chain; c(0-25) alone is 25s.
        r = run_of(agent("a", 0, 10), agent("b", 10, 30), agent("c", 0, 25),
                   edges=[Edge("a", "b", "artifact", "exact")])
        cp = insights.compute(r, now=100)["critical_path"]
        self.assertEqual([c["agent_id"] for c in cp["chain"]], ["a", "b"])
        self.assertAlmostEqual(cp["duration_s"], 30.0)
        self.assertAlmostEqual(cp["share"], 1.0)

    def test_inferred_edges_do_not_define_the_path(self):
        r = run_of(agent("a", 0, 10), agent("b", 10, 30),
                   edges=[Edge("a", "b", "handoff", "inferred")])
        cp = insights.compute(r, now=100)["critical_path"]
        self.assertEqual([c["agent_id"] for c in cp["chain"]], ["b"])

    def test_overlap_is_not_double_counted(self):
        # b starts inside a: the chain adds only b's time past a's end.
        r = run_of(agent("a", 0, 20), agent("b", 10, 30),
                   edges=[Edge("a", "b", "spawn", "exact")])
        cp = insights.compute(r, now=100)["critical_path"]
        self.assertAlmostEqual(cp["duration_s"], 30.0)

    def test_cycle_in_edges_terminates(self):
        r = run_of(agent("a", 0, 10), agent("b", 0, 10),
                   edges=[Edge("a", "b", "artifact", "exact"),
                          Edge("b", "a", "artifact", "exact")])
        self.assertIsNotNone(insights.compute(r, now=100)["critical_path"])

    def test_main_node_edges_are_ignored(self):
        r = run_of(agent("a", 0, 10), edges=[Edge("main", "a", "spawn", "exact")])
        cp = insights.compute(r, now=100)["critical_path"]
        self.assertEqual(len(cp["chain"]), 1)


class TestToolsTokensFiles(unittest.TestCase):
    def test_tool_mix_and_buckets(self):
        a = agent("a", 0, 10, tool_calls=[ToolCall("Read", "x", 1), ToolCall("Read", "y", 2),
                                          ToolCall("Bash", "ls", 3), ToolCall("Write", "z", 4)])
        t = insights.compute(run_of(a), now=100)["tools"]
        self.assertEqual(t["total"], 4)
        self.assertEqual(t["by_tool"][0]["name"], "Read")
        self.assertEqual(t["by_tool"][0]["count"], 2)
        shares = {b["name"]: b["count"] for b in t["buckets"]}
        self.assertEqual(shares, {"Read": 2, "Bash": 1, "Edit": 1})
        self.assertEqual(t["busiest"]["agent_id"], "a")

    def test_cache_hit_ratio_and_top_agents(self):
        a = agent("a", 0, 10, tokens={"input": 100, "output": 50, "cache_read": 800,
                                      "cache_create": 100})
        b = agent("b", 0, 10, tokens={"input": 10, "output": 10})
        tk = insights.compute(run_of(a, b), now=100)["tokens"]
        # read 800 / (input 110 + read 800 + create 100)
        self.assertAlmostEqual(tk["cache_hit_ratio"], 800 / 1010)
        self.assertEqual(tk["top_agents"][0]["agent_id"], "a")
        self.assertAlmostEqual(sum(x["share"] for x in tk["top_agents"]), 1.0)

    def test_no_tokens_means_no_ratio(self):
        self.assertIsNone(insights.compute(run_of(agent("a", 0, 1)), now=2)["tokens"]
                          ["cache_hit_ratio"])

    def test_cost_by_model_uses_the_price_table(self):
        table = PriceTable([("m*", {"input": 1.0, "output": 2.0,
                                    "cache_read": 0.5, "cache_create": 1.0})])
        a = agent("a", 0, 10, tokens_by_model={"m1": {"input": 1_000_000, "output": 500_000}})
        row = insights.compute(run_of(a), now=100, table=table)["tokens"]["by_model"][0]
        self.assertAlmostEqual(row["cost"], 2.0)

    def test_files_contended_needs_two_writers(self):
        a = agent("a", 0, 1, files_written=["/p/x", "/p/y"], files_read=["/p/r"])
        b = agent("b", 0, 1, files_written=["/p/x"], files_read=["/p/r"])
        f = insights.compute(run_of(a, b), now=2)["files"]
        self.assertEqual([c["path"] for c in f["contended"]], ["/p/x"])
        self.assertEqual(f["read"][0]["agents"], 2)

    def test_secrets_in_descriptions_are_scrubbed(self):
        a = agent("a", 0, 10, description="deploy with sk-ant-api03-" + "A" * 40)
        out = insights.compute(run_of(a), now=100)
        self.assertNotIn("A" * 40, repr(out))


class TestWiredIntoTheRun(unittest.TestCase):
    def test_summary_carries_insights(self):
        r = run_of(agent("a", 0, 10))
        r.insights = insights.compute(r, now=100)
        self.assertIn("insights", r.to_summary_dict())


if __name__ == "__main__":
    unittest.main()
