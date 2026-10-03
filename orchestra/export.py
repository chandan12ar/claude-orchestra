"""Getting a run OUT of the dashboard: JSON for tools, CSV for spreadsheets.

Works from the same scrubbed summary the API serves, so an export can never
contain anything the dashboard itself would not show.
"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Columns, in order. Stable on purpose: people build spreadsheets and scripts
# on top of an export, so adding a column goes at the END.
AGENT_COLUMNS = (
    "agent_id", "parent_agent_id", "agent_type", "description", "model",
    "launch_mode", "status", "started_at", "ended_at", "duration_s",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens",
    "cost", "tool_calls", "files_written", "possible_loop",
)

# A spreadsheet runs a cell that starts with one of these as a FORMULA. Agent
# descriptions come from prompt text, which is not trusted.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

BOM = "﻿"      # lets Excel open the file as UTF-8 instead of guessing


def safe_cell(value: Any) -> Any:
    """Neutralise a text cell that a spreadsheet would execute. Numbers pass through."""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def iso(timestamp: Optional[float]) -> str:
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def _loop_text(loop: Optional[Dict[str, Any]]) -> str:
    if not loop:
        return ""
    calls = loop.get("calls") or []
    names = " <-> ".join("{} {}".format(c.get("tool", ""), c.get("target", "")).strip()
                         for c in calls)
    return "{} x{}: {}".format(loop.get("kind", ""), loop.get("count", ""), names)


def agent_row(agent: Dict[str, Any]) -> List[Any]:
    tokens = agent.get("tokens") or {}
    cost = agent.get("cost")
    values = {
        "agent_id": agent.get("agent_id", ""),
        "parent_agent_id": agent.get("parent_agent_id") or "",
        "agent_type": agent.get("agent_type", ""),
        "description": agent.get("description", ""),
        "model": agent.get("model", ""),
        "launch_mode": agent.get("launch_mode", ""),
        "status": agent.get("status", ""),
        "started_at": iso(agent.get("started_at")),
        "ended_at": iso(agent.get("ended_at")),
        "duration_s": ("" if agent.get("duration_s") is None
                       else round(agent["duration_s"], 1)),
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "cache_read_tokens": tokens.get("cache_read", 0),
        "cache_create_tokens": tokens.get("cache_create", 0),
        "cost": "" if cost is None else round(cost, 6),
        "tool_calls": agent.get("tool_call_count", 0),
        "files_written": agent.get("files_written_count", 0),
        "possible_loop": _loop_text(agent.get("loop")),
    }
    return [safe_cell(values[c]) for c in AGENT_COLUMNS]


def agents_csv(summary: Dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(AGENT_COLUMNS)
    for agent in summary.get("agents", []):
        writer.writerow(agent_row(agent))
    return BOM + out.getvalue()


def run_json(summary: Dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)


def filename(summary: Dict[str, Any], extension: str) -> str:
    safe = "".join(ch for ch in str(summary.get("session_id", ""))
                   if ch.isalnum() or ch in "-_")[:16] or "session"
    return "cuelight-{}.{}".format(safe, extension)


FORMATS = {"csv": ("text/csv; charset=utf-8", agents_csv),
           "json": ("application/json; charset=utf-8", run_json)}


def render(summary: Dict[str, Any], fmt: str):
    """(content_type, body, filename) for a format name, or None if unknown."""
    entry = FORMATS.get(fmt)
    if entry is None:
        return None
    content_type, fn = entry
    return content_type, fn(summary), filename(summary, fmt)
