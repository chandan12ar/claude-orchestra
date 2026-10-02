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
        self._reset_paths: set = set()
        self.diagnostics: Dict[str, int] = {"unparsable_lines": 0, "torn_reads": 0}

    def consume_reset(self, path: str) -> bool:
        """True once if the last read_new() for path restarted from byte 0
        because the file had shrunk (truncated or replaced). A caller that
        accumulates state across calls (e.g. a token-count digest) must
        discard that state too, or the pre-truncation entries get summed
        in twice when the file is re-read from the start."""
        key = os.path.normcase(path)
        if key in self._reset_paths:
            self._reset_paths.discard(key)
            return True
        return False

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
            self._reset_paths.add(key)
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
