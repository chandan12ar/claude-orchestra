"""Time agents spent waiting on the user: prompt intervals, their summary, and the wiring."""

import unittest

from orchestra import events as E
from orchestra import insights
from orchestra import livestate as L
from orchestra.agentlog import ActivityTimes, AgentDigest
from orchestra.model import Agent, Run
from tests.fixtures import ts
from tests.test_livestate import IntegrationCase, at, ev, perm
from tests import fake_secrets as fake


def activity(table):
    """first_after from {agent_id: [offsets]}; "" answers from anyone's activity."""
    times = {k: ActivityTimes() for k in table}
    for k, offsets in table.items():
        for o in offsets:
            times[k].add(at(o))

    def first_after(agent_id, t):
        pool = [times[agent_id]] if agent_id else list(times.values())
        found = [x for x in (p.first_after(t) for p in pool if p) if x is not None]
        return min(found) if found else None
    return first_after


def ask(offset, agent_id="", kind="permission_prompt", message="needs permission"):
    return ev(E.NOTIFICATION, offset, agent_id=agent_id,
              detail={"notification_type": kind, "message": message})


class TestActivityTimes(unittest.TestCase):
    def test_first_after_is_strict_and_handles_out_of_order_adds(self):
        a = ActivityTimes()
        for t in (10.0, 30.0, 20.0):
            a.add(t)
        self.assertEqual(a.times, [10.0, 20.0, 30.0])
        self.assertEqual(a.first_after(10.0), 20.0)
        self.assertEqual(a.first_after(5.0), 10.0)
        self.assertIsNone(a.first_after(30.0))

    def test_capped(self):
        from orchestra import agentlog
        a = ActivityTimes()
        for i in range(agentlog.MAX_ACTIVITY_TIMES + 10):
            a.add(float(i))
        self.assertEqual(len(a.times), agentlog.MAX_ACTIVITY_TIMES)
        self.assertEqual(a.times[0], 10.0)

    def test_the_digest_records_every_timestamped_entry(self):
        d = AgentDigest()
        d.ingest([{"timestamp": ts(5), "type": "user"}, {"type": "bookkeeping"},
                  {"timestamp": ts(9), "type": "assistant"}])
        self.assertEqual(d.activity.times, [at(5), at(9)])


class TestWaitIntervals(unittest.TestCase):
    def test_a_prompt_ends_at_that_agents_next_activity(self):
        [w] = L.waits([ask(100, "a")], activity({"a": [90, 130, 140], "b": [105]}), True)
        self.assertEqual((w.state, w.end, w.seconds(0)), (L.ANSWERED, at(130), 30.0))

    def test_activity_inside_the_grace_window_does_not_answer_it(self):
        [w] = L.waits([ask(100, "a")], activity({"a": [100.5, 120]}), True)
        self.assertEqual(w.end, at(120))

    def test_no_activity_while_live_is_open_and_counts_up_to_now(self):
        [w] = L.waits([ask(100, "a")], activity({"a": [90]}), True)
        self.assertEqual(w.state, L.OPEN)
        self.assertEqual(w.seconds(at(160)), 60.0)

    def test_no_activity_in_an_ended_session_is_unanswered_and_has_no_length(self):
        [w] = L.waits([ask(100, "a")], activity({"a": [90]}), False)
        self.assertEqual(w.state, L.UNANSWERED)
        self.assertEqual(w.seconds(at(999)), 0.0)

    def test_the_main_session_is_answered_by_any_activity(self):
        [w] = L.waits([ask(100)], activity({"a": [150], "b": [120]}), True)
        self.assertEqual(w.end, at(120))

    def test_a_repeated_prompt_before_the_answer_is_the_same_wait(self):
        found = L.waits([ask(100, "a"), ask(110, "a"), ask(150, "a")],
                        activity({"a": [130, 170]}), True)
        self.assertEqual([(w.start, w.end, w.prompts) for w in found],
                         [(at(100), at(130), 2), (at(150), at(170), 1)])

    def test_repeats_of_an_open_prompt_extend_it(self):
        found = L.waits([ask(100, "a"), ask(140, "a")], activity({"a": [90]}), True)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].prompts, 2)

    def test_input_counts_idle_and_errors_do_not(self):
        found = L.waits([ask(10, "a", kind="agent_needs_input"),
                         ask(50, kind="idle_prompt"),
                         ev(E.ERROR, 60, detail={"error_type": "rate_limit"})],
                        activity({"a": [20, 70]}), True)
        self.assertEqual([w.kind for w in found], [L.INPUT])

    def test_messages_are_scrubbed(self):
        [w] = L.waits([ask(1, "a", message="key " + fake.ANTHROPIC_KEY)], activity({"a": [5]}), True)
        self.assertNotIn("AAAABBBB", w.message)


