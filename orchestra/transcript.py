"""Incremental JSONL reading.

Claude Code appends to these files while we read them, so a torn final line is
the normal case. The byte offset is never advanced past an incomplete line;
the next poll re-reads it whole.
"""

import json
import os
from typing import Any, Dict, List, Optional


class IncrementalReader:
    """Remembers how far into each file it has read. Reuse one per server."""

    def __init__(self) -> None:
        self._offsets: Dict[str, int] = {}
        self.diagnostics: Dict[str, int] = {"unparsable_lines": 0, "torn_reads": 0}

    def reset(self, path: Optional[str] = None) -> None:
        if path is None:
            self._offsets.clear()
        else:
            self._offsets.pop(os.path.normcase(path), None)

    def read_new(self, path: str) -> List[Dict[str, Any]]:
        """Return the entries appended since the last call. Never raises."""
        key = os.path.normcase(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            return []

        offset = self._offsets.get(key, 0)
        if size < offset:
            # File was truncated or replaced — start over rather than read garbage.
            offset = 0
        if size == offset:
            return []

        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
        except OSError:
            return []

        # Only whole lines are consumed. If the chunk does not end in a newline,
        # the tail is a partial write: leave it for next time.
        last_newline = chunk.rfind(b"\n")
        if last_newline == -1:
            self.diagnostics["torn_reads"] += 1
            return []
        if last_newline != len(chunk) - 1:
            self.diagnostics["torn_reads"] += 1
        consumable = chunk[:last_newline + 1]
        self._offsets[key] = offset + len(consumable)

        entries: List[Dict[str, Any]] = []
        for raw in consumable.decode("utf-8", errors="replace").split("\n"):
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except ValueError:
                self.diagnostics["unparsable_lines"] += 1
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
            else:
                self.diagnostics["unparsable_lines"] += 1
        return entries

    def read_json(self, path: str) -> Optional[Dict[str, Any]]:
        """Read a whole small JSON file (an agent .meta.json). None on any problem."""
        try:
            with open(path, "rb") as fh:
                parsed = json.loads(fh.read().decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
