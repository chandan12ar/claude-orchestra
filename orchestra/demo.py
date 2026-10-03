"""A synthetic multi-agent session, so the dashboard can be tried without a real run.

``python -m orchestra --demo`` builds a throwaway ``~/.claude`` tree in a temp
directory, points the server at it and keeps the running agents moving, so every
view has something to show: parallel waves, a nested agent, a handoff, a failure,
a possible loop, a stalled agent, a write conflict, a permission prompt and a
price table. Nothing here touches the real ``~/.claude``.

The scenario is fixed (and the numbers seeded) so screenshots and tests are
reproducible; only the clock moves.
"""

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from orchestra.locate import SessionPaths

SESSION_ID = "demo-checkout-v2"
PROJECT_NAME = "northwind-shop"
CWD = "/home/dev/" + PROJECT_NAME

MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}

# Demo prices: illustrative numbers for the cost view, not anyone's real rates.
PRICES = {"currency": "USD", "models": {
    "claude-haiku*": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_create": 1.25},
    "claude-sonnet*": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_create": 3.75},
    "claude-opus*": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_create": 18.75},
}}

SESSION_AGE_S = 780          # the demo session "started" this long ago


@dataclass
class _Agent:
    key: str
    desc: str
    kind: str                 # agentType
    model: str                # alias
    start: int                # seconds after the session began
    end: Optional[int]        # None while running or stalled
    status: str               # completed | failed | running | stalled
    tools: List[Tuple[str, str]] = field(default_factory=list)
    result: str = ""
    depth: int = 1
    parent: Optional[str] = None      # key of the launching agent; None = orchestrator
    wave: str = ""
    quiet_for: int = 0        # stalled: seconds since its last activity
    loop: bool = False

    @property
    def agent_id(self) -> str:
        return "a" + hashlib.sha1(self.key.encode()).hexdigest()[:16]

    @property
    def tool_use_id(self) -> str:
        return "toolu_" + hashlib.sha1(("t" + self.key).encode()).hexdigest()[:20]


def _read(*paths: str) -> List[Tuple[str, str]]:
    return [("Read", CWD + "/" + p) for p in paths]


