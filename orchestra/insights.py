"""Run-level analytics: what a person asks once the run is over (or half over).

The timeline shows *what happened*. These numbers answer *why it took this long
and where the money went*: how parallel the run really was, which chain of agents
set its length, which tools and files dominated, and how well the prompt cache
worked. Everything is derived from the same ``Run`` the dashboard already has, so
it is exact where the data is exact and says nothing where it is not.

Pure function of a Run: no I/O. Output is bounded (every list is capped) and every
string that came from a transcript is scrubbed, like the rest of the payload.
"""

from typing import Any, Dict, List, Optional, Tuple

from orchestra.model import Agent, Run
from orchestra.redact import scrub
from orchestra import verify
from orchestra.edges import normalize_path
from orchestra import outcomes
from orchestra import context
from orchestra import waste
from orchestra import turns

# Mirrors the dashboard's tool taxonomy (app.js TOOL_BUCKETS), so a colour means the
# same thing in the drawer, the ticker and here.
_BUCKETS = (
    ("Read", ("Read", "Grep", "Glob", "NotebookRead", "WebFetch", "WebSearch")),
    ("Edit", ("Edit", "Write", "NotebookEdit")),
    ("Bash", ("Bash", "PowerShell")),
    ("Task", ("Task", "Agent")),
)
# The same exact-evidence kinds the graph ranks by; an inferred edge never decides
# what counts as the critical path.
_EXACT_EDGE_KINDS = ("spawn", "artifact", "message")

MAX_SERIES = 400
MAX_CHAIN = 12
TOP = 8
PULSE_BUCKETS = 48        # points in each live chart
PULSE_MARKERS = 40        # start / finish / failure marks kept for the event strip
PULSE_RATE_S = 60         # "per minute" readouts compare this window with the one before it
WAIT_RECENT = 12          # waits listed one by one, newest first


def bucket_of(tool: str) -> str:
    for label, names in _BUCKETS:
        if tool in names:
            return label
    return "Other"


def _intervals(agent: Agent, now: float) -> List[Tuple[float, float]]:
    """The spans an agent was actually running; an open round runs until `now`."""
    out = []
    for r in agent.rounds:
        if r.started_at is None:
            continue
        end = r.ended_at if r.ended_at is not None else now
        if end >= r.started_at:
            out.append((r.started_at, end))
    return out


def _parallelism(run: Run, now: float) -> Optional[Dict[str, Any]]:
    events: List[Tuple[float, int]] = []
    busy = 0.0
    for agent in run.agents:
        for start, end in _intervals(agent, now):
            events.append((start, 1))
            events.append((end, -1))
            busy += end - start
    if not events:
        return None
    # Ends sort before starts at the same instant, so back-to-back agents do not
    # read as one extra concurrent agent.
    events.sort(key=lambda e: (e[0], e[1]))
    t0, t1 = events[0][0], events[-1][0]
    wall = max(t1 - t0, 0.0)
    series: List[List[float]] = []
    level = peak = 0
    idle = solo = 0.0
    prev_t = t0
    for t, delta in events:
        span = t - prev_t
        if level == 0:
            idle += span
        elif level == 1:
            solo += span
        prev_t = t
        level += delta
        peak = max(peak, level)
        if series and series[-1][0] == t:
            series[-1][1] = level
        else:
            series.append([t, level])
    if len(series) > MAX_SERIES:               # keep the shape, drop the detail
        step = len(series) / MAX_SERIES
        series = [series[int(i * step)] for i in range(MAX_SERIES)] + [series[-1]]
    return {"peak": peak,
            "average": (busy / wall) if wall > 0 else float(peak),
            "busy_s": busy, "wall_s": wall,
            "idle_s": idle, "solo_s": solo,
            "solo_share": (solo / wall) if wall > 0 else 0.0,
            "series": series, "start": t0, "end": t1}


def _duration(agent: Agent, now: float) -> float:
    return sum(end - start for start, end in _intervals(agent, now))


