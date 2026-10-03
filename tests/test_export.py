import csv
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout

from orchestra import export as X
from orchestra.__main__ import main
from orchestra.http import serve
from orchestra.parent import parse_timestamp
from orchestra.service import OrchestraService
from tests.fixtures import build_session, ts

TOKEN = "tok-export"

AGENT = {"agent_id": "a1", "parent_agent_id": None, "agent_type": "Explore",
         "description": "Plan the work", "model": "claude-haiku-4-5",
         "launch_mode": "background", "status": "completed",
         "started_at": 1790000000.0, "ended_at": 1790000060.5, "duration_s": 60.5,
         "tokens": {"input": 10, "output": 20, "cache_read": 30, "cache_create": 40},
         "cost": 0.0123456789, "tool_call_count": 7, "files_written_count": 2,
         "loop": None}


def parse(text):
    assert text.startswith(X.BOM)
    return list(csv.reader(io.StringIO(text[len(X.BOM):])))


class TestSafeCell(unittest.TestCase):
    def test_formula_starters_are_neutralised(self):
        for text in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx",
                     '=HYPERLINK("http://evil","click")'):
            self.assertEqual(X.safe_cell(text), "'" + text)

    def test_ordinary_text_is_untouched(self):
        for text in ("Plan the work", "a=b", "x-y", "", "1+1"):
            self.assertEqual(X.safe_cell(text), text)

    def test_numbers_pass_through_even_when_negative(self):
        self.assertEqual(X.safe_cell(-5), -5)
        self.assertEqual(X.safe_cell(0.5), 0.5)


