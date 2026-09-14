"""Dataclasses for an Orchestra run. No I/O, no parsing."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from orchestra import constants as C
from orchestra.redact import scrub, scrub_obj


LIGHT_TEXT_CAP = 200


def _cap(text: Optional[str], limit: int = LIGHT_TEXT_CAP) -> str:
    """Trim a field carried in the 2-second poll payload.

    Applied AFTER scrub, never before, so a credential can never be split
    across the cut and survive half-redacted. The full text stays available
    on the per-agent detail endpoint.
    """
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class Extraction:
    """A value pulled out of a brief, plus which rule produced it."""
    text: str = ""
    source: str = ""


@dataclass
class Round:
    """One start-to-finish pass of an agent. An agent resumed via SendMessage has several."""
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    status: str = C.RUNNING
    result: str = ""


@dataclass
class ToolCall:
    name: str = ""
    target: str = ""
    timestamp: Optional[float] = None


@dataclass
class Agent:
    agent_id: str
    tool_use_id: str = ""
    parent_agent_id: Optional[str] = None
    spawn_depth: int = 1
    agent_type: str = ""
    description: str = ""
    model: str = ""
    launch_mode: str = "inline"
    brief: str = ""
    objective: Extraction = field(default_factory=Extraction)
    expected_output: Extraction = field(default_factory=Extraction)
    status: str = C.UNKNOWN
    rounds: List[Round] = field(default_factory=list)
    last_activity_at: Optional[float] = None
    result: str = ""
    tokens: Dict[str, int] = field(default_factory=dict)
    tool_calls: List[ToolCall] = field(default_factory=list)
    files_written: List[str] = field(default_factory=list)
    files_read: List[str] = field(default_factory=list)
    transcript_path: str = ""

    @property
    def started_at(self) -> Optional[float]:
        starts = [r.started_at for r in self.rounds if r.started_at is not None]
        return min(starts) if starts else None

    @property
    def ended_at(self) -> Optional[float]:
        """None while any round is still open — an agent mid-resume has no end."""
        if not self.rounds or any(r.ended_at is None for r in self.rounds):
            return None
        return max(r.ended_at for r in self.rounds)

    @property
    def duration_s(self) -> Optional[float]:
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at

    def to_light_dict(self) -> Dict[str, Any]:
        """Everything the timeline and graph need; nothing large."""
        return {
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "spawn_depth": self.spawn_depth,
            "agent_type": self.agent_type,
            "description": scrub(self.description),
            "model": self.model,
            "launch_mode": self.launch_mode,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "last_activity_at": self.last_activity_at,
            "tokens": dict(self.tokens),
            "rounds": [{"started_at": r.started_at, "ended_at": r.ended_at,
                        "status": r.status} for r in self.rounds],
            "objective": _cap(scrub(self.objective.text)),
            "files_written_count": len(self.files_written),
            "tool_call_count": len(self.tool_calls),
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        d = self.to_light_dict()
        d.update({
            "brief": scrub(self.brief),
            "result": scrub(self.result),
            # Overrides the capped copy inherited from to_light_dict: the
            # detail endpoint is exactly where the full text belongs.
            "objective": scrub(self.objective.text),
            "objective_source": self.objective.source,
            "expected_output": scrub(self.expected_output.text),
            "expected_output_source": self.expected_output.source,
            "tool_calls": [{"name": t.name, "target": scrub(t.target),
                            "timestamp": t.timestamp} for t in self.tool_calls],
            # These are transcript-derived paths too: the same string is
            # scrubbed in tool_calls[].target, so leaving it raw here would
            # be a hole in the single chokepoint spec section 11 promises.
            "files_written": [scrub(p) for p in self.files_written],
            "files_read": [scrub(p) for p in self.files_read],
            "transcript_path": scrub(self.transcript_path),
        })
        return d


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    confidence: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"src": self.src, "dst": self.dst, "kind": self.kind,
                "confidence": self.confidence, "evidence": scrub_obj(self.evidence)}


@dataclass
class Batch:
    """Agents launched in the same assistant turn — one parallel wave."""
    turn_uuid: str = ""
    agent_ids: List[str] = field(default_factory=list)
    launched_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"turn_uuid": self.turn_uuid, "agent_ids": list(self.agent_ids),
                "launched_at": self.launched_at}


@dataclass
class HubFile:
    """A file read by many agents but written by none — collapsed out of the graph."""
    path: str = ""
    reader_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": scrub(self.path), "reader_ids": list(self.reader_ids)}


@dataclass
class WriteConflict:
    """A file written by more than one agent — a real correctness risk, not
    just informational the way a shared-read hub file is."""
    path: str = ""
    writer_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": scrub(self.path), "writer_ids": list(self.writer_ids)}


@dataclass
class Run:
    session_id: str
    project_path: str = ""
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    session_live: bool = False
    agents: List[Agent] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    batches: List[Batch] = field(default_factory=list)
    hub_files: List[HubFile] = field(default_factory=list)
    write_conflicts: List[WriteConflict] = field(default_factory=list)
    diagnostics: Dict[str, int] = field(default_factory=dict)

    def agent(self, agent_id: str) -> Optional[Agent]:
        for a in self.agents:
            if a.agent_id == agent_id:
                return a
        return None

    def totals(self) -> Dict[str, Any]:
        counts = {"agents": len(self.agents)}
        for status in (C.RUNNING, C.COMPLETED, C.FAILED, C.STALLED,
                       C.ORPHANED, C.UNKNOWN):
            counts[status] = sum(1 for a in self.agents if a.status == status)
        tokens = {}
        for a in self.agents:
            for k, v in a.tokens.items():
                tokens[k] = tokens.get(k, 0) + v
        counts["tokens"] = tokens
        ends = [a.ended_at for a in self.agents if a.ended_at is not None]
        starts = [a.started_at for a in self.agents if a.started_at is not None]
        counts["wall_time_s"] = (max(ends) - min(starts)) if ends and starts else None
        return counts

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_path": self.project_path,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "session_live": self.session_live,
            "totals": self.totals(),
            "agents": [a.to_light_dict() for a in self.agents],
            "edges": [e.to_dict() for e in self.edges],
            "batches": [b.to_dict() for b in self.batches],
            "hub_files": [h.to_dict() for h in self.hub_files],
            "write_conflicts": [c.to_dict() for c in self.write_conflicts],
            "diagnostics": dict(self.diagnostics),
        }