def _critical_path(run: Run, now: float) -> Optional[Dict[str, Any]]:
    by_id = {a.agent_id: a for a in run.agents}
    spans: Dict[str, Tuple[float, float]] = {}
    for agent in run.agents:
        iv = _intervals(agent, now)
        if iv:
            spans[agent.agent_id] = (min(s for s, _ in iv), max(e for _, e in iv))
    if not spans:
        return None
    preds: Dict[str, List[str]] = {}
    for edge in run.edges:
        if edge.kind in _EXACT_EDGE_KINDS and edge.src in spans and edge.dst in spans:
            # Only forward-in-time edges: a stray cycle can never loop the walk.
            if spans[edge.src][0] <= spans[edge.dst][0] and edge.src != edge.dst:
                preds.setdefault(edge.dst, []).append(edge.src)
    order = sorted(spans, key=lambda a: (spans[a][0], spans[a][1]))
    score: Dict[str, float] = {}
    prev: Dict[str, Optional[str]] = {}
    for aid in order:
        start, end = spans[aid]
        best, best_prev = end - start, None
        for p in preds.get(aid, []):
            if p not in score:
                continue
            # Count only the time this agent adds beyond its predecessor's end.
            added = max(0.0, end - max(start, spans[p][1]))
            if score[p] + added > best + 1e-9:
                best, best_prev = score[p] + added, p
        score[aid], prev[aid] = best, best_prev
    tail = max(order, key=lambda a: (score[a], spans[a][1]))
    chain: List[str] = []
    cur: Optional[str] = tail
    while cur is not None and len(chain) < MAX_CHAIN * 4:
        chain.append(cur)
        cur = prev[cur]
    chain.reverse()
    wall = max(e for _, e in spans.values()) - min(s for s, _ in spans.values())
    return {"duration_s": score[tail],
            "share": (score[tail] / wall) if wall > 0 else 0.0,
            "length": len(chain),
            "chain": [{"agent_id": a, "description": scrub(by_id[a].description),
                       "status": by_id[a].status, "start": spans[a][0], "end": spans[a][1],
                       "duration_s": spans[a][1] - spans[a][0]}
                      for a in chain[-MAX_CHAIN:]]}


def _tools(run: Run, now: float) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    agents_using: Dict[str, set] = {}
    buckets: Dict[str, int] = {}
    busiest: Optional[Tuple[int, Agent]] = None
    total = 0
    for agent in run.agents:
        n = len(agent.tool_calls)
        total += n
        if n and (busiest is None or n > busiest[0]):
            busiest = (n, agent)
        for call in agent.tool_calls:
            counts[call.name or "?"] = counts.get(call.name or "?", 0) + 1
            agents_using.setdefault(call.name or "?", set()).add(agent.agent_id)
            b = bucket_of(call.name)
            buckets[b] = buckets.get(b, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]
    stamps = [c.timestamp for a in run.agents for c in a.tool_calls if c.timestamp]
    span_min = ((max(stamps) - min(stamps)) / 60.0) if len(stamps) > 1 else 0.0
    return {"total": total,
            "by_tool": [{"name": scrub(n), "count": c, "agents": len(agents_using[n]),
                         "bucket": bucket_of(n), "share": c / total if total else 0.0}
                        for n, c in ranked],
            "buckets": [{"name": label, "count": buckets[label],
                         "share": buckets[label] / total if total else 0.0}
                        for label in ("Read", "Edit", "Bash", "Task", "Other") if label in buckets],
            "per_minute": (total / span_min) if span_min > 0 else None,
            "busiest": ({"agent_id": busiest[1].agent_id,
                         "description": scrub(busiest[1].description), "calls": busiest[0]}
                        if busiest else None)}


def _fresh(tokens: Dict[str, int]) -> int:
    """Tokens that were actually processed this run, not served from the cache."""
    return (tokens.get("input", 0) + tokens.get("output", 0)
            + tokens.get("cache_create", 0))


