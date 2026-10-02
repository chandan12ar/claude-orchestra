import os
import tempfile
import unittest

from orchestra import constants as C
from orchestra import events as E
from orchestra import livestate as L
from orchestra.build import RunBuilder
from orchestra.events import Event, EventSpool
from orchestra.parent import parse_timestamp
from tests.fixtures import build_session, ts

T = parse_timestamp(ts(0))


def at(offset):
    return T + offset


def ev(kind, offset, **kw):
    detail = kw.pop("detail", {})
    return Event(kind=kind, session_id="s1", ts=at(offset), detail=detail, **kw)


def perm(offset, **kw):
    return ev(E.NOTIFICATION, offset, detail={"notification_type": "permission_prompt",
                                              "message": "needs permission"}, **kw)


class TestDerive(unittest.TestCase):
    def test_no_events_means_no_ground_truth(self):
        state = L.derive([], at(5))
        self.assertFalse(state.has_events)
        self.assertIsNone(state.attention)

    def test_a_permission_prompt_is_pending_until_activity_follows(self):
        state = L.derive([perm(100)], activity_at=at(95))
        self.assertEqual(state.attention.kind, L.PERMISSION)
        self.assertEqual(state.attention.since, at(100))

    def test_activity_after_the_prompt_clears_it(self):
        state = L.derive([perm(100)], activity_at=at(130))
        self.assertIsNone(state.attention)

    def test_activity_within_the_grace_window_does_not_clear_it(self):
        state = L.derive([perm(100)], activity_at=at(100.5))
        self.assertIsNotNone(state.attention)

    def test_no_transcript_activity_at_all_leaves_it_pending(self):
        self.assertIsNotNone(L.derive([perm(100)], activity_at=None).attention)

    def test_error_carries_its_type(self):
        state = L.derive([ev(E.ERROR, 50, detail={"error_type": "rate_limit"})],
                         activity_at=at(40))
        self.assertEqual((state.attention.kind, state.attention.error_type),
                         (L.ERROR, "rate_limit"))

    def test_other_notification_types_are_not_attention(self):
        for kind in ("auth_success", "agent_completed", "quota_auto_resume_fired", ""):
            e = ev(E.NOTIFICATION, 10, detail={"notification_type": kind})
            self.assertIsNone(L.derive([e], at(0)).attention, kind)

    def test_permission_outranks_an_idle_prompt(self):
        idle = ev(E.NOTIFICATION, 120, detail={"notification_type": "idle_prompt"})
        state = L.derive([perm(100), idle], activity_at=at(90))
        self.assertEqual(state.attention.kind, L.PERMISSION)

    def test_idle_alone_is_reported(self):
        idle = ev(E.NOTIFICATION, 120, detail={"notification_type": "idle_prompt"})
        self.assertEqual(L.derive([idle], at(90)).attention.kind, L.IDLE)

    def test_session_end_is_recorded_and_a_later_start_undoes_it(self):
        end = ev(E.SESSION_END, 200, detail={"reason": "logout"})
        state = L.derive([end], at(190))
        self.assertEqual((state.ended_at, state.end_reason), (at(200), "logout"))
        resumed = L.derive([end, ev(E.SESSION_START, 300)], at(190))
        self.assertIsNone(resumed.ended_at)

    def test_events_are_ordered_by_time_not_arrival(self):
        a = L.derive([ev(E.SESSION_START, 300), ev(E.SESSION_END, 200)], at(0))
        self.assertIsNone(a.ended_at)

    def test_an_agent_stop_is_its_latest_word(self):
        state = L.derive([ev(E.AGENT_START, 10, agent_id="a1"),
                          ev(E.AGENT_STOP, 50, agent_id="a1")], at(40),
                         {"a1": at(49)})
        self.assertEqual(state.agent_stops, {"a1": at(50)})

    def test_a_restarted_agent_is_not_stopped(self):
        state = L.derive([ev(E.AGENT_STOP, 50, agent_id="a1"),
                          ev(E.AGENT_START, 60, agent_id="a1")], at(40))
        self.assertEqual(state.agent_stops, {})

    def test_activity_after_a_stop_means_the_agent_was_resumed(self):
        state = L.derive([ev(E.AGENT_STOP, 50, agent_id="a1")], at(40),
                         {"a1": at(80)})
        self.assertEqual(state.agent_stops, {})

    def test_agent_specific_attention_is_tracked_per_agent(self):
        state = L.derive([perm(100, agent_id="a2")], at(10), {"a2": at(90)})
        self.assertEqual(state.agent_waiting["a2"].kind, L.PERMISSION)

    def test_agent_attention_clears_on_that_agents_own_activity(self):
        state = L.derive([perm(100, agent_id="a2")], activity_at=at(500),
                         agent_activity={"a2": at(150)})
        self.assertEqual(state.agent_waiting, {})

    def test_serialization_scrubs_messages(self):
        e = ev(E.NOTIFICATION, 10, detail={
            "notification_type": "permission_prompt",
            "message": "use sk-ant-api03-" + "Z" * 40 + " ok"})
        d = L.derive([e], at(0)).to_dict()
        self.assertNotIn("sk-ant-api03", d["attention"]["message"])


