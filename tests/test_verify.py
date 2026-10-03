"""Did an agent check its work: the rules, their wiring, and the dashboard's reading of them."""

import importlib
import os
import tempfile
import time
import unittest
from unittest import mock

from orchestra import verify as V
from orchestra.agentlog import AgentDigest
from orchestra.model import Agent, Run, ToolCall
from tests import fake_secrets as fake
from tests.fixtures import ts


def call(name, at, ok=True, **params):
    role, ref = V.classify(name, params)
    target = params.get("file_path") or params.get("command") or ""
    return ToolCall(name=name, target=target, timestamp=at, ok=ok, verify=role, ref=ref)


def edit(at, path="E:/proj/src/app.py"):
    return call("Edit", at, file_path=path)


def bash(at, command, ok=True):
    return call("Bash", at, ok=ok, command=command)


class TestClassify(unittest.TestCase):
    def test_checks_across_ecosystems(self):
        for command in ("pytest -q", "python3 -m unittest discover -s tests", "cd app && npm test",
                        "npm run test:unit", "pnpm lint", "yarn build", "npx tsc --noEmit", "npx vitest run",
                        "cargo clippy", "go vet ./...", "./gradlew test", "dotnet test", "make test",
                        "ruff check .", "mypy src", "npm run db:check", "node --test",
                        "claude plugin validate . --strict"):
            self.assertEqual(V.classify("Bash", {"command": command}), (V.CHECK, ""), command)

    def test_ordinary_commands_are_not_checks(self):
        for command in ("ls -la", "git status", "npm install", "npm ci", "cat README.md", "echo test",
                        "cat vitest.config.ts", "rm -rf .pytest_cache",
                        "git add src/ui/Checkout.tsx scripts/deploy.sh"):
            self.assertEqual(V.classify("Bash", {"command": command})[0], "", command)

    def test_text_inside_a_heredoc_is_not_the_command(self):
        commit = "git commit -F - <<'EOF'\nRun npm test and pytest before merging\nEOF"
        self.assertEqual(V.classify("Bash", {"command": commit})[0], "")
        edit_then_test = "python - <<'EOF'\nprint('x')\nEOF\nnpx vitest run src/a.test.ts"
        self.assertEqual(V.classify("Bash", {"command": edit_then_test})[0], V.CHECK)
        self.assertEqual(V.command_line("cat <<EOF > a\nbody\nEOF"), "cat <<EOF > a")

    def test_code_edits_count_docs_and_scratch_files_do_not(self):
        self.assertEqual(V.classify("Write", {"file_path": r"E:\proj\src\Checkout.tsx"}), (V.EDIT, "checkout.tsx"))
        self.assertEqual(V.classify("NotebookEdit", {"notebook_path": "/p/a.ipynb"})[0], V.EDIT)
        self.assertEqual(V.classify("Edit", {"file_path": "/p/Dockerfile"})[0], V.EDIT)
        for path in ("/p/README.md", "/p/notes.txt", "/p/logo.png",
                     r"C:\Users\me\AppData\Local\Temp\claude\x\scratchpad\gen.py", "/tmp/probe.py"):
            self.assertEqual(V.classify("Write", {"file_path": path})[0], "", path)

    def test_a_long_path_keeps_its_extension(self):
        path = "E:/" + "deep/" * 40 + "module.py"
        self.assertEqual(V.classify("Edit", {"file_path": path})[0], V.EDIT)

    def test_running_a_script_names_it(self):
        self.assertEqual(V.classify("Bash", {"command": 'cd x && python3 -u "E:/p/tools/gen.py" --out a'}),
                         (V.RUN, "gen.py"))
        self.assertEqual(V.classify("PowerShell", {"command": "pwsh -File scripts\\deploy.ps1"}), (V.RUN, "deploy.ps1"))

    def test_a_custom_pattern_adds_checks_and_a_bad_one_is_ignored_with_a_note(self):
        with mock.patch.dict(os.environ, {"ORCHESTRA_VERIFY_PATTERN": r"\bsmoke\.sh\b"}):
            mod = importlib.reload(V)
            self.assertTrue(mod.is_check("bash ci/smoke.sh"))
            self.assertEqual(mod.PATTERN_ERROR, "")
        with mock.patch.dict(os.environ, {"ORCHESTRA_VERIFY_PATTERN": "("}):
            mod = importlib.reload(V)
            self.assertIn("not a valid regular expression", mod.PATTERN_ERROR)
            self.assertTrue(mod.is_check("pytest"))
        importlib.reload(V)


