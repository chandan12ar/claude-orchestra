"""Find an agent, a tool call or a file in the current run.

"Which agent touched checkout.ts?" is the question a long run raises most. The
search matches against the SCRUBBED text, never the raw transcript: matching raw
text and scrubbing only the output would let a caller probe a redacted secret one
character at a time. Results are capped, so a one-letter query cannot return a run.
"""

from typing import Any, Dict, List

from orchestra.model import Run
from orchestra.redact import scrub

MIN_QUERY = 2
MAX_QUERY = 200
MAX_SCANNED_CALLS = 20000
LIMITS = {"agents": 12, "tools": 30, "files": 15}


def _terms(query: str) -> List[str]:
    return [t for t in (query or "").lower().split() if t][:8]


def _hit(terms: List[str], *fields: str) -> bool:
    haystack = " ".join(fields).lower()
    return all(t in haystack for t in terms)


def search(run: Run, query: str) -> Dict[str, Any]:
    query = (query or "").strip()[:MAX_QUERY]
    out: Dict[str, Any] = {"query": query, "agents": [], "tools": [], "files": [],
                           "truncated": False}
    terms = _terms(query)
    if len(query) < MIN_QUERY or not terms:
        return out

    for agent in run.agents:
        text = [scrub(agent.description), scrub(agent.objective.text), agent.agent_id,
                agent.agent_type, agent.model, agent.status]
        if _hit(terms, *text):
            if len(out["agents"]) < LIMITS["agents"]:
                out["agents"].append({"agent_id": agent.agent_id,
                                      "description": scrub(agent.description),
                                      "status": agent.status, "model": agent.model})
            else:
                out["truncated"] = True

    # An agent that repeats one call (a loop, or polling) would otherwise fill the
    # results with copies: identical calls by one agent collapse into one row
    # carrying a count and the time of the latest.
    scanned = 0
    merged: Dict[Any, Dict[str, Any]] = {}
    for agent in run.agents:
        for call in reversed(agent.tool_calls):          # newest first
            scanned += 1
            if scanned > MAX_SCANNED_CALLS:
                out["truncated"] = True
                break
            target = scrub(call.target)
            if not _hit(terms, call.name, target, scrub(agent.description)):
                continue
            key = (agent.agent_id, call.name, target)
            if key in merged:
                merged[key]["count"] += 1
                continue
            merged[key] = {"agent_id": agent.agent_id, "description": scrub(agent.description),
                           "tool": call.name, "target": target, "timestamp": call.timestamp,
                           "count": 1}
    rows = sorted(merged.values(), key=lambda t: -(t["timestamp"] or 0))
    if len(rows) > LIMITS["tools"]:
        out["truncated"] = True
    out["tools"] = rows[:LIMITS["tools"]]

    files: Dict[str, Dict[str, Any]] = {}
    for agent in run.agents:
        for role, paths in (("writers", agent.files_written), ("readers", agent.files_read)):
            for path in paths:
                clean = scrub(path)
                if _hit(terms, clean):
                    slot = files.setdefault(clean, {"path": clean, "writers": [], "readers": []})
                    slot[role].append(agent.agent_id)
    rows = sorted(files.values(), key=lambda f: (-(len(f["writers"]) + len(f["readers"])), f["path"]))
    if len(rows) > LIMITS["files"]:
        out["truncated"] = True
    out["files"] = rows[:LIMITS["files"]]
    return out
