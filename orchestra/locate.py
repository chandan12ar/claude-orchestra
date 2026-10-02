"""Finding Claude Code sessions on disk. The only module that knows the layout."""

import glob
import os
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SessionPaths:
    session_id: str
    session_jsonl: str
    subagents_dir: str
    project_dir: str


@dataclass
class SessionInfo:
    session_id: str
    modified_at: float
    agent_count: int


def claude_root() -> str:
    """The ~/.claude directory. Honours CLAUDE_CONFIG_DIR when set."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude")


def encode_project_dir(path: str) -> str:
    """Claude Code's project-directory name: every non-alphanumeric becomes a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _paths_for(session_jsonl: str, session_id: str) -> SessionPaths:
    project_dir = os.path.dirname(session_jsonl)
    return SessionPaths(
        session_id=session_id,
        session_jsonl=session_jsonl,
        subagents_dir=os.path.join(project_dir, session_id, "subagents"),
        project_dir=project_dir,
    )


def find_session(session_id: str, root: Optional[str] = None) -> Optional[SessionPaths]:
    """Locate a session by id across every project. Encoding-independent."""
    if not session_id:
        return None
    base = os.path.join(root or claude_root(), "projects")
    if not os.path.isdir(base):
        return None
    pattern = os.path.join(base, "*", session_id + ".jsonl")
    matches = sorted(glob.glob(pattern))
    if not matches:
        return None
    return _paths_for(matches[0], session_id)


def find_project_dir(cwd: str, root: Optional[str] = None) -> Optional[str]:
    """Fallback when no session id is available."""
    base = os.path.join(root or claude_root(), "projects")
    candidate = os.path.join(base, encode_project_dir(os.path.abspath(cwd)))
    return candidate if os.path.isdir(candidate) else None


def list_sessions(project_dir: str) -> List[SessionInfo]:
    """Every session in a project, newest first."""
    if not os.path.isdir(project_dir):
        return []
    out: List[SessionInfo] = []
    for path in glob.glob(os.path.join(project_dir, "*.jsonl")):
        session_id = os.path.basename(path)[: -len(".jsonl")]
        subagents = os.path.join(project_dir, session_id, "subagents")
        agent_count = 0
        if os.path.isdir(subagents):
            agent_count = len(glob.glob(os.path.join(subagents, "agent-*.meta.json")))
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append(SessionInfo(session_id=session_id, modified_at=stat.st_mtime,
                               agent_count=agent_count))
    out.sort(key=lambda s: s.modified_at, reverse=True)
    return out
