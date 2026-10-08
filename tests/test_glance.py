"""Insights at a glance: one chip per card worth a look, and cards that fold and stay folded."""

import json
import re
import unittest

from tests import test_insights_ui as ui

FNS = ui.INSIGHT_FNS + ("toggleFold", "foldAll", "openCard")


def run_js(body, summary=None):
    return ui.run_js(FNS, ["BUCKET_VARS"], ("const RUN = %s;\n" % json.dumps(summary) if summary else "") + body)


@unittest.skipIf(ui.NODE is None, "node is not on PATH")
class TestHeadlines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = ui.demo_run(events=True)
        cls.lines = run_js("console.log(JSON.stringify(insHeadlines(RUN.insights, RUN)));", cls.summary)

    def test_worst_first_then_card_order(self):
        tones = [line["tone"] for line in self.lines]
        self.assertEqual(tones, sorted(tones, key=["bad", "warn", "info"].index))
        self.assertEqual([(l["key"], l["tone"]) for l in self.lines[:2]],
                         [("did-they-check-their-work", "bad"), ("what-went-wrong", "bad")])
        info = [l["key"] for l in self.lines if l["tone"] == "info"]
        self.assertEqual(info[:3], ["parallelism", "critical-path", "what-the-run-produced"])

    def test_the_words(self):
        text = {l["key"]: l["text"] for l in self.lines}
        self.assertEqual(text["did-they-check-their-work"], "1 failing check, 2 unchecked")
        self.assertEqual(text["what-went-wrong"], "1 agent stuck retrying, 1 API stall (2m 20s)")
        self.assertEqual(text["where-tokens-were-wasted"], "58.6k tokens rewritten to the cache")
        self.assertEqual(text["what-each-agent-was-told"], "1 agent without your instructions")
        self.assertEqual(text["files"], "1 file written by more than one agent")
        self.assertEqual(text["what-the-run-produced"], "2 commits, 1 PR")
        self.assertEqual(text["how-full-each-context-got"], "context peaked at 19%")
        self.assertTrue(text["waiting-on-you"].startswith("waited on you "))
        self.assertTrue(text["spend"].endswith(" spent"))
        self.assertNotIn("tool-use", text)                  # nothing worth a chip
        for bad in ("NaN", "undefined", "null"):
            self.assertNotIn(bad, json.dumps(self.lines))

    def test_tones_follow_the_numbers(self):
        def lines(mutate):
            s = json.loads(json.dumps(self.summary))
            mutate(s)
            return {l["key"]: l for l in run_js("console.log(JSON.stringify(insHeadlines(RUN.insights, RUN)));", s)}

        def over(s):
            s["cost"]["budget"] = {"limit": 1.0, "spent": 9.0, "ratio": 9.0, "state": "exceeded"}
            s["insights"]["pressure"]["near"] = [{"agent_id": "", "label": "Main session", "fill": 0.9, "tokens": 9}]
            s["insights"]["errors"]["stuck"] = []
            s["insights"]["errors"]["api_count"] = 0
            s["insights"]["checks"]["counts"] = {"checked": 6, "failing": 0, "unchecked": 0}
        got = lines(over)
        self.assertEqual((got["spend"]["tone"], got["spend"]["text"][-8:]), ("bad", "of $1.00"))
        self.assertEqual((got["how-full-each-context-got"]["tone"], got["how-full-each-context-got"]["text"]),
                         ("bad", "1 context near its window"))
        self.assertEqual((got["what-went-wrong"]["tone"], got["what-went-wrong"]["text"]), ("info", "5 failed calls"))
        self.assertEqual((got["did-they-check-their-work"]["tone"], got["did-they-check-their-work"]["text"]),
                         ("info", "6 of 6 checked their work"))


