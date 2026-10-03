"""What each agent was told: instruction files and skills, coverage, and how it is shown."""

import json
import tempfile
import time
import unittest

from orchestra import context as X
from orchestra.model import Agent
from tests import fake_secrets as fake
from tests.fixtures import ts


def instructions(at, *files):
    return {"type": "attachment", "timestamp": ts(at), "attachment": {"type": "instructions", "files": [
        {"path": p, "type": t, "content": c} for p, t, c in files]}}


def nested(at, path, content):
    return {"type": "attachment", "timestamp": ts(at),
            "attachment": {"type": "nested_memory", "path": path, "content": content}}


USER = ("C:/Users/me/.claude/CLAUDE.md", "User", "be brief\n")
PROJ = ("E:/p/CLAUDE.md", "Project", "rules\nmore rules\n")
RULES = ("E:/p/.claude/rules/api.md", "Project", "no logs\n")


def log(*entries):
    out = X.ContextLog()
    out.ingest(list(entries))
    return out


def agent(aid, ctx, desc=None, kind="general-purpose"):
    a = Agent(agent_id=aid, description=desc or aid, agent_type=kind)
    a.context = ctx
    return a


class TestLog(unittest.TestCase):
    def test_files_keep_path_type_and_size_never_content(self):
        c = log(instructions(1, USER, PROJ))
        d = c.to_dict()
        self.assertEqual([(f["type"], f["chars"], f["lines"], f["how"]) for f in d["files"]],
                         [("User", 9, 1, "start"), ("Project", 17, 2, "start")])
        self.assertNotIn("more rules", json.dumps(d))

    def test_nested_memory_as_a_dict_or_its_repr_and_duplicates_once(self):
        as_dict = log(nested(2, "E:/p/src/CLAUDE.md", {"path": "E:/p/src/CLAUDE.md", "type": "Project", "content": "x"}))
        as_repr = log(nested(2, "E:/p/src/CLAUDE.md", "{'path': 'E:/p/src/CLAUDE.md', 'type': 'Project', 'content': 'x'}"))
        for c in (as_dict, as_repr):
            [f] = c.to_dict()["files"]
            self.assertEqual((f["type"], f["how"]), ("Project", "on file access"))
        twice = log(instructions(1, PROJ), instructions(5, PROJ), nested(6, "E:\\p\\CLAUDE.md", "{}"))
        self.assertEqual(len(twice.files), 1)

    def test_skills(self):
        c = log({"type": "attachment", "timestamp": ts(1), "attachment": {
            "type": "skill_listing", "names": ["a", "b", "a"], "skillCount": 3}})
        self.assertEqual((c.to_dict()["skills"], c.to_dict()["skill_names"]), (3, ["a", "b"]))

    def test_garbage_is_ignored(self):
        c = log({"type": "attachment", "attachment": {"type": "instructions", "files": "nope"}},
                {"type": "attachment", "attachment": "nope"}, nested(1, None, "{bad"), "not an entry")
        self.assertTrue(c.empty())

    def test_snapshot_is_frozen_and_paths_are_scrubbed(self):
        c = log(instructions(1, USER))
        snap = c.snapshot()
        c.ingest([instructions(2, PROJ)])
        self.assertEqual(len(snap.files), 1)
        leaky = log(instructions(1, ("E:/p/" + fake.GITHUB_TOKEN + "/CLAUDE.md", "Project", "")))
        self.assertNotIn("ABCDEFGHIJKLMNOP", json.dumps(leaky.to_dict()))


class TestCoverage(unittest.TestCase):
    def test_agents_without_the_main_sessions_project_files_are_named(self):
        main = log(instructions(0, USER, PROJ, RULES))
        good = agent("g", log(instructions(1, USER, PROJ, RULES)))
        worktree = agent("w", log(instructions(1, USER, ("E:/p/.claude/worktrees/wt/CLAUDE.md", "Project", ""),
                                               ("E:/p/.claude/worktrees/wt/.claude/rules/api.md", "Project", ""))))
        bare = agent("b", log(instructions(1, USER)), desc="Explore the API", kind="Explore")
        c = X.coverage(main, [good, worktree, bare, agent("n", None)])
        self.assertEqual((c["agents"], c["covered"]), (3, 2))       # worktree copies count as the same files
        [m] = c["missing"]
        self.assertEqual((m["agent_id"], m["agent_type"], len(m["missing"])), ("b", "Explore", 2))
        by_path = {f["path"]: f for f in c["files"]}
        self.assertEqual((by_path["E:/p/CLAUDE.md"]["agents"], by_path["E:/p/CLAUDE.md"]["main"]), (2, True))

    def test_user_files_are_not_project_rules(self):
        c = X.coverage(log(instructions(0, USER)), [agent("a", log(instructions(1, PROJ)))])
        self.assertEqual((c["required"], c["covered"], c["missing"]), ([], None, []))

    def test_nothing_recorded_is_none(self):
        self.assertIsNone(X.coverage(X.ContextLog(), [agent("a", X.ContextLog()), agent("b", None)]))
        self.assertIsNone(X.coverage(None, []))


class TestDemoAndDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        now = time.time()
        paths, _ = demo.build_demo(tempfile.mkdtemp(), now=now)
        cls.built = RunBuilder(paths, now_fn=lambda: now).refresh()

    def test_the_demo_explore_agent_ran_without_project_rules(self):
        c = self.built.insights["context"]
        self.assertEqual((c["agents"], c["covered"], len(c["required"]), c["skills"]), (13, 12, 2, 4))
        self.assertEqual(c["missing"][0]["label"], "Audit the current checkout flow")
        pay = [a for a in self.built.agents if a.description == "Build the payment adapter"][0]
        self.assertIn("on file access", [f["how"] for f in pay.to_detail_dict()["context"]["files"]])

    def test_what_the_main_session_recorded_survives_later_reads(self):
        # A live session keeps appending; the main session's files, commits and pull
        # requests read on the first pass must still be there on the next one.
        from orchestra import demo
        from orchestra.build import RunBuilder
        now = time.time()
        paths, agents = demo.build_demo(tempfile.mkdtemp(), now=now)
        builder = RunBuilder(paths, now_fn=lambda: now + 10)
        first = builder.refresh()
        demo.Simulator(paths, agents).tick(now=now + 5)
        again = builder.refresh()
        for run in (first, again):
            self.assertEqual(run.insights["context"]["covered"], 12)
            self.assertEqual(run.insights["outcomes"]["prs"], 1)
            self.assertEqual(len(run.main_context.files), 3)

    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    def test_the_card(self):
        ui = self.ui()
        html = ui.card(ui.render_insights(ui.demo_run()), "What each agent was told")
        self.assertIn("12 of 13</strong><span>agents loaded your project instructions", html)
        self.assertIn("Ran without your project instructions", html)
        self.assertIn("CLAUDE.md in dev/northwind-shop", html)
        self.assertIn('data-agent="a', html)
        for bad in ("NaN", "undefined", "null"):
            self.assertNotIn(bad, html)

    def test_hostile_paths_and_labels_are_text(self):
        ui = self.ui()
        evil = '"><img src=x onerror=alert(1)>'

        def hit(summary):
            c = summary["insights"]["context"]
            for f in c["files"]:
                f["path"], f["type"] = evil, evil
            for m in c["missing"]:
                m["label"], m["agent_type"], m["missing"] = evil, evil, [evil]
            c["required"] = [evil]
        html = ui.card(ui.render_insights(ui.demo_run(hit)), "What each agent was told")
        self.assertNotIn("<img", html)

    def test_the_panel_line(self):
        ui = self.ui()
        ctx = {"files": [{"path": "E:/p/CLAUDE.md", "type": "Project"}], "skills": 3}
        run = {"insights": {"context": {"missing": [{"agent_id": "a", "missing": ["E:/p/.claude/rules/api.md"]}]}}}
        out = ui.run_js(("esc", "fileLabel", "contextRow"), [],
                        "console.log(JSON.stringify([contextRow(%s, 'a', %s), contextRow(%s, 'b', %s), contextRow(null, 'a', null)]));"
                        % (json.dumps(ctx), json.dumps(run), json.dumps(ctx), json.dumps(run)))
        self.assertIn("CLAUDE.md in E:/p (Project), 3 skills offered", out[0])
        self.assertIn("Did not load api.md in .claude/rules", out[0])
        self.assertNotIn("Did not load", out[1])
        self.assertEqual(out[2], "")
        self.assertIn("contextRow(agent.context", ui.fn(ui.read("app.js"), "openDrawer"))


if __name__ == "__main__":
    unittest.main()
