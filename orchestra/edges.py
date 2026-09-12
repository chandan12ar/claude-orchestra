"""Inferring how agents were connected.

Three of the four edge kinds are exact. The fourth, handoff, is a guess, and it
is labelled as one and ships the evidence that produced it so a reader can
dismiss it.
"""

import difflib
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from orchestra import constants as C
from orchestra.model import Agent, Edge, HubFile

# Captures the project root so a worktree path collapses onto the main path:
# E:\p\.claude\worktrees\wt\src\a.py  ->  E:\p\src\a.py
# Dropping the root instead would leave worktree paths relative and plain paths
# absolute, and the two would never compare equal.
_WORKTREE = re.compile(r"(^.*?)[\\/]\.claude[\\/]worktrees[\\/][^\\/]+[\\/]",
                       re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")

WRITE_TOOL_NAMES = ("Write", "Edit", "NotebookEdit")
READ_TOOL_NAMES = ("Read",)


def normalize_path(path: str) -> str:
    """Make paths from different agents and worktrees comparable."""
    if not path:
        return ""
    stripped = _WORKTREE.sub(r"\1/", path)
    stripped = stripped.replace("\\", "/")
    stripped = os.path.normpath(stripped).replace("\\", "/")
    return stripped.lower().lstrip("./")


def _paths(agent: Agent, names: Tuple[str, ...]) -> List[Tuple[str, Optional[float]]]:
    out = []
    for call in agent.tool_calls:
        if call.name in names and call.target:
            out.append((normalize_path(call.target), call.timestamp))
    return out


def _spawn_edges(agents: List[Agent]) -> List[Edge]:
    return [Edge(src=a.parent_agent_id or C.ORCHESTRATOR_ID, dst=a.agent_id,
                 kind="spawn", confidence="exact",
                 evidence={"tool_use_id": a.tool_use_id})
            for a in agents]


def _artifact_edges(agents: List[Agent]) -> Tuple[List[Edge], List[HubFile]]:
    writes: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    reads: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for agent in agents:
        for path, at in _paths(agent, WRITE_TOOL_NAMES):
            writes.setdefault(path, []).append((agent.agent_id, at))
        for path, at in _paths(agent, READ_TOOL_NAMES):
            reads.setdefault(path, []).append((agent.agent_id, at))

    edges: List[Edge] = []
    hubs: List[HubFile] = []
    for path, readers in reads.items():
        writers = writes.get(path, [])
        if not writers:
            # Read widely, written by nobody: shared context, not a dependency.
            if len(readers) > C.HUB_FILE_THRESHOLD:
                hubs.append(HubFile(path=path,
                                    reader_ids=[r for r, _ in readers]))
            continue
        for reader_id, read_at in readers:
            for writer_id, write_at in writers:
                if writer_id == reader_id:
                    continue
                if read_at is not None and write_at is not None and read_at < write_at:
                    continue
                edges.append(Edge(src=writer_id, dst=reader_id, kind="artifact",
                                  confidence="exact",
                                  evidence={"path": path, "written_at": write_at,
                                            "read_at": read_at}))
    return edges, hubs


def _message_edges(agents: List[Agent]) -> List[Edge]:
    known = {a.agent_id for a in agents}
    edges = []
    for agent in agents:
        for call in agent.tool_calls:
            if call.name == "SendMessage" and call.target in known:
                edges.append(Edge(src=agent.agent_id, dst=call.target, kind="message",
                                  confidence="exact",
                                  evidence={"at": call.timestamp}))
    return edges


def _shingles(words: List[str], size: int) -> Set[Tuple[str, ...]]:
    return {tuple(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def _longest_run(src_words: List[str], dst_words: List[str]) -> Tuple[int, str]:
    matcher = difflib.SequenceMatcher(None, src_words, dst_words, autojunk=False)
    match = matcher.find_longest_match(0, len(src_words), 0, len(dst_words))
    snippet = " ".join(src_words[match.a:match.a + match.size])
    return match.size, snippet


def _handoff_edges(agents: List[Agent]) -> List[Edge]:
    edges = []
    for src in agents:
        if not src.result or src.ended_at is None:
            continue
        src_words = _WORD.findall(src.result.lower())
        src_shingles = _shingles(src_words, C.SHINGLE_SIZE)
        if not src_shingles:
            continue
        for dst in agents:
            if dst.agent_id == src.agent_id or not dst.brief:
                continue
            if dst.started_at is None or dst.started_at < src.ended_at:
                continue
            dst_words = _WORD.findall(dst.brief.lower())
            overlap = src_shingles & _shingles(dst_words, C.SHINGLE_SIZE)
            score = len(overlap) / float(len(src_shingles))
            run, snippet = _longest_run(src_words, dst_words)
            if score < C.HANDOFF_CONTAINMENT and run < C.HANDOFF_RUN_WORDS:
                continue
            edges.append(Edge(src=src.agent_id, dst=dst.agent_id, kind="handoff",
                              confidence="inferred",
                              evidence={"score": round(score, 3),
                                        "run_words": run,
                                        "snippet": snippet[:400]}))
    return edges


def infer_edges(agents: List[Agent]) -> Tuple[List[Edge], List[HubFile]]:
    """All four kinds, deduplicated. Handoff folds into an exact edge if one exists."""
    edges = _spawn_edges(agents)
    artifact, hubs = _artifact_edges(agents)
    edges.extend(artifact)
    edges.extend(_message_edges(agents))

    seen: Dict[Tuple[str, str, str], Edge] = {}
    deduped: List[Edge] = []
    for edge in edges:
        key = (edge.src, edge.dst, edge.kind)
        if key in seen:
            continue
        seen[key] = edge
        deduped.append(edge)

    exact_pairs = {(e.src, e.dst): e for e in deduped
                   if e.kind in ("artifact", "message")}
    for edge in _handoff_edges(agents):
        existing = exact_pairs.get((edge.src, edge.dst))
        if existing is not None:
            existing.evidence["handoff"] = edge.evidence
            continue
        key = (edge.src, edge.dst, edge.kind)
        if key not in seen:
            seen[key] = edge
            deduped.append(edge)
    return deduped, hubs
