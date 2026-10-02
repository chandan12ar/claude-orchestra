"""Guards against a class of bug that only shows on Windows.

Python decodes a subprocess's output with the *locale* encoding when asked for
text, and on Windows that is cp1252, not UTF-8. Node writes UTF-8. Any test
that reads Node output containing a character like the middle dot or a play
marker then compares mojibake, and passes on Linux and macOS while failing on
Windows (this broke CI for three commits). Always name the encoding.
"""

import glob
import os
import re
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))


class TestSubprocessEncoding(unittest.TestCase):
    def test_no_test_asks_subprocess_for_locale_decoded_text(self):
        offenders = []
        for path in sorted(glob.glob(os.path.join(TESTS, "*.py"))):
            if os.path.basename(path) == os.path.basename(__file__):
                continue
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    if re.search(r"\btext\s*=\s*True\b", line):
                        offenders.append("{}:{}".format(os.path.basename(path), number))
        self.assertEqual(offenders, [],
                         'use encoding="utf-8" instead of text=True')

    def test_every_node_run_names_its_encoding(self):
        for path in sorted(glob.glob(os.path.join(TESTS, "*.py"))):
            if os.path.basename(path) == os.path.basename(__file__):
                continue
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            for match in re.finditer(r"subprocess\.run\(\[NODE", source):
                call = source[match.start():match.start() + 220]
                self.assertIn('encoding="utf-8"', call,
                              "{}: a node subprocess without an encoding".format(
                                  os.path.basename(path)))


class TestFilesAreWrittenAsUtf8(unittest.TestCase):
    def test_generated_js_is_written_with_an_explicit_encoding(self):
        for path in sorted(glob.glob(os.path.join(TESTS, "*.py"))):
            if os.path.basename(path) == os.path.basename(__file__):
                continue
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            for match in re.finditer(r'open\(path, "w"[^)]*\)', source):
                self.assertIn("encoding", match.group(0),
                              "{}: {}".format(os.path.basename(path), match.group(0)))


if __name__ == "__main__":
    unittest.main()
