"""What the API answers. No sockets here."""

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

from orchestra import constants as C
from orchestra.build import RunBuilder
from orchestra.events import EventSpool
from orchestra.locate import find_session, list_sessions


class NotFound(Exception):
    """Raised when a session or agent does not exist."""


class OrchestraService:
    def __init__(self, root: Optional[str] = None, token: str = "",
                 default_session: str = "",
                 now_fn: Callable[[], float] = time.time,
                 max_builders: int = C.MAX_BUILDERS,
                 spool_factory: Optional[Callable[[], Optional[EventSpool]]] = None
                 ) -> None:
        self.root = root
        self.token = token
        self.default_session = default_session
        self.now_fn = now_fn
        # A factory, not one shared spool: a spool's read cursor belongs to the
        # builder consuming it, so a rebuilt (evicted) builder starts from the
        # beginning instead of inheriting a cursor that skips events.
        self.spool_factory = spool_factory
        self.max_builders = max(1, max_builders)
        # Least recently used first. Each builder holds every agent's digest, so
        # an unbounded dict grows with every session the picker ever visits.
        self._builders: "OrderedDict[str, RunBuilder]" = OrderedDict()
        self._lock = threading.Lock()

    def _builder(self, session_id: str) -> RunBuilder:
        session_id = session_id or self.default_session
        with self._lock:
            builder = self._builders.get(session_id)
            if builder is not None:
                self._builders.move_to_end(session_id)
                return builder
            paths = find_session(session_id, root=self.root)
            if paths is None:
                raise NotFound("unknown session: {}".format(session_id))
            spool = self.spool_factory() if self.spool_factory else None
            builder = RunBuilder(paths, now_fn=self.now_fn, spool=spool)
            self._builders[session_id] = builder
            self._evict()
            return builder

    def _evict(self) -> None:
        """Drop the least recently used builders, never the default session.

        An evicted session is simply re-read from disk on its next visit; the
        transcripts are the source of truth, so eviction costs time, not data.
        """
        for victim in list(self._builders):
            if len(self._builders) <= self.max_builders:
                return
            if victim != self.default_session:
                del self._builders[victim]

    def change_token(self, session_id: str = "") -> str:
        """A cheap fingerprint that changes whenever this session's inputs do.

        Only stats files (no reads, no parsing): the live-push stream calls it
        a few times a second. It covers the main transcript, every subagent
        transcript and meta file, and the event spool for the session.
        """
        session_id = session_id or self.default_session
        paths = find_session(session_id, root=self.root)
        if paths is None:
            raise NotFound("unknown session: {}".format(session_id))
        parts: List[str] = []

        def note(path: str) -> None:
            try:
                info = os.stat(path)
            except OSError:
                return
            parts.append("{}:{}:{}".format(os.path.basename(path), info.st_size,
                                           info.st_mtime_ns))

        note(paths.session_jsonl)
        try:
            names = sorted(os.listdir(paths.subagents_dir))
        except OSError:
            names = []
        for name in names:
            if name.startswith("agent-"):
                note(os.path.join(paths.subagents_dir, name))
        if self.spool_factory is not None:
            spool = self.spool_factory()
            if spool is not None:
                note(spool.path_for(session_id))
        return "|".join(parts)

    def run_summary(self, session_id: str = "") -> Dict[str, Any]:
        return self._builder(session_id).refresh().to_summary_dict()

    def agent_detail(self, agent_id: str, session_id: str = "") -> Dict[str, Any]:
        run = self._builder(session_id).refresh()
        agent = run.agent(agent_id)
        if agent is None:
            raise NotFound("unknown agent: {}".format(agent_id))
        return agent.to_detail_dict()

    def session_list(self, session_id: str = "") -> Dict[str, Any]:
        builder = self._builder(session_id)
        sessions: List[Dict[str, Any]] = []
        for info in list_sessions(builder.paths.project_dir):
            sessions.append({"session_id": info.session_id,
                             "modified_at": info.modified_at,
                             "agent_count": info.agent_count})
        return {"project_dir": os.path.basename(builder.paths.project_dir),
                "current": builder.paths.session_id,
                "sessions": sessions}
