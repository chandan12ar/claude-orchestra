import unittest

from orchestra.agentlog import AgentDigest, TokenTally
from tests.fixtures import ts

TS = "2026-09-09T05:00:00.000Z"
TS_LATER = "2026-09-09T05:05:00.000Z"


def assistant(blocks, usage=None, timestamp=TS, model="claude-haiku-4-5-20251001"):
    message = {"role": "assistant", "model": model, "content": blocks}
    if usage:
        message["usage"] = usage
    return {"type": "assistant", "timestamp": timestamp, "message": message}


def tool_use(name, **params):
    return {"type": "tool_use", "id": "t1", "name": name, "input": params}


class TestTokens(unittest.TestCase):
    def test_sums_all_four_token_kinds(self):
        d = AgentDigest()
        d.ingest([assistant([], usage={"input_tokens": 10, "output_tokens": 5,
                                       "cache_read_input_tokens": 100,
                                       "cache_creation_input_tokens": 7})])
        self.assertEqual(d.tokens, {"input": 10, "output": 5,
                                    "cache_read": 100, "cache_create": 7})

    def test_accumulates_across_entries_and_ingests(self):
        d = AgentDigest()
        d.ingest([assistant([], usage={"input_tokens": 10, "output_tokens": 5})])
        d.ingest([assistant([], usage={"input_tokens": 1, "output_tokens": 2})])
        self.assertEqual(d.tokens["input"], 11)
        self.assertEqual(d.tokens["output"], 7)

    def test_missing_usage_is_safe(self):
        d = AgentDigest()
        d.ingest([assistant([])])
        self.assertEqual(d.tokens, {})


class TestModel(unittest.TestCase):
    def test_model_is_captured_from_the_message(self):
        d = AgentDigest()
        d.ingest([assistant([], model="claude-opus-4-1-20260305")])
        self.assertEqual(d.model, "claude-opus-4-1-20260305")

    def test_synthetic_wrapup_message_never_overwrites_the_real_model(self):
        # Claude Code injects a synthetic wrap-up message (model literally
        # "<synthetic>") on an interrupted or errored turn. It must not clobber
        # the last genuine model this agent actually ran on.
        d = AgentDigest()
        d.ingest([assistant([], model="claude-sonnet-5"),
                  assistant([], model="<synthetic>", timestamp=TS_LATER)])
        self.assertEqual(d.model, "claude-sonnet-5")


class TestToolCalls(unittest.TestCase):
    def test_read_and_write_targets_are_file_paths(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Write", file_path="src/a.py", content="x"),
                             tool_use("Read", file_path="src/b.py")])])
        self.assertEqual([(t.name, t.target) for t in d.tool_calls],
                         [("Write", "src/a.py"), ("Read", "src/b.py")])

    def test_files_written_and_read_are_separated(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Write", file_path="src/a.py"),
                             tool_use("Edit", file_path="src/a.py"),
                             tool_use("Read", file_path="src/b.py")])])
        self.assertEqual(d.files_written, ["src/a.py"])
        self.assertEqual(d.files_read, ["src/b.py"])

    def test_bash_target_is_the_truncated_command(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="x" * 300)])])
        self.assertTrue(d.tool_calls[0].target.startswith("x"))
        self.assertLessEqual(len(d.tool_calls[0].target), 123)

    def test_grep_records_pattern_and_reads_path(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Grep", pattern="def foo", path="src/")])])
        self.assertEqual(d.tool_calls[0].target, "def foo")
        self.assertEqual(d.files_read, ["src/"])

    def test_unknown_tool_does_not_raise(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("SomeFutureTool", whatever={"a": 1})])])
        self.assertEqual(d.tool_calls[0].name, "SomeFutureTool")

    def test_unknown_tool_target_falls_back_to_first_string_param(self):
        # No entry in _TARGET_FIELDS for this tool name, so _target_for must
        # scan params.values() and pick the first string it finds.
        d = AgentDigest()
        d.ingest([assistant([tool_use("SomeFutureTool", whatever={"a": 1},
                                      note="fallback text")])])
        self.assertEqual(d.tool_calls[0].target, "fallback text")


