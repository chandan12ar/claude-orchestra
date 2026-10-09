"""What went wrong: API errors, failed calls, retries, timeouts, and how they are shown."""

import json
import tempfile
import time
import unittest

from orchestra import errors as E
from orchestra.parent import parse_timestamp
from tests import fake_secrets as fake
from tests.fixtures import ts


def use(uid, at, name="Bash", **params):
    return {"type": "assistant", "timestamp": ts(at), "message": {
        "id": "m-" + uid, "role": "assistant", "model": "claude-sonnet-5-5",
        "content": [{"type": "tool_use", "id": uid, "name": name, "input": params}]}}


def result(uid, at, content="ok", is_error=False, extra=None):
    entry = {"type": "user", "timestamp": ts(at), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": content, "is_error": is_error}]}}
    if extra:
        entry["toolUseResult"] = extra
    return entry


def call(uid, at, ok=True, name="Bash", content=None, **params):
    params = params or {"command": "npm test"}
    return [use(uid, at, name, **params),
            result(uid, at + 1, content if content is not None else ("ok" if ok else "Exit code 1\n3 failing"),
                   is_error=not ok)]


def api_error(at, kind="rate_limit", status=429, text="You've hit your session limit · resets 4:50pm", uuid=None):
    entry = {"type": "assistant", "timestamp": ts(at), "isApiErrorMessage": True, "error": kind,
             "message": {"id": "e-%s" % at, "role": "assistant", "model": "<synthetic>",
                         "content": [{"type": "text", "text": text}]}}
    if status is not None:
        entry["apiErrorStatus"] = status
    if uuid:
        entry["uuid"] = uuid
    return entry


def reply(at, model="claude-sonnet-5-5"):
    return {"type": "assistant", "timestamp": ts(at), "message": {
        "id": "r-%s" % at, "role": "assistant", "model": model, "content": [{"type": "text", "text": "back"}]}}


def log(*groups):
    out = E.ErrorLog()
    entries = []
    for g in groups:
        entries += g if isinstance(g, list) else [g]
    out.ingest(entries)
    return out


class TestApiErrors(unittest.TestCase):
    def test_kind_status_text_and_the_next_real_reply(self):
        l = log(api_error(100, uuid="x1"), api_error(160, uuid="x2"),     # the same stall, said twice
                reply(170, model="<synthetic>"),          # Claude Code's own wrap-up is not an answer
                reply(5000), api_error(6000, uuid="x3"))
        self.assertEqual([(e["kind"], e["status"], e["repeats"]) for e in l.api],
                         [("rate_limit", 429, 2), ("rate_limit", 429, 1)])
        self.assertEqual(l.api[0]["text"], "You've hit your session limit · resets 4:50pm")
        self.assertEqual([e["resumed_at"] for e in l.api], [parse_timestamp(ts(5000)), None])

    def test_overlapping_stalls_count_their_time_once(self):
        l = log(api_error(100, kind="authentication_failed", status=None, uuid="a"),
                api_error(200, uuid="b"), reply(1100))
        s = E.summary([("", "Main session", "completed", l)], False, parse_timestamp(ts(9000)))
        self.assertEqual([e["lost_s"] for e in s["api"]], [1000.0, 900.0])
        self.assertEqual(s["api_lost_s"], 1000.0)
        self.assertEqual({k: v["lost_s"] for k, v in s["api_kinds"].items()},
                         {"authentication_failed": 1000.0, "rate_limit": 900.0})

    def test_the_same_entry_read_twice_counts_once_and_odd_values_are_dropped(self):
        odd = api_error(100, kind=7, status=True, text="<tool_use_error>boom</tool_use_error>", uuid="x1")
        l = log(odd, api_error(100, uuid="x1"))
        self.assertEqual(len(l.api), 1)
        self.assertEqual((l.api[0]["kind"], l.api[0]["status"], l.api[0]["text"]), ("", None, "boom"))

    def test_the_api_json_reads_as_words(self):
        raw = 'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
        broken = 'API Error: 500 {"type":"error",'
        l = log(api_error(100, kind="server_error", status=529, text=raw, uuid="a"), reply(110),
                api_error(200, kind="server_error", status=500, text=broken + "}", uuid="b"))
        self.assertEqual([e["text"] for e in l.api], ["API Error: 529 Overloaded", broken + "}"])

    def test_secrets_in_the_message_are_scrubbed(self):
        l = log(api_error(100, kind="authentication_failed", status=None, text="token " + fake.ANTHROPIC_KEY + " expired"))
        self.assertNotIn(fake.ANTHROPIC_KEY, json.dumps(l.api))


class TestCalls(unittest.TestCase):
    def test_failures_keep_a_useful_first_line(self):
        l = log(call("a", 0, ok=False), call("b", 10, ok=False, content="<tool_use_error>File has not been read yet</tool_use_error>",
                                          name="Edit", file_path="/x/a.py"),
                call("c", 20, ok=False, content=[{"type": "text", "text": "\n\nExit code 2\n\n\x1b[33mnpm ERR!\x1b[0m missing script"}]),
                call("d", 30))
        self.assertEqual([c[4] for c in l.failed()],
                         ["Exit code 1: 3 failing", "File has not been read yet", "Exit code 2: npm ERR! missing script"])
        self.assertEqual(len(l.done_calls()), 4)

    def test_a_call_without_its_result_yet_is_neither(self):
        l = log(use("a", 0))
        self.assertEqual((l.done_calls(), l.failed()), ([], []))
        self.assertTrue(l.empty())

    def test_timeouts(self):
        bg = {"timedOutAfterMs": 120000, "backgroundTaskId": "b1", "interrupted": False}
        l = log([use("a", 0, command="npm run e2e"), result("a", 120, "moved to the background", extra=bg)],
                [use("b", 200, command="sleep 900"), result("b", 800, "killed", is_error=True,
                                                            extra={"timedOutAfterMs": 600000})],
                [use("c", 900), result("c", 901, extra={"timedOutAfterMs": True})])
        self.assertEqual([(t["target"], t["after_s"], t["background"]) for t in l.timeouts],
                         [("npm run e2e", 120.0, True), ("sleep 900", 600.0, False)])


class TestRetries(unittest.TestCase):
    def test_fixed_then_rerun_is_a_retry_that_worked(self):
        l = log(call("a", 0, ok=False), call("e", 10, name="Edit", file_path="/x/a.py"), call("b", 20))
        r = l.retries()
        self.assertEqual([(x["target"], x["attempts"], x["ok"], x["failed_in_a_row"]) for x in r], [("npm test", 2, True, 1)])
        self.assertEqual(r[0]["error"], "Exit code 1: 3 failing")
        self.assertIsNone(l.stuck())

    def test_too_far_apart_or_a_different_target_is_not_a_retry(self):
        between = [call("r%d" % i, 10 + i, name="Read", file_path="/x/%d" % i) for i in range(E.RETRY_GAP + 1)]
        self.assertEqual(log(call("a", 0, ok=False), *between, call("b", 50)).retries(), [])
        self.assertEqual(log(call("a", 0, ok=False), call("b", 5, command="npm test -- --verbose")).retries(), [])
        two_scripts = log(call("a", 0, ok=False, command="python - <<'EOF'\nprint(1)\nEOF"),
                          call("b", 5, command="python - <<'EOF'\nprint(2)\nEOF"))
        self.assertEqual(two_scripts.retries(), [])                      # same first line, different script
        self.assertEqual(two_scripts.failed()[0][2], "python - <<'EOF'")   # shown by its first line
        same_file = log(call("a", 0, ok=False, name="Edit", file_path="/x/a.py", old_string="x"),
                        call("b", 5, name="Edit", file_path="/x/a.py", old_string="y"))
        self.assertEqual([r["attempts"] for r in same_file.retries()], [2])
        self.assertEqual(log(call("a", 0, ok=False, name="Task", **{"x": 1}), call("b", 5, name="Task", **{"x": 1})).retries(), [])

    def test_failing_again_and_again_while_recent_is_stuck(self):
        l = log(call("a", 0, ok=False), call("b", 5, ok=False), call("c", 10, ok=False))
        s = l.stuck()
        self.assertEqual((s["attempts"], s["failed_in_a_row"], s["ok"]), (3, 3, False))
        self.assertEqual(l.totals()["stuck"], {"tool": "Bash", "target": "npm test", "failed_in_a_row": 3})

    def test_not_stuck_when_it_later_worked_moved_on_or_failed_only_twice(self):
        self.assertIsNone(log(call("a", 0, ok=False), call("b", 5, ok=False), call("c", 10, ok=False),
                              call("d", 15)).stuck())
        moved_on = [call("r%d" % i, 20 + i, name="Read", file_path="/x/%d" % i) for i in range(E.STUCK_RECENT)]
        self.assertIsNone(log(call("a", 0, ok=False), call("b", 5, ok=False), call("c", 10, ok=False), *moved_on).stuck())
        self.assertIsNone(log(call("a", 0, ok=False), call("b", 5, ok=False)).stuck())

    def test_totals(self):
        self.assertIsNone(log(call("a", 0)).totals())
        t = log(api_error(0), reply(50), call("a", 60, ok=False), call("b", 70)).totals()
        self.assertEqual({k: t[k] for k in ("api", "api_kinds", "failed", "calls", "retries", "retries_ok", "timeouts")},
                         {"api": 1, "api_kinds": ["rate_limit"], "failed": 1, "calls": 2, "retries": 1,
                          "retries_ok": 1, "timeouts": 0})

    def test_a_snapshot_does_not_move_with_the_log(self):
        l = log(call("a", 0, ok=False))
        copy = l.snapshot()
        l.ingest(call("b", 5, ok=False) + call("c", 10, ok=False))
        self.assertIsNone(copy.stuck())
        self.assertIsNotNone(l.stuck())


class TestSummary(unittest.TestCase):
    NOW = parse_timestamp(ts(9000))

    def test_lost_time_counts_up_while_live_and_stops_where_the_session_did(self):
        main = log(api_error(100), reply(400), api_error(8000))
        live = E.summary([("", "Main session", "running", main)], True, self.NOW)
        self.assertEqual([(e["lost_s"], e["ongoing"]) for e in live["api"]], [(300.0, False), (1000.0, True)])
        self.assertEqual((live["api_lost_s"], live["api_kinds"]["rate_limit"]["count"]), (1300.0, 2))
        ended = E.summary([("", "Main session", "completed", main)], False, self.NOW)
        self.assertEqual([(e["lost_s"], e["ongoing"]) for e in ended["api"]], [(300.0, False), (None, False)])

    def test_by_tool_by_agent_and_who_is_stuck(self):
        stuck = log(call("a", 0, ok=False), call("b", 5, ok=False), call("c", 10, ok=False))
        fine = log(call("x", 0, ok=False), call("y", 5), call("z", 6, name="Read", ok=False, content="File does not exist.",
                                                             file_path="/x/a.py"))
        s = E.summary([("", "Main session", "running", None), ("a1", "Stuck one", "running", stuck),
                       ("a2", "Fine one", "completed", fine)], True, self.NOW)
        self.assertEqual((s["failed"], s["calls"]), (5, 6))
        self.assertEqual([(t["tool"], t["failed"], t["calls"], t["example"]) for t in s["tools"]],
                         [("Bash", 4, 5, "Exit code 1: 3 failing"), ("Read", 1, 1, "File does not exist.")])
        self.assertEqual([a["agent_id"] for a in s["agents"]], ["a1", "a2"])
        self.assertEqual([x["agent_id"] for x in s["stuck"]], ["a1"])
        self.assertEqual((s["retry_count"], s["retries_ok"]), (2, 1))
        self.assertEqual(s["retries"][0]["attempts"], 3)                      # the longest first
        finished = E.summary([("a1", "Stuck one", "failed", stuck)], True, self.NOW)
        self.assertEqual(finished["stuck"], [])
        self.assertEqual(E.summary([("a1", "Stuck one", "running", stuck)], False, self.NOW)["stuck"], [])

    def test_nothing_wrong_is_none(self):
        self.assertIsNone(E.summary([("", "Main session", "running", log(call("a", 0))), ("b", "B", "running", None)],
                                    True, self.NOW))


class TestBuiltFromTheDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        cls.now = time.time()
        cls.paths, cls.agents = demo.build_demo(tempfile.mkdtemp(), now=cls.now)
        cls.builder = RunBuilder(cls.paths, now_fn=lambda: cls.now + 10)
        cls.built = cls.builder.refresh()
        cls.e = cls.built.insights["errors"]

    def test_the_overloaded_api(self):
        self.assertEqual([(a["agent_id"], a["kind"], a["status"], a["lost_s"]) for a in self.e["api"]],
                         [("", "server_error", 529, 140.0)])

    def test_the_security_review_is_stuck_on_npm_audit(self):
        self.assertEqual([(s["label"], s["target"], s["failed_in_a_row"]) for s in self.e["stuck"]],
                         [("Review the checkout for security issues", "npm audit --omit=dev", 3)])
        security = [a for a in self.built.agents if a.description.startswith("Review the checkout")][0]
        self.assertEqual(security.to_light_dict()["errors"]["stuck"]["failed_in_a_row"], 3)

    def test_the_unit_tests_retried_and_the_e2e_run_timed_out(self):
        unit = [r for r in self.e["retries"] if r["label"] == "Write the unit tests"][0]
        self.assertEqual((unit["target"], unit["attempts"], unit["ok"]), ("npm test", 2, False))
        self.assertEqual([(t["label"], t["after_s"], t["background"]) for t in self.e["timeouts"]],
                         [("Run the end-to-end suite", 120.0, True)])

    def test_it_survives_later_reads(self):
        from orchestra import demo
        demo.Simulator(self.paths, self.agents).tick(now=self.now + 5)
        again = self.builder.refresh().insights["errors"]
        self.assertEqual((again["api_count"], again["timeout_count"], len(again["stuck"])), (1, 1, 1))
        self.assertEqual(again["failed"], self.e["failed"])


class TestShown(unittest.TestCase):
    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    FNS = ("esc", "fmtDuration", "fmtClock", "insMetric", "insCard", "cardKey", "insRank", "apiKind", "callText", "apiLost", "insErrors")

    def card(self, e):
        ui = self.ui()
        return ui.run_js(self.FNS, [], "console.log(JSON.stringify(insErrors({errors: %s})));" % json.dumps(e))

    def test_the_demo_card(self):
        ui = self.ui()
        html = ui.card(ui.render_insights(ui.demo_run()), "What went wrong")
        for text in ("API server error 529", "answered again after 2m 20s", "5 of 92", "tool calls failed",
                     "3 tries, still failing", "Bash npm audit --omit=dev",
                     "Exit code 1: npm ERR! audit endpoint returned an error (503)", "after 2m 00s",
                     "moved to the background", "Failed calls by agent"):
            self.assertIn(text, html)
        for bad in ("NaN", "undefined", "null", "Infinity"):
            self.assertNotIn(bad, html)
        self.assertIn("insChecks(ins) + insErrors(ins)", ui.fn(ui.read("app.js"), "renderInsights"))

    def test_kinds_ongoing_and_worked(self):
        e = {"api": [{"agent_id": "", "label": "Main session", "kind": "rate_limit", "status": 429,
                      "text": "You've hit your session limit · resets 4:50pm", "at": 1, "resumed_at": None,
                      "lost_s": 600, "ongoing": True, "repeats": 3},
                     {"agent_id": "a1", "label": "Helper", "kind": "authentication_failed", "status": None,
                      "text": "", "at": 2, "resumed_at": None, "lost_s": None, "ongoing": False}],
             "api_count": 2, "api_kinds": {}, "api_lost_s": 600, "failed": 1, "calls": 10,
             "tools": [{"tool": "Edit", "failed": 1, "calls": 4, "example": "String to replace not found"}],
             "agents": [{"agent_id": "a1", "label": "Helper", "failed": 1, "calls": 10}],
             "retries": [{"agent_id": "a1", "label": "Helper", "tool": "Edit", "target": "/x/a.py", "attempts": 2,
                          "ok": True, "error": "String to replace not found", "at": 3}],
             "retry_count": 4, "retries_ok": 3, "timeouts": [], "timeout_count": 0, "stuck": [], "stuck_after": 3}
        html = self.card(e)
        self.assertIn("usage or rate limit 429", html)
        self.assertIn("no answer yet, 10m 00s", html)
        self.assertIn("resets 4:50pm (3 times)", html)
        self.assertIn("login failed", html)
        self.assertIn("the session stopped there", html)
        self.assertIn('class="check-ok"', html)
        self.assertIn("worked on try 2", html)
        self.assertIn("3 of 4", html)
        self.assertIn("And 3 more.", html)
        self.assertNotIn("Failed calls by agent", html)          # one agent: the by-tool list says it
        self.assertNotIn("Stuck now", html)
        self.assertEqual(self.card(None), "")

    def test_hostile_values_are_text(self):
        ui = self.ui()
        evil = '"><img src=x onerror=alert(1)>'

        def hit(summary):
            e = summary["insights"]["errors"]
            for row in e["api"] + e["retries"] + e["timeouts"] + e["stuck"]:
                for k in ("label", "text", "kind", "tool", "target", "error", "agent_id"):
                    if k in row:
                        row[k] = evil
            for t in e["tools"]:
                t["tool"], t["example"] = evil, evil
        html = ui.card(ui.render_insights(ui.demo_run(hit)), "What went wrong")
        self.assertNotIn("<img", html)

    def test_the_panel_line(self):
        ui = self.ui()
        d = {"api": 1, "api_kinds": ["rate_limit"], "failed": 3, "calls": 6, "retries": 1, "retries_ok": 0,
             "timeouts": 1, "stuck": {"tool": "Bash", "target": "npm audit", "failed_in_a_row": 3}}
        out = ui.run_js(("esc", "apiKind", "callText", "errorsRow"), [],
                        "console.log(JSON.stringify([errorsRow(%s), errorsRow(null)]));" % json.dumps(d))
        self.assertIn("<dt>errors</dt><dd>3 of 6 calls failed · 0 of 1 retry worked · 1 API error (usage or rate limit)"
                      " · 1 past the timeout", out[0])
        self.assertIn("Keeps failing Bash npm audit (3 times in a row)", out[0])
        self.assertEqual(out[1], "")
        self.assertIn("errorsRow(agent.errors)", ui.fn(ui.read("app.js"), "openDrawer"))

    def test_the_health_box(self):
        from tests.test_pressure import health
        ui = self.ui()
        stuck = lambda agent_id: {"agent_id": agent_id, "tool": "Bash", "target": "npm audit", "failed_in_a_row": 4}
        run_ = {"agents": [{"agent_id": "a1", "description": "Review it", "status": "running", "loop": None},
                           {"agent_id": "a2", "description": "Loop it", "status": "running", "loop": {"count": 9}}],
                "insights": {"errors": {"stuck": [stuck("a1"), stuck("a2"), stuck("")]}}}
        out = health(ui, run_)
        self.assertEqual(out["items"], [
            ["RETRYING — Review it: Bash npm audit failed 4 times in a row", "retrying"],
            ["POSSIBLE LOOP — Loop it: same call x9, failing every time", "loop"],
            ["RETRYING — Main session: Bash npm audit failed 4 times in a row", "retrying"]])
        self.assertEqual(out["opened"], ["drawer:a1", "drawer:a2", "card:what-went-wrong"])
        self.assertEqual(out["head"], "<strong>The main session and 2 agent(s) need attention</strong>")


if __name__ == "__main__":
    unittest.main()
