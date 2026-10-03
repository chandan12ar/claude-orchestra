"""Ground truth from hook events, merged with what the transcripts show.

Transcripts record what happened. They cannot say an agent is *waiting for you*,
that it died to a rate limit, or that the session was closed. Hook events can --
but they only say a prompt *appeared*. Nothing tells us it was answered, because
we deliberately do not subscribe to per-tool events. The transcript does: the
moment there is activity after the prompt, the prompt is no longer pending.

So: an attention item is pending iff no transcript activity followed it.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

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


ANSWERED = "answered"        # the agent moved again after the prompt
OPEN = "open"                # still waiting, and the session is live
UNANSWERED = "unanswered"    # the session went quiet or ended with the prompt still up

# Prompts that stop an agent until you act. "Claude is waiting for your input"
# (idle) is not one: nothing is blocked, the turn is simply over.
_BLOCKING = (PERMISSION, INPUT)


@dataclass
class Wait:
    """One stretch of an agent sitting on a prompt only you could answer.

    It ends at the agent's next transcript entry. After an approved command that
    entry is the command's result, so the stretch also holds the command's run
    time: the transcript records no separate time for it.
    """
    agent_id: str
    kind: str
    start: float
    end: Optional[float] = None
    state: str = OPEN
    prompts: int = 1
    message: str = ""

    def seconds(self, now: float) -> float:
        """Answered: the measured wait. Open: up to now. Unanswered: unknown, so 0."""
        if self.state == ANSWERED and self.end is not None:
            return max(0.0, self.end - self.start)
        if self.state == OPEN:
            return max(0.0, now - self.start)
        return 0.0


def waits(events: List[E.Event],
          first_after: Callable[[str, float], Optional[float]],
          session_live: bool) -> List[Wait]:
    """Every blocking prompt as a wait interval, oldest first.

    first_after(agent_id, t) gives the earliest transcript activity after t for that
    agent ("" = the main session, which any activity answers). A prompt raised again
    while the same agent is still waiting extends that wait instead of starting one.
    """
    out: List[Wait] = []
    current: Dict[str, Wait] = {}
    for event in sorted(events, key=lambda e: e.ts):
        item = _attention_from(event)
        if item is None or item.kind not in _BLOCKING:
            continue
        owner = item.agent_id
        held = current.get(owner)
        if held is not None and (held.end is None or item.since <= held.end):
            held.prompts += 1
            continue
        end = first_after(owner, item.since + GRACE_S)
        wait = Wait(agent_id=owner, kind=item.kind, start=item.since, end=end,
                    state=ANSWERED if end is not None else (OPEN if session_live else UNANSWERED),
                    message=scrub(item.message))
        current[owner] = wait
        out.append(wait)
    return out


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
