"""What a run produced: commits, pushes, pull requests and test runs, and how they are shown."""

import json
import os
import tempfile
import time
import unittest

from orchestra import outcomes as O
from tests import fake_secrets as fake
from tests.fixtures import ts


def bash(uid, at, command):
    return {"timestamp": ts(at), "type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": command}}]}}


def done(uid, at, git=None, is_error=False):
    entry = {"timestamp": ts(at), "type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "is_error": is_error, "content": "ok"}]}}
    if git is not None:
        entry["toolUseResult"] = {"stdout": "", "gitOperation": git}
    return entry


def pr_link(number, url, at):
    return {"type": "pr-link", "prNumber": number, "prUrl": url, "prRepository": "o/r", "timestamp": ts(at)}


class TestCommitMessage(unittest.TestCase):
    def test_both_quote_styles_and_here_documents(self):
        self.assertEqual(O.commit_message('git add -A && git commit -q -m "fix: one\\n\\nbody"'), "fix: one")
        self.assertEqual(O.commit_message("git commit -m 'feat: two'"), "feat: two")
        heredoc = 'git commit -m "$(cat <<\'EOF\'\nfeat: three\n\nMore.\nEOF\n)"'
        self.assertEqual(O.commit_message(heredoc), "feat: three")
        self.assertEqual(O.commit_message("git commit -F - <<'EOF'\n\ndocs: four\nEOF"), "docs: four")
        self.assertEqual(O.commit_message("git status"), "")


class TestLog(unittest.TestCase):
    def test_recorded_git_operations(self):
        log = O.OutcomeLog()
        log.ingest([bash("c", 1, 'git commit -m "feat: x"'), done("c", 2, {"commit": {"sha": "abc1234", "kind": "committed", "branch": "main"}}),
                    bash("p", 3, "git push"), done("p", 4, {"push": {"branch": "main"}}),
                    bash("r", 5, "gh pr create"), done("r", 6, {"pr": {"number": 7, "url": "https://github.com/o/r/pull/7", "action": "created"}}),
                    bash("m", 7, "git merge x"), done("m", 8, {"branch": {"ref": "x", "action": "merged"}})])
        d = log.to_dict()
        self.assertEqual([(c["sha"], c["branch"], c["message"]) for c in d["commits"]], [("abc1234", "main", "feat: x")])
        self.assertEqual((d["pushes"], d["prs"][0]["number"], d["prs"][0]["repo"], d["prs"][0]["action"]),
                         (1, 7, "o/r", "created"))
        self.assertEqual(d["merges"][0]["ref"], "x")

    def test_a_quiet_commit_still_counts_without_a_sha(self):
        log = O.OutcomeLog()
        log.ingest([bash("c", 1, 'git add -A && git commit -q -m "chore: quiet" && git push -q'),
                    done("c", 2, {"push": {"branch": "feature"}})])
        [c] = log.to_dict()["commits"]
        self.assertEqual((c["sha"], c["branch"], c["message"], c["key"]), ("", "feature", "chore: quiet", "tool:c"))

    def test_failed_dry_run_and_quoted_commits_do_not_count(self):
        log = O.OutcomeLog()
        log.ingest([bash("a", 1, 'git commit -m "x"'), done("a", 2, is_error=True),
                    bash("b", 3, "git commit --dry-run -m y"), done("b", 4),
                    bash("c", 5, "cat <<'EOF' > notes.md\nthen git commit it\nEOF"), done("c", 6),
                    bash("d", 7, "echo legit-commit"), done("d", 8)])
        self.assertEqual(log.to_dict()["commits"], [])

    def test_pr_links_dedupe_and_keep_created(self):
        log = O.OutcomeLog()
        url = "https://github.com/o/r/pull/3"
        log.ingest([pr_link(3, url, 1), bash("r", 2, "gh pr create"),
                    done("r", 3, {"pr": {"number": 3, "url": url, "action": "created"}}), pr_link(3, url, 4)])
        self.assertEqual([(p["number"], p["action"]) for p in log.to_dict()["prs"]], [(3, "created")])

    def test_only_https_links_are_kept(self):
        log = O.OutcomeLog()
        log.ingest([pr_link(1, "javascript:alert(1)", 1), pr_link(2, "http://x/pull/2", 2),
                    pr_link(3, 'https://x/"onmouseover=1', 3)])
        self.assertEqual(log.to_dict()["prs"], [])

    def test_test_runs_are_counted_by_their_result(self):
        log = O.OutcomeLog()
        log.ingest([bash("t1", 1, "pytest"), done("t1", 2), bash("t2", 3, "npm test"), done("t2", 4, is_error=True),
                    bash("l", 5, "ls"), done("l", 6)])
        self.assertEqual(log.to_dict()["checks"], {"passed": 1, "failed": 1})

    def test_an_entry_with_several_results_does_not_trust_its_record(self):
        log = O.OutcomeLog()
        both = {"timestamp": ts(3), "type": "user", "toolUseResult": {"gitOperation": {"push": {"branch": "b"}}},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "ok"},
                                        {"type": "tool_result", "tool_use_id": "b", "content": "ok"}]}}
        log.ingest([bash("a", 1, "git push"), bash("b", 1, "ls"), both])
        self.assertEqual(log.pushes, 0)

    def test_incremental_ingest_pairs_across_batches_and_snapshots_are_frozen(self):
        log = O.OutcomeLog()
        log.ingest([bash("c", 1, 'git commit -m "a"')])
        snap = log.snapshot()
        log.ingest([done("c", 2, {"commit": {"sha": "1111111"}})])
        self.assertEqual((len(snap.commits), len(log.commits)), (0, 1))

    def test_text_is_scrubbed(self):
        log = O.OutcomeLog()
        log.ingest([bash("c", 1, 'git commit -m "key ' + fake.GITHUB_TOKEN + '"'),
                    done("c", 2, {"commit": {"sha": "abc", "branch": "b"}})])
        self.assertNotIn("ABCDEFGHIJKLMNOP", json.dumps(log.to_dict()))