class TestAssess(unittest.TestCase):
    def test_no_code_edits_means_nothing_to_say(self):
        self.assertIsNone(V.assess([bash(1, "pytest"), call("Write", 2, file_path="/p/a.md")], True))

    def test_a_passing_check_after_the_last_edit(self):
        v = V.assess([edit(1), bash(2, "pytest", ok=True)], True)
        self.assertEqual((v.state, v.last_check["ok"], v.final), (V.CHECKED, True, True))

    def test_the_last_check_failing(self):
        v = V.assess([edit(1), bash(2, "pytest", ok=True), bash(3, "pytest", ok=False)], True)
        self.assertEqual(v.state, V.FAILING)

    def test_an_edit_after_the_last_check_is_unchecked_and_says_one_ran_before(self):
        v = V.assess([edit(1), bash(2, "npm test"), edit(3, "/p/src/b.ts")], False)
        self.assertEqual((v.state, v.checked_before, v.final), (V.UNCHECKED, True, False))
        self.assertEqual(v.last_edit["target"], "/p/src/b.ts")

    def test_a_check_still_running_is_not_failing(self):
        v = V.assess([edit(1), bash(2, "pytest", ok=None)], False)
        self.assertEqual(v.state, V.CHECKED)

    def test_running_the_edited_file_counts_running_another_does_not(self):
        wrote = call("Write", 1, file_path="/p/scripts/backup.sh")
        self.assertEqual(V.assess([wrote, bash(2, "bash /p/scripts/backup.sh")], True).state, V.CHECKED)
        self.assertEqual(V.assess([wrote, bash(2, "bash /p/scripts/other.sh")], True).state, V.UNCHECKED)

    def test_calls_out_of_order_are_sorted_by_time(self):
        self.assertEqual(V.assess([bash(5, "pytest"), edit(1)], True).state, V.CHECKED)

    def test_targets_are_scrubbed(self):
        v = V.assess([edit(1), bash(2, "pytest --token " + fake.GITHUB_TOKEN)], True)
        self.assertNotIn("ABCDEFGHIJ", repr(v.to_dict()))