def _scenario() -> List[_Agent]:
    audit = ("Checkout today is a single 900-line handler. Cart totals are recomputed in "
             "three places and the discount rules disagree between the cart page and the "
             "payment step. Findings are in docs/audit.md.")
    providers = ("Compared Stripe, Adyen and Braintree on webhooks, 3-D Secure and refunds. "
                 "Recommend Stripe PaymentIntents with signed webhooks. See docs/providers.md.")
    events = ("Eleven analytics events fire during checkout; four have no consumer and "
              "two are sent twice. The list is in docs/events.md.")
    design = ("Split checkout into a cart service, a payment adapter and a thin UI. The "
              "payment adapter owns webhook handling. The full design is in docs/DESIGN.md.")
    return [
        _Agent("audit", "Audit the current checkout flow", "Explore", "haiku", 0, 118, "completed",
               _read("src/checkout/handler.ts", "src/checkout/totals.ts", "src/cart/page.tsx")
               + [("Grep", "applyDiscount"), ("Grep", "computeTotal"),
                  ("Write", CWD + "/docs/audit.md")], audit, wave="research"),
        _Agent("providers", "Compare payment provider APIs", "general-purpose", "sonnet", 0, 164,
               "completed",
               [("WebSearch", "stripe paymentintents 3ds2 webhooks"),
                ("WebFetch", "https://docs.stripe.com/payments/payment-intents"),
                ("WebFetch", "https://docs.adyen.com/online-payments/"),
                ("WebSearch", "braintree vs stripe refunds api"),
                ("Write", CWD + "/docs/providers.md")], providers, wave="research"),
        _Agent("events", "Inventory analytics events", "Explore", "haiku", 0, 96, "completed",
               [("Grep", "track\\("), ("Grep", "analytics.emit")]
               + _read("src/analytics/events.ts", "src/checkout/handler.ts")
               + [("Write", CWD + "/docs/events.md")], events, wave="research"),
        _Agent("design", "Design the checkout v2 architecture", "Plan", "opus", 170, 330,
               "completed",
               _read("docs/audit.md", "docs/providers.md", "docs/events.md")
               + [("Glob", "src/**/*.ts"), ("Write", CWD + "/docs/DESIGN.md")],
               design, wave="design"),
        _Agent("cart", "Implement the cart service", "general-purpose", "sonnet", 340, 560,
               "completed",
               _read("docs/DESIGN.md", "src/checkout/totals.ts")
               + [("Write", CWD + "/src/cart/service.ts"), ("Write", CWD + "/src/cart/store.ts"),
                  ("Write", CWD + "/src/cart/types.ts"),
                  ("Bash", "npm run typecheck"), ("Edit", CWD + "/src/cart/service.ts"),
                  ("Bash", "npm run typecheck")],
               "Cart service is in src/cart with totals computed once, in one place.",
               wave="build"),
        _Agent("payments", "Build the payment adapter", "general-purpose", "sonnet", 340, 610,
               "completed",
               _read("docs/DESIGN.md", "docs/providers.md")
               + [("Write", CWD + "/src/payments/adapter.ts"),
                  ("Write", CWD + "/src/payments/intents.ts"),
                  ("Bash", "npm run typecheck"), ("Edit", CWD + "/src/payments/adapter.ts")],
               "Payment adapter wraps Stripe PaymentIntents; webhooks are handled by the "
               "nested webhook agent.", wave="build"),
        _Agent("webhooks", "Handle payment webhooks", "general-purpose", "haiku", 405, 540,
               "completed",
               _read("docs/providers.md", "src/payments/adapter.ts")
               + [("Write", CWD + "/src/payments/webhooks.ts"),
                  ("Bash", "npm run test -- webhooks")],
               "Webhook handler verifies signatures and is idempotent on event id.",
               depth=2, parent="payments", wave="build"),
        _Agent("ui", "Build the checkout UI", "general-purpose", "sonnet", 340, None, "running",
               _read("docs/DESIGN.md", "src/cart/page.tsx")
               + [("Write", CWD + "/src/ui/Checkout.tsx"), ("Write", CWD + "/src/ui/Summary.tsx"),
                  ("Write", CWD + "/src/cart/types.ts"),
                  ("Bash", "npm run lint"), ("Edit", CWD + "/src/ui/Checkout.tsx")],
               wave="build"),
        _Agent("migration", "Write the database migration", "general-purpose", "haiku", 345, 470,
               "completed",
               _read("docs/DESIGN.md", "db/schema.sql")
               + [("Write", CWD + "/db/migrations/0042_checkout_v2.sql"),
                  ("Bash", "npm run db:check")],
               "Migration 0042 adds payment_intents and is reversible.", wave="build"),
        _Agent("unit", "Write the unit tests", "general-purpose", "sonnet", 620, 700, "failed",
               _read("src/cart/service.ts", "src/payments/adapter.ts")
               + [("Write", CWD + "/tests/cart.test.ts"), ("Bash", "npm test"),
                  ("Edit", CWD + "/tests/cart.test.ts"), ("Bash", "npm test")],
               "Tests still fail: 3 of 41 assertions fail in cart rounding.", wave="verify"),
        _Agent("e2e", "Run the end-to-end suite", "general-purpose", "sonnet", 625, None, "running",
               _read("tests/e2e/checkout.spec.ts")
               + [("Bash", "npm run e2e -- checkout.spec.ts")] * 9, wave="verify", loop=True),
        _Agent("security", "Review the checkout for security issues", "general-purpose", "opus",
               400, None, "stalled",
               _read("src/payments/adapter.ts", "src/payments/webhooks.ts")
               + [("Grep", "process.env"), ("Bash", "npm audit --omit=dev")],
               wave="review", quiet_for=330),
        _Agent("docs", "Update the developer docs", "general-purpose", "haiku", 700, None, "running",
               _read("docs/DESIGN.md", "README.md")
               + [("Write", CWD + "/docs/checkout.md")], wave="wrapup"),
    ]


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + "{:03d}Z".format(stamp.microsecond // 1000)


def _write_jsonl(path: str, entries: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _append_jsonl(path: str, entries: List[Dict[str, Any]]) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


_TOOL_FIELD = {"Read": "file_path", "Write": "file_path", "Edit": "file_path",
               "Glob": "pattern", "Grep": "pattern", "Bash": "command",
               "WebFetch": "url", "WebSearch": "query", "Agent": "description"}

_SIZE = {"haiku": (900, 260), "sonnet": (2400, 620), "opus": (5200, 1100)}


class _Clock:
    """Wall-clock offsets, fixed at construction so the scenario is reproducible."""

    def __init__(self, now: float) -> None:
        self.t0 = now - SESSION_AGE_S

    def at(self, offset: float) -> float:
        return self.t0 + offset


def _usage(rng: random.Random, model: str, step: int,
           cache_scale: float = 1.0) -> Dict[str, int]:
    base_in, base_out = _SIZE[model]
    cached = int(base_in * (4 + step * 0.6) * rng.uniform(0.8, 1.2) * cache_scale)
    return {"input_tokens": int(base_in * rng.uniform(0.04, 0.09)),
            "output_tokens": int(base_out * rng.uniform(0.5, 1.5)),
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": int(base_in * rng.uniform(0.3, 0.9)) if step < 2
            else int(base_in * rng.uniform(0.02, 0.1))}


def _tool_block(uid: str, name: str, target: str) -> Dict[str, Any]:
    field_name = _TOOL_FIELD.get(name, "file_path")
    params: Dict[str, Any] = {field_name: target}
    if name == "Write":
        params["content"] = "// generated by the demo"
    if name == "Agent":
        params["prompt"] = "Handle the payment webhooks."
    return {"type": "tool_use", "id": uid, "name": name, "input": params}


def _agent_entries(agent: _Agent, clock: _Clock, rng: random.Random,
                   now_off: float) -> List[Dict[str, Any]]:
    """The agent's own transcript: one assistant+result pair per tool call."""
    model_id = MODELS[agent.model]
    stop = agent.end if agent.end is not None else now_off
    if agent.status == "stalled":
        stop = now_off - agent.quiet_for
    calls = agent.tools
    span = max(stop - agent.start - 8, 1)
    entries: List[Dict[str, Any]] = []
    for i, (name, target) in enumerate(calls):
        offset = agent.start + 4 + span * (i + 1) / (len(calls) + 1)
        if agent.end is None and agent.status == "running" and offset > now_off - 2:
            break
        uid = "{}_{}".format(agent.key, i)
        entries.append({
            "isSidechain": True, "agentId": agent.agent_id, "timestamp": _iso(clock.at(offset)),
            "type": "assistant", "uuid": "u-" + uid,
            "message": {"id": "msg_" + uid, "role": "assistant", "model": model_id,
                        "content": [_tool_block(uid, name, target)],
                        "usage": _usage(rng, agent.model, i)}})
        entries.append({
            "isSidechain": True, "agentId": agent.agent_id,
            "timestamp": _iso(clock.at(offset + rng.uniform(0.6, 2.4))), "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": uid, "content": "ok"}]}})
    if agent.status in ("completed", "failed") and agent.result:
        uid = agent.key + "_final"
        entries.append({
            "isSidechain": True, "agentId": agent.agent_id,
            "timestamp": _iso(clock.at(stop - 1)), "type": "assistant", "uuid": "u-" + uid,
            "message": {"id": "msg_" + uid, "role": "assistant", "model": model_id,
                        "content": [{"type": "text", "text": agent.result}],
                        "usage": _usage(rng, agent.model, len(calls))}})
    return entries


