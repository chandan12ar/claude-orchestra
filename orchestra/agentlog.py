"""Digesting a subagent's own transcript into the numbers the dashboard shows."""

import bisect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from orchestra.model import ToolCall
from orchestra.verify import classify
from orchestra.changes import EDIT_TOOLS, ChangeLog, extract
from orchestra.outcomes import OutcomeLog
from orchestra.waste import WasteLog
from orchestra.errors import ErrorLog
from orchestra.context import ContextLog


def _result_for(entry: Dict[str, Any]) -> Any:
    """The entry's toolUseResult, only when it can belong to one result: an entry
    carrying several tool results has a single toolUseResult that cannot be told apart."""
    results = [b for b in content_blocks(entry) if b.get("type") == "tool_result"]
    return entry.get("toolUseResult") if len(results) == 1 else None
from orchestra.parent import content_blocks, parse_timestamp

_MAX_TARGET = 120

# Which input field best describes what a tool call acted on.
_TARGET_FIELDS = {
    "Read": "file_path", "Write": "file_path", "Edit": "file_path",
    "NotebookEdit": "notebook_path", "Glob": "pattern", "Grep": "pattern",
    "Bash": "command", "PowerShell": "command", "Agent": "description",
    "Task": "description", "SendMessage": "to", "Skill": "skill",
    "WebFetch": "url", "WebSearch": "query",
}

_WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path",
                "NotebookEdit": "notebook_path"}
_READ_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path"}

_USAGE_FIELDS = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_input_tokens", "cache_read"),
    ("cache_creation_input_tokens", "cache_create"),
)


def _usage_of(message: Dict[str, Any]) -> Dict[str, int]:
    usage = message.get("usage")
    out: Dict[str, int] = {}
    if isinstance(usage, dict):
        for source, label in _USAGE_FIELDS:
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and value:
                out[label] = value
    return out


def _bump(counts: Dict[str, int], delta: Dict[str, int], sign: int) -> None:
    for label, value in delta.items():
        total = counts.get(label, 0) + sign * value
        if total:
            counts[label] = total
        else:
            counts.pop(label, None)


def apply_usage(message: Dict[str, Any], tokens: Dict[str, int],
                by_model: Dict[str, Dict[str, int]],
                seen: Dict[str, Any], fallback_model: str) -> str:
    """Fold one transcript message's usage into running totals, once per API message.

    Claude Code writes ONE transcript entry per content block (thinking, text,
    every tool_use...) and every one of them repeats the whole message's usage.
    In a real session 271 assistant entries carried usage for only 103 distinct
    API messages, so summing per entry overstates every count (output tokens by
    2.7x there). `message.id` identifies the API message: when it recurs, the
    latest usage replaces the earlier one instead of adding to it. Entries with
    no id (older formats) are counted individually, as before.

    Returns the model the usage was attributed to.
    """
    usage = _usage_of(message)
    model = message.get("model")
    if not isinstance(model, str) or not model or model == "<synthetic>":
        model = fallback_model
    if not usage:
        return model
    mid = message.get("id")
    if isinstance(mid, str) and mid:
        previous = seen.get(mid)
        if previous is not None:
            old_model, old_usage = previous
            _bump(tokens, old_usage, -1)
            _bump(by_model.setdefault(old_model, {}), old_usage, -1)
            if not by_model.get(old_model):
                by_model.pop(old_model, None)
        seen[mid] = (model, usage)
    _bump(tokens, usage, +1)
    _bump(by_model.setdefault(model, {}), usage, +1)
    return model


class TokenTally:
    """Tokens only -- for the orchestrator's own transcript, where keeping every
    tool call (as AgentDigest does) would be needless memory."""

    def __init__(self) -> None:
        self.tokens: Dict[str, int] = {}
        self.by_model: Dict[str, Dict[str, int]] = {}
        self.model = ""
        self._seen: Dict[str, Any] = {}

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            message = entry.get("message")
            if isinstance(message, dict):
                self.model = apply_usage(message, self.tokens, self.by_model,
                                         self._seen, self.model)

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]


MAX_TOKEN_EVENTS = 4000
MAX_ACTIVITY_TIMES = 50000


class ActivityTimes:
    """When a transcript was written to, so a prompt can find the first activity after it.

    Kept sorted. Capped: past the cap the oldest times are dropped. At one entry every
    couple of seconds that is more than a day of continuous work for one transcript.
    """

    def __init__(self) -> None:
        self.times: List[float] = []

    def add(self, at: float) -> None:
        if not self.times or at >= self.times[-1]:
            self.times.append(at)
        else:
            bisect.insort(self.times, at)
        if len(self.times) > MAX_ACTIVITY_TIMES:
            del self.times[:len(self.times) - MAX_ACTIVITY_TIMES]

    def first_after(self, t: float) -> Optional[float]:
        """The earliest activity strictly after t, or None."""
        i = bisect.bisect_right(self.times, t)
        return self.times[i] if i < len(self.times) else None

    def clear(self) -> None:
        self.times = []


def _fresh_total(tokens: Dict[str, int]) -> int:
    """Tokens actually processed: input, output and new cache writes (not cache reads)."""
    return (tokens.get("input", 0) + tokens.get("output", 0)
            + tokens.get("cache_create", 0))


def _target_for(name: str, params: Dict[str, Any]) -> str:
    field_name = _TARGET_FIELDS.get(name)
    value = params.get(field_name) if field_name else None
    if value is None:
        for candidate in params.values():
            if isinstance(candidate, str) and candidate:
                value = candidate
                break
    text = str(value) if value is not None else ""
    return text[:_MAX_TARGET] + "..." if len(text) > _MAX_TARGET else text


