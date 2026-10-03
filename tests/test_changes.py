"""What each agent changed: patches into per-file diffs, their summary, and the diff view."""

import json
import tempfile
import time
import unittest

from orchestra import changes as CH
from orchestra import insights
from orchestra.agentlog import AgentDigest
from orchestra.model import Agent, Run
from tests import fake_secrets as fake
from tests.fixtures import ts

PATCH = [{"oldStart": 3, "oldLines": 3, "newStart": 3, "newLines": 4,
          "lines": [" a", "-b", "+B", "+B2", " c"]}]


def use(uid, name, at, **params):
    return {"timestamp": ts(at), "type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": uid, "name": name, "input": params}]}}


def result(uid, at, tool_result=None, is_error=False):
    entry = {"timestamp": ts(at), "type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": is_error, "content": "ok"}]}}
    if tool_result is not None:
        entry["toolUseResult"] = tool_result
    return entry


class TestExtract(unittest.TestCase):
    def test_a_recorded_patch_is_used_as_is(self):
        c = CH.extract("Edit", {"file_path": "/p/a.py"}, {"filePath": "/p/a.py", "structuredPatch": PATCH})
        self.assertEqual((c["path"], c["created"]), ("/p/a.py", False))
        self.assertEqual((c["hunks"][0].old_start, c["hunks"][0].new_start, c["hunks"][0].lines),
                         (3, 3, [" a", "-b", "+B", "+B2", " c"]))

    def test_a_created_file_is_all_additions(self):
        c = CH.extract("Write", {"file_path": "/p/n.py", "content": "x"},
                       {"type": "create", "filePath": "/p/n.py", "content": "one\ntwo", "structuredPatch": []})
        self.assertTrue(c["created"])
        self.assertEqual(c["hunks"][0].lines, ["+one", "+two"])

    def test_without_a_recorded_result_the_input_is_used(self):
        c = CH.extract("Edit", {"file_path": "/p/a.py", "old_string": "x = 1", "new_string": "x = 2\ny = 3"}, None)
        self.assertEqual(c["hunks"][0].lines, ["-x = 1", "+x = 2", "+y = 3"])
        self.assertIsNone(c["hunks"][0].old_start)
        m = CH.extract("MultiEdit", {"file_path": "/p/a.py", "edits": [{"old_string": "a", "new_string": "b"},
                                                                      {"old_string": "c", "new_string": "d"}]}, None)
        self.assertEqual([h.lines for h in m["hunks"]], [["-a", "+b"], ["-c", "+d"]])
        n = CH.extract("NotebookEdit", {"notebook_path": "/p/a.ipynb", "new_source": "print(1)"}, {})
        self.assertEqual(n["hunks"][0].lines, ["+print(1)"])

    def test_no_file_or_another_tool_is_nothing(self):
        self.assertIsNone(CH.extract("Read", {"file_path": "/p/a"}, {}))
        self.assertIsNone(CH.extract("Edit", {}, {}))


class TestDigest(unittest.TestCase):
    def test_successful_edits_are_recorded_and_failed_ones_are_not(self):
        d = AgentDigest()
        d.ingest([use("e1", "Edit", 1, file_path="/p/a.py"),
                  result("e1", 2, {"filePath": "/p/a.py", "structuredPatch": PATCH}),
                  use("e2", "Edit", 3, file_path="/p/b.py", old_string="q", new_string="r"),
                  result("e2", 4, is_error=True)])
        self.assertEqual(list(d.changes.files), ["/p/a.py"])
        self.assertEqual(d.changes.totals(), {"files": 1, "added": 2, "removed": 1, "scratch_files": 0})

    def test_an_entry_with_several_results_does_not_trust_its_single_tool_use_result(self):
        d = AgentDigest()
        both = {"timestamp": ts(2), "type": "user", "toolUseResult": {"filePath": "/p/a.py", "structuredPatch": PATCH},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "e1", "content": "ok"},
                                        {"type": "tool_result", "tool_use_id": "e2", "content": "ok"}]}}
        d.ingest([use("e1", "Edit", 1, file_path="/p/a.py", old_string="1", new_string="2"),
                  use("e2", "Edit", 1, file_path="/p/b.py", old_string="3", new_string="4"), both])
        self.assertEqual([f.hunks[0].lines for f in d.changes.files.values()], [["-1", "+2"], ["-3", "+4"]])

    def test_repeated_edits_to_one_file_accumulate(self):
        d = AgentDigest()
        for i in range(3):
            d.ingest([use("e%d" % i, "Edit", i * 2, file_path="/p/a.py"),
                      result("e%d" % i, i * 2 + 1, {"filePath": "/p/a.py", "structuredPatch": PATCH})])
        f = d.changes.files["/p/a.py"]
        self.assertEqual((f.edits, f.added, f.removed, len(f.hunks)), (3, 6, 3, 3))


