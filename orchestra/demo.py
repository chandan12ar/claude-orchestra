"""A synthetic multi-agent session, so the dashboard can be tried without a real run.

``python -m orchestra --demo`` builds a throwaway ``~/.claude`` tree in a temp
directory, points the server at it and keeps the running agents moving, so every
view has something to show: parallel waves, a nested agent, a handoff, a failure,
a possible loop, a stalled agent, a write conflict, checked, failing and
unchecked work, permission prompts (answered
ones and one still waiting on you) and a price table. Nothing here touches the real ``~/.claude``.

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
PR_URL = "https://github.com/northwind/shop/pull/42"
DEMO_TITLE = "Checkout rewrite with payments"
# What you asked, in order: (seconds after the session began, text, how it was sent).
DEMO_PROMPTS = (
    (-6, "Plan checkout v2 for the shop: audit the current flow, compare payment providers, "
         "inventory the analytics events, then design it", "typed"),
    (327, "Design looks good. Build it: cart, payments with webhooks, the UI and the migration", "typed"),
    (611, "Commit and open a PR, then run the verify wave and write up what is left", "suggestion_accepted"),
)
DEMO_LAST_PROMPT = DEMO_PROMPTS[-1][1]
DEMO_API_ERROR = 'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'
DEMO_RECAP = ("Goal: ship checkout v2 (cart service, payment adapter, webhooks) behind a flag. "
              "The build wave is in and PR #42 is open; the verify wave is running, the unit "
              "tests failed twice and the docs agent is waiting on your permission. "
              "Next: approve the docs agent, then review the failing payment test.")


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
    asks: Tuple[int, ...] = ()   # a permission prompt just before each of these tool calls
    waiting: int = 0          # > 0: sitting on a prompt raised this many seconds ago
    fails: Tuple[int, ...] = ()  # tool calls whose result is an error (a failing test run)
    fail_output: str = "Exit code 1\n3 failing"
    timeouts: Tuple[int, ...] = ()  # commands that ran past their timeout and went to the background

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
               wave="build", asks=(3,)),
        _Agent("payments", "Build the payment adapter", "general-purpose", "sonnet", 340, 610,
               "completed",
               _read("docs/DESIGN.md", "docs/providers.md")
               + [("Write", CWD + "/src/payments/adapter.ts"),
                  ("Write", CWD + "/src/payments/intents.ts"),
                  ("Bash", "npm run typecheck"), ("Edit", CWD + "/src/payments/adapter.ts")],
               "Payment adapter wraps Stripe PaymentIntents; webhooks are handled by the "
               "nested webhook agent.", wave="build", asks=(4,)),
        _Agent("webhooks", "Handle payment webhooks", "general-purpose", "haiku", 405, 540,
               "completed",
               _read("docs/providers.md", "src/payments/adapter.ts")
               + [("Write", CWD + "/src/payments/webhooks.ts"),
                  ("Bash", "npm run test -- webhooks"),
                  ("Bash", 'git commit -m "feat: verify webhook signatures and dedupe by event id"')],
               "Webhook handler verifies signatures and is idempotent on event id.",
               depth=2, parent="payments", wave="build", asks=(0,)),
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
               "Tests still fail: 3 of 41 assertions fail in cart rounding.", wave="verify", fails=(3, 5)),
        _Agent("e2e", "Run the end-to-end suite", "general-purpose", "sonnet", 625, None, "running",
               _read("tests/e2e/checkout.spec.ts")
               + [("Bash", "npm run e2e -- checkout.spec.ts")] * 9, wave="verify", loop=True, timeouts=(1,)),
        _Agent("security", "Review the checkout for security issues", "general-purpose", "opus",
               400, None, "stalled",
               _read("src/payments/adapter.ts", "src/payments/webhooks.ts")
               + [("Grep", "process.env")] + [("Bash", "npm audit --omit=dev")] * 3,
               wave="review", quiet_for=330, fails=(3, 4, 5),
               fail_output="Exit code 1\nnpm ERR! audit endpoint returned an error (503)"),
        _Agent("docs", "Update the developer docs", "general-purpose", "haiku", 700, None, "running",
               _read("docs/DESIGN.md", "README.md")
               + [("Write", CWD + "/docs/checkout.md")], wave="wrapup", waiting=45),
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


_USER_MD = "/home/dev/.claude/CLAUDE.md"
_PROJECT_MD = CWD + "/CLAUDE.md"
_RULES_MD = CWD + "/.claude/rules/payments.md"
_NESTED_MD = CWD + "/src/payments/CLAUDE.md"
_SKILLS = ["frontend-design", "security-review", "simplify", "write-tests"]


def _context_entries(at: float, project: bool, agent: Optional["_Agent"] = None) -> List[Dict[str, Any]]:
    """The instructions and skill_listing attachments Claude Code writes when a context starts."""
    files = [{"path": _USER_MD, "type": "User", "content": "Prefer small commits.\nRun the tests before you say done.\n"}]
    if project:
        files += [{"path": _PROJECT_MD, "type": "Project",
                   "content": "# Northwind shop\n\nTypeScript, strict mode. Money is integer cents.\n" * 6},
                  {"path": _RULES_MD, "type": "Project", "content": "Never log card numbers.\nWebhooks must be idempotent.\n"}]
    out = []
    for kind, body in (("instructions", {"files": files}),
                       ("skill_listing", {"names": _SKILLS, "skillCount": len(_SKILLS), "isInitial": True})):
        entry = {"type": "attachment", "timestamp": _iso(at), "uuid": "ctx-{}-{}".format(kind, agent.key if agent else "main"),
                 "attachment": dict(body, type=kind)}
        if agent is not None:
            entry.update({"isSidechain": True, "agentId": agent.agent_id})
        out.append(entry)
    return out


def _git_result(command: str) -> Optional[Dict[str, Any]]:
    """The gitOperation Claude Code records for a successful git or gh command."""
    if command.startswith("git commit"):
        sha = hashlib.sha1(command.encode()).hexdigest()[:7]
        return {"stdout": "", "gitOperation": {"commit": {"sha": sha, "kind": "committed", "branch": "checkout-v2"}}}
    if command.startswith("git push"):
        return {"stdout": "", "gitOperation": {"push": {"branch": "checkout-v2"}}}
    if command.startswith("gh pr create"):
        return {"stdout": PR_URL, "gitOperation": {"pr": {"number": 42, "url": PR_URL, "action": "created"}}}
    return None


def _edit_result(name: str, target: str) -> Optional[Dict[str, Any]]:
    """The toolUseResult Claude Code records for a successful Write or Edit (the patch),
    or for a git command (what it did)."""
    if name == "Bash":
        return _git_result(target)
    stem = os.path.splitext(os.path.basename(target))[0]
    ext = os.path.splitext(target)[1].lower()
    if name == "Write":
        if ext == ".sql":
            lines = ["-- 0042: checkout v2", "CREATE TABLE payment_intents (", "  id TEXT PRIMARY KEY,",
                     "  cart_id TEXT NOT NULL REFERENCES carts(id),", "  amount_cents INTEGER NOT NULL,",
                     "  status TEXT NOT NULL DEFAULT 'created'", ");"]
        elif ext == ".md":
            lines = ["# " + stem.replace("-", " ").title(), "", "Written by the demo agent."]
        else:
            lines = ["// " + stem + ": checkout v2", "", "export interface " + stem.title().replace("-", "") + "Options {",
                     "  currency: string;", "  retries?: number;", "}", "",
                     "export function create" + stem.title().replace("-", "") + "(options: "
                     + stem.title().replace("-", "") + "Options) {", "  return { ...options, createdAt: Date.now() };",
                     "}"]
        content = "\n".join(lines)
        return {"type": "create", "filePath": target, "content": content, "structuredPatch": [],
                "originalFile": None}
    if name == "Edit":
        return {"filePath": target, "structuredPatch": [{
            "oldStart": 12, "oldLines": 4, "newStart": 12, "newLines": 5,
            "lines": [" export function total(items: Item[]) {",
                      "-  return items.reduce((sum, i) => sum + i.price, 0);",
                      "+  // Quantities were ignored, so a cart of two showed the price of one.",
                      "+  return items.reduce((sum, i) => sum + i.price * i.quantity, 0);",
                      " }"]}]}
    return None


def _stop_offset(agent: _Agent, now_off: float) -> float:
    if agent.status == "stalled":
        return now_off - agent.quiet_for
    if agent.waiting:
        return now_off - agent.waiting
    return agent.end if agent.end is not None else now_off


def _call_offset(agent: _Agent, i: int, now_off: float) -> float:
    """Seconds after the session began at which the agent's i-th tool call lands."""
    span = max(_stop_offset(agent, now_off) - agent.start - 8, 1)
    return agent.start + 4 + span * (i + 1) / (len(agent.tools) + 1)


