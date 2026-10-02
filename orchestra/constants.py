"""Every tunable threshold in Orchestra, in one place.

Each number can be overridden with an ``ORCHESTRA_*`` environment variable,
read once at import. A value that is missing, not a number, or outside its
allowed range is ignored in favour of the default: a typo in an environment
variable must never stop the dashboard from starting.
"""

import os
from typing import Callable, Dict, Tuple, TypeVar

T = TypeVar("T", int, float)

# name -> (environment variable, default, minimum, maximum)
TUNABLES: Dict[str, Tuple[str, float, float, float]] = {}


def _tunable(name: str, env: str, default: T, minimum: T, maximum: T,
             cast: Callable[[str], T]) -> T:
    TUNABLES[name] = (env, default, minimum, maximum)
    raw = os.environ.get(env)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
    except ValueError:
        return default
    return value if minimum <= value <= maximum else default


_BIG = 10 ** 9

# An agent with an open round and no activity this long reads as `stalled`.
STALL_THRESHOLD_S = _tunable("STALL_THRESHOLD_S", "ORCHESTRA_STALL_SECONDS",
                             300, 10, _BIG, int)
# A session whose main transcript was touched this recently counts as live.
SESSION_LIVE_THRESHOLD_S = _tunable("SESSION_LIVE_THRESHOLD_S",
                                    "ORCHESTRA_SESSION_LIVE_SECONDS",
                                    600, 10, _BIG, int)
# A file read by more than this many agents, written by none, is "shared context".
HUB_FILE_THRESHOLD = _tunable("HUB_FILE_THRESHOLD", "ORCHESTRA_HUB_FILE_THRESHOLD",
                              3, 1, _BIG, int)
HANDOFF_CONTAINMENT = _tunable("HANDOFF_CONTAINMENT",
                               "ORCHESTRA_HANDOFF_CONTAINMENT",
                               0.15, 0.01, 1.0, float)
HANDOFF_RUN_WORDS = _tunable("HANDOFF_RUN_WORDS", "ORCHESTRA_HANDOFF_RUN_WORDS",
                             40, 8, _BIG, int)
SHINGLE_SIZE = _tunable("SHINGLE_SIZE", "ORCHESTRA_SHINGLE_SIZE", 8, 2, 64, int)
# The server shuts itself down after this long with no request.
IDLE_SHUTDOWN_S = _tunable("IDLE_SHUTDOWN_S", "ORCHESTRA_IDLE_SHUTDOWN_SECONDS",
                           1800, 60, _BIG, int)
DEFAULT_PORT = _tunable("DEFAULT_PORT", "ORCHESTRA_PORT", 7717, 1024, 65535, int)
MAX_BUILDERS = _tunable("MAX_BUILDERS", "ORCHESTRA_MAX_BUILDERS", 8, 1, 1000, int)

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STALLED = "stalled"
ORPHANED = "orphaned"
WAITING = "waiting"
UNKNOWN = "unknown"

ORCHESTRATOR_ID = "main"
