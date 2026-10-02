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

    def test_dot_led_path_is_not_collapsed_onto_a_different_file(self):
        # A bare `.lstrip("./")` strips characters, not a prefix string, so a
        # dot-led relative path like a hidden-directory workflow file must not
        # collapse onto the unrelated file with the same name minus the dot.
        a = normalize_path(".github/workflows/ci.yml")
        b = normalize_path("github/workflows/ci.yml")
        self.assertNotEqual(a, b)

    def test_leading_dot_slash_still_collapses_normally(self):
        self.assertEqual(normalize_path("./src/a.py"), normalize_path("src/a.py"))


class TestSpawnEdges(unittest.TestCase):
    def test_orchestrator_is_the_default_parent(self):
        edges, _, _ = infer_edges([agent("a1", 0, 10)])
        spawn = [e for e in edges if e.kind == "spawn"]
        self.assertEqual(len(spawn), 1)
        self.assertEqual(spawn[0].src, C.ORCHESTRATOR_ID)
        self.assertEqual(spawn[0].dst, "a1")
        self.assertEqual(spawn[0].confidence, "exact")

    def test_nested_agent_points_at_its_launcher(self):
        edges, _, _ = infer_edges([agent("a1", 0, 100), agent("a2", 10, 50, parent="a1")])
        spawn = {(e.src, e.dst) for e in edges if e.kind == "spawn"}
        self.assertIn(("a1", "a2"), spawn)


class TestArtifactEdges(unittest.TestCase):
    def test_write_then_read_creates_an_edge_with_evidence(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 5)])
        b = agent("a2", 20, 30, reads=[("src/main.py", 25)])
        edges, _, _ = infer_edges([a, b])
        art = [e for e in edges if e.kind == "artifact"]
        self.assertEqual(len(art), 1)
        self.assertEqual((art[0].src, art[0].dst), ("a1", "a2"))
        self.assertEqual(art[0].confidence, "exact")
        self.assertIn("main.py", art[0].evidence["path"])

    def test_read_before_write_creates_no_edge(self):
        a = agent("a1", 20, 30, writes=[("src/main.py", 25)])
        b = agent("a2", 0, 10, reads=[("src/main.py", 5)])
        edges, _, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_self_read_creates_no_edge(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 2)], reads=[("src/main.py", 5)])
        edges, _, _ = infer_edges([a])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_worktree_paths_still_match(self):
        a = agent("a1", 0, 10, writes=[(r"E:\p\.claude\worktrees\wt\src\a.py", 5)])
        b = agent("a2", 20, 30, reads=[(r"E:\p\src\a.py", 25)])
        edges, _, _ = infer_edges([a, b])
        self.assertEqual(len([e for e in edges if e.kind == "artifact"]), 1)

    def test_hub_file_is_collapsed_not_edged(self):
        writer = agent("w", 0, 5, writes=[("other.py", 1)])
        readers = [agent("r{}".format(i), 10 + i, 20 + i, reads=[("PLAN.md", 11 + i)])
                   for i in range(C.HUB_FILE_THRESHOLD + 1)]
        edges, hubs, _ = infer_edges([writer] + readers)
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])
        self.assertEqual(len(hubs), 1)
        self.assertIn("plan.md", hubs[0].path)
        self.assertEqual(len(hubs[0].reader_ids), C.HUB_FILE_THRESHOLD + 1)

    def test_missing_write_timestamp_creates_no_edge(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", None)])
        b = agent("a2", 20, 30, reads=[("src/main.py", 25)])
        edges, _, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_missing_read_timestamp_creates_no_edge(self):
        a = agent("a1", 0, 10, writes=[("other.py", 5)])
        b = agent("a2", 20, 30, reads=[("other.py", None)])
        edges, _, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "artifact"], [])

    def test_a_file_written_during_the_run_is_never_a_hub(self):
        writer = agent("w", 0, 5, writes=[("PLAN.md", 1)])
        readers = [agent("r{}".format(i), 10 + i, 20 + i, reads=[("PLAN.md", 11 + i)])
                   for i in range(C.HUB_FILE_THRESHOLD + 1)]
        edges, hubs, _ = infer_edges([writer] + readers)
        self.assertEqual(hubs, [])
        self.assertEqual(len([e for e in edges if e.kind == "artifact"]),
                         C.HUB_FILE_THRESHOLD + 1)


