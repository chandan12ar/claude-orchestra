"""Catch me up: what Claude Code itself calls a session, and what it last said about it.

Claude Code writes these into the main transcript:

* ``ai-title`` (``aiTitle``): a short title it generates, repeated through the file;
* ``custom-title`` (``customTitle``): the title you gave the session with /rename;
* a ``system`` entry with subtype ``away_summary`` (``content``, ``timestamp``): the
  "while you were away" recap it writes when you come back to a quiet session;
* ``last-prompt`` (``lastPrompt``): the start of the last thing you asked.

SessionMeta keeps the latest of each, on one line, bounded and redacted. A recap
describes the moment it was written, so it is marked stale once the session works
again. peek_title reads only the ends of a transcript, for lists (the session picker,
the fleet beyond its build budget) that must not build a whole run per session.
"""

import json
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from orchestra.parent import parse_timestamp
from orchestra.redact import scrub

MAX_TITLE = 80
MAX_RECAP = 400
MAX_PROMPT = 200
# Work this long after a recap means the recap no longer describes the session.
STALE_AFTER_S = 60.0
PEEK_BYTES = 256 * 1024
_PEEK_CACHE_SIZE = 256


def _clean(text: Any, limit: int) -> str:
    """One line, secrets scrubbed before cutting (a cut must not split a secret past
    recognition), at most ``limit`` characters with an ellipsis when cut."""
    if not isinstance(text, str):
        return ""
    text = scrub(" ".join(text.split()))
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


class SessionMeta:
    def __init__(self) -> None:
        self.ai_title = ""
        self.custom_title = ""
        self.recap = ""
        self.recap_at: Optional[float] = None
        self.last_prompt = ""
        self.last_work_at: Optional[float] = None

    def ingest(self, entries: List[Any]) -> None:
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type")
            if kind == "ai-title":
                self.ai_title = _clean(entry.get("aiTitle"), MAX_TITLE) or self.ai_title
            elif kind == "custom-title":
                self.custom_title = _clean(entry.get("customTitle"), MAX_TITLE) or self.custom_title
            elif kind == "last-prompt":
                self.last_prompt = _clean(entry.get("lastPrompt"), MAX_PROMPT) or self.last_prompt
            elif kind == "system" and entry.get("subtype") == "away_summary":
                text = _clean(entry.get("content"), MAX_RECAP)
                if text:
                    self.recap = text
                    self.recap_at = parse_timestamp(entry.get("timestamp"))
            elif kind in ("user", "assistant"):
                at = parse_timestamp(entry.get("timestamp"))
                if at is not None and (self.last_work_at is None or at > self.last_work_at):
                    self.last_work_at = at

    @property
    def title(self) -> str:
        return self.custom_title or self.ai_title

    def empty(self) -> bool:
        return not (self.title or self.recap or self.last_prompt)

    def snapshot(self) -> "SessionMeta":
        copy = SessionMeta()
        copy.__dict__.update(self.__dict__)
        return copy

    def to_dict(self) -> Optional[Dict[str, Any]]:
        if self.empty():
            return None
        stale = (self.recap_at is not None and self.last_work_at is not None
                 and self.last_work_at > self.recap_at + STALE_AFTER_S)
        return {"title": self.title,
                "title_source": "you" if self.custom_title else ("claude" if self.ai_title else ""),
                "recap": self.recap, "recap_at": self.recap_at if self.recap else None,
                "recap_stale": bool(self.recap and stale),
                "last_prompt": self.last_prompt}


# -- peeking at a transcript's title without reading all of it ---------------

_cache: "OrderedDict[Tuple[str, int, int], Dict[str, str]]" = OrderedDict()
_cache_lock = threading.Lock()


def _titles_in(text: str) -> Tuple[str, str]:
    """The last ai-title and custom-title in a chunk of JSON lines."""
    ai = custom = ""
    for line in text.splitlines():
        if '"ai-title"' not in line and '"custom-title"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue          # a line cut by the chunk boundary
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "ai-title":
            ai = _clean(entry.get("aiTitle"), MAX_TITLE) or ai
        elif entry.get("type") == "custom-title":
            custom = _clean(entry.get("customTitle"), MAX_TITLE) or custom
    return ai, custom


def peek_title(path: str, max_bytes: int = PEEK_BYTES) -> Dict[str, str]:
    """{"title", "title_source"} from the end of a transcript (its start if the end has
    none), or {} when there is no title or no file. Cached per file size and mtime."""
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    key = (path, stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return dict(_cache[key])
    found: Dict[str, str] = {}
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, stat.st_size - max_bytes))
            ai, custom = _titles_in(fh.read(max_bytes).decode("utf-8", errors="replace"))
            if not (ai or custom) and stat.st_size > max_bytes:
                fh.seek(0)
                ai, custom = _titles_in(fh.read(max_bytes).decode("utf-8", errors="replace"))
    except OSError:
        return {}
    if custom or ai:
        found = {"title": custom or ai, "title_source": "you" if custom else "claude"}
    with _cache_lock:
        _cache[key] = found
        while len(_cache) > _PEEK_CACHE_SIZE:
            _cache.popitem(last=False)
    return dict(found)
