"""Digesting a subagent's own transcript into the numbers the dashboard shows."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from orchestra.model import ToolCall
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
    model: str = ""
    ended_mid_tool: bool = False
    _open_tool_ids: set = field(default_factory=set)
    _usage_seen: Dict[str, Any] = field(default_factory=dict)

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if at and (self.last_activity_at is None or at > self.last_activity_at):
                self.last_activity_at = at
            message = entry.get("message")
            if isinstance(message, dict):
                model = message.get("model")
                # Claude Code injects a synthetic wrap-up message (model
                # literally "<synthetic>") on an interrupted or errored turn.
                # It's not a real model — recording it would overwrite the
                # last genuine one this agent actually ran on.
                if isinstance(model, str) and model and model != "<synthetic>":
                    self.model = model
                apply_usage(message, self.tokens, self.tokens_by_model,
                            self._usage_seen, self.model)
            self._add_blocks(entry, at)
        self.ended_mid_tool = bool(self._open_tool_ids)

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
                self.tool_calls.append(
                    ToolCall(name=name, target=_target_for(name, params), timestamp=at))
                self._open_tool_ids.add(str(block.get("id", "")))
                self._record_files(name, params)
            elif kind == "tool_result":
                self._open_tool_ids.discard(str(block.get("tool_use_id", "")))

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
