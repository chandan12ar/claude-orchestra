"""Catch me up: a session's title, its latest recap and your last prompt, and where they are shown."""

import json
import os
import tempfile
import time
import unittest

from orchestra import sessionmeta as M
from tests import fake_secrets as fake
from tests.fixtures import ts


def title(kind, text):
    key = "aiTitle" if kind == "ai-title" else "customTitle"
    return {"type": kind, key: text, "sessionId": "s"}


def recap(at, text):
    return {"type": "system", "subtype": "away_summary", "content": text, "timestamp": ts(at),
            "isMeta": False}


def prompt(text):
    return {"type": "last-prompt", "lastPrompt": text, "sessionId": "s"}


def said(at, kind="user"):
    return {"type": kind, "timestamp": ts(at), "message": {"role": kind, "content": "x"}}


def meta(*entries):
    m = M.SessionMeta()
    m.ingest(list(entries))
    return m


class TestMeta(unittest.TestCase):
    def test_the_title_you_set_beats_claudes(self):
        d = meta(title("ai-title", "ECG report analysis"), title("custom-title", "My ECG work")).to_dict()
        self.assertEqual((d["title"], d["title_source"]), ("My ECG work", "you"))
        d = meta(title("ai-title", "ECG report analysis")).to_dict()
        self.assertEqual((d["title"], d["title_source"]), ("ECG report analysis", "claude"))

    def test_the_newest_recap_and_prompt_win(self):
        d = meta(recap(10, "Goal: first."), prompt("one"), recap(50, "Goal: second."), prompt("two")).to_dict()
        self.assertEqual((d["recap"], d["last_prompt"]), ("Goal: second.", "two"))
        self.assertEqual(d["recap_at"], M.parse_timestamp(ts(50)))

    def test_a_recap_is_stale_once_the_session_works_again(self):
        fresh = meta(said(5, "assistant"), recap(10, "Goal: x."), said(30)).to_dict()
        self.assertFalse(fresh["recap_stale"])          # within a minute: the same pause
        later = meta(said(5, "assistant"), recap(10, "Goal: x."), said(200, "assistant")).to_dict()
        self.assertTrue(later["recap_stale"])

    def test_text_is_one_line_bounded_and_scrubbed(self):
        long_title = "word " * 40
        d = meta(title("ai-title", long_title), recap(1, "Goal:\n  ship it.\n\nNext: " + "x" * 600),
                 prompt("use " + fake.GITHUB_TOKEN + " please")).to_dict()
        self.assertLessEqual(len(d["title"]), M.MAX_TITLE)
        self.assertTrue(d["title"].endswith("…"))
        self.assertTrue(d["recap"].startswith("Goal: ship it. Next: "))
        self.assertLessEqual(len(d["recap"]), M.MAX_RECAP)
        self.assertNotIn("ABCDEFGHIJKLMNOP", d["last_prompt"])

    def test_garbage_is_ignored_and_nothing_is_none(self):
        m = meta({"type": "ai-title", "aiTitle": 5}, {"type": "custom-title"}, "nope",
                 {"type": "system", "subtype": "away_summary", "content": None},
                 {"type": "last-prompt", "lastPrompt": ["x"]}, title("ai-title", "   "))
        self.assertTrue(m.empty())
        self.assertIsNone(m.to_dict())

    def test_snapshot_is_frozen(self):
        m = meta(title("ai-title", "A"))
        snap = m.snapshot()
        m.ingest([title("custom-title", "B")])
        self.assertEqual((snap.to_dict()["title"], m.to_dict()["title"]), ("A", "B"))


