"""Search across agents, tool calls and files."""

import unittest

from orchestra import search
from orchestra.model import Agent, Extraction, Round, Run, ToolCall


def agent(aid, desc, calls=(), written=(), read=(), objective=""):
    a = Agent(agent_id=aid, description=desc, status="completed")
    a.rounds = [Round(started_at=0, ended_at=1)]
    a.tool_calls = [ToolCall(n, t, i) for i, (n, t) in enumerate(calls)]
    a.files_written, a.files_read = list(written), list(read)
    a.objective = Extraction(objective, "")
    return a


RUN = Run(session_id="s", agents=[
    agent("a1", "Build the checkout UI", [("Edit", "/p/src/ui/Checkout.tsx"), ("Bash", "npm run lint")],
          written=["/p/src/ui/Checkout.tsx"], objective="Ship the payment form"),
    agent("a2", "Write the unit tests", [("Read", "/p/src/ui/Checkout.tsx")],
          read=["/p/src/ui/Checkout.tsx"]),
])


class TestSearch(unittest.TestCase):
    def test_finds_tool_calls_and_files_across_agents(self):
        out = search.search(RUN, "checkout.tsx")
        self.assertEqual({t["agent_id"] for t in out["tools"]}, {"a1", "a2"})
        self.assertEqual(len(out["files"]), 1)
        self.assertEqual(out["files"][0]["writers"], ["a1"])
        self.assertEqual(out["files"][0]["readers"], ["a2"])

    def test_agents_match_description_and_objective(self):
        self.assertEqual([a["agent_id"] for a in search.search(RUN, "unit tests")["agents"]], ["a2"])
        self.assertEqual([a["agent_id"] for a in search.search(RUN, "payment form")["agents"]], ["a1"])

    def test_all_terms_must_match(self):
        self.assertEqual(search.search(RUN, "checkout lint")["tools"][0]["tool"], "Bash")
        self.assertEqual(search.search(RUN, "lint tests")["tools"], [])

    def test_short_or_blank_query_returns_nothing(self):
        for q in ("", " ", "a"):
            out = search.search(RUN, q)
            self.assertEqual((out["agents"], out["tools"], out["files"]), ([], [], []))

    def test_secret_in_a_target_is_neither_shown_nor_searchable(self):
        secret = "sk-ant-api03-" + "Z" * 40
        run = Run(session_id="s", agents=[agent("a", "deploy", [("Bash", "curl -H " + secret)])])
        self.assertEqual(search.search(run, "ZZZZZZZZZZ")["tools"], [])
        self.assertNotIn("ZZZZZZZZZZ", repr(search.search(run, "curl")))

    def test_results_are_capped_and_flagged(self):
        many = agent("a", "x", [("Read", "/p/file%d" % i) for i in range(100)])
        out = search.search(Run(session_id="s", agents=[many]), "file")
        self.assertEqual(len(out["tools"]), search.LIMITS["tools"])
        self.assertTrue(out["truncated"])


if __name__ == "__main__":
    unittest.main()


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        import tempfile
        import urllib.request
        from orchestra.http import serve
        from orchestra.parent import parse_timestamp
        from orchestra.service import OrchestraService
        from tests.fixtures import build_session, ts
        self.urlopen = urllib.request.urlopen
        root = tempfile.mkdtemp()
        build_session(root, "s1")
        service = OrchestraService(root=root, token="tok", default_session="s1",
                                   now_fn=lambda: parse_timestamp(ts(150)))
        self.server, _ = serve(service, port=0)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def test_search_finds_the_shared_plan_file(self):
        import json
        resp = self.urlopen(self.base + "/api/search?q=PLAN.md&k=tok", timeout=5)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data["files"])
        self.assertTrue(data["tools"])

    def test_requires_the_token(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.urlopen(self.base + "/api/search?q=PLAN.md", timeout=5)
        self.assertEqual(ctx.exception.code, 403)


class TestRepeatedCalls(unittest.TestCase):
    def test_identical_calls_by_one_agent_collapse_into_one_counted_row(self):
        looper = agent("a", "e2e", [("Bash", "npm run e2e")] * 9 + [("Read", "/p/spec.ts")])
        out = search.search(Run(session_id="s", agents=[looper]), "e2e")
        rows = [t for t in out["tools"] if t["tool"] == "Bash"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 9)

    def test_the_same_call_by_different_agents_stays_separate(self):
        a1 = agent("a1", "one", [("Bash", "npm test")])
        a2 = agent("a2", "two", [("Bash", "npm test")])
        out = search.search(Run(session_id="s", agents=[a1, a2]), "npm test")
        self.assertEqual(sorted(t["agent_id"] for t in out["tools"]), ["a1", "a2"])
