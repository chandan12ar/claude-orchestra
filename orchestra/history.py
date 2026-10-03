"""Run history: how did this run compare with the last ones?

Strictly opt-in (ORCHESTRA_HISTORY=on), because unlike everything else here it
writes to disk and keeps data after the session is gone. So it keeps the least
that answers the question:

  METRICS ONLY -- counts, durations, tokens, cost, the project directory. No
  prompts, briefs, results, task descriptions, or file paths.

It lives in the per-user data directory (never ~/.claude, never the OS temp
dir), is pruned by age and by count, and can never break the dashboard: every
database problem is swallowed and surfaced as `error`.
"""

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from orchestra import constants as C
from orchestra.redact import scrub

SCHEMA_VERSION = 1

# (column, SQL type). Order is the order shown to the user.
COLUMNS = (
    ("session_id", "TEXT PRIMARY KEY"),
    ("project_path", "TEXT"), ("project_name", "TEXT"),
    ("first_seen_at", "REAL"), ("last_seen_at", "REAL"),
    ("started_at", "REAL"), ("ended_at", "REAL"), ("session_live", "INTEGER"),
    ("agents", "INTEGER"), ("completed", "INTEGER"), ("failed", "INTEGER"),
    ("running", "INTEGER"), ("stalled", "INTEGER"), ("waiting", "INTEGER"),
    ("wall_s", "REAL"), ("tokens_total", "INTEGER"), ("cache_hit_ratio", "REAL"),
    ("cost", "REAL"), ("currency", "TEXT"),
    ("write_conflicts", "INTEGER"), ("loops", "INTEGER"),
    ("edges_exact", "INTEGER"), ("edges_inferred", "INTEGER"),
)
NAMES = tuple(name for name, _ in COLUMNS)

# Metrics a comparison reports a change for.
COMPARABLE = ("agents", "completed", "failed", "wall_s", "tokens_total",
              "cost", "write_conflicts", "loops", "cache_hit_ratio")

MIN_INTERVAL_S = 30.0       # at most one write per session per this long


def enabled() -> bool:
    return os.environ.get("ORCHESTRA_HISTORY", "").strip().lower() in (
        "on", "1", "true", "yes")


def default_path() -> str:
    override = os.environ.get("ORCHESTRA_HISTORY_DB")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share")
    path = os.path.join(base, "cuelight", "history.sqlite")
    legacy = os.path.join(base, "workflow", "history.sqlite")
    # Before 0.4.0 the product was "Workflow": keep appending to history already recorded there.
    return legacy if not os.path.exists(path) and os.path.exists(legacy) else path


def metrics_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """The few numbers worth keeping from a run summary. Nothing textual but the
    project directory (scrubbed)."""
    totals = summary.get("totals") or {}
    tokens = dict(totals.get("tokens") or {})
    orchestrator = (summary.get("orchestrator") or {}).get("tokens") or {}
    for kind, count in orchestrator.items():
        tokens[kind] = tokens.get(kind, 0) + count
    context = tokens.get("input", 0) + tokens.get("cache_read", 0) + tokens.get("cache_create", 0)
    cost = summary.get("cost") or {}
    path = scrub(summary.get("project_path") or "")
    edges = summary.get("edges") or []
    return {
        "session_id": summary.get("session_id", ""),
        "project_path": path,
        "project_name": path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1],
        "started_at": summary.get("started_at"),
        "ended_at": summary.get("ended_at"),
        "session_live": 1 if summary.get("session_live") else 0,
        "agents": totals.get("agents", 0),
        "completed": totals.get("completed", 0),
        "failed": totals.get("failed", 0) + totals.get("orphaned", 0),
        "running": totals.get("running", 0),
        "stalled": totals.get("stalled", 0),
        "waiting": totals.get("waiting", 0),
        "wall_s": totals.get("wall_time_s"),
        "tokens_total": sum(tokens.values()),
        "cache_hit_ratio": (tokens.get("cache_read", 0) / context) if context else None,
        "cost": cost.get("total") if cost.get("enabled") else None,
        "currency": cost.get("currency") if cost.get("enabled") else None,
        "write_conflicts": len(summary.get("write_conflicts") or []),
        "loops": sum(1 for a in summary.get("agents", []) if a.get("loop")),
        "edges_exact": sum(1 for e in edges if e.get("confidence") == "exact"),
        "edges_inferred": sum(1 for e in edges if e.get("confidence") != "exact"),
    }


