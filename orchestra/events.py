"""Live events: an agent-neutral schema and the on-disk spool hooks write to.

Transcripts say what an agent *did*. They cannot say what it is *waiting on*, or
that it died to a rate limit, or that the session was closed. Claude Code hooks
can. A tiny hook process appends one line per event here; the server tails it.

Why a file and not a socket: the hook may fire before any dashboard exists, the
server's port and token change every launch, and a hook must never block or fail
Claude Code. Appending a line has none of those problems.

Only whitelisted, capped, redacted fields are ever stored. A raw hook payload
carries tool inputs and prompts; none of that is kept.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from orchestra.redact import scrub
from orchestra.statedir import state_dir

SCHEMA_VERSION = 1

# Canonical kinds. A future adapter for another agent maps onto these.
SESSION_START = "session_start"
SESSION_END = "session_end"
AGENT_START = "agent_start"
AGENT_STOP = "agent_stop"
NOTIFICATION = "notification"
ERROR = "error"
TURN_END = "turn_end"
KINDS = (SESSION_START, SESSION_END, AGENT_START, AGENT_STOP,
         NOTIFICATION, ERROR, TURN_END)

# Hook event name -> canonical kind (Claude Code).
_CLAUDE_KINDS = {
    "SessionStart": SESSION_START,
    "SessionEnd": SESSION_END,
    "SubagentStart": AGENT_START,
    "SubagentStop": AGENT_STOP,
    "Notification": NOTIFICATION,
    "StopFailure": ERROR,
    "Stop": TURN_END,
}

# detail key -> payload keys to look in, first hit wins. Hook payload field
# names are read tolerantly: a renamed field degrades to "" instead of failing.
_DETAIL_FIELDS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    SESSION_START: (("source", "source"), ("model", "model")),
    SESSION_END: (("reason", "reason"),),
    AGENT_START: (),
    AGENT_STOP: (("message", "last_assistant_message"),),
    NOTIFICATION: (("notification_type", "notification_type"),
                   ("message", "message")),
    ERROR: (("error_type", "error_type"), ("message", "error_message")),
    TURN_END: (),
}

DETAIL_CAP = 500
MAX_STDIN_BYTES = 1_000_000
FILE_MAX_BYTES = 1_000_000
RETENTION_S = 7 * 24 * 3600


@dataclass
class Event:
    kind: str
    session_id: str
    ts: float
    source: str = "claude-code"
    agent_id: str = ""
    agent_type: str = ""
    cwd: str = ""
    detail: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"v": SCHEMA_VERSION, "kind": self.kind,
                "session_id": self.session_id, "ts": self.ts,
                "source": self.source, "agent_id": self.agent_id,
                "agent_type": self.agent_type, "cwd": self.cwd,
                "detail": dict(self.detail)}

    @staticmethod
    def from_dict(raw: Any) -> Optional["Event"]:
        """Validate untrusted spool content. Anything off-schema is None."""
        if not isinstance(raw, dict) or raw.get("v") != SCHEMA_VERSION:
            return None
        kind, session_id, ts = raw.get("kind"), raw.get("session_id"), raw.get("ts")
        if kind not in KINDS or not isinstance(session_id, str) or not session_id:
            return None
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            return None
        detail = raw.get("detail")
        detail = ({str(k): str(v)[:DETAIL_CAP] for k, v in detail.items()}
                  if isinstance(detail, dict) else {})

        def text(key: str) -> str:
            value = raw.get(key)
            return value if isinstance(value, str) else ""

        return Event(kind=kind, session_id=session_id, ts=float(ts),
                     source=text("source") or "claude-code",
                     agent_id=text("agent_id"), agent_type=text("agent_type"),
                     cwd=text("cwd"), detail=detail)


def _clean(value: Any) -> str:
    # scrub BEFORE capping: capping first could split a credential so half of it
    # survives the redaction patterns.
    return scrub(str(value))[:DETAIL_CAP] if value not in (None, "") else ""


def normalize_claude_hook(payload: Any, now: Optional[float] = None) -> Optional[Event]:
    """A Claude Code hook payload -> Event, or None if it is not one we record."""
    if not isinstance(payload, dict):
        return None
    kind = _CLAUDE_KINDS.get(str(payload.get("hook_event_name", "")))
    session_id = payload.get("session_id")
    if kind is None or not isinstance(session_id, str) or not session_id:
        return None
    detail = {}
    for name, key in _DETAIL_FIELDS[kind]:
        cleaned = _clean(payload.get(key))
        if cleaned:
            detail[name] = cleaned
    return Event(kind=kind, session_id=session_id,
                 ts=time.time() if now is None else now,
                 agent_id=_clean(payload.get("agent_id")),
                 agent_type=_clean(payload.get("agent_type")),
                 cwd=_clean(payload.get("cwd")), detail=detail)


def _safe_name(session_id: str) -> str:
    return "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:128]


class EventSpool:
    """Append-only per-session JSONL files under <state dir>/events/."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = root or os.path.join(state_dir(), "events")
        self._offsets: Dict[str, int] = {}
        self._inodes: Dict[str, int] = {}
        self.dropped = 0   # malformed or off-schema lines seen by readers

    def path_for(self, session_id: str) -> str:
        return os.path.join(self.root, _safe_name(session_id) + ".jsonl")

    # -- writing (the hook process) ---------------------------------------

    def append(self, event: Event) -> None:
        name = _safe_name(event.session_id)
        if not name:
            return
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        path = self.path_for(event.session_id)
        try:
            if os.path.getsize(path) > FILE_MAX_BYTES:
                os.replace(path, path + ".old")
        except OSError:
            pass
        line = (json.dumps(event.to_dict(), separators=(",", ":")) + "\n").encode()
        # One write on an O_APPEND descriptor: concurrent hooks never interleave
        # inside a line this small.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def prune(self, max_age_s: float = RETENTION_S,
              now: Optional[float] = None) -> int:
        """Delete spool files untouched for max_age_s. Returns how many."""
        now = time.time() if now is None else now
        removed = 0
        try:
            names = os.listdir(self.root)
        except OSError:
            return 0
        for name in names:
            path = os.path.join(self.root, name)
            try:
                if now - os.path.getmtime(path) > max_age_s:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
        return removed

    # -- reading (the server) ---------------------------------------------

    def read_all(self, session_id: str) -> List[Event]:
        """Every event for a session, oldest first. Does not move the cursor."""
        return self._read(self.path_for(session_id), 0)[0]

    def read_new(self, session_id: str) -> List[Event]:
        """Events appended since the last call for this session."""
        path = self.path_for(session_id)
        events, offset = self._read(path, self._offsets.get(path, 0), track=True)
        self._offsets[path] = offset
        return events

    def _read(self, path: str, offset: int,
              track: bool = False) -> Tuple[List[Event], int]:
        try:
            info = os.stat(path)
            # Rotation swaps in a new file that may be as large as the old read
            # offset, so size alone cannot tell "same file, more data" from
            # "different file". The file's identity can.
            if track:
                if self._inodes.get(path, info.st_ino) != info.st_ino:
                    offset = 0
                self._inodes[path] = info.st_ino
            if info.st_size < offset:        # truncated: start over
                offset = 0
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
        except OSError:
            return [], offset
        end = chunk.rfind(b"\n")
        if end < 0:                  # only a torn tail so far
            return [], offset
        events: List[Event] = []
        for raw in chunk[:end].split(b"\n"):
            if not raw.strip():
                continue
            try:
                event = Event.from_dict(json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                event = None
            if event is None:
                self.dropped += 1
            else:
                events.append(event)
        return events, offset + end + 1

    def sessions(self) -> List[Tuple[str, float]]:
        """(session_id, last_modified) for every spooled session, newest first."""
        out = []
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            try:
                out.append((name[:-len(".jsonl")],
                            os.path.getmtime(os.path.join(self.root, name))))
            except OSError:
                continue
        out.sort(key=lambda item: item[1], reverse=True)
        return out