def _launch_entry(agent: _Agent, turn: str, at: float, prompt: str,
                  sidechain_of: Optional[_Agent]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "uuid": turn, "timestamp": _iso(at), "type": "assistant", "cwd": CWD,
        "message": {"id": "msg_launch_" + agent.key, "role": "assistant",
                    "model": MODELS["opus"],
                    "content": [{"type": "tool_use", "id": agent.tool_use_id, "name": "Agent",
                                 "input": {"description": agent.desc, "prompt": prompt,
                                           "model": agent.model}}],
                    "usage": {"input_tokens": 60, "output_tokens": 180,
                              "cache_read_input_tokens": 24000,
                              "cache_creation_input_tokens": 900}}}
    if sidechain_of is not None:
        entry["isSidechain"] = True
        entry["agentId"] = sidechain_of.agent_id
    return entry


def _launched(agent: _Agent, at: float, sidechain_of: Optional[_Agent]) -> Dict[str, Any]:
    text = "Async agent launched successfully.\nagentId: {} (internal ID)\n".format(agent.agent_id)
    entry: Dict[str, Any] = {
        "uuid": "r-" + agent.key, "timestamp": _iso(at), "type": "user",
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": agent.tool_use_id,
             "content": [{"type": "text", "text": text}]}]}}
    if sidechain_of is not None:
        entry["isSidechain"] = True
        entry["agentId"] = sidechain_of.agent_id
    return entry


