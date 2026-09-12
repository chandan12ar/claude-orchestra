"""Parsing the orchestrator's transcript: launches, results, and notifications.

Isolated from build.py because these shapes are the most likely thing to change
between Claude Code versions. Every accessor tolerates missing and unexpected
fields — a format change should degrade the view, not crash it.
"""

import calendar
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

AGENT_TOOL_NAMES = ("Agent", "Task")

_AGENT_ID_RE = re.compile(r"agentId:\s*([0-9a-f]{8,})")
_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
_BACKGROUND_MARKER = "Async agent launched"


def _tag(block: str, name: str) -> str:
    m = re.search(r"<{0}>(.*?)</{0}>".format(name), block, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_timestamp(value: Any) -> Optional[float]:
    """ISO-8601 with a trailing Z to a POSIX float. None if unparsable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.rstrip("Z")
    frac = 0.0
    if "." in text:
        text, _, frac_text = text.partition(".")
        try:
            frac = float("0." + frac_text)
        except ValueError:
            frac = 0.0
    try:
        parsed = time.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return calendar.timegm(parsed) + frac


def _content_blocks(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _content_text(entry: Dict[str, Any]) -> str:
    message = entry.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return ""


@dataclass
class LaunchRecord:
    tool_use_id: str
    description: str = ""
    prompt: str = ""
    model: str = ""
    launched_at: Optional[float] = None
    turn_uuid: str = ""
    launcher_agent_id: Optional[str] = None


@dataclass
class ResultRecord:
    tool_use_id: str
    agent_id: str = ""
    launch_mode: str = "inline"
    inline_result: str = ""
    is_error: bool = False
    at: Optional[float] = None


@dataclass
class Notification:
    agent_id: str
    tool_use_id: str = ""
    status: str = ""
    result: str = ""
    summary: str = ""
    at: Optional[float] = None


@dataclass
class ParentIndex:
    """Accumulates launches, results, and notifications across incremental reads."""
    launches: Dict[str, LaunchRecord] = field(default_factory=dict)
    results: Dict[str, ResultRecord] = field(default_factory=dict)
    notifications: Dict[str, List[Notification]] = field(default_factory=dict)
    cwd: str = ""
    last_entry_at: float = 0.0

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            if at:
                self.last_entry_at = max(self.last_entry_at, at)
            if not self.cwd and isinstance(entry.get("cwd"), str):
                self.cwd = entry["cwd"]
            self._ingest_blocks(entry, at)
            self._ingest_notifications(entry, at)

    def _ingest_blocks(self, entry: Dict[str, Any], at: Optional[float]) -> None:
        launcher = entry.get("agentId") if entry.get("isSidechain") else None
        for block in _content_blocks(entry):
            kind = block.get("type")
            if kind == "tool_use" and block.get("name") in AGENT_TOOL_NAMES:
                params = block.get("input") if isinstance(block.get("input"), dict) else {}
                tool_use_id = str(block.get("id", ""))
                if not tool_use_id:
                    continue
                self.launches[tool_use_id] = LaunchRecord(
                    tool_use_id=tool_use_id,
                    description=str(params.get("description", "")),
                    prompt=str(params.get("prompt", "")),
                    model=str(params.get("model", "")),
                    launched_at=at,
                    turn_uuid=str(entry.get("uuid", "")),
                    launcher_agent_id=str(launcher) if launcher is not None else None,
                )
            elif kind == "tool_result":
                tool_use_id = str(block.get("tool_use_id", ""))
                if not tool_use_id:
                    continue
                text = _result_text(block)
                is_background = _BACKGROUND_MARKER in text
                match = _AGENT_ID_RE.search(text)
                self.results[tool_use_id] = ResultRecord(
                    tool_use_id=tool_use_id,
                    agent_id=match.group(1) if match else "",
                    launch_mode="background" if is_background else "inline",
                    inline_result="" if is_background else text.strip(),
                    is_error=bool(block.get("is_error")),
                    at=at,
                )

    def _ingest_notifications(self, entry: Dict[str, Any], at: Optional[float]) -> None:
        text = _content_text(entry)
        if "<task-notification>" not in text:
            return
        for body in _NOTIFICATION_RE.findall(text):
            agent_id = _tag(body, "task-id")
            if not agent_id:
                continue
            note = Notification(
                agent_id=agent_id,
                tool_use_id=_tag(body, "tool-use-id"),
                status=_tag(body, "status"),
                result=_tag(body, "result"),
                summary=_tag(body, "summary"),
                at=at,
            )
            bucket = self.notifications.setdefault(agent_id, [])
            if note not in bucket:
                bucket.append(note)
        for notes in self.notifications.values():
            notes.sort(key=lambda n: (n.at is None, n.at))


content_blocks = _content_blocks
content_text = _content_text
