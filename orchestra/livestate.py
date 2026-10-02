"""Ground truth from hook events, merged with what the transcripts show.

Transcripts record what happened. They cannot say an agent is *waiting for you*,
that it died to a rate limit, or that the session was closed. Hook events can --
but they only say a prompt *appeared*. Nothing tells us it was answered, because
we deliberately do not subscribe to per-tool events. The transcript does: the
moment there is activity after the prompt, the prompt is no longer pending.

So: an attention item is pending iff no transcript activity followed it.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from orchestra import events as E
from orchestra.redact import scrub

# Clock skew between a hook's receive time and a transcript entry's own
# timestamp. Activity within this window of the event is treated as not-after.
GRACE_S = 1.0

PERMISSION = "permission"
INPUT = "input"
IDLE = "idle"
ERROR = "error"

# Higher wins when several things are pending at once.
_PRIORITY = {PERMISSION: 4, ERROR: 3, INPUT: 2, IDLE: 1}

_NOTIFICATION_KINDS = {
    "permission_prompt": PERMISSION,
    "agent_needs_input": INPUT,
    "elicitation_dialog": INPUT,
    "elicitation_url_dialog": INPUT,
    "idle_prompt": IDLE,
}


@dataclass
class Attention:
    kind: str
    since: float
    message: str = ""
    error_type: str = ""
    agent_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "since": self.since,
                "message": scrub(self.message), "error_type": self.error_type,
                "agent_id": self.agent_id}


@dataclass
class LiveState:
    has_events: bool = False
    last_event_at: Optional[float] = None
    attention: Optional[Attention] = None
    ended_at: Optional[float] = None
    end_reason: str = ""
    # agent_id -> time of its SubagentStop, only while that is its latest word.
    agent_stops: Dict[str, float] = field(default_factory=dict)
    # agent_id -> what that specific agent is waiting on.
    agent_waiting: Dict[str, Attention] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_events": self.has_events,
            "last_event_at": self.last_event_at,
            "attention": self.attention.to_dict() if self.attention else None,
            "ended": ({"at": self.ended_at, "reason": scrub(self.end_reason)}
                      if self.ended_at is not None else None),
        }


def _attention_from(event: E.Event) -> Optional[Attention]:
    if event.kind == E.NOTIFICATION:
        kind = _NOTIFICATION_KINDS.get(event.detail.get("notification_type", ""))
        if kind is None:
            return None
        return Attention(kind=kind, since=event.ts, agent_id=event.agent_id,
                         message=event.detail.get("message", ""))
    if event.kind == E.ERROR:
        return Attention(kind=ERROR, since=event.ts, agent_id=event.agent_id,
                         error_type=event.detail.get("error_type", ""),
                         message=event.detail.get("message", ""))
    return None


def derive(events: List[E.Event], activity_at: Optional[float],
           agent_activity: Optional[Dict[str, float]] = None) -> LiveState:
    """Reduce an event list to the current state.

    activity_at     latest transcript activity of the session (any agent).
    agent_activity  latest transcript activity per agent id.
    """
    state = LiveState()
    if not events:
        return state
    agent_activity = agent_activity or {}
    ordered = sorted(events, key=lambda e: e.ts)
    state.has_events = True
    state.last_event_at = ordered[-1].ts

    pending: List[Attention] = []
    agent_last: Dict[str, E.Event] = {}
    for event in ordered:
        if event.kind == E.SESSION_START:
            state.ended_at, state.end_reason = None, ""
        elif event.kind == E.SESSION_END:
            state.ended_at = event.ts
            state.end_reason = event.detail.get("reason", "")
        elif event.kind in (E.AGENT_START, E.AGENT_STOP) and event.agent_id:
            agent_last[event.agent_id] = event
        item = _attention_from(event)
        if item is not None:
            pending.append(item)

    for agent_id, event in agent_last.items():
        if event.kind != E.AGENT_STOP:
            continue
        # Activity after the stop means the agent was resumed.
        if agent_activity.get(agent_id, 0.0) > event.ts + GRACE_S:
            continue
        state.agent_stops[agent_id] = event.ts

    def still_pending(item: Attention) -> bool:
        seen = (agent_activity.get(item.agent_id) if item.agent_id else activity_at)
        return seen is None or seen <= item.since + GRACE_S

    live = [i for i in pending if still_pending(i)]
    # Only the newest attention of each kind matters; an older prompt of the
    # same kind that was never answered has been superseded.
    best: Dict[str, Attention] = {}
    for item in live:
        key = item.kind
        if item.agent_id:
            key += ":" + item.agent_id
        if key not in best or item.since >= best[key].since:
            best[key] = item
    for key, item in best.items():
        if item.agent_id and item.kind in (PERMISSION, INPUT):
            state.agent_waiting[item.agent_id] = item
    if best:
        state.attention = max(best.values(),
                              key=lambda i: (_PRIORITY[i.kind], i.since))
    return state
