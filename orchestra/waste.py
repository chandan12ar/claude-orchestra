"""Where tokens were wasted: prompt-cache rebuilds, the biggest things pulled into context,
and re-reads of files that came back unchanged.

Claude Code caches the conversation so each API call re-sends it at the cache-read price.
When the cache expires (after a few minutes idle, or an hour on the longer cache) or is
invalidated (a model switch, a compaction), the next call writes the whole conversation to
the cache again at the cache-write price. That shows in the call's usage as a large
``cache_creation_input_tokens`` with little ``cache_read_input_tokens``. On real transcripts a
third of all cache writes were such rebuilds, most after an hour or more idle.

Per transcript (the main session, or one agent) WasteLog keeps each API call's usage, once per
``message.id`` (Claude Code repeats a message's usage on every content block), compaction
markers, the largest tool results, and Read results whose text matched the previous read of
the same file. orchestra.insights adds prices and the prompts you were answering meanwhile.
"""

import hashlib
import json
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from orchestra.parent import parse_timestamp
from orchestra.redact import scrub

# A call is a rebuild when it writes at least this much to the cache while reading back
# less than REBUILD_READ_SHARE of that.
REBUILD_MIN_TOKENS = 10_000
REBUILD_READ_SHARE = 0.2
IDLE_SHORT_S = 300        # the default prompt cache lives five minutes
IDLE_LONG_S = 3600        # the longer cache lives an hour
BIG_RESULT_CHARS = 20_000
CHARS_PER_TOKEN = 4       # an estimate: tool results are text, and roughly 4 characters a token
MAX_CALLS = 50_000
MAX_BIG = 12
MAX_KEYS = 2_000

IDLE_LONG, IDLE_SHORT, MODEL, COMPACTION, UNKNOWN = "idle_long", "idle_short", "model", "compaction", "unknown"


