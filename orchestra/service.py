"""What the API answers. No sockets here."""

import os
import time
from typing import Any, Callable, Dict, List, Optional

from orchestra.build import RunBuilder
from orchestra.locate import find_session, list_sessions


class NotFound(Exception):
    """Raised when a session or agent does not exist."""


class OrchestraService:
    def __init__(self, root: Optional[str] = None, token: str = "",
                 default_session: str = "",
                 now_fn: Callable[[], float] = time.time) -> None:
        self.root = root
        self.token = token
        self.default_session = default_session
        self.now_fn = now_fn
        self._builders: Dict[str, RunBuilder] = {}

    def _builder(self, session_id: str) -> RunBuilder:
        session_id = session_id or self.default_session
        if session_id not in self._builders:
            paths = find_session(session_id, root=self.root)
            if paths is None:
                raise NotFound("unknown session: {}".format(session_id))
            self._builders[session_id] = RunBuilder(paths, now_fn=self.now_fn)
        return self._builders[session_id]

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
                             "agent_count": info.agent_count,
                             "size_bytes": info.size_bytes})
        return {"project_dir": os.path.basename(builder.paths.project_dir),
                "current": builder.paths.session_id,
                "sessions": sessions}