def run_with(waits, live=True, agents=()):
    r = Run(session_id="s", agents=list(agents), session_live=live)
    r.waits = waits
    return r


def wait(agent_id, start, end=None, state=L.ANSWERED, prompts=1):
    return L.Wait(agent_id=agent_id, kind=L.PERMISSION, start=start, end=end, state=state,
                  prompts=prompts)


class TestSummary(unittest.TestCase):
    def test_none_without_waits(self):
        self.assertIsNone(insights.compute(run_with([]), now=1)["waits"])

    def test_your_time_counts_overlaps_once_and_agent_time_adds_them(self):
        # a: 0-30, b: 10-40 (overlap 20 s), c: 100-110.
        r = run_with([wait("a", 0, 30), wait("b", 10, 40), wait("c", 100, 110)])
        w = insights.compute(r, now=200)["waits"]
        self.assertAlmostEqual(w["you_s"], 50.0)
        self.assertAlmostEqual(w["agent_s"], 70.0)
        self.assertEqual(w["count"], 3)

    def test_open_waits_run_to_now_and_unanswered_ones_have_no_length(self):
        r = run_with([wait("a", 100, state=L.OPEN), wait("b", 50, state=L.UNANSWERED, prompts=3)])
        w = insights.compute(r, now=160)["waits"]
        self.assertEqual((w["you_s"], w["open"], w["unanswered"], w["count"], w["prompts"]),
                         (60.0, 1, 1, 1, 4))
        self.assertEqual(w["longest"]["agent_id"], "a")

    def test_per_agent_rows_are_sorted_by_time_and_labelled(self):
        named = Agent(agent_id="a", description="Build the UI")
        r = run_with([wait("a", 0, 10), wait("", 0, 50), wait("a", 20, 25)], agents=[named])
        rows = insights.compute(r, now=99)["waits"]["by_agent"]
        self.assertEqual([(x["label"], x["seconds"], x["count"]) for x in rows],
                         [("Main session", 50.0, 1), ("Build the UI", 15.0, 2)])

    def test_recent_is_newest_first_and_bounded(self):
        r = run_with([wait("a", i * 10, i * 10 + 5) for i in range(30)])
        recent = insights.compute(r, now=999)["waits"]["recent"]
        self.assertEqual(len(recent), insights.WAIT_RECENT)
        self.assertEqual(recent[0]["start"], 290)

    def test_labels_are_scrubbed(self):
        named = Agent(agent_id="a", description="deploy " + fake.ANTHROPIC_KEY)
        w = insights.compute(run_with([wait("a", 0, 5)], agents=[named]), now=9)["waits"]
        self.assertNotIn("AAAABBBB", repr(w))


class TestBuilt(IntegrationCase):
    """Fixture: a2 is active at +80 and +139, a3 at +80 and +81, the main session up to +140."""

    def test_an_answered_prompt_lands_on_its_agent(self):
        self.put(perm(100, agent_id="a2"))
        a2 = self.run_at(150).agent("a2")
        self.assertAlmostEqual(a2.waited_s, 39.0, places=2)
        self.assertEqual((a2.wait_count, a2.wait_open_since), (1, None))

    def test_an_open_prompt_marks_the_agent_and_reaches_the_summary(self):
        self.put(perm(100, agent_id="a2"), perm(90, agent_id="a3"))
        run = self.run_at(150)
        a3 = run.agent("a3")
        self.assertEqual((a3.status, a3.wait_open_since), ("waiting", at(90)))
        light = a3.to_light_dict()
        self.assertEqual((light["wait_count"], light["wait_open_since"]), (1, at(90)))
        w = run.to_summary_dict()["insights"]["waits"]
        self.assertAlmostEqual(w["you_s"], 60.0, places=2)      # 90..150 covers 100..139
        self.assertAlmostEqual(w["agent_s"], 99.0, places=2)    # 39 + 60

    def test_a_prompt_left_up_when_the_session_ended_is_unanswered(self):
        self.put(perm(145), ev(E.SESSION_END, 146))
        w = self.run_at(150, mtime_offset=140).insights["waits"]
        self.assertEqual((w["unanswered"], w["you_s"]), (1, 0.0))

    def test_no_prompts_means_no_block(self):
        self.assertIsNone(self.run_at(150).insights["waits"])


if __name__ == "__main__":
    unittest.main()