class TestDigestRecordsOutcomes(unittest.TestCase):
    def test_results_mark_calls_ok_or_failed_and_classify_them(self):
        d = AgentDigest()
        d.ingest([
            {"timestamp": ts(1), "type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "e", "name": "Edit", "input": {"file_path": "/p/a.py"}},
                {"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "pytest"}}]}},
            {"timestamp": ts(2), "type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "e", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "t", "is_error": True, "content": "Exit code 1"}]}}])
        self.assertEqual([(c.verify, c.ok) for c in d.tool_calls], [(V.EDIT, True), (V.CHECK, False)])
        self.assertFalse(d.ended_mid_tool)


class TestSummary(unittest.TestCase):
    def agent(self, aid, state, final=True, status="completed", desc=None):
        a = Agent(agent_id=aid, description=desc or aid, status=status)
        if state:
            a.verification = {"state": state, "final": final, "last_edit": {}, "last_check": None,
                              "checked_before": False}
        return a

    def test_counts_and_the_agents_worth_a_look_failing_first(self):
        out = V.summary([self.agent("a", V.CHECKED), self.agent("b", V.UNCHECKED),
                         self.agent("c", V.FAILING), self.agent("d", None)])
        self.assertEqual((out["edited"], out["counts"]), (3, {"checked": 1, "failing": 1, "unchecked": 1}))
        self.assertEqual([r["agent_id"] for r in out["attention"]], ["c", "b"])

    def test_labels_are_scrubbed(self):
        out = V.summary([self.agent("a", V.UNCHECKED, desc="ship " + fake.ANTHROPIC_KEY)])
        self.assertNotIn("AAAABBBB", repr(out))


class TestDemo(unittest.TestCase):
    def test_the_demo_shows_every_outcome(self):
        from orchestra import demo
        from orchestra.build import RunBuilder
        now = time.time()
        paths, _ = demo.build_demo(tempfile.mkdtemp(), now=now)
        run = RunBuilder(paths, now_fn=lambda: now).refresh()
        self.assertEqual(run.insights["checks"]["counts"], {"checked": 3, "failing": 1, "unchecked": 2})
        by = {a.description: a.verification for a in run.agents}
        self.assertEqual(by["Write the unit tests"]["state"], V.FAILING)
        self.assertEqual((by["Build the payment adapter"]["state"], by["Build the payment adapter"]["final"]),
                         (V.UNCHECKED, True))
        self.assertFalse(by["Build the checkout UI"]["final"])
        self.assertIn("verification", run.agents[0].to_light_dict())


class TestDashboard(unittest.TestCase):
    """The card, the wording and the Health box, under node where it is available."""

    @classmethod
    def setUpClass(cls):
        from tests import test_insights_ui as ui
        cls.ui = ui

    def node(self):
        if self.ui.NODE is None:
            self.skipTest("node is not on PATH")

    def test_wording_for_every_state(self):
        self.node()
        edit = {"tool": "Edit", "target": "E:/p/src/very/long/path/adapter.ts", "at": 1}
        cases = [
            {"state": "checked", "final": True, "last_edit": edit, "checked_before": False,
             "last_check": {"tool": "Bash", "target": "npm test", "ok": True}},
            {"state": "checked", "final": False, "last_edit": edit, "checked_before": False,
             "last_check": {"tool": "Bash", "target": "npm test", "ok": None}},
            {"state": "failing", "final": True, "last_edit": edit, "checked_before": False,
             "last_check": {"tool": "Bash", "target": "pytest", "ok": False}},
            {"state": "unchecked", "final": True, "last_edit": edit, "checked_before": True, "last_check": None},
            {"state": "unchecked", "final": False, "last_edit": edit, "checked_before": False, "last_check": None},
        ]
        import json
        out = self.ui.run_js(("fileName", "checkText"), [],
                             "console.log(JSON.stringify(%s.map(checkText)));" % json.dumps(cases))
        self.assertEqual(out, [
            "passed after the last edit: npm test",
            "check running: npm test",
            "last check failed: pytest",
            "no test, build or lint after its last edit (adapter.ts); one ran before it",
            "no test, build or lint after its last edit (adapter.ts), so far"])

    def test_the_card_on_the_demo(self):
        self.node()
        html = self.ui.card(self.ui.render_insights(self.ui.demo_run()), "Did they check their work?")
        self.assertIn("3 of 6</strong><span>ran a check after their last edit", html)
        self.assertIn('<li class="check-failing" data-agent="', html)
        self.assertIn("Write the unit tests", html)
        self.assertIn("unchecked so far", html)                 # the UI agent is still running
        for bad in ("NaN", "undefined", "null"):
            self.assertNotIn(bad, html)

    def test_hostile_text_stays_text(self):
        self.node()
        evil = '"><img src=x onerror=alert(1)>'

        def hit(summary):
            for row in summary["insights"]["checks"]["attention"]:
                row["label"] = evil
                row["last_edit"]["target"] = evil
                if row["last_check"]:
                    row["last_check"]["target"] = evil
        html = self.ui.card(self.ui.render_insights(self.ui.demo_run(hit)), "Did they check their work?")
        self.assertNotIn("<img", html)

    def test_a_bad_custom_pattern_is_explained_on_the_card(self):
        self.node()

        def bad(summary):
            summary["insights"]["checks"]["pattern_error"] = "ORCHESTRA_VERIFY_PATTERN is not valid"
        html = self.ui.card(self.ui.render_insights(self.ui.demo_run(bad)), "Did they check their work?")
        self.assertIn("ORCHESTRA_VERIFY_PATTERN is not valid", html)

    def test_finished_unchecked_and_failing_agents_reach_the_health_box(self):
        body = self.ui.fn(self.ui.read("app.js"), "renderHealth")
        self.assertIn("v.final && v.state !== \"checked\"", body)
        self.assertIn("UNCHECKED — ", body)
        self.assertIn("CHECKS FAILING — ", body)
        self.assertIn("checked its work", self.ui.fn(self.ui.read("app.js"), "openDrawer"))


if __name__ == "__main__":
    unittest.main()
