import datetime
import unittest

from orchestra.parent import ParentIndex


def utc(year, month, day, hour, minute, second, frac=0.0):
    """Expected epoch, computed with datetime rather than the code under test.

    Using a hand-written constant here hides local-time bugs and gets typo'd;
    computing it a different way than parent.py does still catches a
    timegm-vs-mktime mistake.
    """
    moment = datetime.datetime(year, month, day, hour, minute, second,
                               tzinfo=datetime.timezone.utc)
    return moment.timestamp() + frac


TS1 = "2026-09-09T04:57:11.912Z"
TS2 = "2026-09-09T04:57:14.245Z"
TS3 = "2026-09-09T04:59:50.984Z"

LAUNCH = {
    "uuid": "turn-1", "timestamp": TS1, "type": "assistant",
    "cwd": r"E:\god_ai\dev-token-dashboard",
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Agent",
         "input": {"description": "Implement Task 1", "model": "haiku",
                   "prompt": "You are implementing Task 1..."}}]}}

BACKGROUND_RESULT = {
    "uuid": "r-1", "timestamp": TS2, "type": "user",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
            {"type": "text", "text": "Async agent launched successfully.\n"
                                     "agentId: ad434e54374f9fc8b (internal ID)\n"}]}]}}

NOTIFICATION = {
    "uuid": "n-1", "timestamp": TS3, "type": "user",
    "message": {"role": "user", "content":
        "<task-notification>\n<task-id>ad434e54374f9fc8b</task-id>\n"
        "<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n"
        "<summary>Agent finished</summary>\n<result>## Summary\n\nDONE</result>\n"
        "</task-notification>"}}

INLINE_LAUNCH = {
    "uuid": "turn-2", "timestamp": TS1, "type": "assistant",
    "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_2", "name": "Task",
         "input": {"description": "Search the repo", "prompt": "Find all callers."}}]}}

INLINE_RESULT = {
    "uuid": "r-2", "timestamp": TS2, "type": "user",
    "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_2",
         "content": [{"type": "text", "text": "Found 3 callers in src/."}]}]}}


