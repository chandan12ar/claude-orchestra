"""The Activity tab's search: every tool call in the run, matched, marked and stepped through."""

import json
import re
import unittest

from tests import test_insights_ui as ui

FNS = ("esc", "fmtClock", "toolBucket", "markTerms", "activitySearching", "localCalls", "callsCount", "callsHtml")


def run_js(body, details=None, run=None):
    pre = "global.window = {ORCHESTRA_DETAILS: %s};\n" % json.dumps(details or {})
    pre += "state.run = %s;\n" % json.dumps(run or {"agents": []})
    return ui.run_js(FNS, ["TOOL_BUCKETS"], pre + body)


DETAILS = {
    "a1": {"description": "Build the checkout UI", "tool_calls": [
        {"name": "Edit", "target": "/p/src/ui/Checkout.tsx", "timestamp": 10, "ok": True},
        {"name": "Bash", "target": "npm run lint", "timestamp": 20, "ok": False},
        {"name": "Bash", "target": "npm run lint", "timestamp": 30, "ok": False},
        {"name": "Bash", "target": "npm run lint", "timestamp": 40, "ok": True}]},
    "a2": {"description": "Write the unit tests", "tool_calls": [
        {"name": "Read", "target": "/p/src/ui/Checkout.tsx", "timestamp": 15, "ok": True},
        {"name": "Bash", "target": "npm test", "timestamp": 50, "ok": False}]},
}
RUN = {"agents": [{"agent_id": "a1", "description": "Build the checkout UI"},
                  {"agent_id": "a2", "description": "Write the unit tests"}]}


@unittest.skipIf(ui.NODE is None, "node is not on PATH")
class TestLocalCalls(unittest.TestCase):
    """A static report has no server; it must find exactly what /api/calls would."""

    def local(self, query, failed=False, details=DETAILS, run=RUN):
        return run_js("console.log(JSON.stringify(localCalls(%s, %s)));" % (json.dumps(query), json.dumps(failed)),
                      details, run)

    def test_the_same_rules_as_the_server(self):
        out = self.local("lint")
        self.assertEqual([(r["tool"], r["count"], r["failed"], r["ok"]) for r in out["rows"]], [("Bash", 3, 2, True)])
        self.assertEqual([r["agent_id"] for r in self.local("checkout.tsx")["rows"]], ["a2", "a1"])
        self.assertEqual(self.local("unit tests")["matched"], 2)
        failed = self.local("", True)
        self.assertEqual([(r["target"], r["failed"]) for r in failed["rows"]], [("npm test", 1), ("npm run lint", 2)])
        self.assertEqual(failed["matched"], 3)
        self.assertEqual(self.local("a")["rows"], [])

    def test_it_agrees_with_the_server_on_a_whole_run(self):
        from orchestra import search
        from orchestra.build import RunBuilder
        from orchestra import demo
        import tempfile, time
        root = tempfile.mkdtemp()
        now = time.time()
        paths, _ = demo.build_demo(root, now=now)
        built = RunBuilder(paths, now_fn=lambda: now).refresh()
        details = {a.agent_id: a.to_detail_dict() for a in built.agents}
        light = {"agents": [{"agent_id": a.agent_id, "description": a.description} for a in built.agents]}
        for query, failed in (("npm", False), ("src", False), ("", True), ("test", True)):
            server = search.calls(built, query, failed)
            local = self.local(query, failed, details, light)
            key = lambda r: (r["agent_id"], r["tool"], r["target"], r["count"], r["failed"])
            self.assertEqual(sorted(map(key, local["rows"])), sorted(map(key, server["rows"])), (query, failed))
            self.assertEqual(local["matched"], server["matched"])
            self.assertGreater(server["matched"], 0, (query, failed))


@unittest.skipIf(ui.NODE is None, "node is not on PATH")
class TestResults(unittest.TestCase):
    def html(self, res, setup=""):
        return run_js(setup + "console.log(JSON.stringify(callsHtml(%s)));" % json.dumps(res))

    RES = {"query": "lint", "failed_only": False, "matched": 3, "truncated": False, "rows": [
        {"agent_id": "a1", "description": "Build the checkout UI", "tool": "Bash", "target": "npm run lint",
         "timestamp": 40, "ok": True, "count": 3, "failed": 2},
        {"agent_id": "a2", "description": "Write the unit tests", "tool": "Bash", "target": "eslint src/<b>",
         "timestamp": 30, "ok": False, "count": 1, "failed": 1}]}

    def test_rows_mark_the_match_and_say_how_often_and_how_many_failed(self):
        html = self.html(self.RES)
        self.assertEqual(len(re.findall(r'<div class="ticker-row call-row[^"]*" data-agent=', html)), 2)
        self.assertIn("npm run <mark>lint</mark>", html)
        self.assertIn('<span class="call-count">×3</span>', html)
        self.assertIn('<span class="call-failed">2 failed</span>', html)
        self.assertIn("es<mark>lint</mark> src/&lt;b&gt;", html)            # marked, and still escaped
        self.assertNotIn("<b>", html)

    def test_the_current_match(self):
        html = self.html(self.RES, "state.callAt = 1;\n")
        self.assertEqual(re.findall(r'class="ticker-row call-row( current)?"', html), ["", " current"])

    def test_the_count_line(self):
        count = lambda res, setup="": run_js(setup + "console.log(JSON.stringify(callsCount(%s)));" % json.dumps(res))
        self.assertEqual(count(self.RES), "3 calls match")
        self.assertEqual(count(self.RES, "state.callAt = 1;\n"), "3 calls match · row 2 of 2")
        failed = dict(self.RES, query="", failed_only=True, matched=1)
        self.assertEqual(count(failed), "1 failed call")
        both = dict(self.RES, failed_only=True, matched=2, truncated=True)
        self.assertEqual(count(both), "2 failed calls match · showing the newest 2 rows")
        self.assertEqual(count(dict(self.RES, rows=[], matched=0)), "No tool call matches")

    def test_mark_terms(self):
        mark = lambda text, q: run_js("console.log(JSON.stringify(markTerms(%s, %s)));" % (json.dumps(text), json.dumps(q)))
        self.assertEqual(mark("Run NPM test", "npm"), "Run <mark>NPM</mark> test")
        self.assertEqual(mark("a<b>npm", "npm b"), "a&lt;<mark>b</mark>&gt;<mark>npm</mark>")
        self.assertEqual(mark("npm", ""), "npm")

    def test_when_the_tab_is_searching(self):
        searching = lambda setup: run_js(setup + "console.log(JSON.stringify(activitySearching()));")
        self.assertFalse(searching("state.callQuery = '';"))
        self.assertFalse(searching("state.callQuery = ' a ';"))
        self.assertTrue(searching("state.callQuery = 'np';"))
        self.assertTrue(searching("state.callQuery = ''; state.callFailed = true;"))


class TestWiring(unittest.TestCase):
    def test_the_bar_sits_above_the_live_tail(self):
        html = ui.read("index.html")
        section = html[html.index('id="view-activity"'):html.index('id="ticker"')]
        for needle in ('id="calls-q"', 'type="search"', 'id="calls-failed"', 'aria-pressed="false"', 'id="calls-count"'):
            self.assertIn(needle, section)

    def test_the_tail_gives_way_while_searching_and_boot_sets_it_up(self):
        js = ui.read("app.js")
        tail = js[js.index("function renderTicker()"):]
        self.assertIn("activitySearching()", tail[:400])
        self.assertIn("setupActivitySearch();", js)


if __name__ == "__main__":
    unittest.main()
