import json
import os
import tempfile
import unittest

from orchestra.transcript import IncrementalReader


class TranscriptTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "session.jsonl")
        self.reader = IncrementalReader()

    def write(self, text, mode="ab"):
        with open(self.path, mode) as fh:
            fh.write(text.encode("utf-8"))

    def line(self, **kw):
        return json.dumps(kw) + "\n"


class TestIncrementalRead(TranscriptTestCase):
    def test_reads_all_lines_on_first_pass(self):
        self.write(self.line(a=1) + self.line(a=2))
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [1, 2])

    def test_second_pass_returns_only_new_lines(self):
        self.write(self.line(a=1))
        self.reader.read_new(self.path)
        self.write(self.line(a=2))
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [2])

    def test_no_new_data_returns_empty(self):
        self.write(self.line(a=1))
        self.reader.read_new(self.path)
        self.assertEqual(self.reader.read_new(self.path), [])

    def test_torn_final_line_is_not_consumed_and_is_read_whole_next_time(self):
        complete = self.line(a=1)
        torn = '{"a": 2, "b": "half'
        self.write(complete + torn)
        first = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in first], [1])
        self.assertEqual(self.reader.diagnostics["torn_reads"], 1)

        self.write('way"}\n')
        second = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in second], [2])
        self.assertEqual(second[0]["b"], "halfway")

    def test_unparsable_complete_line_is_counted_and_skipped(self):
        self.write(self.line(a=1) + "this is not json\n" + self.line(a=3))
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [1, 3])
        self.assertEqual(self.reader.diagnostics["unparsable_lines"], 1)

    def test_invalid_utf8_does_not_raise(self):
        with open(self.path, "wb") as fh:
            fh.write(b'{"a": "caf\xe9"}\n')
        entries = self.reader.read_new(self.path)
        self.assertEqual(len(entries), 1)

    def test_truncated_file_resets_offset(self):
        self.write(self.line(a=1) + self.line(a=2))
        self.reader.read_new(self.path)
        self.write(self.line(a=9), mode="wb")
        entries = self.reader.read_new(self.path)
        self.assertEqual([e["a"] for e in entries], [9])

    def test_missing_file_returns_empty(self):
        self.assertEqual(self.reader.read_new(os.path.join(self.dir, "nope.jsonl")), [])


class TestReadJson(TranscriptTestCase):
    def test_reads_a_meta_file(self):
        p = os.path.join(self.dir, "agent.meta.json")
        with open(p, "w") as fh:
            json.dump({"agentType": "general-purpose"}, fh)
        self.assertEqual(self.reader.read_json(p)["agentType"], "general-purpose")

    def test_missing_or_broken_returns_none(self):
        self.assertIsNone(self.reader.read_json(os.path.join(self.dir, "nope.json")))
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        self.assertIsNone(self.reader.read_json(bad))


if __name__ == "__main__":
    unittest.main()
