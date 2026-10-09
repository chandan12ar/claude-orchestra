"""The History tab's pure rendering, executed under node."""

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
    return js[start:js.index("];\n", start) + 3]


def run_js(body):
    js = read("app.js")
    prelude = "\n".join([
        const(js, "HISTORY_METRICS"), const(js, "FOCUS_NAMES"),
        *[fn(js, n) for n in ("esc", "fmtCount", "fmtDuration", "fmtMoney", "fmtPct",
                              "fmtWhen", "fmtHistoryValue", "deltaVerdict", "focusMatches",
                              "noteFocus", "restoreFocus", "renderHistory", "renderCompare")]])
    program = prelude + "\n" + body
    path = os.path.join(tempfile.mkdtemp(), "h.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


SETUP = """
// esc() in app.js escapes through the DOM; this stub does what a real div does.
global.document = {createElement: () => {
  let text = "";
  return {set textContent(v) { text = String(v); },
          get innerHTML() { return text.replace(/&/g, "&amp;").replace(/</g, "&lt;")
                                       .replace(/>/g, "&gt;"); }};
}};
const box = {innerHTML: "", querySelectorAll: () => []};
const $ = (id) => box;
const state = {history: {data: null, selected: [], compare: null}};
"""


def render(data, selected=(), compare=None):
    return run_js(SETUP + "state.history.data = %s; state.history.selected = %s; "
                  "state.history.compare = %s; renderHistory(); "
                  "console.log(JSON.stringify(box.innerHTML));"
                  % (json.dumps(data), json.dumps(list(selected)), json.dumps(compare)))


def run_row(sid="s1", **over):
    row = {"session_id": sid, "project_name": "api", "started_at": 1790000000,
           "first_seen_at": 1790000000, "agents": 4, "failed": 1, "wall_s": 125.0,
           "tokens_total": 12000, "cost": 1.5, "currency": "USD"}
    row.update(over)
    return row


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestStates(unittest.TestCase):
    def test_loading(self):
        self.assertIn("Loading", render(None))

    def test_off_explains_how_to_turn_it_on_and_what_is_kept(self):
        html = render({"enabled": False, "runs": [], "error": ""})
        self.assertIn("ORCHESTRA_HISTORY=on", html)
        self.assertIn("never prompts", html)

    def test_an_error_is_shown_and_escaped(self):
        html = render({"enabled": True, "runs": [], "error": "<b>boom</b>"})
        self.assertIn("&lt;b&gt;boom", html)
        self.assertNotIn("<b>boom", html)

    def test_empty_says_runs_appear_as_you_view_them(self):
        self.assertIn("No runs recorded yet",
                      render({"enabled": True, "runs": [], "error": ""}))


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestTable(unittest.TestCase):
    def test_rows_show_the_run_numbers(self):
        html = render({"enabled": True, "error": "", "runs": [run_row()]})
        for text in ("api", ">4<", "2m 05s", "12.0k", "$1.50"):
            self.assertIn(text, html)

    def test_a_hostile_project_name_cannot_inject_markup(self):
        html = render({"enabled": True, "error": "", "runs": [
            run_row(project_name='<img src=x onerror=alert(1)>', session_id='a"b')]})
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn('data-session="a"b"', html)

    def test_missing_values_are_dashes_not_zeros(self):
        html = render({"enabled": True, "error": "", "runs": [
            run_row(cost=None, wall_s=None)]})
        self.assertGreaterEqual(html.count("—"), 2)

    def test_selected_rows_are_marked(self):
        data = {"enabled": True, "error": "", "runs": [run_row("s1"), run_row("s2")]}
        html = render(data, selected=["s2"])
        self.assertIn('data-session="s2" aria-selected="true"', html)
        self.assertIn('data-session="s1" aria-selected="false"', html)

    def test_prompts_to_select_two_runs(self):
        html = render({"enabled": True, "error": "", "runs": [run_row()]}, selected=["s1"])
        self.assertIn("Select two runs", html)


def compare_payload(**delta):
    a, b = run_row("a", started_at=1000), run_row("b", started_at=2000)
    base = {k: None for k in ("agents", "completed", "failed", "wall_s", "tokens_total",
                              "cost", "loops", "write_conflicts", "cache_hit_ratio")}
    base.update(delta)
    return {"a": a, "b": b, "delta": base}


def change(a, b):
    return {"a": a, "b": b, "change": b - a, "pct": ((b - a) / a * 100) if a else None}


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestCompare(unittest.TestCase):
    DATA = {"enabled": True, "error": "", "runs": [run_row("a"), run_row("b")]}

    def html(self, **delta):
        return render(self.DATA, ["a", "b"], compare_payload(**delta))

    def test_more_failures_is_bad_and_fewer_is_good(self):
        self.assertIn('delta-bad">+2', self.html(failed=change(1, 3)))
        self.assertIn('delta-good">−1', self.html(failed=change(3, 2)))

    def test_more_completed_is_good(self):
        self.assertIn('delta-good">+1', self.html(completed=change(2, 3)))

    def test_cheaper_is_good_and_dearer_is_bad(self):
        self.assertIn("delta-good", self.html(cost=change(2.0, 1.0)))
        self.assertIn("delta-bad", self.html(cost=change(1.0, 2.0)))

    def test_agent_count_is_neutral_because_more_is_not_better_or_worse(self):
        self.assertIn('delta-flat">+2', self.html(agents=change(2, 4)))

    def test_a_shorter_run_reads_as_minus_not_negative_minutes(self):
        html = self.html(wall_s=change(300, 150))
        self.assertIn("−2m 30s", html)
        self.assertNotIn("-2m", html)
        self.assertIn("delta-good", html)

    def test_no_change_reads_as_zero_and_flat(self):
        self.assertIn('delta-flat">0', self.html(loops=change(1, 1)))

    def test_cache_hit_is_shown_in_percentage_points(self):
        html = self.html(cache_hit_ratio=change(0.5, 0.75))
        self.assertIn("+25.0 pts", html)
        self.assertIn("delta-good", html)

    def test_an_unknown_metric_is_n_a_not_zero(self):
        self.assertIn("n/a", self.html())

    def test_percent_is_omitted_when_the_baseline_was_zero(self):
        html = self.html(failed=change(0, 2))
        self.assertIn("+2<", html.replace("</td>", "<"))

    def test_the_comparison_names_both_runs_later_first(self):
        html = self.html(failed=change(1, 2))
        self.assertIn(" vs ", html)

    def test_while_the_comparison_loads_it_says_so(self):
        html = render(self.DATA, ["a", "b"], None)
        self.assertIn("Comparing", html)


def order(ids, runs):
    js = read("app.js")
    program = fn(js, "orderRuns") + "\nconsole.log(JSON.stringify(orderRuns(%s, %s)));" % (
        json.dumps(ids), json.dumps(runs))
    path = os.path.join(tempfile.mkdtemp(), "o.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestOrderRuns(unittest.TestCase):
    RUNS = [{"session_id": "new", "started_at": 2000}, {"session_id": "old", "started_at": 1000},
            {"session_id": "mid", "started_at": 1500, "first_seen_at": 9}]

    def test_the_earlier_run_comes_first_whatever_the_click_order(self):
        self.assertEqual(order(["new", "old"], self.RUNS), ["old", "new"])
        self.assertEqual(order(["old", "new"], self.RUNS), ["old", "new"])

    def test_falls_back_to_first_seen_when_a_run_has_no_start(self):
        runs = [{"session_id": "a", "started_at": None, "first_seen_at": 50},
                {"session_id": "b", "started_at": 100}]
        self.assertEqual(order(["b", "a"], runs), ["a", "b"])

    def test_an_unknown_run_does_not_throw(self):
        self.assertEqual(len(order(["ghost", "old"], self.RUNS)), 2)

    def test_does_not_mutate_the_selection(self):
        js = read("app.js")
        body = js[js.index("function orderRuns("):]
        self.assertIn("ids.slice()", body[:body.index("\n}\n")])


class TestWiring(unittest.TestCase):
    def test_the_comparison_is_requested_in_time_order(self):
        js = read("app.js")
        body = js[js.index("async function loadCompare("):]
        self.assertIn("orderRuns(state.history.selected, runs)", body[:body.index("\n}\n")])

    def test_tab_and_section_in_the_page_and_the_report_shell(self):
        from orchestra import report
        for html in (read("index.html"), report._SHELL):
            self.assertIn('data-view="history"', html)
            self.assertIn('id="view-history"', html)
            self.assertIn('id="history"', html)

    def test_the_tab_is_hidden_in_a_static_report(self):
        from orchestra import report
        i = report._SHELL.index('data-view="history"')
        self.assertIn("hidden", report._SHELL[i - 60:i])
        self.assertIn('tab.dataset.view === "history"', read("app.js"))

    def test_opening_the_tab_loads_the_history(self):
        js = read("app.js")
        self.assertIn('if (view === "history") loadHistory();', js)

    def test_every_interpolated_value_in_the_table_is_escaped(self):
        js = read("app.js")
        body = js[js.index("function renderHistory("):]
        body = body[:body.index("\n}\n")]
        for raw in ("+ r.project_name", "+ r.session_id", "+ r.agents +", "+ r.failed +"):
            self.assertNotIn(raw, body)


if __name__ == "__main__":
    unittest.main()
