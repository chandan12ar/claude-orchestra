"""What each agent was told: the instruction files (CLAUDE.md, rules, memory) and skills it had.

Claude Code records them in every transcript it writes, subagents' included: an
``instructions`` attachment when a context starts (``files``: path, type such as User,
Project, Local or AutoMem, and content) and a ``nested_memory`` attachment when a
CLAUDE.md is loaded later because the agent touched a file under it. A ``skill_listing``
attachment names the skills offered. Only paths, types and sizes are kept, never the
content: what an agent was told is a question about which files, not a copy of them.

The question that matters is coverage: did an agent run with the project rules the main
session had? orchestra.insights compares each agent's files with the main session's.
"""

import ast
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from orchestra.edges import normalize_path
from orchestra.parent import parse_timestamp
from orchestra.redact import scrub

MAX_FILES = 100
MAX_SKILLS = 200
# Instruction types that belong to the project, so an agent without them ran without its rules.
PROJECT_TYPES = ("Project", "Local")


def _as_dict(value: Any) -> Dict[str, Any]:
    """nested_memory content is a dict, or (in some versions) its Python repr as a string."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("{"):
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class ContextLog:
    def __init__(self) -> None:
        self.files: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.skills: List[str] = []
        self.skill_count = 0

    def ingest(self, entries: List[Dict[str, Any]]) -> None:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "attachment":
                continue
            item = entry.get("attachment")
            if not isinstance(item, dict):
                continue
            at = parse_timestamp(entry.get("timestamp"))
            kind = item.get("type")
            if kind == "instructions" and isinstance(item.get("files"), list):
                for f in item["files"]:
                    if isinstance(f, dict):
                        self._file(f.get("path"), f.get("type"), f.get("content"), at, "start")
            elif kind == "nested_memory":
                inner = _as_dict(item.get("content"))
                self._file(item.get("path") or inner.get("path"), inner.get("type") or "Nested",
                           inner.get("content"), at, "on file access")
            elif kind == "skill_listing":
                names = item.get("names")
                if isinstance(names, list):
                    for name in names:
                        if isinstance(name, str) and name not in self.skills and len(self.skills) < MAX_SKILLS:
                            self.skills.append(name)
                count = item.get("skillCount")
                if isinstance(count, int):
                    self.skill_count = max(self.skill_count, count)

    def _file(self, path: Any, kind: Any, content: Any, at: Optional[float], how: str) -> None:
        if not isinstance(path, str) or not path or len(self.files) >= MAX_FILES:
            return
        key = normalize_path(path)
        if key in self.files:
            return
        text = content if isinstance(content, str) else ""
        self.files[key] = {"path": path, "type": str(kind or ""), "chars": len(text),
                           "lines": len(text.splitlines()), "at": at, "how": how}

    def keys(self, types: Optional[tuple] = None) -> List[str]:
        return [k for k, f in self.files.items() if types is None or f["type"] in types]

    def empty(self) -> bool:
        return not self.files and not self.skills and not self.skill_count

    def snapshot(self) -> "ContextLog":
        copy = ContextLog()
        copy.files = OrderedDict((k, dict(v)) for k, v in self.files.items())
        copy.skills = list(self.skills)
        copy.skill_count = self.skill_count
        return copy

    def to_dict(self) -> Dict[str, Any]:
        return {"files": [dict(f, path=scrub(f["path"])) for f in self.files.values()],
                "skills": max(self.skill_count, len(self.skills)),
                "skill_names": [scrub(s) for s in self.skills[:60]]}


def coverage(main: Optional[ContextLog], agents: List[Any]) -> Optional[Dict[str, Any]]:
    """Which agents loaded the main session's project instruction files, and which files
    were loaded by how many agents. None when nothing was recorded anywhere."""
    logs = [(a, a.context) for a in agents if getattr(a, "context", None) is not None and not a.context.empty()]
    if not logs and (main is None or main.empty()):
        return None
    required = main.keys(PROJECT_TYPES) if main is not None else []
    files: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for key in (main.keys() if main is not None else []):
        f = main.files[key]
        files[key] = {"path": scrub(f["path"]), "type": f["type"], "chars": f["chars"], "main": True, "agents": 0}
    missing = []
    for agent, log in logs:
        for key in log.keys():
            f = log.files[key]
            entry = files.setdefault(key, {"path": scrub(f["path"]), "type": f["type"], "chars": f["chars"],
                                           "main": False, "agents": 0})
            entry["agents"] += 1
        lacking = [files[k]["path"] for k in required if k not in log.files]
        if lacking:
            missing.append({"agent_id": agent.agent_id, "label": scrub(agent.description)[:60],
                            "agent_type": scrub(agent.agent_type), "status": agent.status, "missing": lacking})
    covered = len(logs) - len(missing)
    return {"agents": len(logs), "required": [files[k]["path"] for k in required],
            "covered": covered if required else None, "missing": missing[:20],
            "files": sorted(files.values(), key=lambda f: (not f["main"], -f["agents"], f["path"]))[:30],
            "skills": max([log.skill_count for _, log in logs] + [main.skill_count if main else 0])}
