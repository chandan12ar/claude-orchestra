"""Claude Code hook entrypoint: record one event, never get in the way.

Registered (async) for the events that carry information a transcript cannot:
session start/end, subagent start/stop, notifications, API failures, turn end.

Contract, because this runs inside every Claude Code session of every user:
  * it ALWAYS exits 0 -- a nonzero exit (2 especially) can block or noisy-fail
    Claude Code, and a monitoring tool must never do either;
  * it writes nothing to stdout and nothing to stderr;
  * it does a single bounded read of stdin and a single append, then exits;
  * it stores only whitelisted, redacted fields (see orchestra/events.py).

Runs as a plain script (`python hook.py`) so it needs no install step; the
sys.path line makes the sibling `orchestra` package importable that way.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def record(raw: str) -> bool:
    """Parse one hook payload and spool it. True if an event was written."""
    from orchestra.events import EventSpool, normalize_claude_hook

    event = normalize_claude_hook(json.loads(raw))
    if event is None:
        return False
    spool = EventSpool()
    spool.append(event)
    if event.kind == "session_start":
        spool.prune()               # opportunistic retention, once per session
    return True


def events_disabled() -> bool:
    """ORCHESTRA_EVENTS=off (or 0/false/no) turns recording off entirely."""
    return os.environ.get("ORCHESTRA_EVENTS", "").strip().lower() in (
        "off", "0", "false", "no")


def main() -> int:
    if events_disabled():
        return 0
    try:
        from orchestra.events import MAX_STDIN_BYTES
        record(sys.stdin.read(MAX_STDIN_BYTES))
    except BaseException:  # noqa: BLE001 - nothing may escape a hook
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
