"""What each agent changed: per-file diffs, from the edit results Claude Code records.

When an Edit or Write succeeds, Claude Code writes the patch it applied next to the
tool result (``toolUseResult.structuredPatch``: hunks with line numbers and ' ', '-',
'+' lines) and, for a new file, its content. That is the exact change, so nothing is
re-diffed or guessed. A transcript without it (older versions, other tools) falls back
to the tool input: Edit's old and new strings, Write's content.

Bounded: an agent keeps at most MAX_LINES diff lines; past that only the counts grow.
Every line and path is scrubbed when it leaves (to_dicts), like everything else.
"""

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from orchestra.redact import scrub
from orchestra.verify import is_scratch

MAX_LINES = 1500          # diff lines kept per agent (a static report embeds every agent's)
MAX_LINE_CHARS = 300      # a longer line is cut (after scrubbing)
MAX_FILES = 200           # files listed per agent

EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
_PATH_FIELDS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
                "NotebookEdit": "notebook_path"}


@dataclass
class Hunk:
    old_start: Optional[int]
    new_start: Optional[int]
    lines: List[str]              # each begins with " ", "-" or "+"
    at: Optional[float] = None


@dataclass
class FileChanges:
    path: str
    created: bool = False
    edits: int = 0
    added: int = 0
    removed: int = 0
    first_at: Optional[float] = None
    last_at: Optional[float] = None
    hunks: List[Hunk] = field(default_factory=list)
    truncated: bool = False       # some of its lines were not kept
    scratch: bool = False         # in a scratch or temp folder: not part of the project


def _lines(text: Any) -> List[str]:
    if not isinstance(text, str) or text == "":
        return []
    return text.replace("\r\n", "\n").split("\n")


def _hunks_from_patch(patch: Any) -> Optional[List[Hunk]]:
    if not isinstance(patch, list):
        return None
    out = []
    for h in patch:
        if not isinstance(h, dict) or not isinstance(h.get("lines"), list):
            continue
        lines = [str(l) for l in h["lines"] if isinstance(l, str) and l[:1] in (" ", "-", "+")]
        out.append(Hunk(old_start=h.get("oldStart") if isinstance(h.get("oldStart"), int) else None,
                        new_start=h.get("newStart") if isinstance(h.get("newStart"), int) else None,
                        lines=lines))
    return out


def extract(name: str, params: Dict[str, Any], result: Any) -> Optional[Dict[str, Any]]:
    """One successful edit as {path, created, hunks}; None when it names no file."""
    if name not in EDIT_TOOLS:
        return None
    result = result if isinstance(result, dict) else {}
    path = result.get("filePath") or params.get(_PATH_FIELDS[name])
    if not isinstance(path, str) or not path:
        return None
    created = result.get("type") == "create" or (name == "Write" and "originalFile" in result
                                                  and result.get("originalFile") is None)
    hunks = _hunks_from_patch(result.get("structuredPatch"))
    if created and not hunks:
        content = result.get("content", params.get("content"))
        hunks = [Hunk(old_start=0, new_start=1, lines=["+" + l for l in _lines(content)])]
    if hunks is None:
        if name == "Edit":
            hunks = [Hunk(None, None, ["-" + l for l in _lines(params.get("old_string"))]
                          + ["+" + l for l in _lines(params.get("new_string"))])]
        elif name == "MultiEdit" and isinstance(params.get("edits"), list):
            hunks = [Hunk(None, None, ["-" + l for l in _lines(e.get("old_string"))]
                          + ["+" + l for l in _lines(e.get("new_string"))])
                     for e in params["edits"] if isinstance(e, dict)]
        elif name == "NotebookEdit":
            hunks = [Hunk(None, None, ["+" + l for l in _lines(params.get("new_source"))])]
        else:
            hunks = [Hunk(None, None, ["+" + l for l in _lines(params.get("content"))])]
    return {"path": path, "created": bool(created), "hunks": [h for h in hunks if h.lines]}


class ChangeLog:
    """An agent's changes, file by file, in the order it first touched them."""

    def __init__(self) -> None:
        self.files: "OrderedDict[str, FileChanges]" = OrderedDict()
        self.kept = 0

    def add(self, change: Optional[Dict[str, Any]], at: Optional[float]) -> None:
        if not change:
            return
        f = self.files.get(change["path"])
        if f is None:
            if len(self.files) >= MAX_FILES:
                return
            f = self.files[change["path"]] = FileChanges(path=change["path"], first_at=at,
                                                          scratch=is_scratch(change["path"]))
        f.created = f.created or change["created"]
        f.edits += 1
        f.last_at = at
        for hunk in change["hunks"]:
            f.added += sum(1 for l in hunk.lines if l[:1] == "+")
            f.removed += sum(1 for l in hunk.lines if l[:1] == "-")
            room = MAX_LINES - self.kept
            if room <= 0:
                f.truncated = True
                continue
            if len(hunk.lines) > room:
                hunk.lines = hunk.lines[:room]
                f.truncated = True
            hunk.at = at
            self.kept += len(hunk.lines)
            f.hunks.append(hunk)

    def snapshot(self) -> "ChangeLog":
        """A copy the next read cannot change under a reader (hunks are never edited once kept)."""
        copy = ChangeLog()
        copy.kept = self.kept
        for path, f in self.files.items():
            copy.files[path] = replace(f, hunks=list(f.hunks))
        return copy

    def totals(self) -> Dict[str, int]:
        """Project files only: scratch files are listed but not counted."""
        real = [f for f in self.files.values() if not f.scratch]
        return {"files": len(real), "added": sum(f.added for f in real),
                "removed": sum(f.removed for f in real),
                "scratch_files": len(self.files) - len(real)}

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [{"path": scrub(f.path), "created": f.created, "edits": f.edits, "added": f.added,
                 "removed": f.removed, "first_at": f.first_at, "last_at": f.last_at,
                 "truncated": f.truncated, "scratch": f.scratch,
                 "hunks": [{"old_start": h.old_start, "new_start": h.new_start, "at": h.at,
                            "lines": _scrub_lines(h.lines)} for h in f.hunks]}
                for f in self.files.values()]


def _scrub_lines(lines: List[str]) -> List[str]:
    """Scrub a hunk as one text, so a secret spread over lines (a PEM block) is caught,
    then cut long lines. Cut after scrubbing, never before, so no secret is split."""
    out = []
    for text in scrub("\n".join(lines)).split("\n"):
        if text[:1] not in (" ", "-", "+"):
            text = " " + text
        out.append(text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + "…")
    return out