class TestPeek(unittest.TestCase):
    def write(self, lines, filler=0):
        path = os.path.join(tempfile.mkdtemp(), "s.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines[:1]:
                fh.write(json.dumps(line) + "\n")
            for i in range(filler):
                fh.write(json.dumps({"type": "user", "message": {"content": "pad " * 40}}) + "\n")
            for line in lines[1:]:
                fh.write(json.dumps(line) + "\n")
        return path

    def test_reads_the_title_from_the_end_of_a_big_file(self):
        path = self.write([said(1), title("ai-title", "Old"), title("ai-title", "Newer")], filler=4000)
        self.assertGreater(os.path.getsize(path), M.PEEK_BYTES)
        self.assertEqual(M.peek_title(path), {"title": "Newer", "title_source": "claude"})

    def test_the_title_you_set_wins_in_a_peek_too(self):
        path = self.write([said(1), title("ai-title", "Claude's"), title("custom-title", "Mine"),
                           title("ai-title", "Claude's")])
        self.assertEqual(M.peek_title(path)["title"], "Mine")

    def test_falls_back_to_the_start_when_the_end_has_no_title(self):
        path = self.write([title("ai-title", "Early title"), said(2)], filler=4000)
        self.assertEqual(M.peek_title(path)["title"], "Early title")

    def test_missing_or_untitled_files_give_nothing(self):
        self.assertEqual(M.peek_title(os.path.join(tempfile.mkdtemp(), "none.jsonl")), {})
        self.assertEqual(M.peek_title(self.write([said(1)])), {})

    def test_a_changed_file_is_read_again(self):
        path = self.write([title("ai-title", "First")])
        self.assertEqual(M.peek_title(path)["title"], "First")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(title("custom-title", "Renamed")) + "\n")
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(M.peek_title(path)["title"], "Renamed")


class TestBuiltAndServed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        cls.now = time.time()
        cls.root = tempfile.mkdtemp()
        cls.paths, cls.agents = demo.build_demo(cls.root, now=cls.now)
        cls.builder = RunBuilder(cls.paths, now_fn=lambda: cls.now + 10)
        cls.built = cls.builder.refresh()

    def test_the_demo_session_has_a_title_recap_and_last_prompt(self):
        s = self.built.to_summary_dict()["session"]
        self.assertEqual((s["title"], s["title_source"]), ("Checkout rewrite with payments", "claude"))
        self.assertTrue(s["recap"].startswith("Goal:"))
        self.assertTrue(s["last_prompt"])

    def test_what_was_read_survives_later_refreshes(self):
        # The main session's records must outlast the next incremental read (the
        # reset-every-refresh bug the instruction-file feature once had).
        from orchestra import demo
        demo.Simulator(self.paths, self.agents).tick(now=self.now + 5)
        again = self.builder.refresh().to_summary_dict()["session"]
        self.assertEqual(again["title"], "Checkout rewrite with payments")

    def test_sessions_list_and_fleet_carry_the_title(self):
        from orchestra.service import OrchestraService
        service = OrchestraService(default_session=self.paths.session_id, root=self.root,
                                   now_fn=lambda: self.now + 10)
        listed = service.session_list(self.paths.session_id)["sessions"]
        self.assertEqual(listed[0]["title"], "Checkout rewrite with payments")
        fleet = service.fleet()["sessions"]
        row = [s for s in fleet if s["session_id"] == self.paths.session_id][0]
        self.assertEqual(row["title"], "Checkout rewrite with payments")
        self.assertTrue(row["recap"].startswith("Goal:"))


class TestDemoNeighbours(unittest.TestCase):
    def test_the_demo_fleet_shows_each_kind_of_row(self):
        from orchestra import demo
        from orchestra.service import OrchestraService
        now = time.time()
        root = tempfile.mkdtemp()
        demo.build_demo(root, now=now)
        demo.write_side_sessions(root, now)
        rows = {s["session_id"]: s for s in OrchestraService(root=root, now_fn=lambda: now).fleet()["sessions"]}
        ecg, orders = rows["demo-ecg-report"], rows["demo-orders-migration"]
        self.assertEqual((ecg["title"], ecg["title_source"], ecg["recap_stale"]), ("ECG report analysis", "claude", False))
        self.assertEqual((orders["title_source"], orders["recap_stale"], orders["session_live"]), ("you", True, False))
        self.assertEqual((rows["demo-docs-pass"]["recap"], rows["demo-docs-pass"]["last_prompt"]),
                         ("", "Fix the broken links in the onboarding section"))
        self.assertEqual(rows["demo-scratch"]["title"], "")


