"""Your prompts, and what each one set off.

The main transcript is split at each prompt you typed (or accepted, or queued). Claude Code
writes a prompt as a ``user`` entry carrying ``promptId`` and ``promptSource``; the same type
also carries tool results, injected reminders and agent-completion notifications, which are
not prompts. A ``system`` entry with subtype ``turn_duration`` closes each model turn.

Per prompt TurnLog keeps the main session's API usage (once per ``message.id``), its tool-call
spans, its successful file edits and the latest entry time. orchestra.insights adds what the
rest of the run already knows, matched by time: the agents launched in the window, commits,
and the prompts you answered meanwhile.
"""

import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

from orchestra.edges import normalize_path
from orchestra.parent import parse_timestamp
from orchestra.redact import scrub

MAX_TURNS = 1000
MAX_PROMPT = 500
SHOWN = 200
MAX_NAMES = 20
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_NOT_PROMPTS = ("<local-command", "<system-reminder>", "<task-notification", "<bash-input>",
                "<bash-stdout>", "<bash-stderr>", "Caveat:")
_COMMAND_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S)
_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S)


def prompt_of(entry: Dict[str, Any]) -> Optional[str]:
    """The prompt's text when ``entry`` is something you sent, else None."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = " ".join(b.get("text") or "" for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return None
    text = content.strip()
    if not text or text.startswith(_NOT_PROMPTS):
        return None
    command = _COMMAND_RE.search(text)
    if command:                                  # a slash command: shown as you typed it
        args = _ARGS_RE.search(text)
        text = command.group(1) + (" " + args.group(1) if args and args.group(1) else "")
    text = scrub(" ".join(text.split()))
    return text if len(text) <= MAX_PROMPT else text[:MAX_PROMPT - 1].rstrip() + "…"


def _new_turn(at: float, text: str, source: str) -> Dict[str, Any]:
    return {"at": at, "prompt": text, "source": source, "last_at": at, "record_at": None,
            "local": False, "usage": {}, "spans": [], "edits": set()}


class TurnLog:
    def __init__(self) -> None:
        self.turns: List[Dict[str, Any]] = []
        self._calls: Dict[str, Tuple[int, str, Dict[str, int]]] = {}
        self._open: Dict[str, Tuple[int, float, str, str]] = {}

    def ingest(self, entries: List[Any]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            text = prompt_of(entry)
            if text is not None and at is not None:
                if len(self.turns) < MAX_TURNS:
                    self.turns.append(_new_turn(at, text, str(entry.get("promptSource") or "")))
                continue
            if not self.turns:
                continue                         # before the first prompt: nothing to attribute it to
            index = len(self.turns) - 1
            turn = self.turns[index]
            if at is not None and at > turn["last_at"]:
                turn["last_at"] = at
            if entry.get("type") == "system" and entry.get("subtype") == "turn_duration" and at is not None:
                turn["record_at"] = at
            if entry.get("type") == "system" and entry.get("subtype") == "local_command":
                turn["local"] = True             # /plugin, /model and the like: Claude Code ran it, the model did not
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            if entry.get("type") == "assistant":
                self._usage(message, index)
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str) and at is not None:
                    params = block.get("input") if isinstance(block.get("input"), dict) else {}
                    path = params.get("file_path") or params.get("notebook_path") or ""
                    self._open[block["id"]] = (index, at, str(block.get("name") or ""), str(path))
                elif block.get("type") == "tool_result":
                    opened = self._open.pop(block.get("tool_use_id"), None)
                    if opened is None or at is None:
                        continue
                    t_index, start, name, path = opened
                    self.turns[t_index]["spans"].append((start, max(start, at)))
                    if name in EDIT_TOOLS and path and not block.get("is_error"):
                        self.turns[t_index]["edits"].add(normalize_path(path))

    def _usage(self, message: Dict[str, Any], index: int) -> None:
        usage = message.get("usage")
        mid = message.get("id")
        if not isinstance(usage, dict) or not isinstance(mid, str) or not mid:
            return
        model = message.get("model") if isinstance(message.get("model"), str) else ""
        counts = {}
        for source, label in (("input_tokens", "input"), ("output_tokens", "output"),
                              ("cache_read_input_tokens", "cache_read"),
                              ("cache_creation_input_tokens", "cache_create")):
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                counts[label] = value
        previous = self._calls.get(mid)
        if previous is not None:                 # the same API message again: latest usage wins
            index = previous[0]
            model = model if model and model != "<synthetic>" else previous[1]
        self._calls[mid] = (index, model, counts)

    def usage_by_turn(self) -> List[Dict[str, Dict[str, int]]]:
        out: List[Dict[str, Dict[str, int]]] = [{} for _ in self.turns]
        for index, model, counts in self._calls.values():
            if index >= len(out):
                continue
            bucket = out[index].setdefault(model or "", {})
            for kind, value in counts.items():
                bucket[kind] = bucket.get(kind, 0) + value
        return out

    def snapshot(self) -> "TurnLog":
        copy = TurnLog()
        copy.turns = [dict(t, spans=list(t["spans"]), edits=set(t["edits"])) for t in self.turns]
        copy._calls = dict(self._calls)
        return copy


# -- interval arithmetic for the time split ----------------------------------

def _union(spans: List[Tuple[float, float]], lo: float, hi: float) -> List[Tuple[float, float]]:
    clipped = sorted((max(lo, a), min(hi, b)) for a, b in spans if min(hi, b) > max(lo, a))
    out: List[Tuple[float, float]] = []
    for a, b in clipped:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _minus(spans: List[Tuple[float, float]], cut: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    out = []
    for a, b in spans:
        pieces = [(a, b)]
        for c, d in cut:
            pieces = [p for x, y in pieces for p in ((x, min(y, c)), (max(x, d), y)) if p[1] > p[0]]
        out.extend(pieces)
    return out


def _length(spans: List[Tuple[float, float]]) -> float:
    return sum(b - a for a, b in spans)


def _fresh(tokens: Dict[str, int]) -> int:
    return tokens.get("input", 0) + tokens.get("output", 0) + tokens.get("cache_create", 0)


def summary(log: Optional[TurnLog], run: Any, table: Any, now: float) -> Optional[Dict[str, Any]]:
    """One row per prompt with how long it took, where the time went and what it set off."""
    if log is None or not log.turns:
        return None
    usage = log.usage_by_turn()
    commits: Dict[str, Dict[str, Any]] = {}
    for source in [getattr(run, "main_outcomes", None)] + [a.outcomes for a in run.agents]:
        for key, c in (getattr(source, "commits", None) or {}).items():
            if c.get("at") is not None and (key not in commits or c["at"] < commits[key]["at"]):
                commits[key] = c
    waits = [(w.start, w.start + w.seconds(now)) for w in run.waits if w.state != "unanswered"]
    rows = []
    turns = log.turns
    for i, turn in enumerate(turns):
        start = turn["at"]
        nxt = turns[i + 1]["at"] if i + 1 < len(turns) else None
        window_end = nxt if nxt is not None else float("inf")
        agents = [a for a in run.agents if a.started_at is not None and start <= a.started_at < window_end]
        ends = [turn["last_at"]] + ([turn["record_at"]] if turn["record_at"] else [])
        running = False
        for a in agents:
            if a.ended_at is not None:
                ends.append(a.ended_at)
            else:
                running = True
        if nxt is None and running and run.session_live:
            end = now
        else:
            end = max(ends)
            if nxt is not None:
                end = min(end, nxt)
        end = max(end, start)
        ongoing = nxt is None and run.session_live and (running or turn["record_at"] is None)
        idle = not usage[i] and not turn["spans"] and not agents and turn["record_at"] is None
        if idle and (turn["local"] or not ongoing):
            continue                             # a local command (/plugin, /compact): no turn ran
        waited = _union(waits, start, end)
        agent_spans = [(a.started_at, a.ended_at if a.ended_at is not None else end) for a in agents]
        busy = _minus(_union(agent_spans + turn["spans"], start, end), waited)
        duration = end - start
        you, work = _length(waited), _length(busy)
        tokens_by_model = {m: dict(t) for m, t in usage[i].items()}
        for a in agents:
            for m, t in (a.tokens_by_model or {}).items():
                bucket = tokens_by_model.setdefault(m, {})
                for kind, value in t.items():
                    bucket[kind] = bucket.get(kind, 0) + value
        cost = None
        if table is not None:
            cost, _ = table.cost(tokens_by_model)
        made = sorted((c for c in commits.values() if start <= c["at"] < window_end), key=lambda c: c["at"])
        files = set(turn["edits"])
        for a in agents:
            if a.changes is not None:
                files.update(normalize_path(p) for p, f in a.changes.files.items() if not f.scratch)
        rows.append({
            "n": i + 1, "at": start, "prompt": turn["prompt"], "source": turn["source"],
            "seconds": duration, "ongoing": bool(ongoing),
            "split": {"you": you, "work": work, "claude": max(0.0, duration - you - work)},
            "agents": len(agents), "agent_ids": [a.agent_id for a in agents][:12],
            "tokens": sum(_fresh(t) for t in tokens_by_model.values()), "cost": cost,
            "files": len(files),
            "file_names": sorted(scrub(f) for f in files)[:MAX_NAMES],
            "commits": len(made),
            "commit_list": [{"sha": scrub(str(c.get("sha") or ""))[:12], "message": scrub(str(c.get("message") or ""))[:120]}
                            for c in made[:5]],
        })
    if not rows:
        return None                              # only local commands: no turn ran
    durations = [r["seconds"] for r in rows if not r["ongoing"]]
    costs = [r["cost"] for r in rows if r["cost"] is not None]
    total = sum(costs) if costs else sum(r["tokens"] for r in rows)
    key = "cost" if costs else "tokens"
    top = max(rows, key=lambda r: r[key] or 0)
    return {"prompts": len(rows), "median_s": statistics.median(durations) if durations else None,
            "currency": table.currency if table is not None else None,
            "top": {"n": top["n"], "prompt": top["prompt"], "value": top[key],
                    "share": (top[key] / total) if total else None, "by": key},
            "rows": rows[-SHOWN:]}
