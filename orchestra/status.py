"""Reconstructing an agent's lifecycle from the records it left behind.

A task-notification can fire more than once for the same agent, because a
finished agent can be resumed with SendMessage. An agent therefore has a list
of rounds, not a single start and end.
"""

import re
from typing import List, Optional, Tuple

from orchestra import constants as C
from orchestra.agentlog import AgentDigest
from orchestra.model import Round
from orchestra.parent import LaunchRecord, Notification, ResultRecord

# Since Claude Code 2.1.277 an agent hands its report back through a SubagentHandback
# tool call, and the parent's tool result or task-notification holds only this pointer.
_HANDBACK_POINTER = re.compile(r"delivered to you as a message from \S+ \(its SubagentHandback call\)")
# Timestamps come from two files; allow this much skew between them.
HANDBACK_SLACK_S = 5.0


def is_handback_pointer(text: str) -> bool:
    """The short pointer only, never a real report that happens to mention the tool."""
    return bool(text) and len(text) < 400 and bool(_HANDBACK_POINTER.search(text))


def fill_handbacks(rounds: List[Round], handbacks: List[Tuple[Optional[float], str]]) -> None:
    """Put back the report a pointer refers to: the latest one handed back by the round's end.

    Not only reports inside the round: the call comes 8-14 s before the parent hears of
    it, and the same stop is sometimes notified twice, 1 ms apart, making a "round" that
    starts after its report (both seen on real sessions).
    """
    for round_ in rounds:
        if not is_handback_pointer(round_.result):
            continue
        for at, message in handbacks:   # transcript order: the last match is the latest
            if at is not None and round_.ended_at is not None and at > round_.ended_at + HANDBACK_SLACK_S:
                continue
            if message:
                round_.result = message


def _notification_status(raw: str) -> str:
    return C.COMPLETED if (raw or "").strip().lower() == "completed" else C.FAILED


def build_rounds(launch: Optional[LaunchRecord],
                 result: Optional[ResultRecord],
                 notifications: List[Notification],
                 digest: AgentDigest) -> List[Round]:
    """One Round per observed start-to-finish pass, plus an open one if still going."""
    started = launch.launched_at if launch else None
    rounds: List[Round] = []

    if notifications:
        cursor = started
        for note in notifications:
            rounds.append(Round(started_at=cursor, ended_at=note.at,
                                status=_notification_status(note.status),
                                result=note.result))
            cursor = note.at
        last_end = rounds[-1].ended_at
        activity = digest.last_activity_at
        # Activity after the final notification means the agent was resumed.
        if activity is not None and last_end is not None and activity > last_end:
            rounds.append(Round(started_at=last_end, ended_at=None, status=C.RUNNING))
        return rounds

    if result is not None and result.launch_mode == "inline" and result.at is not None:
        return [Round(started_at=started, ended_at=result.at,
                      status=C.FAILED if result.is_error else C.COMPLETED,
                      result=result.inline_result)]

    if started is None and digest.last_activity_at is None:
        return []
    return [Round(started_at=started, ended_at=None, status=C.RUNNING)]


def compute_status(rounds: List[Round], digest: AgentDigest, now: float,
                   session_live: bool) -> str:
    """Precedence is deliberate: see the plan's Task 8 notes."""
    if not rounds:
        return C.UNKNOWN

    final = rounds[-1]
    if final.ended_at is not None:
        return final.status

    if not session_live:
        # Dying while holding an open tool call is visible evidence of failure;
        # plain absence only tells us the session went away.
        return C.FAILED if digest.ended_mid_tool else C.ORPHANED

    last_seen = digest.last_activity_at or final.started_at
    if last_seen is not None and (now - last_seen) > C.STALL_THRESHOLD_S:
        return C.STALLED
    return C.RUNNING
