"""fmtDuration, executed under node."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
APP_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static", "app.js")


def fmt_all(values):
    with open(APP_JS, encoding="utf-8") as fh:
        js = fh.read()
    start = js.index("function fmtDuration(")
    src = js[start:js.index("\n}\n", start) + 3]
    program = src + "\nconsole.log(JSON.stringify(%s.map(fmtDuration)));" % json.dumps(values)
    path = os.path.join(tempfile.mkdtemp(), "d.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestFmtDuration(unittest.TestCase):
    def test_formats(self):
        cases = [(None, "—"), (0, "0s"), (59, "59s"), (60, "1m 00s"),
                 (125, "2m 05s"), (3599, "59m 59s"), (3600, "1h 00m"),
                 (3725, "1h 02m"), (86400, "24h 00m"), (2029000, "563h 36m")]
        got = fmt_all([v for v, _ in cases])
        self.assertEqual(got, [want for _, want in cases])

    def test_rounding_never_shows_sixty(self):
        # 119.6s used to print "1m 60s"; 59.6s printed "60s"; 3599.6s "59m 60s".
        self.assertEqual(fmt_all([119.6, 59.6, 3599.6]), ["2m 00s", "1m 00s", "1h 00m"])

    def test_negative_keeps_sign_outside(self):
        self.assertEqual(fmt_all([-30, -150]), ["-30s", "-2m 30s"])


if __name__ == "__main__":
    unittest.main()