class TestLog(unittest.TestCase):
    def test_kept_lines_are_capped_but_counts_stay_complete(self):
        log = CH.ChangeLog()
        big = {"path": "/p/big.py", "created": True,
               "hunks": [CH.Hunk(0, 1, ["+x"] * (CH.MAX_LINES + 50))]}
        log.add(big, 1.0)
        log.add({"path": "/p/next.py", "created": False, "hunks": [CH.Hunk(1, 1, ["+y"])]}, 2.0)
        self.assertEqual(log.kept, CH.MAX_LINES)
        self.assertEqual(log.files["/p/big.py"].added, CH.MAX_LINES + 50)
        self.assertTrue(log.files["/p/big.py"].truncated and log.files["/p/next.py"].truncated)

    def test_scratch_files_are_listed_but_not_counted(self):
        log = CH.ChangeLog()
        log.add({"path": r"C:\Users\me\AppData\Local\Temp\x\scratchpad\gen.py", "created": True,
                 "hunks": [CH.Hunk(0, 1, ["+a"])]}, 1.0)
        self.assertEqual(log.totals(), {"files": 0, "added": 0, "removed": 0, "scratch_files": 1})
        self.assertTrue(log.to_dicts()[0]["scratch"])

    def test_secrets_are_scrubbed_even_across_lines_and_long_lines_are_cut_after(self):
        log = CH.ChangeLog()
        pem = ["+" + l for l in fake.RSA_PEM.split("\n")]
        log.add({"path": "/p/k.py", "created": True,
                 "hunks": [CH.Hunk(0, 1, pem + ["+token = '" + fake.GITHUB_TOKEN + "'", "+" + "z" * 900])]}, 1.0)
        text = json.dumps(log.to_dicts())
        self.assertNotIn(fake.PEM_BODY, text)
        self.assertNotIn("ABCDEFGHIJKLMNOP", text)
        lines = log.to_dicts()[0]["hunks"][0]["lines"]
        self.assertTrue(all(l[:1] in " +-" for l in lines))
        self.assertTrue(all(len(l) <= CH.MAX_LINE_CHARS + 1 for l in lines))

    def test_a_snapshot_does_not_move_when_the_log_does(self):
        log = CH.ChangeLog()
        log.add({"path": "/p/a.py", "created": False, "hunks": [CH.Hunk(1, 1, ["+a"])]}, 1.0)
        snap = log.snapshot()
        log.add({"path": "/p/a.py", "created": False, "hunks": [CH.Hunk(2, 2, ["+b"])]}, 2.0)
        log.add({"path": "/p/b.py", "created": False, "hunks": [CH.Hunk(1, 1, ["+c"])]}, 3.0)
        self.assertEqual((len(snap.files), snap.files["/p/a.py"].added, len(snap.files["/p/a.py"].hunks)), (1, 1, 1))


