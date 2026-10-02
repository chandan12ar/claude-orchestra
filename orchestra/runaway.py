"""Spotting an agent that is going in circles.

Deliberately simple and explainable: two patterns, both visible in the tool-call
list the reader can open themselves. A loop is reported as POSSIBLE with its
evidence, never as a verdict -- polling a build with the same Bash command, or
an edit/test cycle, looks exactly like this and can be perfectly legitimate.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestra.model import ToolCall

# Calls that are repetitive by nature and say nothing about being stuck.
IGNORED_TOOLS = ("TodoWrite", "TaskUpdate", "TaskCreate", "TaskList", "TaskGet")


@dataclass
class Loop:
    kind: str                     # "repeat" | "cycle"
    count: int                    # how many calls the pattern spans
    calls: List[Tuple[str, str]]  # the distinct (tool, target) pairs involved

    def to_dict(self) -> Dict[str, Any]:
        first = self.calls[0]
        return {"kind": self.kind, "count": self.count,
                "tool": first[0], "target": first[1],
                "calls": [{"tool": t, "target": g} for t, g in self.calls]}


def detect_loop(tool_calls: Sequence[ToolCall], repeats: int,
                cycle_calls: int) -> Optional[Loop]:
    """The pattern the agent is in RIGHT NOW (at the end of its call list)."""
    calls = [(c.name, c.target) for c in tool_calls
             if c.name and c.name not in IGNORED_TOOLS]

    # 1. The same call, repeated back to back.
    if calls and repeats > 1:
        last, run = calls[-1], 0
        for call in reversed(calls):
            if call != last:
                break
            run += 1
        if run >= repeats:
            return Loop("repeat", run, [last])

    # 2. A tight cycle: the last N calls use only two distinct calls and
    # alternate between them (A B A B ...), not merely contain both.
    if cycle_calls >= 4 and len(calls) >= cycle_calls:
        window = calls[-cycle_calls:]
        distinct = list(dict.fromkeys(window))
        if len(distinct) == 2:
            alternating = all(window[i] != window[i + 1] for i in range(len(window) - 1))
            if alternating:
                return Loop("cycle", cycle_calls, distinct)
    return None