class TestAgentsCsv(unittest.TestCase):
    def rows(self, agent=AGENT, **top):
        summary = {"session_id": "s1", "agents": [agent]}
        summary.update(top)
        return parse(X.agents_csv(summary))

    def test_header_is_the_stable_column_list(self):
        header = self.rows()[0]
        self.assertEqual(tuple(header), X.AGENT_COLUMNS)
        self.assertEqual(header[0], "agent_id")

    def test_values(self):
        row = dict(zip(X.AGENT_COLUMNS, self.rows()[1]))
        self.assertEqual(row["agent_id"], "a1")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["input_tokens"], "10")
        self.assertEqual(row["cache_create_tokens"], "40")
        self.assertEqual(row["cost"], "0.012346")
        self.assertEqual(row["duration_s"], "60.5")
        self.assertEqual(row["tool_calls"], "7")

    def test_timestamps_are_iso8601_utc(self):
        row = dict(zip(X.AGENT_COLUMNS, self.rows()[1]))
        self.assertRegex(row["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(X.iso(0), "1970-01-01T00:00:00Z")

    def test_unfinished_and_unpriced_agents_have_blank_cells_not_zero(self):
        agent = dict(AGENT, ended_at=None, duration_s=None, cost=None)
        row = dict(zip(X.AGENT_COLUMNS, self.rows(agent)[1]))
        self.assertEqual((row["ended_at"], row["duration_s"], row["cost"]), ("", "", ""))

    def test_a_hostile_description_cannot_become_a_formula(self):
        agent = dict(AGENT, description='=cmd|"/c calc"!A1')
        row = dict(zip(X.AGENT_COLUMNS, self.rows(agent)[1]))
        self.assertTrue(row["description"].startswith("'="))

    def test_commas_quotes_and_newlines_round_trip(self):
        agent = dict(AGENT, description='say "hi", then\nleave')
        row = dict(zip(X.AGENT_COLUMNS, self.rows(agent)[1]))
        self.assertEqual(row["description"], 'say "hi", then\nleave')

    def test_a_possible_loop_is_described(self):
        agent = dict(AGENT, loop={"kind": "repeat", "count": 9, "calls": [
            {"tool": "Bash", "target": "npm test"}]})
        row = dict(zip(X.AGENT_COLUMNS, self.rows(agent)[1]))
        self.assertEqual(row["possible_loop"], "repeat x9: Bash npm test")

    def test_unicode_survives(self):
        agent = dict(AGENT, description="caf\u00e9 \u2192 \u65e5\u672c")
        row = dict(zip(X.AGENT_COLUMNS, self.rows(agent)[1]))
        self.assertEqual(row["description"], "caf\u00e9 \u2192 \u65e5\u672c")

    def test_no_agents_is_just_the_header(self):
        self.assertEqual(len(parse(X.agents_csv({"agents": []}))), 1)

    def test_rows_use_crlf_for_spreadsheets(self):
        self.assertIn("\r\n", X.agents_csv({"agents": [AGENT]}))


class TestRenderAndFilename(unittest.TestCase):
    def test_unknown_format_is_none(self):
        self.assertIsNone(X.render({"session_id": "s"}, "xml"))

    def test_json_round_trips(self):
        summary = {"session_id": "s", "agents": [AGENT], "totals": {"agents": 1}}
        _, body, _ = X.render(summary, "json")
        self.assertEqual(json.loads(body), summary)

    def test_filename_cannot_carry_path_or_header_injection(self):
        for evil in ('../../etc/passwd', 'a"b', "a\r\nSet-Cookie: x=1", "a b;c"):
            name = X.filename({"session_id": evil}, "csv")
            self.assertRegex(name, r"^workflow-[A-Za-z0-9_-]+\.csv$")

    def test_empty_session_id_still_gets_a_name(self):
        self.assertEqual(X.filename({"session_id": ""}, "json"), "workflow-session.json")


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        self.service = OrchestraService(root=self.root, token=TOKEN,
                                        default_session="s1",
                                        now_fn=lambda: parse_timestamp(ts(150)))
        self.server, _ = serve(self.service, port=0)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def get(self, path, token=TOKEN):
        url = self.base + path + (("&" if "?" in path else "?") + "k=" + token if token else "")
        return urllib.request.urlopen(url, timeout=5)


class TestEndpoint(ServerCase):
    def test_csv_download(self):
        resp = self.get("/api/export?format=csv")
        self.assertIn("text/csv", resp.headers["Content-Type"])
        self.assertEqual(resp.headers["Content-Disposition"],
                         'attachment; filename="workflow-s1.csv"')
        rows = parse(resp.read().decode("utf-8"))
        self.assertEqual(len(rows), 1 + 3)                    # header + 3 agents
        self.assertEqual({r[0] for r in rows[1:]}, {"a1", "a2", "a3"})

    def test_json_download_is_the_run_summary(self):
        resp = self.get("/api/export?format=json")
        data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(len(data["agents"]), 3)

    def test_default_format_is_csv(self):
        self.assertIn("text/csv", self.get("/api/export").headers["Content-Type"])

    def test_unknown_format_is_a_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/export?format=xml")
        self.assertEqual(ctx.exception.code, 400)

    def test_requires_the_token(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/export?format=csv", token=None)
        self.assertEqual(ctx.exception.code, 403)

    def test_unknown_session_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/export?format=csv&session=nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_the_export_is_scrubbed_like_the_dashboard(self):
        # Descriptions pass through scrub() in to_light_dict, so a secret in a
        # task description never reaches an export.
        import json as _json
        meta = os.path.join(self.root, "projects", "E--proj", "s1", "subagents",
                            "agent-a1.meta.json")
        with open(meta, encoding="utf-8") as fh:
            data = _json.load(fh)
        data["description"] = "deploy with key sk-ant-api03-" + "Z" * 40
        with open(meta, "w", encoding="utf-8") as fh:
            _json.dump(data, fh)
        for fmt in ("csv", "json"):
            body = self.get("/api/export?format=" + fmt).read().decode("utf-8")
            self.assertNotIn("sk-ant-api03", body)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        os.environ["CLAUDE_CONFIG_DIR"] = self.root
        os.environ["ORCHESTRA_STATE_DIR"] = tempfile.mkdtemp()
        os.environ["ORCHESTRA_PRICES"] = os.path.join(self.root, "none.json")
        for key in ("CLAUDE_CONFIG_DIR", "ORCHESTRA_STATE_DIR", "ORCHESTRA_PRICES"):
            self.addCleanup(os.environ.pop, key, None)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(list(argv))
        return code, out.getvalue().strip()

    def test_writes_a_csv_named_for_the_session(self):
        directory = tempfile.mkdtemp()
        code, printed = self.run_cli("--session", "s1", "--export", "csv", "--out", directory)
        self.assertEqual(code, 0)
        self.assertEqual(printed, os.path.join(directory, "workflow-s1.csv"))
        with open(printed, encoding="utf-8", newline="") as fh:
            self.assertEqual(len(parse(fh.read())), 4)

    def test_writes_json_to_an_explicit_file(self):
        target = os.path.join(tempfile.mkdtemp(), "run.json")
        code, _ = self.run_cli("--session", "s1", "--export", "json", "--out", target)
        self.assertEqual(code, 0)
        with open(target, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["session_id"], "s1")

    def test_csv_keeps_crlf_on_every_platform(self):
        target = os.path.join(tempfile.mkdtemp(), "r.csv")
        self.run_cli("--session", "s1", "--export", "csv", "--out", target)
        with open(target, "rb") as fh:
            raw = fh.read()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\r\r\n", raw)

    def test_default_location_is_the_cwd_flag(self):
        directory = tempfile.mkdtemp()
        code, printed = self.run_cli("--session", "s1", "--export", "csv", "--cwd", directory)
        self.assertEqual(os.path.dirname(printed), directory)

    def test_unknown_session_fails_clearly(self):
        code, printed = self.run_cli("--session", "ghost", "--export", "csv")
        self.assertEqual(code, 2)
        self.assertIn("ghost", printed)


if __name__ == "__main__":
    unittest.main()


class TestExportUi(unittest.TestCase):
    STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "orchestra", "static")

    def read(self, name):
        with open(os.path.join(self.STATIC, name), encoding="utf-8") as fh:
            return fh.read()

    def test_menu_is_in_the_page_and_the_report_shell_and_starts_hidden(self):
        from orchestra import report
        for html in (self.read("index.html"), report._SHELL):
            self.assertRegex(html, r'<select id="export-select"[^>]*hidden')
            self.assertIn('value="csv"', html)
            self.assertIn('value="json"', html)

    def test_only_offered_on_the_live_dashboard(self):
        js = self.read("app.js")
        self.assertIn("exportSelect && !state.offline", js)

    def test_download_goes_through_the_server_with_the_token_and_session(self):
        js = self.read("app.js")
        body = js[js.index("function downloadExport("):]
        body = body[:body.index("\n}\n")]
        for needle in ("/api/export?format=", "TOKEN", "state.sessionId"):
            self.assertIn(needle, body)

    def test_the_menu_resets_after_use(self):
        js = self.read("app.js")
        self.assertIn('exportSelect.value = ""', js)
