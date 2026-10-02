"""deriveRunAt: rebuild what a run looked like at time t (executed under node)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

NODE = shutil.which("node")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "orchestra", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def fn(js, name):
    start = js.index("function {}(".format(name))
    return js[start:js.index("\n}\n", start) + 3]


def run_js(expression):
    js = read("app.js")
    program = "\n".join(fn(js, n) for n in
                        ("replayBounds", "agentAt", "deriveRunAt")) + \
        "\nconsole.log(JSON.stringify(" + expression + "));"
    path = os.path.join(tempfile.mkdtemp(), "r.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


def agent(aid, rounds, status="completed", **extra):
    a = {"agent_id": aid, "status": status, "rounds": [
        {"started_at": s, "ended_at": e, "status": st} for s, e, st in rounds],
        "tokens": {"output": 99}, "cost": 1.5, "loop": {"kind": "repeat"},
        "tool_call_count": 7, "files_written_count": 2, "last_activity_at": None}
    a.update(extra)
    return a


RUN = {
    "session_id": "s", "started_at": 100, "ended_at": 400, "session_live": False,
    "agents": [
        agent("a1", [(100, 160, "completed")]),
        agent("a2", [(170, 240, "failed")], status="failed"),
        agent("a3", [(170, None, "running")], status="stalled"),
    ],
    "edges": [
        {"src": "main", "dst": "a1", "kind": "spawn"},
        {"src": "main", "dst": "a2", "kind": "spawn"},
        {"src": "a1", "dst": "a2", "kind": "handoff"},
        {"src": "a2", "dst": "a3", "kind": "artifact"},
    ],
    "batches": [{"turn_uuid": "t1", "agent_ids": ["a1"]},
                {"turn_uuid": "t2", "agent_ids": ["a2", "a3"]}],
    "hub_files": [{"path": "x"}], "write_conflicts": [{"path": "y"}],
    "live": {"attention": {"kind": "permission"}}, "cost": {"enabled": True},
    "orchestrator": {"tokens": {}},
}


def at(t, run=RUN):
    return run_js("deriveRunAt(%s, %s)" % (json.dumps(run), t))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestStatusAtATime(unittest.TestCase):
    def statuses(self, t):
        return {a["agent_id"]: a["status"] for a in at(t)["agents"]}

    def test_before_anything_launched_there_are_no_agents(self):
        self.assertEqual(at(99)["agents"], [])

    def test_an_agent_is_running_from_its_launch(self):
        self.assertEqual(self.statuses(100), {"a1": "running"})
        self.assertEqual(self.statuses(130), {"a1": "running"})

    def test_it_keeps_its_final_outcome_once_ended(self):
        self.assertEqual(self.statuses(165), {"a1": "completed"})

    def test_failure_appears_only_when_it_happened(self):
        self.assertEqual(self.statuses(200)["a2"], "running")
        self.assertEqual(self.statuses(240)["a2"], "failed")

    def test_the_exact_end_instant_counts_as_ended(self):
        self.assertEqual(self.statuses(160)["a1"], "completed")

    def test_an_agent_that_never_ended_is_running_not_its_live_status(self):
        # Its live status is "stalled" (clock-based); history cannot know that.
        self.assertEqual(self.statuses(399)["a3"], "running")

    def test_late_agents_appear_at_their_launch_time(self):
        self.assertNotIn("a3", self.statuses(169))
        self.assertIn("a3", self.statuses(170))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestResumedAgents(unittest.TestCase):
    RUN2 = dict(RUN, agents=[agent("a1", [(100, 160, "completed"), (200, 260, "failed")])])

    def status(self, t):
        return at(t, self.RUN2)["agents"][0]["status"]

    def test_finished_then_resumed_then_finished_again(self):
        self.assertEqual(self.status(180), "completed")     # between rounds
        self.assertEqual(self.status(220), "running")       # resumed
        self.assertEqual(self.status(260), "failed")        # second outcome

    def test_a_future_round_is_not_shown_yet(self):
        rounds = at(180, self.RUN2)["agents"][0]["rounds"]
        self.assertEqual(len(rounds), 1)

    def test_ended_at_is_none_while_a_round_is_open(self):
        self.assertIsNone(at(220, self.RUN2)["agents"][0]["ended_at"])


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestDerivedRun(unittest.TestCase):
    def test_totals_are_recomputed_for_that_moment(self):
        t = at(200)["totals"]
        self.assertEqual((t["agents"], t["running"], t["completed"]), (3, 2, 1))
        t = at(250)["totals"]
        self.assertEqual((t["running"], t["completed"], t["failed"]), (1, 1, 1))

    def test_wall_time_runs_from_first_launch(self):
        self.assertEqual(at(130)["totals"]["wall_time_s"], 30)
        # Same rule as the live totals: the span of the agents that have ENDED
        # (a2 ended at 240); a still-open agent does not stretch it.
        self.assertEqual(at(250)["totals"]["wall_time_s"], 140)

    def test_edges_to_agents_that_do_not_exist_yet_are_hidden(self):
        kinds = {(e["src"], e["dst"]) for e in at(130)["edges"]}
        self.assertEqual(kinds, {("main", "a1")})
        self.assertIn(("a1", "a2"), {(e["src"], e["dst"]) for e in at(180)["edges"]})

    def test_the_orchestrator_root_stays_a_valid_edge_end(self):
        self.assertTrue(any(e["src"] == "main" for e in at(100)["edges"]))

    def test_batches_shrink_to_the_launched_agents_and_vanish_when_empty(self):
        self.assertEqual([b["agent_ids"] for b in at(130)["batches"]], [["a1"]])
        self.assertEqual([b["agent_ids"] for b in at(180)["batches"]], [["a1"], ["a2", "a3"]])
        self.assertEqual(at(99)["batches"], [])

    def test_numbers_that_cannot_be_known_at_t_are_not_shown_as_final(self):
        a = at(200)["agents"][0]
        self.assertEqual((a["tokens"], a["cost"], a["loop"], a["tool_call_count"]),
                         ({}, None, None, 0))
        d = at(200)
        self.assertEqual((d["cost"], d["orchestrator"], d["live"]), (None, None, None))
        self.assertEqual((d["hub_files"], d["write_conflicts"]), ([], []))

    def test_session_is_live_until_the_end_of_the_run(self):
        self.assertTrue(at(200)["session_live"])
        self.assertFalse(at(400)["session_live"])

    def test_an_open_agent_is_drawn_up_to_the_replay_time(self):
        a3 = [a for a in at(250)["agents"] if a["agent_id"] == "a3"][0]
        self.assertEqual(a3["last_activity_at"], 250)

    def test_a_finished_agent_keeps_its_own_last_activity(self):
        run = dict(RUN, agents=[agent("a1", [(100, 160, "completed")],
                                      last_activity_at=159)])
        self.assertEqual(at(300, run)["agents"][0]["last_activity_at"], 159)

    def test_the_axis_is_fixed_to_the_whole_run_not_to_t(self):
        self.assertEqual(at(130)["replay_window"], [100, 400])
        self.assertEqual(at(300)["replay_window"], [100, 400])

    def test_the_replay_time_is_recorded(self):
        self.assertEqual(at(222)["replay_at"], 222)

    def test_the_original_run_is_not_mutated(self):
        out = run_js("(() => { const r = %s; const before = JSON.stringify(r); "
                     "deriveRunAt(r, 200); return JSON.stringify(r) === before; })()"
                     % json.dumps(RUN))
        self.assertTrue(out)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestBoundsAndRobustness(unittest.TestCase):
    def test_bounds_span_launch_to_last_known_activity(self):
        self.assertEqual(run_js("replayBounds(%s)" % json.dumps(RUN)),
                         {"start": 100, "end": 400})

    def test_bounds_without_a_start_are_none(self):
        self.assertIsNone(run_js("replayBounds({agents: [], started_at: null})"))

    def test_bounds_use_agent_activity_when_the_run_has_no_end(self):
        run = dict(RUN, ended_at=None)
        run["agents"] = [agent("a", [(100, None, "running")], last_activity_at=333)]
        self.assertEqual(run_js("replayBounds(%s)" % json.dumps(run))["end"], 333)

    def test_agents_with_no_rounds_never_appear(self):
        run = dict(RUN, agents=[agent("ghost", [])])
        self.assertEqual(at(300, run)["agents"], [])

    def test_agents_with_null_start_never_appear(self):
        run = dict(RUN, agents=[agent("x", [(None, None, "running")])])
        self.assertEqual(at(300, run)["agents"], [])


class TestWiring(unittest.TestCase):
    def test_functions_are_defined(self):
        js = read("app.js")
        for name in ("replayBounds", "agentAt", "deriveRunAt"):
            self.assertIn("function {}(".format(name), js)


if __name__ == "__main__":
    unittest.main()


class TestTimelineHonoursTheReplayWindow(unittest.TestCase):
    def test_time_window_prefers_the_replay_window(self):
        js = read("app.js")
        body = js[js.index("function timeWindow("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("if (run.replay_window) return run.replay_window;", body)
        # ...and it must come BEFORE the scan of agents, or it never applies.
        self.assertLess(body.index("replay_window"), body.index("for (const agent"))

    def test_header_does_not_claim_a_token_count_during_a_replay(self):
        js = read("app.js")
        body = js[js.index("function renderHeader("):]
        body = body[:body.index("\n}\n")]
        self.assertIn("run.replay_at !== undefined", body)


class TestReplayWiring(unittest.TestCase):
    def test_button_and_bar_exist_in_the_page_and_in_the_report_shell(self):
        from orchestra import report
        for html in (read("index.html"), report._SHELL):
            self.assertIn('id="replay-toggle"', html)
            self.assertRegex(html, r'<div id="replay-bar"[^>]*hidden')

    def test_replay_works_offline_so_it_is_not_gated_on_the_live_server(self):
        js = read("app.js")
        body = js[js.index("function toggleReplay("):]
        self.assertNotIn("state.offline", body[:body.index("\n}\n")])

    def test_the_poll_keeps_the_true_run_and_derives_the_displayed_one(self):
        js = read("app.js")
        self.assertIn("state.liveRun = run;", js)
        self.assertIn("deriveRunAt(run, replayClamp(state.replay.t, run))", js)

    def test_notifications_pill_and_summary_keep_reading_the_true_run(self):
        js = read("app.js")
        self.assertIn("buildSummaryMarkdown(state.liveRun || state.run)", js)
        self.assertIn("pillModel(state.liveRun || state.run, state.fleet)", js)

    def test_switching_session_leaves_replay_mode(self):
        js = read("app.js")
        body = js[js.index("function switchSession("):]
        self.assertIn("if (state.replay.on) toggleReplay();", body[:body.index("\n}\n")])

    def test_controls_are_built_with_textcontent_not_inner_html(self):
        js = read("app.js")
        body = js[js.index("function buildReplayBar("):]
        self.assertNotIn("innerHTML", body[:body.index("\n}\n")])
