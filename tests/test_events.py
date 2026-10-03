import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from orchestra import events as E
from orchestra.events import Event, EventSpool, normalize_claude_hook
from orchestra.statedir import UnsafeStateDir, state_dir


def hook(name, **fields):
    payload = {"hook_event_name": name, "session_id": "sess-1",
               "cwd": "/work/proj", "transcript_path": "/x/y.jsonl"}
    payload.update(fields)
    return payload


class TestNormalize(unittest.TestCase):
    def test_maps_each_recorded_hook_to_a_canonical_kind(self):
        expected = {"SessionStart": E.SESSION_START, "SessionEnd": E.SESSION_END,
                    "SubagentStart": E.AGENT_START, "SubagentStop": E.AGENT_STOP,
                    "Notification": E.NOTIFICATION, "StopFailure": E.ERROR,
                    "Stop": E.TURN_END}
        for name, kind in expected.items():
            self.assertEqual(normalize_claude_hook(hook(name)).kind, kind)

    def test_notification_keeps_type_and_message(self):
        ev = normalize_claude_hook(hook("Notification",
                                        notification_type="permission_prompt",
                                        message="Claude needs permission"))
        self.assertEqual(ev.detail, {"notification_type": "permission_prompt",
                                     "message": "Claude needs permission"})

    def test_subagent_events_carry_agent_identity(self):
        ev = normalize_claude_hook(hook("SubagentStop", agent_id="a1",
                                        agent_type="Explore"))
        self.assertEqual((ev.agent_id, ev.agent_type), ("a1", "Explore"))

    def test_error_reads_type_and_message(self):
        ev = normalize_claude_hook(hook("StopFailure", error_type="rate_limit",
                                        error_message="slow down"))
        self.assertEqual(ev.detail["error_type"], "rate_limit")
        self.assertEqual(ev.detail["message"], "slow down")

    def test_unrecorded_events_are_ignored(self):
        for name in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Bogus"):
            self.assertIsNone(normalize_claude_hook(hook(name)))

    def test_junk_payloads_are_ignored(self):
        for junk in (None, [], "x", 3, {}, {"hook_event_name": "Stop"},
                     {"hook_event_name": "Stop", "session_id": ""}):
            self.assertIsNone(normalize_claude_hook(junk))

    def test_tool_inputs_and_prompts_are_never_stored(self):
        ev = normalize_claude_hook(hook(
            "Notification", message="hi", tool_input={"command": "rm -rf /"},
            prompt="my secret plan", user_input="more"))
        blob = json.dumps(ev.to_dict())
        for leaked in ("rm -rf", "secret plan", "more", "tool_input"):
            self.assertNotIn(leaked, blob)

    def test_secrets_in_messages_are_redacted_before_storage(self):
        ev = normalize_claude_hook(hook(
            "Notification", message="token sk-ant-api03-" + "A" * 40))
        self.assertNotIn("sk-ant-api03", ev.detail["message"])

    def test_redaction_happens_before_the_length_cap(self):
        secret = "ghp_" + "B" * 36
        ev = normalize_claude_hook(hook(
            "Notification", message="x" * (E.DETAIL_CAP - 10) + " " + secret))
        self.assertNotIn("ghp_", ev.detail["message"])

    def test_detail_is_capped(self):
        ev = normalize_claude_hook(hook("Notification", message="y" * 5000))
        self.assertLessEqual(len(ev.detail["message"]), E.DETAIL_CAP)

    def test_renamed_fields_degrade_to_empty_not_an_error(self):
        ev = normalize_claude_hook(hook("Notification", something_new="x"))
        self.assertEqual(ev.detail, {})


class TestEventValidation(unittest.TestCase):
    def good(self, **over):
        raw = Event(kind=E.AGENT_STOP, session_id="s", ts=5.0).to_dict()
        raw.update(over)
        return raw

    def test_round_trip(self):
        ev = Event(kind=E.ERROR, session_id="s", ts=9.5, agent_id="a",
                   detail={"error_type": "rate_limit"})
        self.assertEqual(Event.from_dict(ev.to_dict()), ev)

    def test_rejects_off_schema_input(self):
        for bad in (None, [], {}, self.good(v=2), self.good(kind="nope"),
                    self.good(session_id=""), self.good(session_id=5),
                    self.good(ts="now"), self.good(ts=True)):
            self.assertIsNone(Event.from_dict(bad))

    def test_non_dict_detail_is_dropped_not_fatal(self):
        self.assertEqual(Event.from_dict(self.good(detail="oops")).detail, {})


class SpoolCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tempfile.mkdtemp(), "events")
        self.spool = EventSpool(self.root)

    def ev(self, kind=E.AGENT_STOP, session="s1", ts=1.0, **kw):
        return Event(kind=kind, session_id=session, ts=ts, **kw)