class TestActivityAndFinalText(unittest.TestCase):
    def test_last_activity_tracks_latest_timestamp(self):
        d = AgentDigest()
        d.ingest([assistant([], timestamp=TS), assistant([], timestamp=TS_LATER)])
        d2 = AgentDigest()
        d2.ingest([assistant([], timestamp=TS_LATER)])
        self.assertEqual(d.last_activity_at, d2.last_activity_at)

    def test_final_text_is_the_last_assistant_text_block(self):
        d = AgentDigest()
        d.ingest([assistant([{"type": "text", "text": "first"}]),
                  assistant([{"type": "text", "text": "final answer"}])])
        self.assertEqual(d.final_text, "final answer")

    def test_ends_mid_tool_when_last_entry_is_an_unanswered_tool_use(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="sleep 100")])])
        self.assertTrue(d.ended_mid_tool)

    def test_not_mid_tool_when_a_result_followed(self):
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="ls")]),
                  {"type": "user", "timestamp": TS_LATER, "message": {"role": "user",
                   "content": [{"type": "tool_result", "tool_use_id": "t1",
                                "content": "ok"}]}}])
        self.assertFalse(d.ended_mid_tool)

    def test_mid_tool_flag_tracks_correctly_across_separate_ingest_calls(self):
        # Production driving pattern: each poll delivers only the new lines
        # since the last read, as separate ingest() calls.
        d = AgentDigest()
        d.ingest([assistant([tool_use("Bash", command="sleep 100")])])
        self.assertTrue(d.ended_mid_tool)

        d.ingest([{"type": "user", "timestamp": TS_LATER, "message": {"role": "user",
                   "content": [{"type": "tool_result", "tool_use_id": "t1",
                                "content": "ok"}]}}])
        self.assertFalse(d.ended_mid_tool)

        d.ingest([assistant([tool_use("Bash", command="sleep 200")],
                            timestamp=TS_LATER)])
        self.assertTrue(d.ended_mid_tool)


class TestTokenEvents(unittest.TestCase):
    """When tokens were spent, for the live charts (fresh = input + output + cache writes)."""

    def usage(self, i, o, cw=0, cr=0):
        return {"input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": cw,
                "cache_read_input_tokens": cr}

    def test_records_fresh_tokens_with_the_time_they_were_spent(self):
        d = AgentDigest()
        d.ingest([assistant([], usage=self.usage(10, 5, 7, cr=1000), timestamp=TS)])
        d.ingest([assistant([], usage=self.usage(1, 2), timestamp=TS_LATER)])
        self.assertEqual([added for _, added in d.token_events], [22, 3])     # cache reads are not fresh
        self.assertLess(d.token_events[0][0], d.token_events[1][0])

    def test_one_api_message_repeated_per_content_block_counts_once(self):
        entry = assistant([], usage=self.usage(10, 5))
        entry["message"]["id"] = "msg_1"
        d = AgentDigest()
        d.ingest([entry, dict(entry), dict(entry)])
        self.assertEqual(sum(added for _, added in d.token_events), 15)

    def test_no_usage_or_no_timestamp_records_nothing(self):
        d = AgentDigest()
        d.ingest([assistant([]), {"type": "assistant", "message": {"role": "assistant", "content": [],
                                                                   "usage": self.usage(5, 5)}}])
        self.assertEqual(d.token_events, [])

    def test_the_list_is_capped_and_still_adds_up(self):
        import orchestra.agentlog as agentlog
        d = AgentDigest()
        for i in range(agentlog.MAX_TOKEN_EVENTS + 25):
            d.ingest([assistant([], usage=self.usage(0, 1))])
        self.assertEqual(len(d.token_events), agentlog.MAX_TOKEN_EVENTS)
        self.assertEqual(sum(added for _, added in d.token_events), agentlog.MAX_TOKEN_EVENTS + 25)


if __name__ == "__main__":
    unittest.main()