class HistoryStore:
    def __init__(self, path: Optional[str] = None,
                 now_fn=time.time) -> None:
        self.path = path or default_path()
        self.now_fn = now_fn
        self.error = ""
        self._last: Dict[str, tuple] = {}      # session_id -> (written_at, live)
        self._ready = False

    # -- plumbing -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        fresh = not os.path.exists(self.path)
        conn = sqlite3.connect(self.path, timeout=3.0)
        try:
            if fresh:
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            if not self._ready:
                conn.execute("CREATE TABLE IF NOT EXISTS runs ({})".format(
                    ", ".join("{} {}".format(n, t) for n, t in COLUMNS)))
                conn.execute("CREATE INDEX IF NOT EXISTS runs_seen ON runs(last_seen_at)")
                conn.execute("PRAGMA user_version = {}".format(SCHEMA_VERSION))
                conn.commit()
                self._ready = True
            conn.row_factory = sqlite3.Row
        except BaseException:
            # A handle left open on a broken file blocks deleting or replacing
            # it on Windows, which is exactly what recovery needs to do.
            conn.close()
            raise
        return conn

    # -- writing ------------------------------------------------------------

    def maybe_record(self, summary: Dict[str, Any]) -> bool:
        """Record a run, rate-limited. Returns True if a row was written."""
        sid = summary.get("session_id")
        if not sid or not summary.get("agents"):
            return False
        now = self.now_fn()
        live = bool(summary.get("session_live"))
        previous = self._last.get(sid)
        if previous and previous[1] == live and now - previous[0] < MIN_INTERVAL_S:
            return False
        try:
            self._upsert(metrics_from_summary(summary), now)
            self._last[sid] = (now, live)
            self.error = ""
            return True
        except (sqlite3.Error, OSError) as exc:
            self.error = "history unavailable: {}".format(exc)
            return False

    def _upsert(self, row: Dict[str, Any], now: float) -> None:
        conn = self._connect()
        try:
            row = dict(row, last_seen_at=now)
            existing = conn.execute("SELECT first_seen_at FROM runs WHERE session_id = ?",
                                    (row["session_id"],)).fetchone()
            row["first_seen_at"] = existing["first_seen_at"] if existing else now
            columns = [n for n in NAMES if n in row]
            conn.execute(
                "INSERT OR REPLACE INTO runs ({}) VALUES ({})".format(
                    ", ".join(columns), ", ".join("?" for _ in columns)),
                [row[n] for n in columns])
            self._prune(conn, now)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _prune(conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM runs WHERE last_seen_at < ?",
                     (now - C.HISTORY_DAYS * 86400,))
        conn.execute(
            "DELETE FROM runs WHERE session_id NOT IN "
            "(SELECT session_id FROM runs ORDER BY last_seen_at DESC LIMIT ?)",
            (C.HISTORY_MAX_RUNS,))

    # -- reading ------------------------------------------------------------

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), C.HISTORY_MAX_RUNS))
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY COALESCE(started_at, last_seen_at) DESC "
                    "LIMIT ?", (limit,)).fetchall()
            finally:
                conn.close()
            self.error = ""
            return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as exc:
            self.error = "history unavailable: {}".format(exc)
            return []

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM runs WHERE session_id = ?",
                                   (session_id,)).fetchone()
            finally:
                conn.close()
            return dict(row) if row else None
        except (sqlite3.Error, OSError) as exc:
            self.error = "history unavailable: {}".format(exc)
            return None

    def compare(self, a_id: str, b_id: str) -> Optional[Dict[str, Any]]:
        """b relative to a. None if either run is not in the history."""
        a, b = self.get(a_id), self.get(b_id)
        if a is None or b is None:
            return None
        delta: Dict[str, Any] = {}
        for key in COMPARABLE:
            x, y = a.get(key), b.get(key)
            if x is None or y is None:
                delta[key] = None
                continue
            delta[key] = {"a": x, "b": y, "change": y - x,
                          "pct": ((y - x) / x * 100.0) if x else None}
        return {"a": a, "b": b, "delta": delta}