class IntegrationCase(unittest.TestCase):
    """The fixture run: a1, a2 completed; a3 running (last activity at +81s)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root)
        self.spool = EventSpool(os.path.join(tempfile.mkdtemp(), "events"))

    def run_at(self, now_offset, mtime_offset=None):
        mtime = at(now_offset if mtime_offset is None else mtime_offset)
        os.utime(self.paths.session_jsonl, (mtime, mtime))
        builder = RunBuilder(self.paths, now_fn=lambda: at(now_offset),
                             spool=self.spool)
        return builder.refresh()

    def put(self, *events):
        for e in events:
            self.spool.append(e)


class TestServiceIntegration(IntegrationCase):
    def service(self, **kw):
        from orchestra.service import OrchestraService
        return OrchestraService(
            root=self.root, token="t", default_session="s1",
            now_fn=lambda: at(150),
            spool_factory=lambda: EventSpool(self.spool.root), **kw)

    def test_summary_includes_live_state(self):
        os.utime(self.paths.session_jsonl, (at(150), at(150)))
        self.put(perm(145))
        summary = self.service().run_summary("s1")
        self.assertEqual(summary["live"]["attention"]["kind"], "permission")

    def test_an_evicted_and_rebuilt_builder_still_sees_earlier_events(self):
        build_session(self.root, "s2")
        service = self.service(max_builders=1)
        os.utime(self.paths.session_jsonl, (at(150), at(150)))
        self.put(perm(145))
        service.run_summary("s1")
        service.run_summary("s2")                 # evicts s1's builder
        self.assertEqual(service.run_summary("s1")["live"]["attention"]["kind"],
                         "permission")

    def test_no_factory_means_transcript_only(self):
        from orchestra.service import OrchestraService
        service = OrchestraService(root=self.root, token="t",
                                   default_session="s1", now_fn=lambda: at(150))
        self.assertIsNone(service.run_summary("s1")["live"])


class TestBuildIntegration(IntegrationCase):
    def test_without_a_spool_nothing_changes(self):
        os.utime(self.paths.session_jsonl, (at(150), at(150)))
        run = RunBuilder(self.paths, now_fn=lambda: at(150)).refresh()
        self.assertIsNone(run.live)

    def test_no_events_means_live_is_none(self):
        self.assertIsNone(self.run_at(150).live)

    def test_a_pending_permission_prompt_reaches_the_summary(self):
        # Latest transcript entry is a1's/a3's, the newest at +140; the prompt
        # arrives after all of it.
        self.put(perm(145))
        summary = self.run_at(150).to_summary_dict()
        self.assertEqual(summary["live"]["attention"]["kind"], "permission")
        self.assertTrue(summary["live"]["has_events"])

    def test_the_prompt_clears_once_the_transcript_moves_on(self):
        self.put(perm(100))                      # a2's final entry is at +139
        self.assertIsNone(self.run_at(150).live["attention"])

    def test_session_end_makes_the_session_not_live_immediately(self):
        self.put(ev(E.SESSION_END, 145, detail={"reason": "logout"}))
        # mtime is fresh (within the 600s window) -- the old rule says "live".
        run = self.run_at(150, mtime_offset=140)
        self.assertFalse(run.session_live)
        self.assertEqual(run.live["ended"]["reason"], "logout")

    def test_a_resumed_session_is_live_again(self):
        self.put(ev(E.SESSION_END, 145))
        run = self.run_at(160, mtime_offset=155)    # written after the end
        self.assertTrue(run.session_live)

    def test_a_subagent_stop_closes_a_running_agent(self):
        # a3 has no notification, so the transcripts alone say "running".
        self.assertEqual(self.run_at(150).agent("a3").status, C.RUNNING)
        self.put(ev(E.AGENT_STOP, 120, agent_id="a3"))
        a3 = self.run_at(150).agent("a3")
        self.assertEqual(a3.status, C.COMPLETED)
        self.assertEqual(a3.ended_at, at(120))

    def test_a_stop_after_an_unanswered_tool_call_is_a_failure(self):
        # Make a3 die mid-tool: drop its tool_result.
        path = os.path.join(self.paths.subagents_dir, "agent-a3.jsonl")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()[:1]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        self.put(ev(E.AGENT_STOP, 120, agent_id="a3"))
        self.assertEqual(self.run_at(150).agent("a3").status, C.FAILED)

    def test_an_agent_that_is_waiting_on_you_reads_as_waiting_not_stalled(self):
        self.put(perm(140, agent_id="a3"))
        run = self.run_at(150)
        self.assertEqual(run.agent("a3").status, C.WAITING)
        self.assertEqual(run.totals()["waiting"], 1)

    def test_events_are_read_incrementally_across_refreshes(self):
        os.utime(self.paths.session_jsonl, (at(150), at(150)))
        builder = RunBuilder(self.paths, now_fn=lambda: at(150), spool=self.spool)
        self.assertIsNone(builder.refresh().live)
        self.put(perm(145))
        self.assertEqual(builder.refresh().live["attention"]["kind"], "permission")
        self.assertEqual(len(builder._events), 1)
        builder.refresh()
        self.assertEqual(len(builder._events), 1)       # not re-read

    def test_event_history_is_bounded(self):
        from orchestra import build
        builder = RunBuilder(self.paths, now_fn=lambda: at(150), spool=self.spool)
        for i in range(build.MAX_EVENTS + 50):
            self.spool.append(ev(E.TURN_END, 1000 + i))
        builder.refresh()
        self.assertEqual(len(builder._events), build.MAX_EVENTS)


if __name__ == "__main__":
    unittest.main()