class TestSummary(unittest.TestCase):
    def log(self, *entries):
        log = O.OutcomeLog()
        log.ingest(list(entries))
        return log

    def test_duplicates_from_a_forked_agent_count_once_for_whoever_recorded_first(self):
        commit = [bash("c", 1, 'git commit -m "x"'), done("c", 2, {"commit": {"sha": "abc"}})]
        quiet = [bash("q", 3, 'git commit -q -m "y"'), done("q", 4)]
        s = O.summary([("", "Main session", self.log(*commit + quiet)), ("fork", "Fork", self.log(*commit + quiet))],
                      None, 1000)
        self.assertEqual(s["commits"], 2)
        self.assertEqual({c["label"] for c in s["recent_commits"]}, {"Main session"})

    def test_cost_and_tokens_per_commit_and_pr(self):
        log = self.log(bash("c", 1, 'git commit -m "x"'), done("c", 2, {"commit": {"sha": "a"}}),
                       bash("d", 3, 'git commit -m "y"'), done("d", 4, {"commit": {"sha": "b"}}),
                       pr_link(1, "https://github.com/o/r/pull/1", 5))
        s = O.summary([("", "", log)], {"enabled": True, "total": 3.0, "currency": "USD"}, 900)
        self.assertEqual(s["per"]["commit"], {"cost": 1.5, "tokens": 450})
        self.assertEqual(s["per"]["pr"], {"cost": 3.0, "tokens": 900})
        none = O.summary([("", "", log)], {"enabled": False}, 900)
        self.assertEqual((none["per"]["commit"]["cost"], none["currency"]), (None, None))

    def test_nothing_produced_is_none(self):
        self.assertIsNone(O.summary([("", "", O.OutcomeLog()), ("a", "A", None)], None, 0))


class TestBuiltAndShown(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from orchestra import demo
        from orchestra.build import RunBuilder
        from orchestra.pricing import PriceSource
        root = tempfile.mkdtemp()
        now = time.time()
        paths, _ = demo.build_demo(root, now=now)
        prices = os.path.join(root, "p.json")
        demo.write_prices(prices)
        cls.built = RunBuilder(paths, now_fn=lambda: now, prices=PriceSource(prices)).refresh()

    def test_the_demo_main_session_and_an_agent_both_produce(self):
        o = self.built.insights["outcomes"]
        self.assertEqual((o["commits"], o["prs"], o["pushes"]), (2, 1, 1))
        self.assertEqual({c["label"] for c in o["recent_commits"]}, {"Main session", "Handle payment webhooks"})
        self.assertIsNotNone(o["per"]["commit"]["cost"])
        hooks = [a for a in self.built.agents if a.description == "Handle payment webhooks"][0]
        self.assertEqual(hooks.to_light_dict()["produced"], {"commits": 1, "prs": 0, "pushes": 0})
        self.assertEqual(len(hooks.to_detail_dict()["outcomes"]["commits"]), 1)

    def ui(self):
        from tests import test_insights_ui as ui
        if ui.NODE is None:
            self.skipTest("node is not on PATH")
        return ui

    def test_the_card_links_the_pull_request_safely(self):
        ui = self.ui()
        html = ui.card(ui.render_insights(ui.demo_run()), "What the run produced")
        self.assertIn('<a href="https://github.com/northwind/shop/pull/42" target="_blank" rel="noopener noreferrer">#42 northwind/shop</a>', html)
        self.assertIn("feat: verify webhook signatures", html)
        for bad in ("NaN", "undefined", "null"):
            self.assertNotIn(bad, html)

    def test_hostile_values_are_text_and_bad_links_are_not_links(self):
        ui = self.ui()
        evil = '"><img src=x onerror=alert(1)>'

        def hit(summary):
            o = summary["insights"]["outcomes"]
            o["pull_requests"][0]["url"] = "javascript:alert(1)"
            o["pull_requests"][0]["repo"] = evil
            for c in o["recent_commits"]:
                c["message"], c["sha"], c["branch"] = evil, evil, evil
        html = ui.card(ui.render_insights(ui.demo_run(hit)), "What the run produced")
        self.assertNotIn("<img", html)
        self.assertNotIn("javascript:", html)

    def test_the_panel_line(self):
        ui = self.ui()
        o = {"commits": [{}, {}], "prs": [{"number": 4}], "pushes": 1, "checks": {"passed": 2, "failed": 1}, "merges": []}
        out = ui.run_js(("esc", "producedRow"), [], "console.log(JSON.stringify([producedRow(%s), producedRow(null)]));" % json.dumps(o))
        self.assertEqual(out, ["<dt>produced</dt><dd>2 commits, PR #4, 1 push, 2 of 3 test runs passed</dd>", ""])
        self.assertIn("producedRow(agent.outcomes)", ui.fn(ui.read("app.js"), "openDrawer"))


if __name__ == "__main__":
    unittest.main()
