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
        self.assertEqual(entries[0]["a"], "caf�")

    def test_unescaped_line_separator_in_string_is_not_split(self):
        # Node's JSON.stringify does not escape U+2028/U+2029, so a single
        # complete, valid JSON line can legally contain a literal (raw,
        # unescaped) line-separator character inside a string value.
        # str.splitlines() would treat that character as a line break and
        # shred the entry into two fragments that both fail to parse;
        # str.split("\n") must not. Built by hand with chr(0x2028) (not via
        # self.line/json.dumps) because json.dumps would escape the
        # character rather than emit it raw, which would not reproduce
        # the bug.
        sep = chr(0x2028)
        raw_line = '{"a": 1, "text": "before' + sep + 'after"}\n'
        self.write(raw_line)
        entries = self.reader.read_new(self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["text"], "before" + sep + "after")

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
