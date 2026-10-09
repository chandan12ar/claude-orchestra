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


ROUTE_FNS = FNS + ("graphRoute", "graphPath")
ROUTE_CONSTS = CONSTS + ("GRAPH_LANE_PAD",)

# Samples each drawn segment exactly as graphPath draws it (a cubic with flat tangents at both
# ends) and reports any sample inside a node that is not one of the edge's own two.
ROUTE_CHECK = """
function cubic(p, q, t) {
  const dx = (q.x >= p.x ? 1 : -1) * Math.max(12, Math.min(Math.abs(q.x - p.x) / 2, 160));
  const c1 = {x: p.x + dx, y: p.y}, c2 = {x: q.x - dx, y: q.y};
  const u = 1 - t;
  return {x: u*u*u*p.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*q.x,
          y: u*u*u*p.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*q.y};
}
const hits = [];
const routes = [];
for (const e of L.edges) {
  const pts = graphRoute(L, e);
  routes.push({src: e.src, dst: e.dst, points: pts.length, d: graphPath(pts)});
  for (let s = 0; s + 1 < pts.length; s++) {
    for (let k = 1; k < 40; k++) {
      const p = cubic(pts[s], pts[s + 1], k / 40);
      for (const n of L.nodes) {
        if (n.id === e.src || n.id === e.dst) continue;
        if (p.x > n.x + 2 && p.x < n.x + NODE_W - 2 && p.y > n.y + 2 && p.y < n.y + NODE_H - 2) {
          hits.push(e.src + ">" + e.dst + " through " + n.id);
          break;
        }
      }
    }
  }
}
console.log(JSON.stringify({hits: [...new Set(hits)], routes}));
"""


def routed(run):
    js = read("app.js")
    prelude = "\n".join([SETUP] + [const(js, c) for c in ROUTE_CONSTS] + [fn(js, n) for n in ROUTE_FNS])
    program = prelude + "\nconst L = layoutGraph(%s);\n" % json.dumps(run) + ROUTE_CHECK
    path = os.path.join(tempfile.mkdtemp(), "r.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(program)
    proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-1500:])
    return json.loads(proc.stdout)


@unittest.skipIf(NODE is None, "node is not on PATH")
class TestEdgeRouting(unittest.TestCase):
    """An edge that skips columns must not run through the nodes it skips."""

    # a -> b -> c, with x also in b's column, and a long edge a -> c that a straight
    # curve would draw straight through b.
    BLOCKED = run_of([agent("a", 0), agent("b", 1), agent("c", 2)],
                     [edge("main", "a", "spawn"), edge("a", "b"), edge("b", "c"), edge("a", "c", "message")])

    def test_a_blocked_long_edge_bends_around(self):
        out = routed(self.BLOCKED)
        self.assertEqual(out["hits"], [])
        long_edge = next(r for r in out["routes"] if (r["src"], r["dst"]) == ("a", "c"))
        self.assertGreater(long_edge["points"], 2)

    def test_edges_between_neighbouring_columns_are_drawn_as_before(self):
        out = routed(self.BLOCKED)
        short = next(r for r in out["routes"] if (r["src"], r["dst"]) == ("a", "b"))
        self.assertEqual(short["points"], 2)
        # The same single curve as before routing existed: bend = half the gap, 36..160.
        self.assertRegex(short["d"], r"^M[\d.]+,[\d.]+ C[\d.]+,[\d.]+ [\d.]+,[\d.]+ [\d.]+,[\d.]+$")

    def test_a_clear_long_edge_keeps_one_curve(self):
        # Placed by hand: a and c on the top row, b two rows down in the column between.
        def points(b_y):
            js = read("app.js")
            prelude = "\n".join([SETUP] + [const(js, c) for c in ROUTE_CONSTS] + [fn(js, n) for n in ROUTE_FNS])
            program = prelude + """
const col = NODE_W + COL_GAP;
const nodes = [{id: "a", x: 20, y: 20}, {id: "b", x: 20 + col, y: %d}, {id: "c", x: 20 + 2 * col, y: 20}];
const L = {nodes, byId: {a: nodes[0], b: nodes[1], c: nodes[2]}, height: 400};
console.log(JSON.stringify(graphRoute(L, {src: "a", dst: "c"}).length));
""" % b_y
            path = os.path.join(tempfile.mkdtemp(), "p.js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(program)
            proc = subprocess.run([NODE, path], capture_output=True, encoding="utf-8", timeout=60)
            if proc.returncode != 0:
                raise AssertionError(proc.stderr[-1500:])
            return json.loads(proc.stdout)
        self.assertEqual(points(200), 2)       # nothing in the way: the same single curve
        self.assertGreater(points(20), 2)      # b in the way: around it

    def test_an_edge_back_to_an_earlier_column_leaves_from_the_left(self):
        # x is ranked after y by its exact edges, then hands back to y (an inferred handoff).
        run = run_of([agent("y", 0), agent("m", 1), agent("x", 2)],
                     [edge("main", "y", "spawn"), edge("y", "m"), edge("m", "x"),
                      edge("x", "y", "handoff", "inferred")])
        out = routed(run)
        self.assertEqual(out["hits"], [])
        back = next(r for r in out["routes"] if (r["src"], r["dst"]) == ("x", "y"))
        nodes = {n["id"]: n for n in layout(run)["nodes"]}
        start = float(back["d"][1:].split(",")[0])
        self.assertEqual(start, nodes["x"]["x"])                      # x's left side
        end_x = float(back["d"].split(" ")[-1].split(",")[0])
        self.assertEqual(end_x, nodes["y"]["x"] + 210 + 6)             # y's right side, short of the arrow

    def test_no_edge_in_the_demo_runs_through_a_node(self):
        root = tempfile.mkdtemp()
        now = time.time()
        paths, _ = demo.build_demo(root, now=now)
        built = RunBuilder(paths, now_fn=lambda: now).refresh().to_summary_dict()
        out = routed({"agents": built["agents"], "edges": built["edges"]})
        self.assertEqual(out["hits"], [])

    def test_many_long_edges_still_route_quickly_and_cleanly(self):
        agents = [agent("a%d" % i, i) for i in range(60)]
        edges = [edge("main", "a0", "spawn")] + [edge("a%d" % i, "a%d" % (i + 1)) for i in range(59)]
        edges += [edge("a%d" % i, "a%d" % (i + 4), "message") for i in range(0, 55, 2)]
        t0 = time.time()
        out = routed(run_of(agents, edges))
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(out["hits"], [])


if __name__ == "__main__":
    unittest.main()
