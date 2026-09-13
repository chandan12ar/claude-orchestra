import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from orchestra.__main__ import (main, portfile_path, read_portfile, url_for,
                                write_portfile)
from tests.fixtures import build_session


class TestPortfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["ORCHESTRA_STATE_DIR"] = self.tmp
        self.addCleanup(os.environ.pop, "ORCHESTRA_STATE_DIR", None)

    def test_round_trip(self):
        write_portfile("s1", 7717, "tok", 4242)
        info = read_portfile("s1")
        self.assertEqual(info["port"], 7717)
        self.assertEqual(info["token"], "tok")
        self.assertEqual(info["pid"], 4242)

    def test_missing_portfile_is_none(self):
        self.assertIsNone(read_portfile("nope"))

    def test_corrupt_portfile_is_none(self):
        with open(portfile_path("s2"), "w") as fh:
            fh.write("{not json")
        self.assertIsNone(read_portfile("s2"))

    def test_path_is_keyed_by_session(self):
        self.assertNotEqual(portfile_path("a"), portfile_path("b"))
        self.assertTrue(portfile_path("a").startswith(self.tmp))

    def test_url_contains_loopback_port_and_token(self):
        url = url_for({"port": 7717, "token": "abc", "session": "s1"})
        self.assertTrue(url.startswith("http://127.0.0.1:7717/"))
        self.assertIn("k=abc", url)
        self.assertIn("session=s1", url)


class TestReportCommand(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        os.environ["CLAUDE_CONFIG_DIR"] = self.root
        os.environ["ORCHESTRA_STATE_DIR"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.addCleanup(os.environ.pop, "ORCHESTRA_STATE_DIR", None)

    def test_report_writes_a_file_and_prints_its_path(self):
        target = os.path.join(self.root, "r.html")
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--session", "s1", "--report", target])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(target))
        self.assertIn(target, out.getvalue())

    def test_unknown_session_exits_nonzero_with_a_clear_message(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--session", "missing", "--report", "x.html"])
        self.assertEqual(code, 2)
        self.assertIn("missing", out.getvalue())


class TestStopCommand(unittest.TestCase):
    def setUp(self):
        os.environ["ORCHESTRA_STATE_DIR"] = tempfile.mkdtemp()
        self.addCleanup(os.environ.pop, "ORCHESTRA_STATE_DIR", None)

    def test_stop_without_a_server_is_not_an_error(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--session", "s1", "--stop"])
        self.assertEqual(code, 0)
        self.assertIn("not running", out.getvalue().lower())

    def test_stop_removes_a_stale_portfile(self):
        write_portfile("s1", 7717, "tok", 999999)  # pid that does not exist
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--session", "s1", "--stop"])
        self.assertIsNone(read_portfile("s1"))


if __name__ == "__main__":
    unittest.main()