def _notification(agent: _Agent, at: float) -> Dict[str, Any]:
    body = ("<task-notification>\n<task-id>{}</task-id>\n<tool-use-id>{}</tool-use-id>\n"
            "<status>{}</status>\n<summary>{}</summary>\n<result>{}</result>\n"
            "</task-notification>").format(agent.agent_id, agent.tool_use_id, agent.status,
                                           agent.desc, agent.result)
    return {"uuid": "n-" + agent.key, "timestamp": _iso(at), "type": "user",
            "message": {"role": "user", "content": body}}


def _prompt_for(agent: _Agent, by_key: Dict[str, _Agent]) -> str:
    """A brief that quotes upstream results, which is what makes handoff edges."""
    parts = ["You are working on checkout v2 for the Northwind shop.\n\n## Task\n\n" + agent.desc + ".\n"]
    upstream = {"design": ("audit", "providers", "events"),
                "cart": ("design",), "payments": ("design",), "ui": ("design",),
                "migration": ("design",), "unit": ("cart", "payments"),
                "e2e": ("ui", "payments"), "security": ("design",),
                "docs": ("design",)}.get(agent.key, ())
    for key in upstream:
        quoted = by_key[key].result
        if quoted:
            parts.append("Context from an earlier agent:\n" + quoted + "\n")
    parts.append("## Deliverable\n\nReport what you changed and what is left.\n")
    return "\n".join(parts)


def build_demo(root: str, now: Optional[float] = None,
               session_id: str = SESSION_ID) -> Tuple[SessionPaths, List[_Agent]]:
    """Write the synthetic tree under ``root`` and return where the session lives."""
    now = time.time() if now is None else now
    clock = _Clock(now)
    now_off = now - clock.t0
    rng = random.Random(7)
    agents = _scenario()
    by_key = {a.key: a for a in agents}

    project = os.path.join(root, "projects", PROJECT_NAME)
    session_jsonl = os.path.join(project, session_id + ".jsonl")
    subagents = os.path.join(project, session_id, "subagents")

    main: List[Dict[str, Any]] = []
    turns: Dict[str, str] = {}
    for agent in agents:
        if agent.parent is None:
            turns.setdefault(agent.wave, "turn-" + agent.wave)
    nested: Dict[str, List[Dict[str, Any]]] = {}
    for agent in agents:
        launcher = by_key[agent.parent] if agent.parent else None
        at = clock.at(agent.start)
        turn = turns[agent.wave] if launcher is None else "turn-" + agent.key
        launch = _launch_entry(agent, turn, at,
                               _prompt_for(agent, by_key), launcher)
        ack = _launched(agent, at + 1.0, launcher)
        if launcher is None:
            main += [launch, ack]
        else:
            nested.setdefault(launcher.key, []).extend([launch, ack])
        if agent.status in ("completed", "failed") and agent.end is not None:
            main.append(_notification(agent, clock.at(agent.end)))
    main.sort(key=lambda e: e["timestamp"])
    main.append({"uuid": "orch-now", "timestamp": _iso(now - 3), "type": "assistant", "cwd": CWD,
                 "message": {"id": "msg_orch_now", "role": "assistant", "model": MODELS["opus"],
                             "content": [{"type": "text", "text": "Waiting on the verify wave."}],
                             "usage": {"input_tokens": 80, "output_tokens": 240,
                                       "cache_read_input_tokens": 61000,
                                       "cache_creation_input_tokens": 1200}}})
    _write_jsonl(session_jsonl, main)

    os.makedirs(subagents, exist_ok=True)
    for agent in agents:
        with open(os.path.join(subagents, "agent-{}.meta.json".format(agent.agent_id)),
                  "w", encoding="utf-8") as fh:
            json.dump({"agentType": agent.kind, "description": agent.desc,
                       "toolUseId": agent.tool_use_id, "spawnDepth": agent.depth,
                       "requestShape": "background", "model": agent.model}, fh)
        entries = _agent_entries(agent, clock, rng, now_off)
        entries = nested.get(agent.key, []) + entries
        entries.sort(key=lambda e: e["timestamp"])
        _write_jsonl(os.path.join(subagents, "agent-{}.jsonl".format(agent.agent_id)), entries)

    paths = SessionPaths(session_id=session_id, session_jsonl=session_jsonl,
                         subagents_dir=subagents, project_dir=project)
    return paths, agents


