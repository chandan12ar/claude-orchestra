"""The synthetic demo session: it must exercise every state the dashboard draws."""

import os
import tempfile
import time
import unittest

from orchestra import demo
from orchestra.build import RunBuilder
from orchestra.pricing import PriceSource


class TestDemoSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.now = time.time()
        cls.paths, cls.agents = demo.build_demo(cls.root, now=cls.now)
        prices = os.path.join(cls.root, "prices.json")
        demo.write_prices(prices)
        cls.built = RunBuilder(cls.paths, now_fn=lambda: cls.now,
                             prices=PriceSource(prices)).refresh()

    def test_every_state_is_present(self):
        totals = self.built.totals()
        self.assertEqual(totals["agents"], 13)
        self.assertEqual(totals["completed"], 8)
        self.assertEqual(totals["failed"], 1)
        self.assertEqual(totals["running"], 3)
        self.assertEqual(totals["stalled"], 1)
        self.assertTrue(self.built.session_live)

    def test_nesting_edges_loop_and_conflict(self):
        by_desc = {a.description: a for a in self.built.agents}
        webhooks = by_desc["Handle payment webhooks"]
        self.assertEqual(webhooks.spawn_depth, 2)
        self.assertEqual(webhooks.parent_agent_id, by_desc["Build the payment adapter"].agent_id)
        self.assertIsNotNone(by_desc["Run the end-to-end suite"].loop)
        kinds = {e.kind for e in self.built.edges}
        self.assertIn("spawn", kinds)
        self.assertIn("artifact", kinds)
        self.assertTrue(any(c.path.endswith("src/cart/types.ts")
                            for c in self.built.write_conflicts))

    def test_parallel_waves(self):
        sizes = sorted(len(b.agent_ids) for b in self.built.batches)
        self.assertIn(3, sizes)      # the research wave
        self.assertIn(4, sizes)      # the build wave

    def test_cost_is_priced_for_every_model(self):
        self.assertIsNotNone(self.built.cost)
        self.assertGreater(self.built.cost["total"], 0)
        self.assertEqual(self.built.cost.get("unpriced", []), [])

    def test_simulator_keeps_running_agents_alive(self):
        sim = demo.Simulator(self.paths, self.agents)
        before = sum(a.tool_call_count if hasattr(a, "tool_call_count") else len(a.tool_calls)
                     for a in self.built.agents)
        # Three agents run; the docs agent sits on a permission prompt, so it stays still.
        self.assertEqual(sim.tick(now=self.now + 5), 2)
        after_run = RunBuilder(self.paths, now_fn=lambda: self.now + 6).refresh()
        after = sum(len(a.tool_calls) for a in after_run.agents)
        self.assertEqual(after, before + 2)

    def test_prompts_make_answered_overlapping_and_open_waits(self):
        from orchestra.events import EventSpool
        root = tempfile.mkdtemp()
        paths, _ = demo.build_demo(root, now=self.now)
        spool = EventSpool(os.path.join(tempfile.mkdtemp(), "events"))
        demo.write_events(spool, paths.session_id, self.now)
        run = RunBuilder(paths, now_fn=lambda: self.now, spool=spool).refresh()
        w = run.insights["waits"]
        self.assertEqual((w["count"], w["open"], w["unanswered"]), (5, 1, 0))
        self.assertLess(w["you_s"], w["agent_s"])            # two of them overlap
        docs = [a for a in run.agents if a.description == "Update the developer docs"][0]
        self.assertEqual(docs.status, "waiting")
        self.assertEqual(run.live["attention"]["kind"], "permission")

    def test_deterministic_scenario(self):
        other = tempfile.mkdtemp()
        _, again = demo.build_demo(other, now=self.now)
        self.assertEqual([a.agent_id for a in again], [a.agent_id for a in self.agents])


if __name__ == "__main__":
    unittest.main()