class TestSpool(SpoolCase):
    def test_append_then_read(self):
        self.spool.append(self.ev(ts=1.0))
        self.spool.append(self.ev(E.ERROR, ts=2.0))
        got = self.spool.read_all("s1")
        self.assertEqual([e.kind for e in got], [E.AGENT_STOP, E.ERROR])

    def test_read_new_only_returns_what_was_appended_since(self):
        self.spool.append(self.ev(ts=1.0))
        self.assertEqual(len(self.spool.read_new("s1")), 1)
        self.assertEqual(self.spool.read_new("s1"), [])
        self.spool.append(self.ev(ts=2.0))
        self.assertEqual([e.ts for e in self.spool.read_new("s1")], [2.0])

    def test_sessions_are_isolated(self):
        self.spool.append(self.ev(session="a"))
        self.spool.append(self.ev(session="b"))
        self.assertEqual(len(self.spool.read_all("a")), 1)

    def test_a_torn_final_line_waits_for_the_rest(self):
        self.spool.append(self.ev(ts=1.0))
        path = self.spool.path_for("s1")
        with open(path, "ab") as fh:
            fh.write(b'{"v":1,"kind":"error"')           # writer mid-line
        self.assertEqual(len(self.spool.read_new("s1")), 1)
        with open(path, "ab") as fh:
            fh.write(b',"session_id":"s1","ts":3}\n')
        self.assertEqual([e.kind for e in self.spool.read_new("s1")], [E.ERROR])

    def test_garbage_lines_are_counted_and_skipped(self):
        self.spool.append(self.ev())
        with open(self.spool.path_for("s1"), "ab") as fh:
            fh.write(b"not json\n{\"v\":9}\n")
        self.spool.append(self.ev(ts=4.0))
        self.assertEqual(len(self.spool.read_all("s1")), 2)
        self.assertEqual(self.spool.dropped, 2)

    def test_hostile_session_id_cannot_escape_the_directory(self):
        self.spool.append(self.ev(session="../../etc/passwd"))
        path = self.spool.path_for("../../etc/passwd")
        self.assertEqual(os.path.dirname(path), self.root)
        self.assertEqual(os.listdir(self.root), ["etcpasswd.jsonl"])

    def test_session_id_that_sanitizes_to_nothing_writes_nothing(self):
        self.spool.append(self.ev(session="///"))
        self.assertFalse(os.path.exists(self.root) and os.listdir(self.root))

    def test_oversized_file_rotates_and_the_reader_recovers(self):
        self.spool.append(self.ev(ts=1.0))
        self.spool.read_new("s1")
        with mock.patch.object(E, "FILE_MAX_BYTES", 10):
            self.spool.append(self.ev(ts=2.0))
        self.assertTrue(os.path.exists(self.spool.path_for("s1") + ".old"))
        self.assertEqual([e.ts for e in self.spool.read_new("s1")], [2.0])

    def test_sessions_listing_is_newest_first(self):
        self.spool.append(self.ev(session="old"))
        self.spool.append(self.ev(session="new"))
        os.utime(self.spool.path_for("old"), (1000, 1000))
        self.assertEqual([s for s, _ in self.spool.sessions()], ["new", "old"])

    def test_prune_removes_only_stale_files(self):
        self.spool.append(self.ev(session="old"))
        self.spool.append(self.ev(session="new"))
        os.utime(self.spool.path_for("old"), (1000, 1000))
        self.assertEqual(self.spool.prune(max_age_s=3600), 1)
        self.assertEqual([s for s, _ in self.spool.sessions()], ["new"])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_spool_files_and_dir_are_private(self):
        self.spool.append(self.ev())
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode) & 0o077, 0)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.spool.path_for("s1")).st_mode) & 0o077, 0)


class TestStateDir(unittest.TestCase):
    def test_override_is_used_and_private(self):
        target = os.path.join(tempfile.mkdtemp(), "st")
        with mock.patch.dict(os.environ, {"ORCHESTRA_STATE_DIR": target}):
            self.assertEqual(state_dir(), target)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o700)

    @unittest.skipUnless(os.name == "posix", "POSIX ownership")
    def test_a_directory_owned_by_someone_else_is_refused(self):
        target = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"ORCHESTRA_STATE_DIR": target}), \
                mock.patch("os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(UnsafeStateDir):
                state_dir()

    @unittest.skipUnless(os.name == "posix", "POSIX symlinks")
    def test_a_symlinked_state_dir_is_refused(self):
        real = tempfile.mkdtemp()
        link = os.path.join(tempfile.mkdtemp(), "link")
        os.symlink(real, link)
        with mock.patch.dict(os.environ, {"ORCHESTRA_STATE_DIR": link}):
            with self.assertRaises(UnsafeStateDir):
                state_dir()

    def test_default_is_per_user_not_a_shared_fixed_name(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORCHESTRA_STATE_DIR", None)
            self.assertNotEqual(os.path.basename(state_dir()), "orchestra")


if __name__ == "__main__":
    unittest.main()