class TestWriteConflicts(unittest.TestCase):
    def test_two_agents_writing_the_same_file_is_a_conflict(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 5)])
        b = agent("a2", 20, 30, writes=[("src/main.py", 25)])
        _, _, conflicts = infer_edges([a, b])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("main.py", conflicts[0].path)
        self.assertEqual(conflicts[0].writer_ids, ["a1", "a2"])

    def test_a_single_writer_is_never_a_conflict(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 5)])
        b = agent("a2", 20, 30, reads=[("src/main.py", 25)])
        _, _, conflicts = infer_edges([a, b])
        self.assertEqual(conflicts, [])

    def test_the_same_agent_writing_twice_is_not_a_conflict(self):
        a = agent("a1", 0, 10, writes=[("src/main.py", 5), ("src/main.py", 8)])
        _, _, conflicts = infer_edges([a])
        self.assertEqual(conflicts, [])

    def test_worktree_paths_still_collapse_onto_the_same_conflict(self):
        a = agent("a1", 0, 10, writes=[(r"E:\p\.claude\worktrees\wt\src\a.py", 5)])
        b = agent("a2", 20, 30, writes=[(r"E:\p\src\a.py", 25)])
        _, _, conflicts = infer_edges([a, b])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].writer_ids, ["a1", "a2"])


class TestMessageEdges(unittest.TestCase):
    def test_sendmessage_to_a_known_agent(self):
        a = agent("a1", 0, 10, sends=[("a2", 8)])
        b = agent("a2", 20, 30)
        edges, _, _ = infer_edges([a, b])
        msg = [e for e in edges if e.kind == "message"]
        self.assertEqual((msg[0].src, msg[0].dst), ("a1", "a2"))

    def test_sendmessage_to_an_unknown_target_is_dropped(self):
        a = agent("a1", 0, 10, sends=[("somebody-else", 8)])
        edges, _, _ = infer_edges([a])
        self.assertEqual([e for e in edges if e.kind == "message"], [])


SHARED = ("the parser must normalize windows paths before comparing them "
          "because otherwise every artifact edge silently fails to match and "
          "the dependency graph comes out completely empty on windows machines ")


class TestHandoffEdges(unittest.TestCase):
    def test_quoted_result_creates_an_inferred_edge_with_evidence(self):
        a = agent("a1", 0, 10, result="Findings: " + SHARED)
        b = agent("a2", 20, 30, brief="Fix this finding: " + SHARED + " Go.")
        edges, _, _ = infer_edges([a, b])
        hand = [e for e in edges if e.kind == "handoff"]
        self.assertEqual(len(hand), 1)
        self.assertEqual(hand[0].confidence, "inferred")
        self.assertGreater(hand[0].evidence["score"], 0)
        self.assertIn("normalize windows paths", hand[0].evidence["snippet"])

    def test_no_edge_backwards_in_time(self):
        a = agent("a1", 40, 50, result="Findings: " + SHARED)
        b = agent("a2", 0, 10, brief="Fix this finding: " + SHARED)
        edges, _, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "handoff"], [])

    def test_unrelated_text_creates_no_edge(self):
        a = agent("a1", 0, 10, result="Everything passed, nothing to report.")
        b = agent("a2", 20, 30, brief="Write a haiku about the ocean.")
        edges, _, _ = infer_edges([a, b])
        self.assertEqual([e for e in edges if e.kind == "handoff"], [])

    def test_handoff_folds_into_an_existing_exact_edge(self):
        a = agent("a1", 0, 10, writes=[("out.md", 5)], result="Findings: " + SHARED)
        b = agent("a2", 20, 30, reads=[("out.md", 25)],
                  brief="Fix this finding: " + SHARED)
        edges, _, _ = infer_edges([a, b])
        pair = [e for e in edges if (e.src, e.dst) == ("a1", "a2")
                and e.kind in ("artifact", "handoff")]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0].kind, "artifact")
        self.assertIn("handoff", pair[0].evidence)

    def test_handoff_folds_into_every_exact_edge_for_the_pair(self):
        a = agent("a1", 0, 10, writes=[("out.md", 5)], sends=[("a2", 6)],
                  result="Findings: " + SHARED)
        b = agent("a2", 20, 30, reads=[("out.md", 25)],
                  brief="Fix this finding: " + SHARED)
        edges, _, _ = infer_edges([a, b])
        pair_edges = [e for e in edges if (e.src, e.dst) == ("a1", "a2")]
        kinds = {e.kind for e in pair_edges}
        self.assertEqual(kinds, {"artifact", "message"})
        self.assertNotIn("handoff", kinds)
        for e in pair_edges:
            self.assertIn("handoff", e.evidence)


