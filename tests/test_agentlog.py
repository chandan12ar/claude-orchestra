import unittest

from orchestra.agentlog import AgentDigest

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


if __name__ == "__main__":
    unittest.main()
