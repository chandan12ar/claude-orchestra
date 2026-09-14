"""Assembling a Run from a session's files.

Pure given the file contents: no clock of its own (now_fn is injected), no
network, no writes. Everything the dashboard shows is decided here.
"""

import glob
import os
import threading
import time
from typing import Callable, Dict, List, Optional

from orchestra import constants as C
from orchestra.agentlog import AgentDigest
from orchestra.edges import infer_edges
from orchestra.extract import extract_expected_output, extract_objective
from orchestra.locate import SessionPaths
from orchestra.model import Agent, Batch, Run
from orchestra.parent import ParentIndex
from orchestra.status import build_rounds, compute_status
from orchestra.transcript import IncrementalReader


class RunBuilder:
    """Holds the incremental state between polls. One per session."""

    def __init__(self, paths: SessionPaths,
                 now_fn: Callable[[], float] = time.time) -> None:
        self.paths = paths
        self.now_fn = now_fn
        # ThreadingHTTPServer runs a thread per connection, and every one of
        # them calls refresh() on this same builder. refresh mutates the
        # reader's byte offsets and the per-agent digests, which accumulate
        # with += and append -- so two interleaved refreshes consume the same
        # bytes twice and the counts stay permanently doubled.
        self._lock = threading.Lock()
        self._reader = IncrementalReader()
        self._parent = ParentIndex()
        self._digests: Dict[str, AgentDigest] = {}
        self._metas: Dict[str, dict] = {}

    def refresh(self) -> Run:
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> Run:
        now = self.now_fn()
        self._parent.ingest(self._reader.read_new(self.paths.session_jsonl))
        self._scan_subagents()
        session_live = self._session_live(now)

        agents = [self._assemble(agent_id, now, session_live)
                  for agent_id in sorted(self._agent_ids())]
        agents = [a for a in agents if a is not None]
        agents.sort(key=lambda a: (a.started_at is None, a.started_at or 0))

        edges, hubs, conflicts = infer_edges(agents)
        starts = [a.started_at for a in agents if a.started_at is not None]
        ends = [a.ended_at for a in agents if a.ended_at is not None]

        return Run(
            session_id=self.paths.session_id,
            project_path=self._parent.cwd,
            started_at=min(starts) if starts else None,
            ended_at=max(ends) if ends and not session_live else None,
            session_live=session_live,
            agents=agents,
            edges=edges,
            batches=self._batches(agents),
            hub_files=hubs,
            write_conflicts=conflicts,
            diagnostics=dict(self._reader.diagnostics),
        )

    # -- internals ---------------------------------------------------------

    def _session_live(self, now: float) -> bool:
        try:
            age = now - os.path.getmtime(self.paths.session_jsonl)
        except OSError:
            return False
        return age <= C.SESSION_LIVE_THRESHOLD_S

    def _scan_subagents(self) -> None:
        directory = self.paths.subagents_dir
        if not os.path.isdir(directory):
            return
        for meta_path in glob.glob(os.path.join(directory, "agent-*.meta.json")):
            agent_id = os.path.basename(meta_path)[len("agent-"):-len(".meta.json")]
            meta = self._reader.read_json(meta_path)
            if meta:
                self._metas[agent_id] = meta
        for log_path in glob.glob(os.path.join(directory, "agent-*.jsonl")):
            agent_id = os.path.basename(log_path)[len("agent-"):-len(".jsonl")]
            entries = self._reader.read_new(log_path)
            if not entries:
                continue
            digest = self._digests.setdefault(agent_id, AgentDigest())
            digest.ingest(entries)
            # A nested agent's launches live in its own transcript.
            self._parent.ingest(entries)

    def _agent_ids(self) -> List[str]:
        ids = set(self._metas) | set(self._digests)
        for result in self._parent.results.values():
            # An agent id is only real if its tool_result answers an actual
            # Agent/Task launch. The id is scraped from result TEXT, so a
            # transcript that merely QUOTES another session's launch output —
            # which happens whenever anyone inspects transcripts — would
            # otherwise invent a phantom agent with no meta, no transcript and
            # no launch, showing up as an empty "unknown" row.
            if result.tool_use_id not in self._parent.launches:
                continue
            if result.agent_id:
                ids.add(result.agent_id)
        return sorted(ids)

    def _tool_use_id_for(self, agent_id: str) -> str:
        meta = self._metas.get(agent_id) or {}
        if meta.get("toolUseId"):
            return str(meta["toolUseId"])
        for result in self._parent.results.values():
            if result.agent_id == agent_id:
                return result.tool_use_id
        for notes in self._parent.notifications.get(agent_id, []):
            if notes.tool_use_id:
                return notes.tool_use_id
        return ""

    def _assemble(self, agent_id: str, now: float, session_live: bool) -> Optional[Agent]:
        meta = self._metas.get(agent_id) or {}
        digest = self._digests.get(agent_id) or AgentDigest()
        tool_use_id = self._tool_use_id_for(agent_id)
        launch = self._parent.launches.get(tool_use_id)
        result = self._parent.results.get(tool_use_id)
        notifications = self._parent.notifications.get(agent_id, [])

        rounds = build_rounds(launch, result, notifications, digest)
        status = compute_status(rounds, digest, now, session_live)

        brief = launch.prompt if launch else ""
        description = str(meta.get("description") or (launch.description if launch else ""))
        final_result = ""
        for round_ in reversed(rounds):
            if round_.result:
                final_result = round_.result
                break
        if not final_result and rounds and rounds[-1].ended_at is not None:
            final_result = digest.final_text

        shape = meta.get("requestShape")
        launch_mode = "background" if shape == "background" else (
            result.launch_mode if result else "inline")

        log_path = os.path.join(self.paths.subagents_dir,
                                "agent-{}.jsonl".format(agent_id))
        return Agent(
            agent_id=agent_id,
            tool_use_id=tool_use_id,
            # A fork's own transcript replays its full inherited history —
            # including the very entry that launched it, tagged (like every
            # entry in that file) with isSidechain=True and its own agentId.
            # Read naively, that looks like the agent spawning itself. An
            # agent can never legitimately be its own parent, so that specific
            # shape is treated as the top-level launch it actually was.
            parent_agent_id=(launch.launcher_agent_id
                             if launch and launch.launcher_agent_id != agent_id else None),
            spawn_depth=int(meta.get("spawnDepth") or 1),
            agent_type=str(meta.get("agentType") or ""),
            description=description,
            # digest.model is what the agent's own transcript actually reports
            # (e.g. "claude-sonnet-5") — the ground truth. meta/launch only
            # carry the short alias *requested* at spawn time ("sonnet"), which
            # is a worse answer to "which model ran this" whenever the real
            # one is known.
            model=str(digest.model or meta.get("model")
                      or (launch.model if launch else "")),
            launch_mode=launch_mode,
            brief=brief,
            objective=extract_objective(brief, description),
            expected_output=extract_expected_output(brief, description),
            status=status,
            rounds=rounds,
            last_activity_at=digest.last_activity_at,
            result=final_result,
            tokens=dict(digest.tokens),
            tool_calls=list(digest.tool_calls),
            files_written=list(digest.files_written),
            files_read=list(digest.files_read),
            transcript_path=log_path,
        )

    def _batches(self, agents: List[Agent]) -> List[Batch]:
        by_tool_use = {a.tool_use_id: a.agent_id for a in agents if a.tool_use_id}
        grouped: Dict[str, Batch] = {}
        for launch in self._parent.launches.values():
            agent_id = by_tool_use.get(launch.tool_use_id)
            if not agent_id:
                continue
            batch = grouped.setdefault(
                launch.turn_uuid,
                Batch(turn_uuid=launch.turn_uuid, launched_at=launch.launched_at))
            batch.agent_ids.append(agent_id)
            if launch.launched_at is not None:
                if batch.launched_at is None or launch.launched_at < batch.launched_at:
                    batch.launched_at = launch.launched_at
        batches = list(grouped.values())
        for batch in batches:
            batch.agent_ids.sort()
        batches.sort(key=lambda b: (b.launched_at is None, b.launched_at or 0))
        return batches