@unittest.skipIf(ui.NODE is None, "node is not on PATH")
class TestStripAndFolding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary = ui.demo_run(events=True)

    def render(self, folded=None, summary=None):
        pre = "state.folded = new Set(%s);\n" % json.dumps(folded) if folded is not None else ""
        return run_js(pre + "renderInsights(RUN); console.log(JSON.stringify(box.innerHTML));", summary or self.summary)

    def test_every_chip_jumps_to_a_card_that_is_there(self):
        html = self.render()
        cards = set(re.findall(r'data-card="([^"]+)"', html))
        jumps = re.findall(r'data-jump="([^"]+)"', html)
        self.assertTrue(jumps)
        self.assertLessEqual(set(jumps), cards)
        self.assertTrue(html.startswith('<nav class="glance" aria-label="Insights at a glance">'))
        self.assertIn('data-fold-all="fold">Fold all</button>', html)
        self.assertEqual(html.count('class="card-fold"'), len(cards))
        self.assertEqual(html.count('aria-expanded="true"'), len(cards))

    def test_a_folded_card_keeps_its_title_and_headline_only(self):
        html = self.render(["what-went-wrong", "tool-use"])
        errors = html.split('data-card="what-went-wrong">')[1].split("</section>")[0]
        self.assertIn('aria-expanded="false">What went wrong</button>', errors)
        self.assertIn('<p class="card-headline" data-tone="bad">1 agent stuck retrying, 1 API stall (2m 20s)</p>', errors)
        self.assertNotIn("card-sub", errors)
        self.assertNotIn("API errors", errors)
        tools = html.split('data-card="tool-use">')[1].split("</section>")[0]
        self.assertNotIn("card-headline", tools)                  # no headline: the title alone
        self.assertIn('class="card folded" data-card="tool-use"', html)
        self.assertIn('data-fold-all="unfold">Unfold all</button>', html)
        self.assertIn("<h4>By model</h4>", html)                   # the open cards are whole

    def test_hostile_values_are_text(self):
        evil = '"><img src=x onerror=alert(1)>'
        s = json.loads(json.dumps(self.summary))
        s["cost"]["currency"] = evil
        s["insights"]["errors"]["stuck"] = [{"agent_id": evil, "label": evil, "tool": evil, "target": evil,
                                             "failed_in_a_row": 3}]
        self.assertNotIn("<img", self.render(["what-went-wrong", "spend"], s))
        self.assertNotIn("<img", self.render(None, s))

    def test_cards_outside_insights_do_not_fold(self):
        out = run_js('console.log(JSON.stringify(insCard("Your prompts", "sub", "<p>x</p>")));')
        self.assertEqual(out, '<section class="card" data-card="your-prompts"><h3>Your prompts</h3>'
                              '<p class="card-sub">sub</p><p>x</p></section>')


@unittest.skipIf(ui.NODE is None, "node is not on PATH")
class TestRemembered(unittest.TestCase):
    STORE = """
const saved = {};
global.localStorage = {getItem: (k) => (k in saved ? saved[k] : null), setItem: (k, v) => { saved[k] = v; }};
"""

    def test_read_back_and_bad_values_ignored(self):
        out = run_js(self.STORE + """
const seen = [];
saved["cuelight-folded"] = JSON.stringify(["spend", 7, null, "files"]);
seen.push([...foldedCards()]);
state.folded = undefined; saved["cuelight-folded"] = "{not json";
seen.push([...foldedCards()]);
state.folded = undefined; saved["cuelight-folded"] = JSON.stringify({spend: true});
seen.push([...foldedCards()]);
console.log(JSON.stringify(seen));""")
        self.assertEqual(out, [["spend", "files"], [], []])

    def test_storage_that_throws_still_folds_for_the_visit(self):
        out = run_js("""
global.localStorage = {getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }};
foldedCards().add("spend");
saveFolded();
console.log(JSON.stringify([...foldedCards()]));""")
        self.assertEqual(out, ["spend"])

    def test_toggle_fold_all_and_jumping_open(self):
        out = run_js(self.STORE + """
const keys = ["parallelism", "spend", "files"];
document.querySelectorAll = () => keys.map((k) => ({getAttribute: () => k}));
document.querySelector = () => null;
global.getComputedStyle = () => ({position: "sticky"});
global.window = {scrollY: 0, scrollTo: () => {}};
function setView(v) { state.view = v; }
state.run = RUN;
const steps = [];
toggleFold("spend"); steps.push([...foldedCards()], saved["cuelight-folded"]);
toggleFold("spend"); steps.push([...foldedCards()]);
foldAll(true); steps.push([...foldedCards()]);
openCard("files"); steps.push([...foldedCards()], state.view);
foldAll(false); steps.push([...foldedCards()], saved["cuelight-folded"]);
console.log(JSON.stringify(steps));""", ui.demo_run())
        self.assertEqual(out, [["spend"], '["spend"]',                  # folded, and remembered
                               [],                                     # unfolded again
                               ["parallelism", "spend", "files"],     # fold all
                               ["parallelism", "spend"], "insights",  # jumping to a card opens it
                               [], "[]"])                              # unfold all


if __name__ == "__main__":
    unittest.main()