def _text_of(content: Any) -> Tuple[str, str]:
    """(text the model sees, a stable fingerprint) for a tool_result's content."""
    if isinstance(content, str):
        return content, content
    if isinstance(content, list):
        texts = [c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text"]
        try:
            fingerprint = json.dumps(content, sort_keys=True, default=str)
        except (TypeError, ValueError):
            fingerprint = "".join(texts)
        return "".join(texts), fingerprint
    return "", ""


def _target(name: str, params: Any) -> str:
    if not isinstance(params, dict):
        return ""
    for key in ("file_path", "notebook_path", "path", "url", "command", "pattern", "query"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return scrub(value.splitlines()[0] if value else "")[:160]
    return ""


class WasteLog:
    def __init__(self) -> None:
        # message id -> [first seen at, model, cache_create, cache_read, input, output]; the first
        # three token counts together are the context the model saw on that call
        # (orchestra.pressure), and all four price the call (orchestra.spend).
        self.calls: "OrderedDict[str, List[Any]]" = OrderedDict()
        # Compactions, once each (Claude Code can write the same boundary twice): at, trigger,
        # pre and post tokens, duration.
        self.compactions: List[Dict[str, Any]] = []
        self._compaction_keys: set = set()
        self.big: List[Dict[str, Any]] = []
        self.rereads = 0
        self.reread_chars = 0
        self._tools: Dict[str, Tuple[str, Any]] = {}
        self._last_read: Dict[str, str] = {}

    def ingest(self, entries: List[Any]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary" and at is not None:
                self._compaction(entry, at)
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            if entry.get("type") == "assistant":
                self._call(message, at)
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    if len(self._tools) < MAX_KEYS * 10:
                        self._tools[block["id"]] = (str(block.get("name") or ""), block.get("input"))
                elif block.get("type") == "tool_result":
                    self._result(block, at)

    def _compaction(self, entry: Dict[str, Any], at: float) -> None:
        meta = entry.get("compactMetadata") if isinstance(entry.get("compactMetadata"), dict) else {}
        key = entry.get("uuid") or "{}|{}".format(at, meta.get("preTokens"))
        if key in self._compaction_keys or len(self.compactions) >= 500:
            return
        self._compaction_keys.add(key)

        def number(name: str) -> Optional[int]:
            value = meta.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        trigger = meta.get("trigger")
        self.compactions.append({"at": at, "trigger": trigger if trigger in ("manual", "auto") else "",
                                 "pre_tokens": number("preTokens"), "post_tokens": number("postTokens"),
                                 "duration_s": (number("durationMs") or 0) / 1000.0 or None})

    def _call(self, message: Dict[str, Any], at: Optional[float]) -> None:
        mid, usage = message.get("id"), message.get("usage")
        if not isinstance(mid, str) or not mid or not isinstance(usage, dict):
            return
        model = message.get("model")
        if not isinstance(model, str) or model == "<synthetic>":
            model = ""

        def count(key: str) -> int:
            value = usage.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

        created, read = count("cache_creation_input_tokens"), count("cache_read_input_tokens")
        fresh, output = count("input_tokens"), count("output_tokens")
        if mid in self.calls:                      # the same API message again: latest usage wins
            row = self.calls[mid]
            row[1] = model or row[1]
            row[2], row[3], row[4], row[5] = created, read, fresh, output
        elif len(self.calls) < MAX_CALLS:
            self.calls[mid] = [at, model, created, read, fresh, output]

    def _result(self, block: Dict[str, Any], at: Optional[float]) -> None:
        name, params = self._tools.pop(block.get("tool_use_id"), ("", None))
        text, fingerprint = _text_of(block.get("content"))
        if name == "Read" and isinstance(params, dict) and not block.get("is_error"):
            key = "{}|{}|{}".format(params.get("file_path"), params.get("offset"), params.get("limit"))
            digest = hashlib.sha1(fingerprint.encode("utf-8", "replace")).hexdigest()
            if self._last_read.get(key) == digest:
                self.rereads += 1
                self.reread_chars += len(text)
            elif len(self._last_read) < MAX_KEYS or key in self._last_read:
                self._last_read[key] = digest
        if len(text) >= BIG_RESULT_CHARS:
            self.big.append({"at": at, "tool": name or "tool", "target": _target(name, params), "chars": len(text)})
            self.big.sort(key=lambda b: -b["chars"])
            del self.big[MAX_BIG:]

    # -- reading it back --------------------------------------------------

    def ordered_calls(self) -> List[List[Any]]:
        return sorted((c for c in self.calls.values() if c[0] is not None), key=lambda c: c[0])

    def rebuilds(self) -> List[Dict[str, Any]]:
        out = []
        calls = self.ordered_calls()
        for previous, call in zip(calls, calls[1:]):
            at, model, created, read = call[:4]
            if created < REBUILD_MIN_TOKENS or read >= created * REBUILD_READ_SHARE:
                continue
            gap = at - previous[0]
            if any(previous[0] <= c["at"] <= at for c in self.compactions):
                cause = COMPACTION
            elif model and previous[1] and model != previous[1]:
                cause = MODEL
            elif gap >= IDLE_LONG_S:
                cause = IDLE_LONG
            elif gap >= IDLE_SHORT_S:
                cause = IDLE_SHORT
            else:
                cause = UNKNOWN
            out.append({"at": at, "after": previous[0], "gap_s": gap, "tokens": created, "read": read,
                        "model": model, "cause": cause})
        return out

    def later_calls(self, at: Optional[float]) -> int:
        """API calls after ``at``: each re-sent everything already in context."""
        if at is None:
            return 0
        return sum(1 for c in self.calls.values() if c[0] is not None and c[0] > at)

    def cache_written(self) -> int:
        return sum(c[2] for c in self.calls.values())

    def empty(self) -> bool:
        return not self.calls and not self.big and not self.rereads

    def snapshot(self) -> "WasteLog":
        copy = WasteLog()
        copy.calls = OrderedDict((k, list(v)) for k, v in self.calls.items())
        copy.compactions = [dict(c) for c in self.compactions]
        copy._compaction_keys = set(self._compaction_keys)
        copy.big = [dict(b) for b in self.big]
        copy.rereads, copy.reread_chars = self.rereads, self.reread_chars
        return copy

    def to_dict(self) -> Dict[str, Any]:
        """The agent panel's view: its rebuilds, its biggest results and its re-reads."""
        rebuilds = self.rebuilds()
        return {"rebuilds": rebuilds, "rebuilt_tokens": sum(r["tokens"] for r in rebuilds),
                "big": [dict(b, tokens=b["chars"] // CHARS_PER_TOKEN, carried=self.later_calls(b["at"]))
                        for b in self.big[:5]],
                "rereads": self.rereads, "reread_tokens": self.reread_chars // CHARS_PER_TOKEN}


def _overlapping_wait(waits: List[Any], agent_id: str, start: float, end: float, now: float) -> Optional[Any]:
    """The prompt of this agent that covered most of the idle stretch, if any."""
    best, best_overlap = None, 0.0
    for wait in waits:
        if getattr(wait, "agent_id", None) != agent_id or wait.state == "unanswered":
            continue
        w_end = wait.start + wait.seconds(now)
        overlap = min(end, w_end) - max(start, wait.start)
        if overlap > best_overlap:
            best, best_overlap = wait, overlap
    return best


def summary(sources: List[Tuple[str, str, Optional[WasteLog]]], table: Any, waits: List[Any],
            now: float) -> Optional[Dict[str, Any]]:
    """Rebuilds across the main session and every agent, with the extra they cost above the
    cache-read price; the biggest results; re-reads. None when nothing was recorded."""
    rows: List[Dict[str, Any]] = []
    big: List[Dict[str, Any]] = []
    rereads = reread_chars = written = 0
    priced = table is not None
    unpriced = False
    for agent_id, label, log in sources:
        if log is None or log.empty():
            continue
        written += log.cache_written()
        rereads += log.rereads
        reread_chars += log.reread_chars
        for b in log.big:
            big.append(dict(b, agent_id=agent_id, label=label, tokens=b["chars"] // CHARS_PER_TOKEN,
                            carried=log.later_calls(b["at"])))
        for r in log.rebuilds():
            extra = None
            if priced:
                prices = table.price_for(r["model"])
                if prices is None:
                    unpriced = True
                else:
                    extra = r["tokens"] * max(0.0, prices["cache_create"] - prices["cache_read"]) / 1_000_000
            wait = _overlapping_wait(waits, agent_id, r["after"], r["at"], now)
            rows.append(dict(r, agent_id=agent_id, label=label, extra_cost=extra,
                             wait_s=wait.seconds(now) if wait is not None else None,
                             wait_kind=wait.kind if wait is not None else None))
    if not rows and not big and not rereads:
        return None
    rows.sort(key=lambda r: -r["tokens"])
    rebuilt = sum(r["tokens"] for r in rows)
    causes: Dict[str, int] = {}
    for r in rows:
        causes[r["cause"]] = causes.get(r["cause"], 0) + r["tokens"]
    costs = [r["extra_cost"] for r in rows if r["extra_cost"] is not None]
    big.sort(key=lambda b: -b["chars"])
    return {"rebuilds": len(rows), "rebuilt_tokens": rebuilt,
            "share_of_cache_writes": (rebuilt / written) if written else None,
            "extra_cost": sum(costs) if costs else (0.0 if priced and not rows else None),
            "currency": table.currency if priced else None, "unpriced": unpriced,
            "by_cause": causes, "while_waiting": sum(1 for r in rows if r["wait_s"]),
            "rows": rows[:12], "big": big[:5],
            "rereads": rereads, "reread_tokens": reread_chars // CHARS_PER_TOKEN}