class TestShown(unittest.TestCase):
    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    FNS = ("esc", "fmtDuration", "fleetAgo", "attentionTitle", "fleetStatus", "fleetSub", "fleetRow", "sessionLabel",
           "recapHtml", "tabTitle")

    def call(self, expr):
        ui = self.ui()
        return ui.run_js(self.FNS, [], "console.log(JSON.stringify(%s));" % expr)

    def row(self, **fields):
        s = {"session_id": "abcdef0123", "project_name": "shop", "session_live": True, "attention": None,
             "totals": {"agents": 3, "running": 1, "waiting": 0, "completed": 2, "failed": 0},
             "modified_at": time.time() - 60}
        s.update(fields)
        return self.call("fleetRow(%s, '')" % json.dumps(s))

    def test_rows_lead_with_the_title_and_keep_the_project(self):
        html = self.row(title="ECG report analysis", recap="Goal: report. Next: charts.",
                        recap_at=time.time() - 720)
        self.assertIn('<div class="fleet-title">ECG report analysis<span class="fleet-project">shop</span>', html)
        self.assertRegex(html, "Recap · 12m 0[0-9]s ago: Goal: report. Next: charts.")

    def test_what_needs_you_comes_before_the_recap(self):
        html = self.row(title="T", recap="Goal: r.", attention={"kind": "permission", "message": "Bash"})
        self.assertNotIn("Recap", html)
        self.assertIn("Bash", html)

    def test_without_a_recap_the_last_prompt_then_the_status(self):
        self.assertIn("Last asked: fix the tests", self.row(last_prompt="fix the tests"))
        self.assertIn("1 agent(s) running", self.row())
        plain = self.row()
        self.assertIn('<div class="fleet-title">shop<span class="fleet-id">abcdef01</span>', plain)

    def test_picker_labels_use_titles(self):
        out = self.call("[sessionLabel({session_id: 'abcdef0123', agent_count: 4, title: 'ECG report'}, 'x'),"
                        " sessionLabel({session_id: 'abcdef0123', agent_count: 4}, 'abcdef0123')]")
        self.assertEqual(out, ["ECG report · 4 agents", "abcdef01 · 4 agents (current)"])

    def test_recap_bar_says_how_old_and_whether_stale(self):
        now = time.time()
        fresh = self.call("recapHtml({recap: 'Goal: a.', recap_at: %f, recap_stale: false})" % (now - 300))
        stale = self.call("recapHtml({recap: 'Goal: a.', recap_at: %f, recap_stale: true})" % (now - 300))
        self.assertRegex(fresh, "Recap · 5m 0[0-9]s ago")
        self.assertNotIn("older than", fresh)
        self.assertIn("older than the latest activity", stale)
        self.assertEqual(self.call("[recapHtml(null), recapHtml({title: 'x'})]"), ["", ""])

    def test_the_tab_names_the_session(self):
        out = self.call("[tabTitle({kind: 'permission', count: 2, title: 'ECG'}), tabTitle({kind: 'idle', count: 0})]")
        self.assertEqual(out, ["(2) ECG · Cuelight", "Cuelight"])

    def test_hostile_titles_and_recaps_are_text(self):
        evil = '"><img src=x onerror=alert(1)>'
        html = self.row(title=evil, recap=evil, project_name=evil)
        self.assertNotIn("<img", html)
        self.assertNotIn("<img", self.call("recapHtml({recap: %s, recap_at: 1})" % json.dumps(evil)))
        # The picker label is plain text and goes into textContent, never markup.
        self.assertIn("option.textContent = sessionLabel(", self.ui().fn(self.ui().read("app.js"), "loadSessions"))


if __name__ == "__main__":
    unittest.main()
