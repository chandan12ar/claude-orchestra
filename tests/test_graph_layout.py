"""The Graph view's layout: a pure function of the run, executed under node."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from orchestra import demo
from orchestra.build import RunBuilder

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


SETUP = """
const state = {filterText: "", filterStatuses: new Set()};
const agentMatchesFilter = () => true;
const fmtModelShort = (m) => String(m || "?");
"""

CONSTS = ("NODE_W", "NODE_H", "COL_GAP", "ROW_GAP", "EXACT_KINDS", "GRAPH_SWEEPS")
FNS = ("graphRankColumns", "graphCriticalPath", "graphOrderColumns", "graphCountCrossings",
       "graphPlaceRows", "layoutGraph")


def layout(run):
    js = read("app.js")
    prelude = "\n".join([SETUP] + [const(js, c) for c in CONSTS] + [fn(js, n) for n in FNS])
    program = prelude + "\nconst L = layoutGraph(%s);\n" % json.dumps(run) + """
console.log(JSON.stringify({
  nodes: L.nodes.map((n) => ({id: n.id, column: n.column, x: n.x, y: n.y})),
  edges: L.edges.map((e) => ({src: e.src, dst: e.dst, kind: e.kind, hidden: !!e.hidden})),
  crossings: L.crossings, width: L.width, height: L.height,
  critical: Array.from(L.criticalNodes)}));
"""
    path = os.path.join(tempfile.mkdtemp(), "g.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


def agent(aid, start=0.0, dur=10.0):
    return {"agent_id": aid, "description": aid, "agent_type": "t", "model": "m",
            "status": "completed", "started_at": start, "duration_s": dur}


def edge(src, dst, kind="artifact", conf="exact"):
    return {"src": src, "dst": dst, "kind": kind, "confidence": conf, "evidence": {}}


def run_of(agents, edges):
    return {"agents": agents, "edges": edges}


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestLayout(unittest.TestCase):
    def test_a_chain_flows_left_to_right(self):
        out = layout(run_of([agent("a"), agent("b"), agent("c")],
                            [edge("main", "a", "spawn"), edge("a", "b"), edge("b", "c")]))
        cols = {n["id"]: n["column"] for n in out["nodes"]}
        self.assertEqual([cols[i] for i in ("main", "a", "b", "c")], [0, 1, 2, 3])

    def test_no_two_nodes_overlap(self):
        agents = [agent("a%d" % i) for i in range(20)]
        edges = [edge("main", a["agent_id"], "spawn") for a in agents]
        out = layout(run_of(agents, edges))
        by_col = {}
        for n in out["nodes"]:
            by_col.setdefault(n["column"], []).append(n["y"])
        for ys in by_col.values():
            ys.sort()
            for a, b in zip(ys, ys[1:]):
                self.assertGreaterEqual(b - a, 50 + 24 - 1e-6)    # NODE_H + ROW_GAP

    def test_the_orchestrator_fan_is_hidden_when_a_real_dependency_exists(self):
        out = layout(run_of([agent("a"), agent("b")],
                            [edge("main", "a", "spawn"), edge("main", "b", "spawn"),
                             edge("a", "b")]))
        hidden = {(e["src"], e["dst"]): e["hidden"] for e in out["edges"]}
        self.assertFalse(hidden[("main", "a")])        # a is a root: the launch is the point
        self.assertTrue(hidden[("main", "b")])         # b is explained by a
        self.assertFalse(hidden[("a", "b")])

    def test_children_sit_near_their_parents(self):
        # Two independent pairs: each child should line up with its own parent, not
        # be shuffled to the other pair's row.
        agents = [agent("p1", 0), agent("p2", 1), agent("c1", 2), agent("c2", 3)]
        edges = [edge("main", "p1", "spawn"), edge("main", "p2", "spawn"),
                 edge("p1", "c1"), edge("p2", "c2")]
        out = layout(run_of(agents, edges))
        y = {n["id"]: n["y"] for n in out["nodes"]}
        self.assertEqual(y["p1"], y["c1"])
        self.assertEqual(y["p2"], y["c2"])
        self.assertEqual(out["crossings"], 0)

    def test_crossings_are_untangled(self):
        # Start order is the worst one for the dependencies; the sweeps must fix it.
        agents = [agent("p1", 0), agent("p2", 1), agent("c2", 2), agent("c1", 3)]
        edges = [edge("main", "p1", "spawn"), edge("main", "p2", "spawn"),
                 edge("p1", "c1"), edge("p2", "c2")]
        self.assertEqual(layout(run_of(agents, edges))["crossings"], 0)

    def test_a_cycle_terminates_and_every_node_stays_inside_the_canvas(self):
        out = layout(run_of([agent("a"), agent("b")], [edge("a", "b"), edge("b", "a")]))
        self.assertTrue(all(0 <= n["x"] <= out["width"] and 0 <= n["y"] <= out["height"]
                            for n in out["nodes"]))

    def test_critical_path_prefers_the_longest_chain_by_duration(self):
        agents = [agent("a", 0, 10), agent("b", 10, 20), agent("c", 0, 25)]
        out = layout(run_of(agents, [edge("main", "a", "spawn"), edge("main", "c", "spawn"),
                                     edge("a", "b")]))
        self.assertEqual(set(out["critical"]), {"main", "a", "b"})

    def test_empty_run(self):
        out = layout(run_of([], []))
        self.assertEqual([n["id"] for n in out["nodes"]], ["main"])

    def test_two_hundred_agents_lay_out_quickly(self):
        agents = [agent("a%d" % i, i) for i in range(200)]
        edges = [edge("main", "a0", "spawn")] + [edge("a%d" % i, "a%d" % (i + 1)) for i in range(199)]
        edges += [edge("a%d" % i, "a%d" % (i + 5)) for i in range(0, 190, 3)]
        t0 = time.time()
        out = layout(run_of(agents, edges))
        self.assertEqual(len(out["nodes"]), 201)
        self.assertLess(time.time() - t0, 10)          # includes node start-up

    def test_the_real_demo_run_has_few_crossings(self):
        root = tempfile.mkdtemp()
        now = time.time()
        paths, _ = demo.build_demo(root, now=now)
        built = RunBuilder(paths, now_fn=lambda: now).refresh().to_summary_dict()
        out = layout({"agents": built["agents"], "edges": built["edges"]})
        self.assertLessEqual(out["crossings"], 12)


if __name__ == "__main__":
    unittest.main()
