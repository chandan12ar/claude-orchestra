import json
import os
import re
import tempfile
import unittest

from orchestra.build import RunBuilder
from orchestra.parent import parse_timestamp
from orchestra.report import render_report, write_report
from tests.fixtures import build_session, ts


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)
        self.builder = RunBuilder(self.paths, now_fn=lambda: parse_timestamp(ts(150)))


class TestRenderReport(ReportTestCase):
    def test_contains_no_external_references(self):
        html = render_report(self.builder.refresh(), {})
        self.assertIsNone(re.search(r"""(?:src|href)=["'](?:https?:)?//""", html))

    def test_has_no_link_or_script_src_tags(self):
        html = render_report(self.builder.refresh(), {})
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertNotIn("<script src=", html)

    def test_embeds_the_run_payload(self):
        run = self.builder.refresh()
        html = render_report(run, {})
        match = re.search(r"window\.ORCHESTRA_RUN\s*=\s*(\{.*?\});", html, re.DOTALL)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        self.assertEqual(payload["totals"]["agents"], 3)

    def test_embeds_agent_details(self):
        run = self.builder.refresh()
        details = {a.agent_id: a.to_detail_dict() for a in run.agents}
        html = render_report(run, details)
        match = re.search(r"window\.ORCHESTRA_DETAILS\s*=\s*(\{.*?\});", html, re.DOTALL)
        payload = json.loads(match.group(1))
        self.assertIn("You are planning", payload["a1"]["brief"])

    def test_title_names_the_session(self):
        self.assertIn("s1", render_report(self.builder.refresh(), {}))


class TestWriteReport(ReportTestCase):
    def test_writes_the_file_and_returns_its_path(self):
        target = os.path.join(self.root, "out", "report.html")
        written = write_report(self.builder, target)
        self.assertEqual(written, target)
        self.assertTrue(os.path.isfile(target))
        with open(target, encoding="utf-8") as fh:
            self.assertIn("ORCHESTRA_RUN", fh.read())

    def test_default_name_includes_the_session_id(self):
        written = write_report(self.builder, os.path.join(self.root, ""))
        self.assertIn("s1", os.path.basename(written))


if __name__ == "__main__":
    unittest.main()