def _tokens(run: Run, table: Any) -> Dict[str, Any]:
    kinds: Dict[str, int] = {}
    for agent in run.agents:
        for k, v in agent.tokens.items():
            kinds[k] = kinds.get(k, 0) + v
    orch = (run.orchestrator or {}).get("tokens") or {}
    for k, v in orch.items():
        kinds[k] = kinds.get(k, 0) + v
    read = kinds.get("cache_read", 0)
    denom = kinds.get("input", 0) + read + kinds.get("cache_create", 0)
    fresh_total = sum(_fresh(a.tokens) for a in run.agents)
    top = sorted(run.agents, key=lambda a: -_fresh(a.tokens))[:TOP]
    models: Dict[str, Dict[str, int]] = {}
    for agent in run.agents:
        for model, toks in agent.tokens_by_model.items():
            slot = models.setdefault(model or "(unknown)", {})
            for k, v in toks.items():
                slot[k] = slot.get(k, 0) + v
    orch_model = (run.orchestrator or {}).get("model")
    by_model = []
    for model, toks in models.items():
        cost = table.cost({model: toks})[0] if table is not None and table.price_for(model) else None
        by_model.append({"model": scrub(model), "fresh": _fresh(toks),
                         "cache_read": toks.get("cache_read", 0), "cost": cost})
    by_model.sort(key=lambda m: -m["fresh"])
    return {"by_kind": kinds,
            "cache_hit_ratio": (read / denom) if denom else None,
            "fresh_total": fresh_total,
            "top_agents": [{"agent_id": a.agent_id, "description": scrub(a.description),
                            "fresh": _fresh(a.tokens), "cost": a.cost,
                            "share": (_fresh(a.tokens) / fresh_total) if fresh_total else 0.0}
                           for a in top if _fresh(a.tokens) > 0],
            "by_model": by_model[:TOP],
            "orchestrator": {"model": scrub(orch_model or ""), "fresh": _fresh(orch),
                             "cost": (run.orchestrator or {}).get("cost")} if orch else None}


def _files(run: Run) -> Dict[str, Any]:
    writers: Dict[str, set] = {}
    readers: Dict[str, set] = {}
    for agent in run.agents:
        for path in agent.files_written:
            writers.setdefault(path, set()).add(agent.agent_id)
        for path in agent.files_read:
            readers.setdefault(path, set()).add(agent.agent_id)

    def top(table: Dict[str, set], minimum: int) -> List[Dict[str, Any]]:
        rows = [(p, len(a)) for p, a in table.items() if len(a) >= minimum]
        rows.sort(key=lambda r: (-r[1], r[0]))
        return [{"path": scrub(p), "agents": n} for p, n in rows[:TOP]]

    return {"written": top(writers, 1), "contended": top(writers, 2),
            "read": top(readers, 2),
            "files_written": len(writers), "files_read": len(readers)}


def _slowest(run: Run, now: float) -> List[Dict[str, Any]]:
    rows = [(a, _duration(a, now)) for a in run.agents]
    rows = [r for r in rows if r[1] > 0]
    rows.sort(key=lambda r: -r[1])
    return [{"agent_id": a.agent_id, "description": scrub(a.description),
             "status": a.status, "duration_s": d} for a, d in rows[:5]]