def write_prices(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(PRICES, fh, indent=2)


def write_events(spool: Any, session_id: str, now: Optional[float] = None) -> None:
    """A pending permission prompt, as a hook would have recorded it."""
    from orchestra.events import NOTIFICATION, SESSION_START, Event
    now = time.time() if now is None else now
    spool.append(Event(kind=SESSION_START, session_id=session_id, ts=now - SESSION_AGE_S,
                       cwd=CWD, detail={"source": "startup", "model": MODELS["opus"]}))
    spool.append(Event(kind=NOTIFICATION, session_id=session_id, ts=now - 40, cwd=CWD,
                       detail={"notification_type": "permission_prompt",
                               "message": "Claude needs your permission to use Bash"}))


class Simulator(threading.Thread):
    """Keeps the running agents moving, so the live views have something to do."""

    def __init__(self, paths: SessionPaths, agents: List[_Agent], period_s: float = 4.0) -> None:
        super().__init__(daemon=True)
        self.paths = paths
        self.agents = agents
        self.period_s = period_s
        self.stop_event = threading.Event()
        self._rng = random.Random(11)
        self._step = 0

    def tick(self, now: Optional[float] = None) -> int:
        """Append one tool call to every agent that is still running. Returns how many."""
        now = time.time() if now is None else now
        self._step += 1
        moved = 0
        for agent in self.agents:
            if agent.status != "running":
                continue
            if agent.loop:
                name, target = "Bash", "npm run e2e -- checkout.spec.ts"
            else:
                name, target = self._next_call(agent)
            uid = "{}_live{}".format(agent.key, self._step)
            model_id = MODELS[agent.model]
            path = os.path.join(self.paths.subagents_dir, "agent-{}.jsonl".format(agent.agent_id))
            _append_jsonl(path, [
                {"isSidechain": True, "agentId": agent.agent_id, "timestamp": _iso(now),
                 "type": "assistant", "uuid": "u-" + uid,
                 "message": {"id": "msg_" + uid, "role": "assistant", "model": model_id,
                             "content": [_tool_block(uid, name, target)],
                             "usage": _usage(self._rng, agent.model, 3 + self._step % 5, 0.2)}},
                {"isSidechain": True, "agentId": agent.agent_id, "timestamp": _iso(now + 1.2),
                 "type": "user",
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": uid, "content": "ok"}]}}])
            moved += 1
        # The orchestrator is alive too: this is what keeps the session "live".
        _append_jsonl(self.paths.session_jsonl, [
            {"uuid": "orch-{}".format(self._step), "timestamp": _iso(now), "type": "assistant",
             "cwd": CWD, "message": {"id": "msg_orch_{}".format(self._step), "role": "assistant",
                                     "model": MODELS["opus"],
                                     "content": [{"type": "text", "text": "Still waiting."}],
                                     "usage": {"input_tokens": 20, "output_tokens": 40,
                                               "cache_read_input_tokens": 4000}}}])
        return moved

    def _next_call(self, agent: _Agent) -> Tuple[str, str]:
        pool = {"ui": [("Edit", CWD + "/src/ui/Checkout.tsx"), ("Bash", "npm run lint"),
                       ("Read", CWD + "/src/ui/Summary.tsx"), ("Edit", CWD + "/src/ui/Summary.tsx"),
                       ("Bash", "npm run typecheck")],
                "docs": [("Read", CWD + "/docs/DESIGN.md"), ("Edit", CWD + "/docs/checkout.md"),
                         ("Grep", "checkout"), ("Edit", CWD + "/README.md")]}.get(
            agent.key, [("Read", CWD + "/README.md")])
        return pool[self._step % len(pool)]

    def run(self) -> None:
        while not self.stop_event.wait(self.period_s):
            try:
                self.tick()
            except OSError:
                pass            # a file briefly locked by the reader; try next tick

    def stop(self) -> None:
        self.stop_event.set()
