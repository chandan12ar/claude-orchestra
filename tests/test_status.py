import unittest

from orchestra import constants as C
from orchestra.agentlog import AgentDigest
from orchestra.parent import LaunchRecord, Notification, ResultRecord
from orchestra.status import build_rounds, compute_status

T0, T1, T2, T3 = 1000.0, 1100.0, 1200.0, 1300.0


def launch(at=T0):
    return LaunchRecord(tool_use_id="toolu_1", launched_at=at)


def note(at, status="completed", result="done"):
    return Notification(agent_id="a1", tool_use_id="toolu_1", status=status,
                        result=result, at=at)


def digest(last=None, mid_tool=False):
    d = AgentDigest()
    d.last_activity_at = last
    d.ended_mid_tool = mid_tool
    return d


class TestBuildRounds(unittest.TestCase):
    def test_background_agent_with_one_notification(self):
        rounds = build_rounds(launch(), None, [note(T1)], digest(last=T1))
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].started_at, T0)
        self.assertEqual(rounds[0].ended_at, T1)
        self.assertEqual(rounds[0].status, C.COMPLETED)
        self.assertEqual(rounds[0].result, "done")

    def test_resumed_agent_produces_two_closed_rounds(self):
        rounds = build_rounds(launch(), None,
                              [note(T1, result="first"), note(T3, result="second")],
                              digest(last=T3))
        self.assertEqual(len(rounds), 2)
        self.assertEqual((rounds[0].started_at, rounds[0].ended_at), (T0, T1))
        self.assertEqual((rounds[1].started_at, rounds[1].ended_at), (T1, T3))
        self.assertEqual(rounds[1].result, "second")

    def test_activity_after_last_notification_opens_a_new_round(self):
        rounds = build_rounds(launch(), None, [note(T1)], digest(last=T2))
        self.assertEqual(len(rounds), 2)
        self.assertIsNone(rounds[1].ended_at)
        self.assertEqual(rounds[1].started_at, T1)

    def test_failed_notification_status(self):
        rounds = build_rounds(launch(), None, [note(T1, status="error")], digest(last=T1))
        self.assertEqual(rounds[0].status, C.FAILED)

    def test_inline_agent_closes_on_its_tool_result(self):
        result = ResultRecord(tool_use_id="toolu_1", launch_mode="inline",
                              inline_result="the answer", at=T1)
        rounds = build_rounds(launch(), result, [], digest(last=T1))
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].ended_at, T1)
        self.assertEqual(rounds[0].result, "the answer")
        self.assertEqual(rounds[0].status, C.COMPLETED)

    def test_inline_error_result_is_failed(self):
        result = ResultRecord(tool_use_id="toolu_1", launch_mode="inline",
                              is_error=True, inline_result="boom", at=T1)
        rounds = build_rounds(launch(), result, [], digest(last=T1))
        self.assertEqual(rounds[0].status, C.FAILED)

    def test_no_terminal_record_leaves_one_open_round(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        self.assertEqual(len(rounds), 1)
        self.assertIsNone(rounds[0].ended_at)


class TestComputeStatus(unittest.TestCase):
    def test_closed_round_wins(self):
        rounds = build_rounds(launch(), None, [note(T1)], digest(last=T1))
        self.assertEqual(compute_status(rounds, digest(last=T1), now=99999,
                                        session_live=False), C.COMPLETED)

    def test_running_when_recently_active(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        status = compute_status(rounds, digest(last=T1), now=T1 + 10, session_live=True)
        self.assertEqual(status, C.RUNNING)

    def test_stalled_when_silent_past_threshold_but_session_live(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        status = compute_status(rounds, digest(last=T1),
                                now=T1 + C.STALL_THRESHOLD_S + 1, session_live=True)
        self.assertEqual(status, C.STALLED)

    def test_orphaned_when_session_is_dead(self):
        rounds = build_rounds(launch(), None, [], digest(last=T1))
        status = compute_status(rounds, digest(last=T1), now=T1 + 9999,
                                session_live=False)
        self.assertEqual(status, C.ORPHANED)

    def test_mid_tool_death_beats_orphaned(self):
        d = digest(last=T1, mid_tool=True)
        rounds = build_rounds(launch(), None, [], d)
        self.assertEqual(compute_status(rounds, d, now=T1 + 9999, session_live=False),
                         C.FAILED)

    def test_mid_tool_while_session_live_is_still_running(self):
        d = digest(last=T1, mid_tool=True)
        rounds = build_rounds(launch(), None, [], d)
        self.assertEqual(compute_status(rounds, d, now=T1 + 5, session_live=True),
                         C.RUNNING)

    def test_resumed_agent_mid_second_round_is_running(self):
        d = digest(last=T2)
        rounds = build_rounds(launch(), None, [note(T1)], d)
        self.assertEqual(compute_status(rounds, d, now=T2 + 5, session_live=True),
                         C.RUNNING)

    def test_no_rounds_is_unknown(self):
        self.assertEqual(compute_status([], digest(), now=T1, session_live=True),
                         C.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