class TestUsageIsCountedOncePerApiMessage(unittest.TestCase):
    """Claude Code writes one entry per content block, each repeating the
    message's usage. A real session had 271 such entries for 103 messages."""

    def entry(self, mid, block, usage, model="claude-sonnet-5", at=1):
        message = {"role": "assistant", "id": mid, "model": model,
                   "content": [block], "usage": usage}
        return {"isSidechain": True, "timestamp": ts(at), "type": "assistant",
                "message": message}

    USAGE = {"input_tokens": 2, "output_tokens": 295,
             "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}

    def blocks(self):
        return [{"type": "thinking", "thinking": "hm"},
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "a.py"}},
                {"type": "tool_use", "id": "t2", "name": "Read",
                 "input": {"file_path": "b.py"}}]

    def test_four_blocks_of_one_message_count_once(self):
        d = AgentDigest()
        d.ingest([self.entry("msg_1", b, self.USAGE) for b in self.blocks()])
        self.assertEqual(d.tokens, {"input": 2, "output": 295,
                                    "cache_read": 1000, "cache_create": 50})

    def test_distinct_messages_still_add_up(self):
        d = AgentDigest()
        d.ingest([self.entry("msg_1", self.blocks()[0], self.USAGE),
                  self.entry("msg_2", self.blocks()[0], self.USAGE)])
        self.assertEqual(d.tokens["output"], 590)

    def test_when_a_message_recurs_with_grown_usage_the_latest_wins(self):
        d = AgentDigest()
        early = dict(self.USAGE, output_tokens=10)
        d.ingest([self.entry("msg_1", self.blocks()[0], early),
                  self.entry("msg_1", self.blocks()[1], self.USAGE)])
        self.assertEqual(d.tokens["output"], 295)

    def test_dedup_survives_being_split_across_incremental_reads(self):
        d = AgentDigest()
        d.ingest([self.entry("msg_1", self.blocks()[0], self.USAGE)])
        d.ingest([self.entry("msg_1", self.blocks()[1], self.USAGE),
                  self.entry("msg_1", self.blocks()[2], self.USAGE)])
        self.assertEqual(d.tokens["output"], 295)

    def test_entries_without_an_id_are_each_counted_as_before(self):
        d = AgentDigest()
        for _ in range(3):
            e = self.entry("x", self.blocks()[0], self.USAGE)
            del e["message"]["id"]
            d.ingest([e])
        self.assertEqual(d.tokens["output"], 885)

    def test_tokens_are_attributed_per_model(self):
        d = AgentDigest()
        d.ingest([self.entry("msg_1", self.blocks()[0], self.USAGE, model="claude-opus-5"),
                  self.entry("msg_2", self.blocks()[0], self.USAGE, model="claude-haiku-4-5")])
        self.assertEqual(set(d.tokens_by_model), {"claude-opus-5", "claude-haiku-4-5"})
        self.assertEqual(d.tokens_by_model["claude-opus-5"]["output"], 295)

    def test_a_synthetic_message_is_attributed_to_the_last_real_model(self):
        d = AgentDigest()
        d.ingest([self.entry("msg_1", self.blocks()[0], self.USAGE, model="claude-opus-5"),
                  self.entry("msg_2", self.blocks()[0], self.USAGE, model="<synthetic>")])
        self.assertEqual(list(d.tokens_by_model), ["claude-opus-5"])
        self.assertEqual(d.tokens_by_model["claude-opus-5"]["output"], 590)

    def test_per_model_totals_always_sum_to_the_overall_total(self):
        d = AgentDigest()
        d.ingest([self.entry("m1", self.blocks()[0], self.USAGE, model="a"),
                  self.entry("m1", self.blocks()[1], self.USAGE, model="a"),
                  self.entry("m2", self.blocks()[0], self.USAGE, model="b")])
        for label, total in d.tokens.items():
            self.assertEqual(sum(m.get(label, 0) for m in d.tokens_by_model.values()), total)

    def test_tool_calls_are_still_recorded_per_block(self):
        d = AgentDigest()
        d.ingest([self.entry("msg_1", b, self.USAGE) for b in self.blocks()])
        self.assertEqual([c.target for c in d.tool_calls], ["a.py", "b.py"])


class TestTokenTally(unittest.TestCase):
    def test_counts_the_orchestrators_messages_once_each(self):
        t = TokenTally()
        usage = {"input_tokens": 1, "output_tokens": 100}
        entries = [{"type": "assistant", "message": {
            "id": "m1", "model": "claude-opus-5", "usage": usage, "content": []}}
            for _ in range(4)]
        t.ingest(entries)
        self.assertEqual(t.tokens, {"input": 1, "output": 100})
        self.assertEqual(t.model, "claude-opus-5")

    def test_sidechain_entries_are_not_the_orchestrators(self):
        t = TokenTally()
        t.ingest([{"isSidechain": True, "message": {
            "id": "m1", "usage": {"output_tokens": 999}}}])
        self.assertEqual(t.tokens, {})

    def test_junk_entries_are_ignored(self):
        t = TokenTally()
        t.ingest([None, "x", {}, {"message": "nope"}, {"message": {"usage": 3}}])
        self.assertEqual(t.tokens, {})

    def test_reset_forgets_everything(self):
        t = TokenTally()
        t.ingest([{"message": {"id": "m", "usage": {"output_tokens": 5}}}])
        t.reset()
        self.assertEqual((t.tokens, t.by_model, t.model), ({}, {}, ""))