if __name__ == "__main__":
    unittest.main()


def _reference_handoffs(agents):
    """The pre-optimization algorithm, kept verbatim as the oracle."""
    import difflib
    from orchestra.edges import _WORD, _shingles
    out = {}
    for src in agents:
        if not src.result or src.ended_at is None:
            continue
        src_words = _WORD.findall(src.result.lower())
        src_sh = _shingles(src_words, C.SHINGLE_SIZE)
        if not src_sh:
            continue
        for dst in agents:
            if dst.agent_id == src.agent_id or not dst.brief:
                continue
            if dst.started_at is None or dst.started_at < src.ended_at:
                continue
            dst_words = _WORD.findall(dst.brief.lower())
            overlap = src_sh & _shingles(dst_words, C.SHINGLE_SIZE)
            score = len(overlap) / float(len(src_sh))
            m = difflib.SequenceMatcher(None, src_words, dst_words, autojunk=False)
            match = m.find_longest_match(0, len(src_words), 0, len(dst_words))
            if score < C.HANDOFF_CONTAINMENT and match.size < C.HANDOFF_RUN_WORDS:
                continue
            out[(src.agent_id, dst.agent_id)] = (
                round(score, 3), match.size,
                " ".join(src_words[match.a:match.a + match.size])[:400])
    return out


class TestHandoffCache(unittest.TestCase):
    def _agents(self, n, seed=7):
        import random
        rng = random.Random(seed)
        vocab = ["w%d" % i for i in range(60)]
        shared = " ".join(rng.choice(vocab) for _ in range(120))
        agents = []
        for i in range(n):
            tail = " ".join(rng.choice(vocab) for _ in range(rng.randint(10, 150)))
            # Mix of: shares a long run, shares scattered words, shares nothing.
            kind = i % 3
            result = shared[: rng.randint(40, len(shared))] + " " + tail
            if kind == 0:
                brief = "intro " + shared + " " + tail
            elif kind == 1:
                brief = " ".join(rng.choice(vocab) for _ in range(200))
            else:
                brief = "totally different text " + str(i)
            agents.append(agent("a%d" % i, i * 10, i * 10 + 5,
                                result=result, brief=brief))
        return agents

    def test_matches_the_unoptimized_algorithm_exactly(self):
        for seed in (1, 2, 3):
            agents = self._agents(24, seed)
            expected = _reference_handoffs(agents)
            from orchestra.edges import _handoff_edges
            got = {(e.src, e.dst): (e.evidence["score"], e.evidence["run_words"],
                                    e.evidence["snippet"])
                   for e in _handoff_edges(agents)}
            self.assertEqual(got, expected)

    def test_an_unchanged_poll_scores_no_pair_twice(self):
        from unittest import mock
        from orchestra import edges
        from orchestra.edges import HandoffCache
        agents = self._agents(20)
        cache = HandoffCache()
        edges.infer_edges(agents, cache)
        with mock.patch.object(edges, "_longest_run",
                               side_effect=AssertionError("rescored")):
            first, _, _ = edges.infer_edges(agents, cache)
            second, _, _ = edges.infer_edges(agents, cache)
        self.assertEqual([e.to_dict() for e in first],
                         [e.to_dict() for e in second])

    def test_cache_drops_entries_for_texts_no_longer_present(self):
        from orchestra import edges
        from orchestra.edges import HandoffCache
        cache = HandoffCache()
        edges.infer_edges(self._agents(12), cache)
        before = len(cache._pairs)
        edges.infer_edges(self._agents(3), cache)
        self.assertGreater(before, len(cache._pairs))

    def test_long_orchestration_is_fast_enough_to_poll(self):
        import time
        from orchestra import edges
        agents = self._agents(96)
        cache = edges.HandoffCache()
        edges.infer_edges(agents, cache)          # cold
        start = time.time()
        edges.infer_edges(agents, cache)          # warm: every 2s poll
        self.assertLess(time.time() - start, 0.5)
