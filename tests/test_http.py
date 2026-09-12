import json
import tempfile
import unittest
import urllib.error
import urllib.request

from orchestra.http import serve
from orchestra.parent import parse_timestamp
from orchestra.service import OrchestraService
from tests.fixtures import build_session, ts

TOKEN = "test-token-123"


class HttpTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        service = OrchestraService(root=self.root, token=TOKEN, default_session="s1",
                                   now_fn=lambda: parse_timestamp(ts(150)))
        self.server, self.thread = serve(service, port=0)
        self.base = "http://127.0.0.1:{}".format(self.server.server_port)
        self.addCleanup(self.server.shutdown)

    def get(self, path, token=TOKEN, host=None):
        url = self.base + path
        if token is not None:
            url += ("&" if "?" in path else "?") + "k=" + token
        request = urllib.request.Request(url)
        if host:
            request.add_header("Host", host)
        return urllib.request.urlopen(request, timeout=5)

    def get_json(self, path, **kw):
        return json.loads(self.get(path, **kw).read().decode("utf-8"))


class TestAuth(HttpTestCase):
    def test_missing_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run", token=None)
        self.assertEqual(ctx.exception.code, 403)

    def test_wrong_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run", token="nope")
        self.assertEqual(ctx.exception.code, 403)

    def test_non_loopback_host_header_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run", host="evil.example.com")
        self.assertEqual(ctx.exception.code, 403)

    def test_cross_site_origin_is_rejected(self):
        request = urllib.request.Request(
            self.base + "/api/run?k=" + TOKEN,
            headers={"Origin": "https://evil.example.com"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 403)

    def test_loopback_origin_is_allowed(self):
        request = urllib.request.Request(
            self.base + "/api/run?k=" + TOKEN,
            headers={"Origin": self.base})
        self.assertEqual(urllib.request.urlopen(request, timeout=5).status, 200)

    def test_static_page_does_not_require_a_token(self):
        self.assertEqual(self.get("/", token=None).status, 200)


class TestApi(HttpTestCase):
    def test_run_returns_the_default_session(self):
        data = self.get_json("/api/run")
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["totals"]["agents"], 3)
        self.assertEqual(len(data["agents"]), 3)

    def test_run_agents_are_light(self):
        agent = self.get_json("/api/run")["agents"][0]
        self.assertNotIn("brief", agent)

    def test_agent_detail_is_heavy(self):
        detail = self.get_json("/api/agent/a1")
        self.assertIn("You are planning", detail["brief"])
        self.assertIn("PLAN.md", detail["expected_output"])
        self.assertIn("tool_calls", detail)

    def test_unknown_agent_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/agent/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_unknown_session_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/run?session=missing")
        self.assertEqual(ctx.exception.code, 404)

    def test_sessions_list(self):
        data = self.get_json("/api/sessions")
        self.assertEqual(data["sessions"][0]["session_id"], "s1")
        self.assertEqual(data["sessions"][0]["agent_count"], 3)

    def test_health_needs_no_token(self):
        self.assertEqual(self.get("/api/health", token=None).status, 200)

    def test_unknown_route_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nothing")
        self.assertEqual(ctx.exception.code, 404)


class TestStatic(HttpTestCase):
    def test_index_is_html(self):
        response = self.get("/", token=None)
        self.assertIn("text/html", response.headers["Content-Type"])

    def test_path_traversal_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../../../etc/passwd", token=None)
        self.assertIn(ctx.exception.code, (403, 404))


class TestBuilderReuse(unittest.TestCase):
    def test_same_session_reuses_one_builder(self):
        root = tempfile.mkdtemp()
        build_session(root, "s1")
        service = OrchestraService(root=root, token=TOKEN, default_session="s1",
                                   now_fn=lambda: parse_timestamp(ts(150)))
        service.run_summary("s1")
        first = service._builders["s1"]
        service.run_summary("s1")
        self.assertIs(service._builders["s1"], first)


if __name__ == "__main__":
    unittest.main()
