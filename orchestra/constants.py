"""Every tunable threshold in Orchestra. No logic lives here."""

STALL_THRESHOLD_S = 300
SESSION_LIVE_THRESHOLD_S = 600
HUB_FILE_THRESHOLD = 3
HANDOFF_CONTAINMENT = 0.15
HANDOFF_RUN_WORDS = 40
SHINGLE_SIZE = 8
IDLE_SHUTDOWN_S = 1800
DEFAULT_PORT = 7717

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STALLED = "stalled"
ORPHANED = "orphaned"
UNKNOWN = "unknown"

ORCHESTRATOR_ID = "main"
