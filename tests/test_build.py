import os
import tempfile
import unittest

from orchestra import constants as C
from orchestra.build import RunBuilder
from orchestra.parent import parse_timestamp
from tests.fixtures import (agent_entry, build_nested_session, build_session, ts,
                            write_jsonl)

NOW_LIVE = parse_timestamp(ts(150))
NOW_DEAD = NOW_LIVE + C.SESSION_LIVE_THRESHOLD_S + 60


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)

    def build(self, now=NOW_LIVE, live=True):
        builder = RunBuilder(self.paths, now_fn=lambda: now)
        if not live:
            os.utime(self.paths.session_jsonl, (now - 99999, now - 99999))
        else:
            os.utime(self.paths.session_jsonl, (now, now))
        return builder.refresh()


class TestRunAssembly(BuildTestCase):
    def test_finds_all_three_agents(self):
        run = self.build()
        self.assertEqual({a.agent_id for a in run.agents}, {"a1", "a2", "a3"})

    def test_project_path_comes_from_the_transcript(self):
        self.assertEqual(self.build().project_path, r"E:\proj")

    def test_metadata_is_attached(self):
        a1 = self.build().agent("a1")
        self.assertEqual(a1.agent_type, "general-purpose")
        self.assertEqual(a1.description, "Plan the work")
        self.assertEqual(a1.launch_mode, "background")
        self.assertEqual(a1.model, "haiku")

    def test_brief_and_extraction(self):
        a1 = self.build().agent("a1")
        self.assertIn("You are planning", a1.brief)
        self.assertIn("A written plan at PLAN.md", a1.expected_output.text)
        self.assertIn("Deliverable", a1.expected_output.source)

    def test_statuses(self):
        run = self.build()
        self.assertEqual(run.agent("a1").status, C.COMPLETED)
        self.assertEqual(run.agent("a2").status, C.COMPLETED)
        self.assertEqual(run.agent("a3").status, C.RUNNING)

    def test_running_agent_becomes_orphaned_when_session_dies(self):
        run = self.build(now=NOW_DEAD, live=False)
        self.assertEqual(run.agent("a3").status, C.ORPHANED)

    def test_durations(self):
        a1 = self.build().agent("a1")
        self.assertEqual(a1.duration_s, 60.0)
        self.assertIsNone(self.build().agent("a3").duration_s)

    def test_tokens_are_summed_per_agent_and_per_run(self):
        run = self.build()
        self.assertEqual(run.agent("a1").tokens["input"], 100)
        self.assertEqual(run.totals()["tokens"]["input"], 510)

    def test_results_are_captured(self):
        run = self.build()
        self.assertIn("Wrote the plan", run.agent("a1").result)
        self.assertEqual(run.agent("a3").result, "")

    def test_files_written_and_read(self):
        run = self.build()
        self.assertEqual(len(run.agent("a1").files_written), 1)
        self.assertEqual(len(run.agent("a2").files_read), 1)


class TestBatchesAndEdges(BuildTestCase):
    def test_agents_launched_in_one_turn_form_a_batch(self):
        run = self.build()
        waves = {tuple(sorted(b.agent_ids)) for b in run.batches}
        self.assertIn(("a2", "a3"), waves)
        self.assertIn(("a1",), waves)

    def test_spawn_edges_come_from_the_orchestrator(self):
        run = self.build()
        spawn = {(e.src, e.dst) for e in run.edges if e.kind == "spawn"}
        self.assertEqual(spawn, {("main", "a1"), ("main", "a2"), ("main", "a3")})

    def test_artifact_edges_follow_the_plan_file(self):
        run = self.build()
        artifact = {(e.src, e.dst) for e in run.edges if e.kind == "artifact"}
        self.assertEqual(artifact, {("a1", "a2"), ("a1", "a3")})

    def test_handoff_folds_into_the_artifact_edge_for_a2(self):
        run = self.build()
        edge = [e for e in run.edges
                if (e.src, e.dst) == ("a1", "a2") and e.kind == "artifact"][0]
        self.assertIn("handoff", edge.evidence)

    def test_summary_dict_is_json_serializable(self):
        import json
        json.dumps(self.build().to_summary_dict())


class TestIncrementalRefresh(BuildTestCase):
    def test_second_refresh_picks_up_new_activity(self):
        builder = RunBuilder(self.paths, now_fn=lambda: NOW_LIVE)
        os.utime(self.paths.session_jsonl, (NOW_LIVE, NOW_LIVE))
        first = builder.refresh()
        self.assertEqual(first.agent("a3").tokens.get("output", 0), 0)

        path = os.path.join(self.paths.subagents_dir, "agent-a3.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            import json
            fh.write(json.dumps(agent_entry(
                [{"type": "text", "text": "Implemented part two."}], 145,
                usage={"output_tokens": 33})) + "\n")

        second = builder.refresh()
        self.assertEqual(second.agent("a3").tokens["output"], 33)

    def test_diagnostics_report_unparsable_lines(self):
        path = os.path.join(self.paths.subagents_dir, "agent-a3.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("not json at all\n")
        run = self.build()
        self.assertEqual(run.diagnostics["unparsable_lines"], 1)


class TestNestedAgents(unittest.TestCase):
    """spawnDepth > 1 has no real-world sample yet; this is its only coverage."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_nested_session(self.root)

    def build(self):
        os.utime(self.paths.session_jsonl, (NOW_LIVE, NOW_LIVE))
        return RunBuilder(self.paths, now_fn=lambda: NOW_LIVE).refresh()

    def test_both_agents_are_found(self):
        self.assertEqual({a.agent_id for a in self.build().agents}, {"a1", "a1b"})

    def test_nested_agent_records_its_launcher(self):
        run = self.build()
        self.assertIsNone(run.agent("a1").parent_agent_id)
        self.assertEqual(run.agent("a1b").parent_agent_id, "a1")

    def test_spawn_depth_is_preserved(self):
        self.assertEqual(self.build().agent("a1b").spawn_depth, 2)

    def test_spawn_edge_points_from_the_parent_agent(self):
        spawn = {(e.src, e.dst) for e in self.build().edges if e.kind == "spawn"}
        self.assertEqual(spawn, {("main", "a1"), ("a1", "a1b")})

    def test_nested_agent_brief_comes_from_the_inner_launch(self):
        self.assertIn("inner part", self.build().agent("a1b").brief.lower())


if __name__ == "__main__":
    unittest.main()
