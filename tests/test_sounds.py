"""Sound decisions are pure logic in app.js; run them (and the synth) under node."""

import json
import os
import re
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


def const(js, name):
    start = js.index("const {} =".format(name))
    return js[start:js.index(";\n", start) + 2]


def run_js(body):
    js = read("app.js")
    prelude = "\n".join([const(js, "SOUND_PRIORITY"), const(js, "SOUND_NOTES"),
                         fn(js, "topSound"), fn(js, "computeSounds"),
                         fn(js, "computeFleetSounds"), fn(js, "playSound"),
                         fn(js, "audioContext")])
    path = os.path.join(tempfile.mkdtemp(), "s.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prelude + "\n" + body)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


def agents(*statuses):
    return "[" + ", ".join('{agent_id: "a%d", status: "%s"}' % (i, s)
                           for i, s in enumerate(statuses)) + "]"


def run_obj(att=None, ags=("running",), running=1, waiting=0, live=True):
    attn = ('{kind: "%s", since: 5}' % att) if att else "null"
    return ('{live: {attention: %s}, agents: %s, session_live: %s, '
            'totals: {running: %d, waiting: %d}}'
            % (attn, agents(*ags), str(live).lower(), running, waiting))


MEMO = '{seeded: false, attKey: "", failed: new Set(), running: 0}'


def sequence(*runs):
    """Feed runs to computeSounds in order; return the list of results."""
    return run_js("const memo = %s; const out = []; for (const r of [%s]) "
                  "out.push(computeSounds(r, memo)); console.log(JSON.stringify(out));"
                  % (MEMO, ", ".join(runs)))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestComputeSounds(unittest.TestCase):
    def test_the_first_poll_only_seeds_and_is_silent(self):
        out = sequence(run_obj("permission", ("failed",), 0))
        self.assertEqual(out, [[]])

    def test_a_new_permission_prompt_sounds_the_alert_once(self):
        out = sequence(run_obj(), run_obj("permission"), run_obj("permission"))
        self.assertEqual(out, [[], ["alert"], []])

    def test_an_api_error_is_a_fail_sound(self):
        self.assertEqual(sequence(run_obj(), run_obj("error"))[1], ["fail"])

    def test_idle_prompts_never_sound(self):
        self.assertEqual(sequence(run_obj(), run_obj("idle"))[1], [])

    def test_a_newly_failed_agent_sounds_fail_but_an_old_failure_does_not(self):
        out = sequence(run_obj(None, ("failed", "running")),
                       run_obj(None, ("failed", "running")),
                       run_obj(None, ("failed", "failed")))
        self.assertEqual(out, [[], [], ["fail"]])

    def test_everything_finishing_cleanly_sounds_done(self):
        out = sequence(run_obj(None, ("running",), 1),
                       run_obj(None, ("completed",), 0))
        self.assertEqual(out[1], ["done"])

    def test_finishing_with_a_failure_is_not_a_success_sound(self):
        out = sequence(run_obj(None, ("running", "running"), 2),
                       run_obj(None, ("failed", "completed"), 0))
        self.assertNotIn("done", out[1])
        self.assertIn("fail", out[1])

    def test_waiting_on_you_is_not_finished(self):
        out = sequence(run_obj(None, ("running",), 1),
                       run_obj(None, ("waiting",), 0, waiting=1))
        self.assertEqual(out[1], [])

    def test_priority_is_fail_then_alert_then_done(self):
        out = run_js('console.log(JSON.stringify([topSound(["done","alert","fail"]), '
                     'topSound(["done","alert"]), topSound(["done"]), topSound([])]))')
        self.assertEqual(out, ["fail", "alert", "done", None])


def fleet(*sessions):
    return "{sessions: [" + ", ".join(sessions) + "]}"


def sess(sid, kind="permission", live=True, urgency=4, since=1):
    return ('{session_id: "%s", session_live: %s, urgency: %d, '
            'attention: {kind: "%s", since: %d}}'
            % (sid, str(live).lower(), urgency, kind, since))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestComputeFleetSounds(unittest.TestCase):
    def seq(self, viewing, *fleets):
        return run_js(
            'const memo = {seeded: false, keys: {}}; const out = []; '
            'for (const f of [%s]) out.push(computeFleetSounds(f, "%s", memo)); '
            'console.log(JSON.stringify(out));' % (", ".join(fleets), viewing))

    def test_seeding_is_silent(self):
        self.assertEqual(self.seq("a", fleet(sess("b"))), [[]])

    def test_another_session_newly_blocked_sounds(self):
        out = self.seq("a", fleet(), fleet(sess("b")), fleet(sess("b")))
        self.assertEqual(out, [[], ["alert"], []])

    def test_the_session_you_are_viewing_is_left_to_the_other_check(self):
        self.assertEqual(self.seq("b", fleet(), fleet(sess("b")))[1], [])

    def test_an_ended_session_is_silent(self):
        self.assertEqual(self.seq("a", fleet(), fleet(sess("b", live=False)))[1], [])

    def test_an_error_elsewhere_is_a_fail_sound(self):
        self.assertEqual(
            self.seq("a", fleet(), fleet(sess("b", "error", urgency=3)))[1], ["fail"])

    def test_a_fresh_prompt_in_the_same_session_sounds_again(self):
        out = self.seq("a", fleet(), fleet(sess("b", since=1)), fleet(sess("b", since=9)))
        self.assertEqual(out[2], ["alert"])


FAKE_AUDIO = r"""
const created = [];
function node(kind) { return { kind, connect() {}, start(t) { created.push(kind + "@" + t.toFixed(2)); },
  stop() {}, frequency: {}, gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} } }; }
const state = { soundEnabled: true, audio: null, lastSoundAt: 0 };
global.window = { AudioContext: function () { this.currentTime = 10; this.state = "running";
  this.destination = {}; this.createOscillator = () => node("osc"); this.createGain = () => node("gain"); } };
"""


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestPlaySound(unittest.TestCase):
    def play(self, script):
        return run_js(FAKE_AUDIO + script + "\nconsole.log(JSON.stringify(created));")

    def test_each_sound_plays_its_notes(self):
        for name, notes in (("alert", 2), ("fail", 2), ("done", 3)):
            created = self.play('playSound("%s");' % name)
            self.assertEqual(len([c for c in created if c.startswith("osc")]), notes, name)

    def test_nothing_plays_while_sound_is_off(self):
        self.assertEqual(self.play('state.soundEnabled = false; playSound("alert");'), [])

    def test_a_second_sound_within_the_window_is_dropped(self):
        created = self.play('playSound("alert"); playSound("fail");')
        self.assertEqual(len([c for c in created if c.startswith("osc")]), 2)

    def test_notes_are_staggered_in_time(self):
        created = self.play('playSound("done");')
        starts = sorted({c for c in created if c.startswith("osc")})
        self.assertEqual(len(starts), 3)

    def test_no_web_audio_degrades_silently(self):
        out = run_js('const state = {soundEnabled: true, audio: null, lastSoundAt: 0};'
                     'global.window = {}; playSound("alert"); console.log("[]");')
        self.assertEqual(out, [])

    def test_unknown_sound_name_is_ignored(self):
        self.assertEqual(self.play('playSound("nope");'), [])


class TestSoundGuarantees(unittest.TestCase):
    def test_no_audio_files_are_ever_referenced(self):
        js = read("app.js")
        for banned in ("new Audio(", ".mp3", ".wav", ".ogg", "<audio"):
            self.assertNotIn(banned, js)
        for name in os.listdir(STATIC):
            self.assertNotRegex(name, r"\.(mp3|wav|ogg|m4a)$")

    def test_off_by_default(self):
        self.assertIn("soundEnabled: false", read("app.js"))

    def test_button_in_page_and_report_shell_and_hidden_until_supported(self):
        from orchestra import report
        for html in (read("index.html"), report._SHELL):
            self.assertRegex(html, r'<button id="sound-toggle"[^>]*hidden')
        js = read("app.js")
        self.assertIn("!state.offline && hasAudio", js)

    def test_sessions_switching_reseeds_the_sound_baseline(self):
        js = read("app.js")
        body = js[js.index("function switchSession("):]
        self.assertIn("state.soundMemo = null", body[:body.index("\n}\n")])

    def test_checks_run_on_every_poll_and_fleet_poll(self):
        js = read("app.js")
        self.assertIn("checkSounds(run);", js)
        self.assertIn("checkFleetSounds(data);", js)


if __name__ == "__main__":
    unittest.main()