def _pulse(run: Run, now: float, par: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Real time series for the live strip: how many agents run, how many tool calls
    land, how many fresh tokens are spent, and when agents started, finished or failed.

    Every series spans the same window (first agent start to `now` while the session
    is live), so the charts line up and the right edge moves as the run does.
    """
    if par is None:
        return None
    start = par["start"]
    end = max(par["end"], now) if run.session_live else par["end"]
    span = max(end - start, 1.0)
    n = PULSE_BUCKETS

    def slot(t: float) -> int:
        return min(n - 1, int((t - start) / span * n))

    calls = [0] * n
    spent = [0] * n
    calls_last = calls_prev = tokens_last = tokens_prev = 0
    for agent in run.agents:
        for call in agent.tool_calls:
            t = call.timestamp
            if t is None or t < start or t > end:
                continue
            calls[slot(t)] += 1
            if t > now - PULSE_RATE_S:
                calls_last += 1
            elif t > now - 2 * PULSE_RATE_S:
                calls_prev += 1
        for t, added in agent.token_events:
            if t < start or t > end:
                continue
            spent[slot(t)] += added
            if t > now - PULSE_RATE_S:
                tokens_last += added
            elif t > now - 2 * PULSE_RATE_S:
                tokens_prev += added
    total = 0
    cumulative = []
    for added in spent:
        total += added
        cumulative.append(total)

    marks: List[Dict[str, Any]] = []
    for agent in run.agents:
        label = scrub(agent.description)[:60]
        if agent.started_at is not None:
            marks.append({"t": agent.started_at, "kind": "start", "agent_id": agent.agent_id,
                          "label": label})
        if agent.ended_at is not None and agent.status in ("completed", "failed"):
            marks.append({"t": agent.ended_at, "kind": "done" if agent.status == "completed" else "fail",
                          "agent_id": agent.agent_id, "label": label})
        elif agent.status == "stalled" and agent.last_activity_at is not None:
            marks.append({"t": agent.last_activity_at, "kind": "stall", "agent_id": agent.agent_id,
                          "label": label})
    marks.sort(key=lambda m: m["t"])
    return {"start": start, "end": end, "now": now, "live": bool(run.session_live), "buckets": n,
            "calls": calls, "tokens": cumulative, "markers": marks[-PULSE_MARKERS:],
            "rate": {"window_s": PULSE_RATE_S, "calls_last": calls_last, "calls_prev": calls_prev,
                     "tokens_last": tokens_last, "tokens_prev": tokens_prev}}


def _union_s(spans: List[Tuple[float, float]]) -> float:
    """Length of the union of intervals: overlapping waits count once."""
    total = 0.0
    cur_start = cur_end = None
    for start, end in sorted(spans):
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        total += cur_end - cur_start
    return total


def _waits(run: Run, now: float) -> Optional[Dict[str, Any]]:
    """How long agents sat on prompts only the user could answer.

    you_s    wall time at least one agent was waiting (overlaps counted once)
    agent_s  every agent's wait added together
    Unanswered prompts (the session ended or went quiet with one still up) are
    counted but have no length: nothing says how long they would have taken.
    """
    if not run.waits:
        return None
    names = {a.agent_id: scrub(a.description)[:60] for a in run.agents}

    def label(agent_id: str) -> str:
        return names.get(agent_id) or ("Main session" if not agent_id else agent_id[:12])

    spans: List[Tuple[float, float]] = []
    per_agent: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []
    longest = None
    for wait in run.waits:
        seconds = wait.seconds(now)
        if wait.state != "unanswered":
            spans.append((wait.start, wait.start + seconds))
        row = {"agent_id": wait.agent_id, "label": label(wait.agent_id), "kind": wait.kind,
               "state": wait.state, "start": wait.start, "end": wait.end,
               "seconds": seconds, "prompts": wait.prompts,
               "message": scrub(wait.message)[:120]}
        rows.append(row)
        entry = per_agent.setdefault(wait.agent_id, {
            "agent_id": wait.agent_id, "label": row["label"], "seconds": 0.0, "count": 0,
            "open_since": None, "unanswered": 0})
        entry["count"] += 1
        entry["seconds"] += seconds
        if wait.state == "open":
            entry["open_since"] = wait.start
        elif wait.state == "unanswered":
            entry["unanswered"] += 1
        if wait.state != "unanswered" and (longest is None or seconds > longest["seconds"]):
            longest = row
    agents = sorted(per_agent.values(), key=lambda e: (-e["seconds"], e["label"]))
    return {"you_s": _union_s(spans),
            "agent_s": sum(e - s for s, e in spans),
            "count": sum(1 for r in rows if r["state"] != "unanswered"),
            "prompts": sum(w.prompts for w in run.waits),
            "open": sum(1 for r in rows if r["state"] == "open"),
            "unanswered": sum(1 for r in rows if r["state"] == "unanswered"),
            "longest": longest,
            "by_agent": agents[:TOP],
            "recent": rows[::-1][:WAIT_RECENT],
            "now": now,
            "live": bool(run.session_live)}


def _changes(run: Run) -> Optional[Dict[str, Any]]:
    """What the run changed: totals, the agents that changed the most, the files changed most."""
    rows = []
    files: Dict[str, Dict[str, Any]] = {}
    for agent in run.agents:
        log = agent.changes
        if log is None:
            continue
        t = log.totals()
        if not t["files"]:
            continue
        rows.append({"agent_id": agent.agent_id, "label": scrub(agent.description)[:60],
                     "status": agent.status, **t})
        for f in log.files.values():
            if f.scratch:
                continue
            # Worktree copies of one file are the same file.
            entry = files.setdefault(normalize_path(f.path), {"path": f.path, "added": 0, "removed": 0,
                                                             "agents": set(), "created": False})
            entry["added"] += f.added
            entry["removed"] += f.removed
            entry["agents"].add(agent.agent_id)
            entry["created"] = entry["created"] or f.created
    if not rows:
        return None
    rows.sort(key=lambda r: (-(r["added"] + r["removed"]), r["label"]))
    hot = sorted(files.items(), key=lambda kv: (-(kv[1]["added"] + kv[1]["removed"]), kv[0]))
    return {"files": len(files),
            "added": sum(r["added"] for r in rows),
            "removed": sum(r["removed"] for r in rows),
            "created": sum(1 for f in files.values() if f["created"]),
            "by_agent": rows[:TOP],
            "top_files": [{"path": scrub(f["path"]), "added": f["added"], "removed": f["removed"],
                           "agents": len(f["agents"]), "created": f["created"]} for _, f in hot[:TOP]]}


def _outcomes(run: Run) -> Optional[Dict[str, Any]]:
    """Commits, pull requests, pushes and test runs across the main session and every agent,
    with the run's cost (or fresh tokens) per commit and per pull request."""
    sources = [("", "Main session", run.main_outcomes)]
    sources += [(a.agent_id, a.description, a.outcomes) for a in run.agents]
    fresh = sum(_fresh(a.tokens) for a in run.agents)
    if run.orchestrator:
        fresh += _fresh(run.orchestrator.get("tokens") or {})
    return outcomes.summary(sources, run.cost, fresh)


def _waste(run: Run, now: float, table: Any) -> Optional[Dict[str, Any]]:
    """Cache rebuilds, big results and unchanged re-reads across the main session and every
    agent, priced when a price table is set, with the prompts you were answering meanwhile."""
    sources = [("", "Main session", run.main_waste)]
    sources += [(a.agent_id, scrub(a.description)[:60] or a.agent_id[:12], a.waste) for a in run.agents]
    return waste.summary(sources, table, run.waits, now)


def compute(run: Run, now: float, table: Any = None) -> Dict[str, Any]:
    """The Insights payload for a run. `table` is the optional PriceTable."""
    par = _parallelism(run, now)
    return {"parallelism": par,
            "pulse": _pulse(run, now, par),
            "critical_path": _critical_path(run, now),
            "tools": _tools(run, now),
            "tokens": _tokens(run, table),
            "files": _files(run),
            "slowest": _slowest(run, now),
            "waits": _waits(run, now),
            "checks": verify.summary(run.agents) if run.agents else None,
            "changes": _changes(run),
            "outcomes": _outcomes(run),
            "context": context.coverage(run.main_context, run.agents),
            "waste": _waste(run, now, table),
            "prompts": turns.summary(run.main_turns, run, table, now)}