# docs/providers.md as the design agent read it: long enough to be the run's biggest result.
_PROVIDERS_DOC = "".join(
    "## {0}\n\nAPI: REST with idempotency keys. Webhooks: signed, retried for 3 days. "
    "Refunds: partial and full, async. Disputes: evidence upload API. Payouts: daily, "
    "T+2 in the EU, T+3 elsewhere. SDKs: Node, Python, Go, Java. Rate limit: 100 requests "
    "a second per account, burst 200. Test mode: full parity, test cards documented.\n\n".format(name)
    for name in ("Stripe", "Adyen", "Braintree", "Mollie", "Checkout.com", "Square") * 12)


def _agent_entries(agent: _Agent, clock: _Clock, rng: random.Random,
                   now_off: float) -> List[Dict[str, Any]]:
    """The agent's own transcript: one assistant+result pair per tool call."""
    model_id = MODELS[agent.model]
    stop = _stop_offset(agent, now_off)
    calls = agent.tools
    # The read-only audit agent runs without the project's instructions; everyone else has them.
    entries: List[Dict[str, Any]] = _context_entries(clock.at(agent.start + 1.5), agent.key != "audit", agent)
    if agent.key == "payments":
        entries.append({"type": "attachment", "timestamp": _iso(clock.at(agent.start + 30)), "isSidechain": True,
                        "agentId": agent.agent_id, "uuid": "ctx-nested-payments",
                        "attachment": {"type": "nested_memory", "path": _NESTED_MD, "displayPath": "src/payments/CLAUDE.md",
                                       "content": {"path": _NESTED_MD, "type": "Project",
                                                   "content": "Amounts are cents. Use PaymentIntents only.\n"}}})
    for i, (name, target) in enumerate(calls):
        offset = _call_offset(agent, i, now_off)
        if agent.end is None and agent.status == "running" and offset > stop - 2:
            break
        uid = "{}_{}".format(agent.key, i)
        entries.append({
            "isSidechain": True, "agentId": agent.agent_id, "timestamp": _iso(clock.at(offset)),
            "type": "assistant", "uuid": "u-" + uid,
            "message": {"id": "msg_" + uid, "role": "assistant", "model": model_id,
                        "content": [_tool_block(uid, name, target)],
                        "usage": _usage(rng, agent.model, i)}})
        output = agent.fail_output if i in agent.fails else "ok"
        if i in agent.timeouts:
            output = ("Command did not complete within its 120s timeout and was moved to the background "
                      "(ID: b{}{}).".format(agent.key, i))
        if agent.key == "design" and target.endswith("docs/providers.md"):
            output = _PROVIDERS_DOC       # a long file read whole: the run's biggest result
        result = {
            "isSidechain": True, "agentId": agent.agent_id,
            "timestamp": _iso(clock.at(offset + rng.uniform(0.6, 2.4))), "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": uid, "is_error": i in agent.fails,
                 "content": output}]}}
        patch = _edit_result(name, target)
        if patch is not None:
            result["toolUseResult"] = patch
        if i in agent.timeouts:
            result["toolUseResult"] = {"stdout": "", "stderr": "", "interrupted": False,
                                       "backgroundTaskId": "b{}{}".format(agent.key, i), "timedOutAfterMs": 120000}
        entries.append(result)
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
                  sidechain_of: Optional[_Agent], context: int = 24000) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "uuid": turn, "timestamp": _iso(at), "type": "assistant", "cwd": CWD,
        "message": {"id": "msg_launch_" + agent.key, "role": "assistant",
                    "model": MODELS["opus"],
                    "content": [{"type": "tool_use", "id": agent.tool_use_id, "name": "Agent",
                                 "input": {"description": agent.desc, "prompt": prompt,
                                           "model": agent.model}}],
                    "usage": {"input_tokens": 60, "output_tokens": 180,
                              "cache_read_input_tokens": context,
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
    parts.append("## Deliverable\n\n" + _DELIVERABLES.get(agent.key, "Report what you changed and what is left.") + "\n")
    return "\n".join(parts)


# What each brief asks for, so the Agents tab sets it beside what came back.
_DELIVERABLES = {
    "audit": "The problems in today's checkout, worst first.",
    "providers": "One recommended payment provider, with the trade-offs.",
    "events": "Which analytics events fire during checkout, and which have no consumer.",
    "design": "An architecture note with the service boundaries and the order to build them.",
    "cart": "The cart service in src/cart, with its tests passing.",
    "payments": "A payment adapter for the chosen provider, with its tests passing.",
    "webhooks": "A webhook handler that verifies signatures and ignores repeats.",
    "ui": "The new checkout UI behind a feature flag.",
    "migration": "A reversible migration for payment intents.",
    "unit": "Unit tests for the cart and payments, all passing.",
    "e2e": "A green end-to-end run of the whole checkout.",
    "security": "Security findings, each with a severity and a fix.",
    "docs": "Developer docs for checkout v2: setup, flags and rollback.",
}


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
        # The orchestrator's context grows as each wave's results come back (orchestra.pressure).
        launch = _launch_entry(agent, turn, at, _prompt_for(agent, by_key), launcher,
                               24000 + int(agent.start * 210) if launcher is None else 24000)
        ack = _launched(agent, at + 1.0, launcher)
        if launcher is None:
            main += [launch, ack]
        else:
            nested.setdefault(launcher.key, []).extend([launch, ack])
        if agent.status in ("completed", "failed") and agent.end is not None:
            main.append(_notification(agent, clock.at(agent.end)))
    main += _context_entries(clock.at(0.2), True)
    # Once the build wave is in, the orchestrator commits, pushes and opens a pull request.
    for i, command in enumerate(('git commit -m "feat: checkout v2 cart service, payment adapter and migration"',
                                 "git push -u origin checkout-v2",
                                 'gh pr create --title "Checkout v2" --body "Cart, payments, webhooks"')):
        uid = "toolu_orch_git{}".format(i)
        at = clock.at(615 + i * 4)
        main.append({"uuid": "orch-git-{}".format(i), "timestamp": _iso(at), "type": "assistant", "cwd": CWD,
                     "message": {"id": "msg_orch_git{}".format(i), "role": "assistant", "model": MODELS["opus"],
                                 "content": [_tool_block(uid, "Bash", command)],
                                 "usage": {"input_tokens": 40, "output_tokens": 90,
                                           "cache_read_input_tokens": 152000 + i * 3000}}})
        main.append({"uuid": "orch-git-r{}".format(i), "timestamp": _iso(at + 1.5), "type": "user", "cwd": CWD,
                     "toolUseResult": _git_result(command),
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": uid, "content": "ok"}]}})
    main.append({"type": "pr-link", "sessionId": session_id, "prNumber": 42, "prUrl": PR_URL,
                 "prRepository": "northwind/shop", "timestamp": _iso(clock.at(624))})
    # Your prompts, each a turn that Claude Code closes with a turn_duration record.
    for i, (offset, text, source) in enumerate(DEMO_PROMPTS):
        main.append({"uuid": "you-{}".format(i), "timestamp": _iso(clock.at(offset)), "type": "user", "cwd": CWD,
                     "promptId": "prompt-{}".format(i), "promptSource": source, "origin": {"kind": "human"},
                     "message": {"role": "user", "content": text}})
    for offset, ms in ((326, 332000), (606, 279000)):
        main.append({"uuid": "turn-end-{}".format(offset), "timestamp": _iso(clock.at(offset)), "type": "system",
                     "subtype": "turn_duration", "durationMs": ms, "messageCount": 40, "isMeta": False, "cwd": CWD})
    # The API was overloaded once while the design agent worked; the orchestrator said nothing more
    # until your next prompt, and its first launch after that is the reply.
    main.append({"uuid": "orch-api-error", "timestamp": _iso(clock.at(200)), "type": "assistant", "cwd": CWD,
                 "isApiErrorMessage": True, "error": "server_error", "apiErrorStatus": 529,
                 "message": {"id": "msg_orch_api_error", "role": "assistant", "model": "<synthetic>",
                             "content": [{"type": "text", "text": DEMO_API_ERROR}],
                             "usage": {"input_tokens": 0, "output_tokens": 0}}})
    main.sort(key=lambda e: e["timestamp"])
    # The orchestrator's context was compacted while the verify wave ran, so its next call
    # wrote the (now shorter) conversation to the prompt cache again: a cache rebuild.
    main.append({"uuid": "orch-compact", "timestamp": _iso(now - 40), "type": "system",
                 "subtype": "compact_boundary", "content": "Conversation compacted", "isMeta": False,
                 "level": "info", "cwd": CWD,
                 "compactMetadata": {"trigger": "auto", "preTokens": 186000, "durationMs": 21400}})
    main.append({"uuid": "orch-now", "timestamp": _iso(now - 3), "type": "assistant", "cwd": CWD,
                 "message": {"id": "msg_orch_now", "role": "assistant", "model": MODELS["opus"],
                             "content": [{"type": "text", "text": "Waiting on the verify wave."}],
                             "usage": {"input_tokens": 80, "output_tokens": 240,
                                       "cache_read_input_tokens": 2400,
                                       "cache_creation_input_tokens": 58600}}})
    # What Claude Code calls the session and says about it: its title (written early and
    # repeated), your last prompt, and the "while you were away" recap.
    title = {"type": "ai-title", "sessionId": session_id, "aiTitle": DEMO_TITLE}
    main.insert(0, title)
    main.append(title)
    main.append({"type": "last-prompt", "sessionId": session_id, "lastPrompt": DEMO_LAST_PROMPT})
    main.append({"uuid": "orch-recap", "timestamp": _iso(now - 1), "type": "system",
                 "subtype": "away_summary", "isMeta": False, "cwd": CWD, "content": DEMO_RECAP})
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
    """The hook events of the run: its start and every permission prompt.

    The orchestrator asked once before the build wave, three build agents asked
    mid-task (two of them at the same time), and the docs agent is waiting now.
    """
    from orchestra.events import NOTIFICATION, SESSION_START, Event
    now = time.time() if now is None else now
    clock = _Clock(now)
    now_off = now - clock.t0

    def prompt(offset: float, agent_id: str, tool: str) -> None:
        spool.append(Event(kind=NOTIFICATION, session_id=session_id, ts=clock.at(offset), cwd=CWD,
                           agent_id=agent_id,
                           detail={"notification_type": "permission_prompt",
                                   "message": "Claude needs your permission to use " + tool}))

    spool.append(Event(kind=SESSION_START, session_id=session_id, ts=clock.t0,
                       cwd=CWD, detail={"source": "startup", "model": MODELS["opus"]}))
    prompt(331, "", "Agent")
    for agent in _scenario():
        for i in agent.asks:
            before = _call_offset(agent, i - 1, now_off) if i else agent.start
            prompt(before + 2.6, agent.agent_id, agent.tools[i][0])
        if agent.waiting:
            prompt(now_off - agent.waiting + 1, agent.agent_id, "Bash")


def write_side_sessions(root: str, now: Optional[float] = None) -> List[str]:
    """A few other recent sessions, so the demo's Fleet view has neighbours: one Claude Code
    titled and recapped, one you renamed whose recap is older than its latest work, one
    with only a last prompt, and one with nothing to name it. Small transcripts in the real
    format, read by the real code. Returns their session ids."""
    now = time.time() if now is None else now
    sides = [
        # session id, project, ago (s), title kind, title, recap (ago, text) or None, last prompt
        ("demo-ecg-report", "ecg-tools", 95, "ai-title", "ECG report analysis",
         (60, "You asked for an ECG analysis report with charts; the PDF is written to "
               "reports/ecg-2026-10.pdf and the summary table is done. Next: check the "
               "arrhythmia section against the cardiologist's notes."),
         "Make the report a PDF with the charts inline"),
        ("demo-orders-migration", PROJECT_NAME, 5400, "custom-title", "Migrate orders table to Postgres 17",
         (9000, "Goal: move the orders table to Postgres 17. The migration ran and the tests "
                "pass. Next: drop the old status column once reads have switched over."),
         "Now switch the reads over and drop the old column"),
        ("demo-docs-pass", "handbook", 300, "ai-title", "Tidy the onboarding handbook", None,
         "Fix the broken links in the onboarding section"),
        ("demo-scratch", "scratch", 12000, "", "", None, ""),
    ]
    made = []
    for session_id, project, ago, kind, title, recap, prompt in sides:
        cwd = "/home/dev/" + project
        end = now - ago
        start = end - 900
        entries: List[Dict[str, Any]] = []
        if kind:
            entries.append({"type": kind, "sessionId": session_id,
                            ("aiTitle" if kind == "ai-title" else "customTitle"): title})
        entries.append({"uuid": session_id + "-u1", "timestamp": _iso(start), "type": "user", "cwd": cwd,
                        "sessionId": session_id,
                        "message": {"role": "user", "content": prompt or "Look around this folder"}})
        entries.append({"uuid": session_id + "-a1", "timestamp": _iso(end), "type": "assistant", "cwd": cwd,
                        "sessionId": session_id,
                        "message": {"id": "msg_" + session_id, "role": "assistant", "model": MODELS["sonnet"],
                                    "content": [{"type": "text", "text": "Done."}],
                                    "usage": {"input_tokens": 120, "output_tokens": 340,
                                              "cache_read_input_tokens": 18000}}})
        if recap is not None:
            entries.append({"uuid": session_id + "-r", "timestamp": _iso(now - recap[0]), "type": "system",
                            "subtype": "away_summary", "isMeta": False, "cwd": cwd, "content": recap[1]})
        if prompt:
            entries.append({"type": "last-prompt", "sessionId": session_id, "lastPrompt": prompt})
        if kind:
            entries.append({"type": kind, "sessionId": session_id,
                            ("aiTitle" if kind == "ai-title" else "customTitle"): title})
        path = os.path.join(root, "projects", project, session_id + ".jsonl")
        _write_jsonl(path, entries)
        os.utime(path, (end, end))
        made.append(session_id)
    return made


class Simulator(threading.Thread):
    """Keeps the running agents moving, so the live views have something to do.

    An agent sitting on a prompt stays still: any activity would answer it.
    """

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
            if agent.status != "running" or agent.waiting:
                continue
            if agent.loop:
                name, target = "Bash", "npm run e2e -- checkout.spec.ts"
            else:
                name, target = self._next_call(agent)
            uid = "{}_live{}".format(agent.key, self._step)
            model_id = MODELS[agent.model]
            path = os.path.join(self.paths.subagents_dir, "agent-{}.jsonl".format(agent.agent_id))
            result = {"isSidechain": True, "agentId": agent.agent_id, "timestamp": _iso(now + 1.2),
                      "type": "user",
                      "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": uid, "content": "ok"}]}}
            patch = _edit_result(name, target)
            if patch is not None:
                result["toolUseResult"] = patch
            _append_jsonl(path, [
                {"isSidechain": True, "agentId": agent.agent_id, "timestamp": _iso(now),
                 "type": "assistant", "uuid": "u-" + uid,
                 "message": {"id": "msg_" + uid, "role": "assistant", "model": model_id,
                             "content": [_tool_block(uid, name, target)],
                             "usage": _usage(self._rng, agent.model, 3 + self._step % 5, 0.2)}},
                result])
            moved += 1
        # The orchestrator is alive too: this is what keeps the session "live".
        _append_jsonl(self.paths.session_jsonl, [
            {"uuid": "orch-{}".format(self._step), "timestamp": _iso(now), "type": "assistant",
             "cwd": CWD, "message": {"id": "msg_orch_{}".format(self._step), "role": "assistant",
                                     "model": MODELS["opus"],
                                     "content": [{"type": "text", "text": "Still waiting."}],
                                     "usage": {"input_tokens": 20, "output_tokens": 40,
                                               # what is left after the compaction, growing slowly
                                               "cache_read_input_tokens": 61000 + self._step * 40}}}])
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