class TestSummary(unittest.TestCase):
    def agent(self, aid, *paths, desc=None):
        a = Agent(agent_id=aid, description=desc or aid)
        a.changes = CH.ChangeLog()
        for p in paths:
            a.changes.add({"path": p, "created": False, "hunks": [CH.Hunk(1, 1, ["-a", "+b", "+c"])]}, 1.0)
        return a

    def test_totals_per_agent_and_worktree_copies_are_one_file(self):
        r = Run(session_id="s", agents=[
            self.agent("a", "E:/p/src/x.ts", "E:/p/src/y.ts"),
            self.agent("b", r"E:\p\.claude\worktrees\wt1\src\x.ts"),
            self.agent("c")])
        c = insights.compute(r, now=10)["changes"]
        self.assertEqual((c["files"], c["added"], c["removed"]), (2, 6, 3))
        self.assertEqual([x["agent_id"] for x in c["by_agent"]], ["a", "b"])
        self.assertEqual(c["top_files"][0]["agents"], 2)

    def test_none_without_changes(self):
        self.assertIsNone(insights.compute(Run(session_id="s", agents=[self.agent("a")]), now=1)["changes"])

    def test_labels_and_paths_are_scrubbed(self):
        r = Run(session_id="s", agents=[self.agent("a", "/p/" + fake.GITHUB_TOKEN + ".py",
                                                   desc="x " + fake.ANTHROPIC_KEY)])
        self.assertNotIn("AAAABBBB", repr(insights.compute(r, now=1)["changes"]))


class TestDemoAndPayloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        now = time.time()
        paths, _ = demo.build_demo(tempfile.mkdtemp(), now=now)
        cls.built = RunBuilder(paths, now_fn=lambda: now).refresh()

    def test_the_demo_has_new_files_and_edits(self):
        c = self.built.insights["changes"]
        self.assertGreater(c["files"], 10)
        self.assertGreater(c["removed"], 0)
        self.assertGreater(c["added"], c["removed"])

    def test_light_payload_has_totals_and_the_detail_has_diffs(self):
        cart = [a for a in self.built.agents if a.description == "Implement the cart service"][0]
        self.assertEqual(cart.to_light_dict()["changes"]["files"], 3)
        files = cart.to_detail_dict()["change_files"]
        self.assertIn("+  return items.reduce((sum, i) => sum + i.price * i.quantity, 0);",
                      [l for f in files for h in f["hunks"] for l in h["lines"]])

    def test_a_static_report_carries_the_diffs(self):
        from orchestra import report
        details = {a.agent_id: a.to_detail_dict() for a in self.built.agents}
        html = report.render_report(self.built, details)
        self.assertIn("price * i.quantity", html)


class TestDiffView(unittest.TestCase):
    """The panel's diff and the Insights card, under node where it is available."""

    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    def test_line_numbers_follow_the_hunk(self):
        ui = self.ui()
        f = {"hunks": [{"old_start": 3, "new_start": 3, "at": None, "lines": [" a", "-b", "+B", " c"]}]}
        html = ui.run_js(("esc", "fmtClock", "diffHtml"), [], "console.log(JSON.stringify(diffHtml(%s)));" % json.dumps(f))
        self.assertIn("@@ \u22123 +3 @@", html)
        self.assertIn('<tr class="diff-del"><td class="ln">4</td><td class="ln"></td>', html)
        self.assertIn('<tr class="diff-add"><td class="ln"></td><td class="ln">4</td>', html)
        self.assertIn('<tr class="diff-ctx"><td class="ln">5</td><td class="ln">5</td>', html)

    def test_hostile_code_and_paths_are_text(self):
        ui = self.ui()
        evil = '"><img src=x onerror=alert(1)>'
        files = [{"path": "/p/" + evil, "created": True, "scratch": False, "edits": 1, "added": 1, "removed": 0,
                  "truncated": True, "hunks": [{"old_start": None, "new_start": None, "at": None, "lines": ["+" + evil]}]}]
        html = ui.run_js(("esc", "fmtClock", "fileName", "diffHtml", "changesHtml"), [],
                         "console.log(JSON.stringify(changesHtml(%s)));" % json.dumps(files))
        self.assertNotIn("<img", html)
        self.assertIn("@@ edit @@", html)
        self.assertIn("counts above are complete", html)
        self.assertIn('<span class="change-tag">new</span>', html)

    def test_the_card_on_the_demo_and_the_panel_wiring(self):
        ui = self.ui()
        html = ui.card(ui.render_insights(ui.demo_run()), "What changed")
        self.assertIn("files changed", html)
        self.assertIn("Most changed files", html)
        self.assertIn('data-agent="a', html)
        self.assertIn("changesHtml(agent.change_files)", ui.fn(ui.read("app.js"), "openDrawer"))


if __name__ == "__main__":
    unittest.main()