@dataclass
class AgentDigest:
    """Accumulates across incremental reads; safe to ingest repeatedly."""
    tokens: Dict[str, int] = field(default_factory=dict)
    tokens_by_model: Dict[str, Dict[str, int]] = field(default_factory=dict)
    tool_calls: List[ToolCall] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    last_activity_at: Optional[float] = None
    final_text: str = ""
    # (when, message) of each SubagentHandback call: since Claude Code 2.1.277 an agent's
    # report travels through that tool, and the parent is only told where to find it.
    handbacks: List[Tuple[Optional[float], str]] = field(default_factory=list)
    model: str = ""
    ended_mid_tool: bool = False
    # (timestamp, fresh tokens added): when the tokens were spent, for the live
    # charts. Capped; past the cap new tokens fold into the last entry.
    token_events: List[Tuple[float, int]] = field(default_factory=list)
    # Every timestamped entry: how a permission prompt learns when the agent moved again.
    activity: ActivityTimes = field(default_factory=ActivityTimes)
    # What it changed, file by file (orchestra.changes), from successful edits.
    changes: ChangeLog = field(default_factory=ChangeLog)
    # What it produced: commits, pushes, pull requests, test runs (orchestra.outcomes).
    outcomes: OutcomeLog = field(default_factory=OutcomeLog)
    # What it was told: instruction files and skills (orchestra.context).
    context: ContextLog = field(default_factory=ContextLog)
    # Where its tokens went to waste: cache rebuilds, big results, re-reads (orchestra.waste).
    waste: WasteLog = field(default_factory=WasteLog)
    # What went wrong: API errors, failed calls, retries, timeouts (orchestra.errors).
    errors: ErrorLog = field(default_factory=ErrorLog)
    # tool_use id -> its call, until the result arrives (and says whether it failed).
    _open_tool_ids: Dict[str, ToolCall] = field(default_factory=dict)
    # tool_use id -> (tool, input) of an edit waiting for its result.
    _pending_edits: Dict[str, Tuple[str, Dict[str, Any]]] = field(default_factory=dict)
    _usage_seen: Dict[str, Any] = field(default_factory=dict)

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if at and (self.last_activity_at is None or at > self.last_activity_at):
                self.last_activity_at = at
            if at:
                self.activity.add(at)
            message = entry.get("message")
            if isinstance(message, dict):
                model = message.get("model")
                # Claude Code injects a synthetic wrap-up message (model
                # literally "<synthetic>") on an interrupted or errored turn.
                # It's not a real model — recording it would overwrite the
                # last genuine one this agent actually ran on.
                if isinstance(model, str) and model and model != "<synthetic>":
                    self.model = model
                before = _fresh_total(self.tokens)
                apply_usage(message, self.tokens, self.tokens_by_model,
                            self._usage_seen, self.model)
                added = _fresh_total(self.tokens) - before
                if added > 0 and at:
                    self._note_tokens(at, added)
            self._add_blocks(entry, at)
        self.ended_mid_tool = bool(self._open_tool_ids)
        self.outcomes.ingest(entries)
        self.context.ingest(entries)
        self.waste.ingest(entries)
        self.errors.ingest(entries)

    @property
    def report(self) -> str:
        """What the agent handed back: its latest SubagentHandback message, else its last text."""
        return self.handbacks[-1][1] if self.handbacks else self.final_text

    def _note_tokens(self, at: float, added: int) -> None:
        if len(self.token_events) >= MAX_TOKEN_EVENTS:
            when, total = self.token_events[-1]
            self.token_events[-1] = (when, total + added)
        else:
            self.token_events.append((at, added))

    def _add_blocks(self, entry: Dict[str, Any], at: Optional[float]) -> None:
        for block in content_blocks(entry):
            kind = block.get("type")
            if kind == "text":
                text = str(block.get("text", "")).strip()
                if text:
                    self.final_text = text
            elif kind == "tool_use":
                name = str(block.get("name", ""))
                params = block.get("input") if isinstance(block.get("input"), dict) else {}
                role, ref = classify(name, params)
                call = ToolCall(name=name, target=_target_for(name, params), timestamp=at,
                                verify=role, ref=ref)
                self.tool_calls.append(call)
                self._open_tool_ids[str(block.get("id", ""))] = call
                if name in EDIT_TOOLS:
                    self._pending_edits[str(block.get("id", ""))] = (name, params)
                if name == "SubagentHandback" and isinstance(params.get("message"), str):
                    self.handbacks.append((at, params["message"].strip()))
                self._record_files(name, params)
            elif kind == "tool_result":
                use_id = str(block.get("tool_use_id", ""))
                call = self._open_tool_ids.pop(use_id, None)
                if call is not None:
                    call.ok = not bool(block.get("is_error"))
                edit = self._pending_edits.pop(use_id, None)
                if edit is not None and not block.get("is_error"):
                    self.changes.add(extract(edit[0], edit[1], _result_for(entry)), at)

    def _record_files(self, name: str, params: Dict[str, Any]) -> None:
        write_field = _WRITE_TOOLS.get(name)
        if write_field:
            path = params.get(write_field)
            if isinstance(path, str) and path and path not in self.files_written:
                self.files_written.append(path)
        read_field = _READ_TOOLS.get(name)
        if read_field:
            path = params.get(read_field)
            if isinstance(path, str) and path and path not in self.files_read:
                self.files_read.append(path)
