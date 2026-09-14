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
    tool_calls: List[ToolCall] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    last_activity_at: Optional[float] = None
    final_text: str = ""
    model: str = ""
    ended_mid_tool: bool = False
    _open_tool_ids: set = field(default_factory=set)

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
                self._add_usage(message.get("usage"))
            self._add_blocks(entry, at)
        self.ended_mid_tool = bool(self._open_tool_ids)

    def _add_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for source, label in _USAGE_FIELDS:
            value = usage.get(source)
            if isinstance(value, int) and value:
                self.tokens[label] = self.tokens.get(label, 0) + value

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
