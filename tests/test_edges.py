import unittest

from orchestra import constants as C
from orchestra.edges import infer_edges, normalize_path
from orchestra.model import Agent, Round, ToolCall


def agent(agent_id, start, end, parent=None, writes=(), reads=(),
          result="", brief="", sends=()):
    calls = []
    for path, at in writes:
        calls.append(ToolCall(name="Write", target=path, timestamp=at))
    for path, at in reads:
        calls.append(ToolCall(name="Read", target=path, timestamp=at))
    for target, at in sends:
        calls.append(ToolCall(name="SendMessage", target=target, timestamp=at))
    return Agent(agent_id=agent_id, tool_use_id="toolu_" + agent_id,
                 parent_agent_id=parent, brief=brief, result=result,
                 tool_calls=calls, status=C.COMPLETED,
                 rounds=[Round(started_at=start, ended_at=end)])


class TestNormalizePath(unittest.TestCase):
    def test_worktree_path_collapses_onto_the_main_path(self):
        a = normalize_path(r"E:\proj\.claude\worktrees\feature-x\src\main.py")
        b = normalize_path(r"E:\proj\src\main.py")
        self.assertEqual(a, b)
        self.assertNotIn("worktrees", a)

    def test_separators_and_case_are_normalized(self):
        self.assertEqual(normalize_path(r"SRC\Main.py"), normalize_path("src/main.py"))


class TestSpawnEdges(unittest.TestCase):
    def test_orchestrator_is_the_default_parent(self):
        edges, _ = infer_edges([agent("a1", 0, 10)])
        spawn = [e for e in edges if e.kind == "spawn"]
        self.assertEqual(len(spawn), 1)
        self.assertEqual(spawn[0].src, C.ORCHESTRATOR_ID)
        self.assertEqual(spawn[0].dst, "a1")
        self.assertEqual(spawn[0].confidence, "exact")

    def test_nested_agent_points_at_its_launcher(self):
        edges, _ = infer_edges([agent("a1", 0, 100), agent("a2", 10, 50, parent="a1")])
        spawn = {(e.src, e.dst) for e in edges if e.kind == "spawn"}
        self.assertIn(("a1", "a2"), spawn)


class TestArtifactEdges(unittest.TestCase):
    def test_write_then_read_creates_an_edge_with_evidence(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 5)])
        b = agent("a2", 20, 30, reads=[("src/main.py", 25)])
        edges, _ = infer_edges([a, b])
        art = [e for e in edges if e.kind == "artifact"]
        self.assertEqual(len(art), 1)
        self.assertEqual((art[0].src, art[0].dst), ("a1", "a2"))
        self.assertEqual(art[0].confidence, "exact")
        self.assertIn("main.py", art[0].evidence["path"])

    def test_read_before_write_creates_no_edge(self):
        a = agent("a1", 20, 30, writes=[("src/main.py", 25)])
        b = agent("a2", 0, 10, reads=[("src/main.py", 5)])
        edges, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_self_read_creates_no_edge(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 2)], reads=[("src/main.py", 5)])
        edges, _ = infer_edges([a])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_worktree_paths_still_match(self):
        a = agent("a1", 0, 10, writes=[(r"E:\p\.claude\worktrees\wt\src\a.py", 5)])
        b = agent("a2", 20, 30, reads=[(r"E:\p\src\a.py", 25)])
        edges, _ = infer_edges([a, b])
        self.assertEqual(len([e for e in edges if e.kind == "artifact"]), 1)

    def test_hub_file_is_collapsed_not_edged(self):
        writer = agent("w", 0, 5, writes=[("other.py", 1)])
        readers = [agent("r{}".format(i), 10 + i, 20 + i, reads=[("PLAN.md", 11 + i)])
                   for i in range(C.HUB_FILE_THRESHOLD + 1)]
        edges, hubs = infer_edges([writer] + readers)
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])
        self.assertEqual(len(hubs), 1)
        self.assertIn("plan.md", hubs[0].path)
        self.assertEqual(len(hubs[0].reader_ids), C.HUB_FILE_THRESHOLD + 1)

    def test_a_file_written_during_the_run_is_never_a_hub(self):
        writer = agent("w", 0, 5, writes=[("PLAN.md", 1)])
        readers = [agent("r{}".format(i), 10 + i, 20 + i, reads=[("PLAN.md", 11 + i)])
                   for i in range(C.HUB_FILE_THRESHOLD + 1)]
        edges, hubs = infer_edges([writer] + readers)
        self.assertEqual(hubs, [])
        self.assertEqual(len([e for e in edges if e.kind == "artifact"]),
                         C.HUB_FILE_THRESHOLD + 1)


class TestMessageEdges(unittest.TestCase):
    def test_sendmessage_to_a_known_agent(self):
        a = agent("a1", 0, 10, sends=[("a2", 8)])
        b = agent("a2", 20, 30)
        edges, _ = infer_edges([a, b])
        msg = [e for e in edges if e.kind == "message"]
        self.assertEqual((msg[0].src, msg[0].dst), ("a1", "a2"))

    def test_sendmessage_to_an_unknown_target_is_dropped(self):
        a = agent("a1", 0, 10, sends=[("somebody-else", 8)])
        edges, _ = infer_edges([a])
        self.assertEqual([e for e in edges if e.kind == "message"], [])


SHARED = ("the parser must normalize windows paths before comparing them "
          "because otherwise every artifact edge silently fails to match and "
          "the dependency graph comes out completely empty on windows machines ")


class TestHandoffEdges(unittest.TestCase):
    def test_quoted_result_creates_an_inferred_edge_with_evidence(self):
        a = agent("a1", 0, 10, result="Findings: " + SHARED)
        b = agent("a2", 20, 30, brief="Fix this finding: " + SHARED + " Go.")
        edges, _ = infer_edges([a, b])
        hand = [e for e in edges if e.kind == "handoff"]
        self.assertEqual(len(hand), 1)
        self.assertEqual(hand[0].confidence, "inferred")
        self.assertGreater(hand[0].evidence["score"], 0)
        self.assertIn("normalize windows paths", hand[0].evidence["snippet"])

    def test_no_edge_backwards_in_time(self):
        a = agent("a1", 40, 50, result="Findings: " + SHARED)
        b = agent("a2", 0, 10, brief="Fix this finding: " + SHARED)
        edges, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "handoff"], [])

    def test_unrelated_text_creates_no_edge(self):
        a = agent("a1", 0, 10, result="Everything passed, nothing to report.")
        b = agent("a2", 20, 30, brief="Write a haiku about the ocean.")
        edges, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "handoff"], [])

    def test_handoff_folds_into_an_existing_exact_edge(self):
        a = agent("a1", 0, 10, writes=[("out.md", 5)], result="Findings: " + SHARED)
        b = agent("a2", 20, 30, reads=[("out.md", 25)],
                  brief="Fix this finding: " + SHARED)
        edges, _ = infer_edges([a, b])
        pair = [e for e in edges if (e.src, e.dst) == ("a1", "a2")
                and e.kind in ("artifact", "handoff")]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0].kind, "artifact")
        self.assertIn("handoff", pair[0].evidence)


if __name__ == "__main__":
    unittest.main()
