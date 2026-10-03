import json
import os
import subprocess
import sys
import tempfile
import unittest

from orchestra import events as E
from orchestra.events import EventSpool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "orchestra", "hook.py")


def run_hook(stdin, state, **extra_env):
    env = dict(os.environ, ORCHESTRA_STATE_DIR=state, **extra_env)
    return subprocess.run([sys.executable, HOOK], input=stdin, env=env,
                          capture_output=True, encoding="utf-8", timeout=30)


class TestHookProcess(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.spool = EventSpool(os.path.join(self.state, "events"))

    def payload(self, **kw):
        base = {"hook_event_name": "Notification", "session_id": "s9",
                "cwd": "/p", "notification_type": "permission_prompt",
                "message": "needs you"}
        base.update(kw)
        return json.dumps(base)

    def test_records_an_event_as_a_standalone_script(self):
        done = run_hook(self.payload(), self.state)
        self.assertEqual(done.returncode, 0)
        got = self.spool.read_all("s9")
        self.assertEqual(got[0].kind, E.NOTIFICATION)
        self.assertEqual(got[0].detail["notification_type"], "permission_prompt")

    def test_silent_on_both_streams(self):
        done = run_hook(self.payload(), self.state)
        self.assertEqual((done.stdout, done.stderr), ("", ""))

    def test_never_fails_whatever_it_is_fed(self):
        for junk in ("", "not json", "[]", "null", "{", "\x00\xff", "9" * 5000):
            done = run_hook(junk, self.state)
            self.assertEqual(done.returncode, 0, junk[:20])
            self.assertEqual((done.stdout, done.stderr), ("", ""), junk[:20])
        self.assertEqual(self.spool.sessions(), [])

    def test_can_be_switched_off(self):
        for value in ("off", "OFF", "0", "false", "no"):
            done = run_hook(self.payload(), self.state, ORCHESTRA_EVENTS=value)
            self.assertEqual(done.returncode, 0)
        self.assertEqual(self.spool.sessions(), [])

    def test_other_values_leave_it_on(self):
        run_hook(self.payload(), self.state, ORCHESTRA_EVENTS="on")
        self.assertEqual(len(self.spool.sessions()), 1)

    def test_ignores_events_it_does_not_record(self):
        run_hook(self.payload(hook_event_name="PreToolUse"), self.state)
        self.assertEqual(self.spool.sessions(), [])

    def test_still_exits_zero_when_the_state_dir_is_unusable(self):
        blocker = os.path.join(self.state, "file")
        open(blocker, "w").close()
        done = run_hook(self.payload(), os.path.join(blocker, "sub"))
        self.assertEqual(done.returncode, 0)
        self.assertEqual((done.stdout, done.stderr), ("", ""))

    def test_stdin_read_is_bounded(self):
        huge = self.payload(message="z" * (E.MAX_STDIN_BYTES * 2))
        done = run_hook(huge, self.state)
        self.assertEqual(done.returncode, 0)

    def test_session_start_prunes_stale_spool_files(self):
        old = EventSpool(os.path.join(self.state, "events"))
        old.append(E.Event(kind=E.TURN_END, session_id="ancient", ts=1.0))
        os.utime(old.path_for("ancient"), (1000, 1000))
        run_hook(self.payload(hook_event_name="SessionStart"), self.state)
        self.assertNotIn("ancient", [s for s, _ in self.spool.sessions()])


class TestHooksManifest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
            self.config = json.load(fh)["hooks"]

    def test_registers_exactly_the_events_the_normalizer_records(self):
        self.assertEqual(set(self.config), set(E._CLAUDE_KINDS))

    def test_never_subscribes_to_per_tool_events(self):
        # Tool calls are already in the transcripts, and a hook is a process
        # spawn per call in every session of every user.
        for name in ("PreToolUse", "PostToolUse", "PostToolBatch",
                     "UserPromptSubmit", "PermissionRequest"):
            self.assertNotIn(name, self.config)

    def test_every_hook_is_async_and_bounded_and_cannot_fail(self):
        for event, groups in self.config.items():
            for group in groups:
                for h in group["hooks"]:
                    self.assertEqual(h["type"], "command", event)
                    self.assertIs(h["async"], True, event)
                    self.assertLessEqual(h["timeout"], 10, event)
                    self.assertTrue(h["command"].rstrip().endswith("|| true"), event)

    def test_command_points_at_a_script_that_exists(self):
        command = self.config["Stop"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/orchestra/hook.py", command)
        self.assertTrue(os.path.isfile(HOOK))

    def test_command_falls_back_from_python3_to_python(self):
        command = self.config["Stop"][0]["hooks"][0]["command"]
        self.assertLess(command.index("python3 "), command.index("|| python "))

    def test_plugin_is_not_a_read_only_claim_breaker(self):
        # The hook must not touch ~/.claude: nothing in it may mention it.
        source = open(HOOK, encoding="utf-8").read() + \
            open(os.path.join(ROOT, "orchestra", "events.py"), encoding="utf-8").read()
        self.assertNotIn(".claude", source.replace("CLAUDE_PLUGIN", ""))


if __name__ == "__main__":
    unittest.main()
