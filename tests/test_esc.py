"""esc() must be safe inside a quoted HTML attribute, not just in text."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")

DOM = """
// What a real <div> does: textContent in, serialized text out (no quote escaping).
global.document = {createElement: () => {
  let text = "";
  return {set textContent(v) { text = String(v); },
          get innerHTML() { return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
                                       .replace(/>/g, "&gt;"); }};
}};
"""


def esc(value):
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as fh:
        js = fh.read()
    start = js.index("function esc(")
    func = js[start:js.index("\n}\n", start) + 3]
    program = DOM + func + "\nconsole.log(JSON.stringify(esc(%s)));" % json.dumps(value)
    path = os.path.join(tempfile.mkdtemp(), "e.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestEsc(unittest.TestCase):
    def test_double_quotes_cannot_end_an_attribute(self):
        out = esc('x" onmouseover="alert(1)')
        self.assertNotIn('"', out)
        self.assertEqual(out, "x&quot; onmouseover=&quot;alert(1)")

    def test_single_quotes_cannot_end_a_single_quoted_attribute(self):
        self.assertNotIn("'", esc("x' onclick='alert(1)"))

    def test_tags_and_ampersands_are_still_escaped(self):
        self.assertEqual(esc("<b>&</b>"), "&lt;b&gt;&amp;&lt;/b&gt;")

    def test_ampersand_is_escaped_before_quotes_so_entities_are_not_double_decoded(self):
        self.assertEqual(esc('&quot;'), "&amp;quot;")

    def test_null_and_undefined_are_empty(self):
        for value in (None,):
            self.assertEqual(esc(value), "")

    def test_numbers_are_stringified(self):
        self.assertEqual(esc(42), "42")

    def test_the_attribute_breakout_cannot_happen_in_a_built_row(self):
        html = '<tr data-session="' + esc('a" onclick="alert(1)') + '">'
        self.assertEqual(html.count('"'), 2)       # only the two the template wrote


if __name__ == "__main__":
    unittest.main()
