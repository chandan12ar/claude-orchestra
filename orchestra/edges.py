"""Inferring how agents were connected.

Three of the four edge kinds are exact. The fourth, handoff, is a guess, and it
is labelled as one and ships the evidence that produced it so a reader can
dismiss it.
"""

import difflib
import hashlib
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from orchestra import constants as C
from orchestra.model import Agent, Edge, HubFile, WriteConflict

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
    return stripped.lower()


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


def _artifact_edges(
    agents: List[Agent],
) -> Tuple[List[Edge], List[HubFile], List[WriteConflict]]:
    writes: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    reads: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for agent in agents:
        for path, at in _paths(agent, WRITE_TOOL_NAMES):
            writes.setdefault(path, []).append((agent.agent_id, at))
        for path, at in _paths(agent, READ_TOOL_NAMES):
            reads.setdefault(path, []).append((agent.agent_id, at))

    conflicts: List[WriteConflict] = []
    for path, writers in writes.items():
        distinct = sorted({writer_id for writer_id, _ in writers})
        if len(distinct) > 1:
            conflicts.append(WriteConflict(path=path, writer_ids=distinct))

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
                if read_at is None or write_at is None or read_at < write_at:
                    continue
                edges.append(Edge(src=writer_id, dst=reader_id, kind="artifact",
                                  confidence="exact",
                                  evidence={"path": path, "written_at": write_at,
                                            "read_at": read_at}))
    return edges, hubs, conflicts


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


def _digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=16).digest()


class HandoffCache:
    """Remembers handoff scores between polls.

    infer_edges runs on every 2-second poll over every agent pair, but a
    finished agent's result and a launched agent's brief never change. Scores
    are keyed by the *content* of the two texts, so an unchanged pair is never
    scored twice. Each call keeps only the entries it used, so the cache
    cannot grow past the current run.
    """

    def __init__(self) -> None:
        self._texts: Dict[bytes, Tuple[List[str], Set[Tuple[str, ...]]]] = {}
        self._pairs: Dict[Tuple[bytes, bytes], Optional[Tuple[float, int, str]]] = {}
        self._used_texts: Dict[bytes, Tuple[List[str], Set[Tuple[str, ...]]]] = {}
        self._used_pairs: Dict[Tuple[bytes, bytes], Optional[Tuple[float, int, str]]] = {}

    def begin(self) -> None:
        self._used_texts = {}
        self._used_pairs = {}

    def end(self) -> None:
        self._texts = self._used_texts
        self._pairs = self._used_pairs

    def text(self, key: bytes, raw: str) -> Tuple[List[str], Set[Tuple[str, ...]]]:
        found = self._texts.get(key) or self._used_texts.get(key)
        if found is None:
            words = _WORD.findall(raw.lower())
            found = (words, _shingles(words, C.SHINGLE_SIZE))
        self._used_texts[key] = found
        return found

    def pair(self, src_key: bytes, dst_key: bytes, src_raw: str, dst_raw: str
             ) -> Optional[Tuple[float, int, str]]:
        """(score, run_words, snippet) when the pair is a handoff, else None."""
        key = (src_key, dst_key)
        if key in self._pairs:
            result = self._pairs[key]
        elif key in self._used_pairs:
            result = self._used_pairs[key]
        else:
            result = self._score(src_key, dst_key, src_raw, dst_raw)
        self._used_pairs[key] = result
        return result

    def _score(self, src_key: bytes, dst_key: bytes, src_raw: str, dst_raw: str
               ) -> Optional[Tuple[float, int, str]]:
        src_words, src_shingles = self.text(src_key, src_raw)
        dst_words, dst_shingles = self.text(dst_key, dst_raw)
        if not src_shingles:
            return None
        overlap = len(src_shingles & dst_shingles)
        score = overlap / float(len(src_shingles))
        # A shared run of N words contains exactly N - SHINGLE_SIZE + 1 shared
        # shingles, so fewer shared shingles than that bound proves the run
        # test cannot pass. SequenceMatcher is the expensive part; skip it
        # whenever the answer is already decided without changing any result.
        min_shingles_for_run = C.HANDOFF_RUN_WORDS - C.SHINGLE_SIZE + 1
        if score < C.HANDOFF_CONTAINMENT and overlap < min_shingles_for_run:
            return None
        run, snippet = _longest_run(src_words, dst_words)
        if score < C.HANDOFF_CONTAINMENT and run < C.HANDOFF_RUN_WORDS:
            return None
        return score, run, snippet


def _handoff_edges(agents: List[Agent],
                   cache: Optional[HandoffCache] = None) -> List[Edge]:
    cache = cache or HandoffCache()
    cache.begin()
    edges = []
    for src in agents:
        if not src.result or src.ended_at is None:
            continue
        src_key = _digest(src.result)
        for dst in agents:
            if dst.agent_id == src.agent_id or not dst.brief:
                continue
            if dst.started_at is None or dst.started_at < src.ended_at:
                continue
            scored = cache.pair(src_key, _digest(dst.brief), src.result, dst.brief)
            if scored is None:
                continue
            score, run, snippet = scored
            edges.append(Edge(src=src.agent_id, dst=dst.agent_id, kind="handoff",
                              confidence="inferred",
                              evidence={"score": round(score, 3),
                                        "run_words": run,
                                        "snippet": snippet[:400]}))
    cache.end()
    return edges


def infer_edges(
    agents: List[Agent],
    cache: Optional[HandoffCache] = None,
) -> Tuple[List[Edge], List[HubFile], List[WriteConflict]]:
    """All four kinds, deduplicated. Handoff folds into an exact edge if one exists."""
    edges = _spawn_edges(agents)
    artifact, hubs, conflicts = _artifact_edges(agents)
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

    exact_pairs: Dict[Tuple[str, str], List[Edge]] = {}
    for e in deduped:
        if e.kind in ("artifact", "message"):
            exact_pairs.setdefault((e.src, e.dst), []).append(e)

    for edge in _handoff_edges(agents, cache):
        existing = exact_pairs.get((edge.src, edge.dst))
        if existing:
            for e in existing:
                e.evidence["handoff"] = edge.evidence
            continue
        key = (edge.src, edge.dst, edge.kind)
        if key not in seen:
            seen[key] = edge
            deduped.append(edge)
    return deduped, hubs, conflicts
