"""Context pressure: how full each context got, and every compaction.

The context a model saw on an API call is its input plus the cache read plus the cache write
for that call (each counted once per ``message.id``; orchestra.waste.WasteLog keeps them).
Claude Code compacts a context when it nears the model's window, or when you run /compact,
and writes a ``compact_boundary`` entry with the trigger, the tokens before and after, and
how long it took. A context near its window is about to lose detail to a compaction.

Transcripts do not record a model's window. The defaults match what real sessions show
(Sonnet and Opus contexts past 700k, so 1M; Haiku 200k) and ORCHESTRA_CONTEXT_LIMITS
overrides them: ``haiku=200000,opus=1000000`` (a case-insensitive part of the model name).
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from orchestra.redact import scrub

DEFAULT_LIMITS: Tuple[Tuple[str, int], ...] = (("haiku", 200_000),)
DEFAULT_LIMIT = 1_000_000
NEAR = 0.8                 # this full is "near its context limit"
MAX_POINTS = 240           # the main session's chart, thinned to this many points
SHOWN_AGENTS = 10


def _parse(raw: str) -> List[Tuple[str, int]]:
    out = []
    for part in (raw or "").split(","):
        name, _, value = part.partition("=")
        name = name.strip().lower()
        try:
            limit = int(value.strip())
        except ValueError:
            continue
        if name and limit > 0:
            out.append((name, limit))
    return out


def limit_for(model: str, overrides: Optional[str] = None) -> int:
    """The assumed context window for a model: an override first, then the defaults."""
    name = (model or "").lower()
    raw = os.environ.get("ORCHESTRA_CONTEXT_LIMITS", "") if overrides is None else overrides
    for part, limit in _parse(raw) + list(DEFAULT_LIMITS):
        if part in name:
            return limit
    return DEFAULT_LIMIT


def calls_of(log: Any) -> List[Tuple[float, int, str]]:
    """(time, context tokens, model) per API call, in time order; calls with no tokens skipped."""
    if log is None:
        return []
    out = []
    for call in log.ordered_calls():
        tokens = call[2] + call[3] + (call[4] if len(call) > 4 else 0)
        if tokens > 0:
            out.append((call[0], tokens, call[1]))
    return out


def series(log: Any) -> List[Tuple[float, int, str]]:
    """The calls plus, at each compaction, the size Claude Code recorded just before it (a
    /compact after a quiet spell can come long after the last call), in time order."""
    calls = calls_of(log)
    out = list(calls)
    for c in (log.compactions if log is not None else []):
        if c["pre_tokens"]:
            before = [p for p in calls if p[0] <= c["at"]]
            model = before[-1][2] if before else (calls[0][2] if calls else "")
            out.append((c["at"], c["pre_tokens"], model))
    out.sort(key=lambda p: p[0])
    return out


def peak_of(log: Any) -> Optional[Dict[str, Any]]:
    """The fullest the context got (tokens, model, its assumed window, the fill) and how full
    it is on the latest call."""
    points = series(log)
    calls = calls_of(log)
    if not points:
        return None
    at, tokens, model = max(points, key=lambda p: p[1])
    limit = limit_for(model)
    last = calls[-1] if calls else points[-1]
    return {"tokens": tokens, "at": at, "model": model, "limit": limit, "fill": tokens / limit,
            "now": last[1], "now_fill": last[1] / limit_for(last[2])}


def _thin(points: List[Tuple[float, int, str]]) -> List[List[float]]:
    """At most MAX_POINTS points, keeping each bucket's highest so peaks survive."""
    if len(points) <= MAX_POINTS:
        return [[p[0], p[1]] for p in points]
    size = len(points) / MAX_POINTS
    out = []
    for i in range(MAX_POINTS):
        bucket = points[int(i * size):int((i + 1) * size)] or [points[-1]]
        best = max(bucket, key=lambda p: p[1])
        out.append([best[0], best[1]])
    return out


def summary(run: Any) -> Optional[Dict[str, Any]]:
    """The main session's curve, compactions and peak; agents ranked by peak; who is near
    the limit right now (live sessions only). None when no call recorded any tokens."""
    main_log = getattr(run, "main_waste", None)
    main_points = series(main_log)
    main_peak = peak_of(main_log)
    agents = []
    for a in run.agents:
        peak = peak_of(a.waste)
        if peak is not None:
            agents.append({"agent_id": a.agent_id, "label": scrub(a.description)[:60] or a.agent_id[:12],
                           "status": a.status, "tokens": peak["tokens"], "limit": peak["limit"],
                           "fill": peak["fill"], "now": peak["now"], "now_fill": peak["now_fill"],
                           "compactions": len(a.waste.compactions)})
    if main_peak is None and not agents:
        return None
    compactions = []
    for c in (main_log.compactions if main_log is not None else []):
        after = c["post_tokens"]
        if after is None:                          # older versions: the next call's context
            later = [p for p in calls_of(main_log) if p[0] > c["at"]]
            after = later[0][1] if later else None
        compactions.append(dict(c, post_tokens=after))
    near = []
    if run.session_live:
        if main_peak is not None and main_peak["now_fill"] >= NEAR:
            near.append({"agent_id": "", "label": "Main session", "fill": main_peak["now_fill"],
                         "tokens": main_peak["now"]})
        for a in agents:
            if a["status"] in ("running", "waiting", "stalled") and a["now_fill"] >= NEAR:
                near.append({"agent_id": a["agent_id"], "label": a["label"], "fill": a["now_fill"],
                             "tokens": a["now"]})
    agents.sort(key=lambda a: -a["tokens"])
    return {"main": dict(main_peak, points=_thin(main_points), compactions=compactions) if main_peak else None,
            "agents": agents[:SHOWN_AGENTS], "agent_count": len(agents),
            "agent_compactions": sum(a["compactions"] for a in agents),
            "near": near, "near_at": NEAR}
