import json
import os
import tempfile
import unittest
from unittest import mock

from orchestra import constants as C
from orchestra.build import RunBuilder
from orchestra.model import ToolCall
from orchestra.parent import parse_timestamp
from orchestra.runaway import Loop, detect_loop
from tests.fixtures import agent_entry, build_session, ts, write_jsonl


def calls(*pairs):
    return [ToolCall(name=n, target=t, timestamp=float(i)) for i, (n, t) in enumerate(pairs)]


A = ("Bash", "npm test")
B = ("Edit", "a.py")
C_ = ("Read", "b.py")


class TestRepeat(unittest.TestCase):
    def test_the_same_call_repeated_enough_times_is_a_loop(self):
        loop = detect_loop(calls(*[A] * 6), repeats=6, cycle_calls=16)
        self.assertEqual((loop.kind, loop.count, loop.calls), ("repeat", 6, [A]))

    def test_one_short_of_the_threshold_is_not(self):
        self.assertIsNone(detect_loop(calls(*[A] * 5), 6, 16))

    def test_count_is_the_full_trailing_run(self):
        loop = detect_loop(calls(C_, B, *[A] * 9), 6, 16)
        self.assertEqual(loop.count, 9)

    def test_only_the_END_of_the_history_matters(self):
        # It looped earlier but has moved on: not a loop right now.
        self.assertIsNone(detect_loop(calls(*[A] * 8, B, C_), 6, 16))

    def test_same_tool_with_different_targets_is_not_a_repeat(self):
        seq = [("Read", "f%d.py" % i) for i in range(10)]
        self.assertIsNone(detect_loop(calls(*seq), 6, 16))

    def test_todo_bookkeeping_is_ignored(self):
        seq = [("TodoWrite", "x")] * 10
        self.assertIsNone(detect_loop(calls(*seq), 6, 16))

    def test_bookkeeping_between_repeats_does_not_hide_them(self):
        seq = []
        for _ in range(6):
            seq += [A, ("TodoWrite", "x")]
        self.assertEqual(detect_loop(calls(*seq), 6, 16).kind, "repeat")

    def test_no_calls_is_no_loop(self):
        self.assertIsNone(detect_loop([], 6, 16))


class TestCycle(unittest.TestCase):
    def test_strict_alternation_is_a_cycle(self):
        loop = detect_loop(calls(*[B, A] * 8), 6, 16)
        self.assertEqual((loop.kind, loop.count), ("cycle", 16))
        self.assertEqual(set(loop.calls), {A, B})

    def test_a_third_distinct_call_breaks_it(self):
        seq = [B, A] * 7 + [B, C_]
        self.assertIsNone(detect_loop(calls(*seq), 6, 16))

    def test_two_calls_that_merely_both_appear_do_not_count(self):
        # A A A B B B ...: only two distinct calls, but not alternating.
        seq = ([A] * 3 + [B] * 3) * 3
        self.assertIsNone(detect_loop(calls(*seq[:16]), 6, 16))

    def test_too_short_a_history_is_not_a_cycle(self):
        self.assertIsNone(detect_loop(calls(*[B, A] * 4), 6, 16))

    def test_window_is_configurable(self):
        self.assertEqual(detect_loop(calls(*[B, A] * 3), 6, 6).kind, "cycle")

    def test_a_repeat_is_reported_in_preference_to_a_cycle(self):
        self.assertEqual(detect_loop(calls(*[A] * 20), 6, 16).kind, "repeat")


class TestSerialization(unittest.TestCase):
    def test_to_dict_shape(self):
        d = Loop("cycle", 16, [A, B]).to_dict()
        self.assertEqual((d["kind"], d["count"], d["tool"], d["target"]),
                         ("cycle", 16, "Bash", "npm test"))
        self.assertEqual(len(d["calls"]), 2)


class TestThroughTheBuilder(unittest.TestCase):
    """a3 in the fixture is the one still running."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)
        self.now = parse_timestamp(ts(150))

    def add_calls(self, agent_id, n, target="npm test", start=82):
        path = os.path.join(self.paths.subagents_dir, "agent-%s.jsonl" % agent_id)
        with open(path, "a", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps(agent_entry(
                    [{"type": "tool_use", "id": "L%s%d" % (agent_id, i), "name": "Bash",
                      "input": {"command": target}}], start + i)) + "\n")
                fh.write(json.dumps({"isSidechain": True, "type": "user",
                                     "timestamp": ts(start + i), "message": {
                                         "role": "user", "content": [{
                                             "type": "tool_result",
                                             "tool_use_id": "L%s%d" % (agent_id, i),
                                             "content": "ok"}]}}) + "\n")

    def build(self):
        os.utime(self.paths.session_jsonl, (self.now, self.now))
        return RunBuilder(self.paths, now_fn=lambda: self.now).refresh()

    def test_a_running_agent_repeating_itself_is_flagged_with_evidence(self):
        self.add_calls("a3", 7)
        loop = self.build().agent("a3").loop
        self.assertEqual((loop["kind"], loop["tool"], loop["target"]),
                         ("repeat", "Bash", "npm test"))
        self.assertGreaterEqual(loop["count"], 7)

    def test_a_healthy_agent_has_no_loop(self):
        self.assertIsNone(self.build().agent("a3").loop)

    def test_a_finished_agent_is_never_flagged(self):
        # a1 completed at +60s; these calls are timestamped before that, so it
        # stays completed (calls AFTER a notification would read as a resume).
        self.add_calls("a1", 9, start=31)
        run = self.build()
        self.assertEqual(run.agent("a1").status, C.COMPLETED)
        self.assertIsNone(run.agent("a1").loop)

    def test_activity_after_completion_means_resumed_and_can_then_loop(self):
        self.add_calls("a1", 9, start=82)             # after the +60s notification
        run = self.build()
        self.assertEqual(run.agent("a1").status, C.RUNNING)
        self.assertIsNotNone(run.agent("a1").loop)

    def test_threshold_follows_the_constant(self):
        self.add_calls("a3", 4)
        with mock.patch.object(C, "LOOP_REPEATS", 3):
            self.assertIsNotNone(self.build().agent("a3").loop)
        self.assertIsNone(self.build().agent("a3").loop)

    def test_the_loop_reaches_the_summary_and_is_scrubbed(self):
        self.add_calls("a3", 7, target="curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789'")
        os.utime(self.paths.session_jsonl, (self.now, self.now))
        summary = RunBuilder(self.paths, now_fn=lambda: self.now).refresh().to_summary_dict()
        a3 = [a for a in summary["agents"] if a["agent_id"] == "a3"][0]
        self.assertEqual(a3["loop"]["kind"], "repeat")
        self.assertNotIn("abcdefghijklmnop", json.dumps(a3["loop"]))


if __name__ == "__main__":
    unittest.main()
