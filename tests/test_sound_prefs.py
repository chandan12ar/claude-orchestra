"""Per-event sound mute and quiet hours: the pure functions, executed under node."""

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


def const(js, name):
    start = js.index("const {} =".format(name))
    return js[start:js.index(";\n", start) + 2]


def run_js(body):
    js = read("app.js")
    prelude = "\n".join([const(js, "SOUND_PREF_DEFAULT"),
                         *[fn(js, n) for n in ("normalizeSoundPrefs", "clockMinutes",
                                               "inQuietHours", "audibleSounds")]])
    path = os.path.join(tempfile.mkdtemp(), "s.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prelude + "\n" + body)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


def at(hh, mm=0):
    return "new Date(2026, 9, 3, {}, {})".format(hh, mm)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestSoundPrefs(unittest.TestCase):
    def test_defaults_let_everything_through(self):
        out = run_js("console.log(JSON.stringify(audibleSounds(['fail','alert','done'],"
                     "normalizeSoundPrefs(null), %s)));" % at(12))
        self.assertEqual(out, ["fail", "alert", "done"])

    def test_muting_one_event_keeps_the_others(self):
        out = run_js("const p = normalizeSoundPrefs({mute:{done:true}});"
                     "console.log(JSON.stringify(audibleSounds(['fail','alert','done'], p, %s)));"
                     % at(12))
        self.assertEqual(out, ["fail", "alert"])

    def test_muted_top_sound_does_not_hide_a_lower_one(self):
        # topSound() runs after filtering, so a muted "fail" lets "alert" through.
        out = run_js("const p = normalizeSoundPrefs({mute:{fail:true}});"
                     "console.log(JSON.stringify(audibleSounds(['fail','alert'], p, %s)));" % at(12))
        self.assertEqual(out, ["alert"])

    def test_daytime_quiet_window(self):
        prefs = "normalizeSoundPrefs({quiet:{on:true, from:'09:00', to:'17:00'}})"
        out = run_js("const p = %s; console.log(JSON.stringify([%s, %s, %s, %s].map(d =>"
                     " inQuietHours(p.quiet, d))));"
                     % (prefs, at(8, 59), at(9), at(16, 59), at(17)))
        self.assertEqual(out, [False, True, True, False])

    def test_overnight_quiet_window_wraps_midnight(self):
        prefs = "normalizeSoundPrefs({quiet:{on:true, from:'22:00', to:'07:00'}})"
        out = run_js("const p = %s; console.log(JSON.stringify([%s, %s, %s, %s, %s].map(d =>"
                     " inQuietHours(p.quiet, d))));"
                     % (prefs, at(21, 59), at(22), at(3), at(6, 59), at(7)))
        self.assertEqual(out, [False, True, True, True, False])

    def test_quiet_hours_silence_everything(self):
        out = run_js("const p = normalizeSoundPrefs({quiet:{on:true, from:'22:00', to:'07:00'}});"
                     "console.log(JSON.stringify(audibleSounds(['fail','alert','done'], p, %s)));"
                     % at(23))
        self.assertEqual(out, [])

    def test_quiet_off_or_empty_window_is_never_quiet(self):
        out = run_js("const a = normalizeSoundPrefs({quiet:{on:false, from:'00:00', to:'23:59'}});"
                     "const b = normalizeSoundPrefs({quiet:{on:true, from:'10:00', to:'10:00'}});"
                     "console.log(JSON.stringify([inQuietHours(a.quiet, %s), inQuietHours(b.quiet, %s)]));"
                     % (at(12), at(10)))
        self.assertEqual(out, [False, False])

    def test_hostile_stored_prefs_fall_back_to_defaults(self):
        out = run_js("const raws = [42, 'x', [], {mute:'yes'}, {quiet:{on:true, from:'99:99', to:'abc'}},"
                     "{mute:{done:'true'}}];"
                     "console.log(JSON.stringify(raws.map(r => {const p = normalizeSoundPrefs(r);"
                     " return [p.mute.done, p.quiet.from, p.quiet.to];})));")
        self.assertEqual(out, [[False, "22:00", "07:00"]] * 6)


if __name__ == "__main__":
    unittest.main()