class TestParentIndex(unittest.TestCase):
    def test_captures_launch_fields(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH])
        launch = idx.launches["toolu_1"]
        self.assertEqual(launch.description, "Implement Task 1")
        self.assertEqual(launch.model, "haiku")
        self.assertIn("implementing Task 1", launch.prompt)
        self.assertEqual(launch.turn_uuid, "turn-1")
        self.assertAlmostEqual(launch.launched_at,
                               utc(2026, 9, 9, 4, 57, 11, 0.912), places=2)

    def test_captures_cwd_and_last_entry_time(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT])
        self.assertEqual(idx.cwd, r"E:\god_ai\dev-token-dashboard")
        # BACKGROUND_RESULT (TS2) is later than LAUNCH (TS1); pinning to the
        # later value catches a regression that latches onto the first entry.
        self.assertAlmostEqual(idx.last_entry_at,
                               utc(2026, 9, 9, 4, 57, 14, 0.245), places=2)

    def test_background_result_extracts_agent_id(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT])
        result = idx.results["toolu_1"]
        self.assertEqual(result.agent_id, "ad434e54374f9fc8b")
        self.assertEqual(result.launch_mode, "background")
        self.assertEqual(result.inline_result, "")

    def test_inline_result_is_the_final_output(self):
        idx = ParentIndex()
        idx.ingest([INLINE_LAUNCH, INLINE_RESULT])
        result = idx.results["toolu_2"]
        self.assertEqual(result.launch_mode, "inline")
        self.assertEqual(result.inline_result, "Found 3 callers in src/.")
        self.assertEqual(result.agent_id, "")

    def test_error_result_is_flagged(self):
        entry = dict(INLINE_RESULT)
        entry["message"] = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_2", "is_error": True,
             "content": "Agent failed to start"}]}
        idx = ParentIndex()
        idx.ingest([INLINE_LAUNCH, entry])
        self.assertTrue(idx.results["toolu_2"].is_error)

    def test_notification_is_parsed(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT, NOTIFICATION])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].status, "completed")
        self.assertEqual(notes[0].tool_use_id, "toolu_1")
        self.assertIn("DONE", notes[0].result)

    def test_notification_delivered_as_a_queue_operation_is_parsed(self):
        # Some Claude Code builds deliver a background agent's completion as a
        # top-level queue-operation entry rather than a plain message — no
        # "message" field at all, so _content_text alone would find nothing.
        entry = {
            "type": "queue-operation", "operation": "enqueue", "timestamp": TS3,
            "sessionId": "s1",
            "content": "<task-notification>\n<task-id>ad434e54374f9fc8b</task-id>\n"
                       "<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n"
                       "<summary>Agent finished</summary>\n<result>DONE</result>\n"
                       "</task-notification>",
        }
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT, entry])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].status, "completed")

    def test_queue_operation_remove_is_not_a_second_notification(self):
        # "remove" is the same enqueued event being dequeued/consumed, not a
        # new completion — treating it as one would double the round count.
        enqueue = {
            "type": "queue-operation", "operation": "enqueue", "timestamp": TS3,
            "content": "<task-notification>\n<task-id>ad434e54374f9fc8b</task-id>\n"
                       "<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n"
                       "</task-notification>",
        }
        remove = dict(enqueue, operation="remove", timestamp="2026-09-09T05:00:00.000Z")
        idx = ParentIndex()
        idx.ingest([enqueue, remove])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 1)

    def test_notification_delivered_as_an_attachment_is_parsed(self):
        entry = {
            "type": "attachment", "timestamp": TS3, "isSidechain": False,
            "attachment": {
                "type": "queued_command", "commandMode": "task-notification",
                "timestamp": TS3,
                "prompt": "<task-notification>\n<task-id>ad434e54374f9fc8b</task-id>\n"
                          "<tool-use-id>toolu_1</tool-use-id>\n<status>completed</status>\n"
                          "<summary>Agent finished</summary>\n<result>DONE</result>\n"
                          "</task-notification>",
            },
        }
        idx = ParentIndex()
        idx.ingest([LAUNCH, BACKGROUND_RESULT, entry])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].status, "completed")

    def test_an_unrelated_attachment_is_not_mistaken_for_a_notification(self):
        entry = {"type": "attachment", "timestamp": TS3,
                 "attachment": {"type": "queued_command", "commandMode": "bash-input",
                                "prompt": "ls -la", "timestamp": TS3}}
        idx = ParentIndex()
        idx.ingest([entry])
        self.assertEqual(idx.notifications, {})

    def test_repeated_notifications_accumulate_in_order(self):
        second = dict(NOTIFICATION)
        second["uuid"] = "n-2"
        second["timestamp"] = "2026-09-09T05:10:00.000Z"
        second["message"] = {"role": "user", "content":
            NOTIFICATION["message"]["content"].replace("DONE", "DONE AGAIN")}
        idx = ParentIndex()
        idx.ingest([NOTIFICATION, second])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 2)
        self.assertIn("DONE AGAIN", notes[1].result)
        self.assertLess(notes[0].at, notes[1].at)

    def test_reingesting_the_same_notification_does_not_duplicate_it(self):
        # transcript.py resets its offset to 0 on truncation/replacement, which
        # re-delivers the whole file; ingest() must stay idempotent.
        idx = ParentIndex()
        idx.ingest([NOTIFICATION])
        idx.ingest([NOTIFICATION])
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 1)

    def test_genuinely_distinct_notifications_for_one_agent_both_land(self):
        second = dict(NOTIFICATION)
        second["uuid"] = "n-2"
        second["timestamp"] = "2026-09-09T05:10:00.000Z"
        second["message"] = {"role": "user", "content":
            NOTIFICATION["message"]["content"].replace("DONE", "DONE AGAIN")}
        idx = ParentIndex()
        idx.ingest([NOTIFICATION, second])
        idx.ingest([NOTIFICATION, second])  # re-deliver both; still no duplicates
        notes = idx.notifications["ad434e54374f9fc8b"]
        self.assertEqual(len(notes), 2)

    def test_non_agent_tool_uses_are_ignored(self):
        entry = {"uuid": "t", "timestamp": TS1, "type": "assistant",
                 "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "toolu_9", "name": "Bash",
                      "input": {"command": "ls"}}]}}
        idx = ParentIndex()
        idx.ingest([entry])
        self.assertEqual(idx.launches, {})

    def test_ingest_is_incremental(self):
        idx = ParentIndex()
        idx.ingest([LAUNCH])
        idx.ingest([BACKGROUND_RESULT])
        self.assertIn("toolu_1", idx.launches)
        self.assertIn("toolu_1", idx.results)

    def test_malformed_entries_do_not_raise(self):
        idx = ParentIndex()
        idx.ingest([{}, {"message": None}, {"message": {"content": "plain string"}},
                    {"message": {"content": [None, 5, {"type": "tool_use"}]}}])
        self.assertEqual(idx.launches, {})

    def test_specific_malformed_shapes_are_skipped_without_polluting_state(self):
        bare_string_message = {"uuid": "s1", "timestamp": TS1, "type": "user",
                               "message": "just a string, not a dict"}
        tool_use_without_id = {"uuid": "s2", "timestamp": TS1, "type": "assistant",
                               "message": {"role": "assistant", "content": [
                                   {"type": "tool_use", "name": "Agent",
                                    "input": {"description": "no id here"}}]}}
        notification_without_task_id = {
            "uuid": "s3", "timestamp": TS1, "type": "user",
            "message": {"role": "user", "content":
                "<task-notification>\n<tool-use-id>toolu_1</tool-use-id>\n"
                "<status>completed</status>\n</task-notification>"}}
        idx = ParentIndex()
        idx.ingest([bare_string_message, tool_use_without_id, notification_without_task_id])
        self.assertEqual(idx.launches, {})
        self.assertEqual(idx.notifications, {})


if __name__ == "__main__":
    unittest.main()
