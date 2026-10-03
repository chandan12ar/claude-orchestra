import unittest

from orchestra.model import Agent, Edge, Extraction, Round, Run
from orchestra import constants as C


def make_agent(**kw):
    defaults = dict(
        agent_id="a1",
        tool_use_id="toolu_1",
        agent_type="general-purpose",
        description="Do the thing",
        model="haiku",
        launch_mode="background",
        brief="A very long brief " * 50,
        result="A very long result " * 50,
    )
    defaults.update(kw)
    return Agent(**defaults)


class TestAgentSerialization(unittest.TestCase):
    def test_light_dict_omits_heavy_fields(self):
        d = make_agent().to_light_dict()
        for heavy in ("brief", "result", "tool_calls"):
            self.assertNotIn(heavy, d)
        self.assertEqual(d["agent_id"], "a1")
        self.assertEqual(d["description"], "Do the thing")

    def test_detail_dict_includes_heavy_fields(self):
        d = make_agent().to_detail_dict()
        self.assertIn("brief", d)
        self.assertIn("result", d)
        self.assertIn("tool_calls", d)

    def test_extraction_source_is_serialized(self):
        a = make_agent(expected_output=Extraction("Return a diff", 'heading "## Deliverable"'))
        d = a.to_detail_dict()
        self.assertEqual(d["expected_output"], "Return a diff")
        self.assertEqual(d["expected_output_source"], 'heading "## Deliverable"')

    def test_duration_spans_first_start_to_last_end(self):
        a = make_agent(rounds=[Round(started_at=100.0, ended_at=150.0),
                               Round(started_at=200.0, ended_at=260.0)])
        self.assertEqual(a.started_at, 100.0)
        self.assertEqual(a.ended_at, 260.0)
        self.assertEqual(a.duration_s, 160.0)

    def test_running_agent_has_no_end(self):
        a = make_agent(rounds=[Round(started_at=100.0, ended_at=None)])
        self.assertIsNone(a.ended_at)
        self.assertIsNone(a.duration_s)


class TestRunSerialization(unittest.TestCase):
    def test_summary_uses_light_agents_and_counts_statuses(self):
        run = Run(
            session_id="s1",
            project_path="E:/p",
            agents=[make_agent(agent_id="a1", status=C.RUNNING),
                    make_agent(agent_id="a2", status=C.COMPLETED),
                    make_agent(agent_id="a3", status=C.FAILED)],
            edges=[Edge(src=C.ORCHESTRATOR_ID, dst="a1", kind="spawn",
                        confidence="exact", evidence={"tool_use_id": "toolu_1"})],
        )
        d = run.to_summary_dict()
        self.assertEqual(d["totals"]["agents"], 3)
        self.assertEqual(d["totals"]["running"], 1)
        self.assertEqual(d["totals"]["completed"], 1)
        self.assertEqual(d["totals"]["failed"], 1)
        self.assertNotIn("brief", d["agents"][0])
        self.assertEqual(d["edges"][0]["kind"], "spawn")


class TestSerializationRedacts(unittest.TestCase):
    def test_brief_and_result_are_scrubbed(self):
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        a = make_agent(brief="use " + token, result="also " + token)
        d = a.to_detail_dict()
        self.assertNotIn("ABCDEFGHIJ", d["brief"])
        self.assertNotIn("ABCDEFGHIJ", d["result"])

    def test_edge_evidence_is_scrubbed(self):
        e = Edge(src="a1", dst="a2", kind="handoff", confidence="inferred",
                 evidence={"snippet": "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})
        self.assertNotIn("ABCDEFGHIJ", e.to_dict()["evidence"]["snippet"])


class TestLightDictIsActuallyLight(unittest.TestCase):
    """The 2-second poll carries the light dict; detail is fetched on click."""

    def test_long_objective_is_capped_in_light_but_whole_in_detail(self):
        long_text = "word " * 500
        a = make_agent(objective=Extraction(long_text, "fallback"))
        light = a.to_light_dict()["objective"]
        self.assertLessEqual(len(light), 201)
        self.assertTrue(light.endswith("…"))
        self.assertEqual(a.to_detail_dict()["objective"], long_text)

    def test_short_objective_is_untouched_and_ungarnished(self):
        a = make_agent(objective=Extraction("Fix the parser.", "imperative line"))
        self.assertEqual(a.to_light_dict()["objective"], "Fix the parser.")

    def test_a_secret_cannot_survive_the_cut_half_redacted(self):
        # Cap runs after scrub, so the credential is already a marker by the
        # time it could be split.
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        a = make_agent(objective=Extraction("x" * 190 + " " + token, "fallback"))
        self.assertNotIn("ABCDEFGHIJ", a.to_light_dict()["objective"])


if __name__ == "__main__":
    unittest.main()


class TestActivityBins(unittest.TestCase):
    def agent(self, stamps, start=0.0, end=100.0, bins=None):
        from orchestra.model import Agent, Round, ToolCall
        a = Agent(agent_id="a", rounds=[Round(started_at=start, ended_at=end)])
        a.last_activity_at = end
        a.tool_calls = [ToolCall("Read", "x", t) for t in stamps]
        return a.to_light_dict()["activity"]

    def test_counts_land_in_the_right_bins(self):
        from orchestra.model import ACTIVITY_BINS
        bins = self.agent([0.0, 1.0, 99.0, 100.0])
        self.assertEqual(len(bins), ACTIVITY_BINS)
        self.assertEqual(bins[0], 2)
        self.assertEqual(bins[-1], 2)         # the final instant belongs to the last bin
        self.assertEqual(sum(bins), 4)

    def test_calls_outside_the_span_or_without_a_time_are_ignored(self):
        self.assertEqual(sum(self.agent([-5.0, 150.0, None])), 0)

    def test_no_calls_or_no_span_means_no_strip(self):
        self.assertEqual(self.agent([]), [])
        self.assertEqual(self.agent([5.0], start=10.0, end=10.0), [])

    def test_an_open_agent_uses_its_last_activity_as_the_end(self):
        from orchestra.model import Agent, Round, ToolCall
        a = Agent(agent_id="a", rounds=[Round(started_at=0.0, ended_at=None)])
        a.last_activity_at = 50.0
        a.tool_calls = [ToolCall("Read", "x", 25.0)]
        bins = a.to_light_dict()["activity"]
        self.assertEqual(sum(bins), 1)
        self.assertEqual(bins.index(1), len(bins) // 2)
