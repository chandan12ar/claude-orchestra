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


if __name__ == "__main__":
    unittest.main()
