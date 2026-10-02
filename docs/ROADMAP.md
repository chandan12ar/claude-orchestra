# Workflow — Roadmap to an enterprise-grade product

Status: **proposal, not committed work.** Written after reading this repo end to
end, reading [Louis-CFM/coucou](https://github.com/Louis-CFM/coucou), and
checking every claim below against current docs. Where something could *not* be
verified from documentation, it is listed under [Spikes](#6-spikes-to-run-before-committing)
instead of being assumed.

---

## 1. Verdict in one paragraph

Coucou and Workflow are **different products that happen to share an input
(Claude Code hooks)**. Coucou is a *companion*: a character in your notch that
tells you "something needs you now" and lets you answer. Workflow is an
*observability tool*: it reconstructs what a multi-agent run did (who spawned
whom, who fed whom, the critical path, cost of tokens, write conflicts).
Adopting Coucou's feature set wholesale would turn Workflow into a worse Coucou.
Adopting the **one thing Coucou does that Workflow structurally can't** — knowing
what an agent is *waiting on right now* — makes Workflow strictly better and
keeps its identity. That is the thesis of this roadmap:

> Workflow = the **record** (transcripts) + the **live truth** (hooks) for
> multi-agent Claude Code, across every session you have running.

## 2. Coucou vs Workflow — what is actually different

| | Coucou | Workflow (today) |
|---|---|---|
| Core job | Alert + respond | Reconstruct + explain |
| Data source | Hook events only, pushed over a Unix socket | Transcript files only, polled every 2 s |
| Knows "blocked on permission"? | Yes | **No** — a transcript has no record of a pending prompt |
| Knows handoffs, critical path, cost, conflicts? | No | Yes |
| Install | Native app (Swift / Tauri), edits `~/.claude/settings.json` | Plugin, Python stdlib only, no build |
| Approvals | Claude Code only — their own `AGENTS.md` says third-party agents "cannot receive approval cards" | None (read-only by promise) |

**Licensing:** Coucou's code is MIT but its name, character, icon and sounds are
reserved to its author. Nothing in it is reusable in a Python-stdlib codebase
anyway. Take the *ideas*; do not copy assets (build sounds synthetically, §4.3).

## 3. The gap that matters most (not in your list)

Two of Workflow's core status rules are guesses, because transcripts can't tell
the difference between "thinking", "waiting for you" and "dead":

- `stalled` = no activity for 300 s (`status.py`)
- session "live" = main transcript modified within 600 s (`build.py`)

Hooks turn both into facts. Verified in the
[hooks reference](https://code.claude.com/docs/en/hooks): `Notification` carries a
type of `permission_prompt`, `idle_prompt`, `agent_needs_input`,
`agent_completed`, …; `StopFailure` carries `rate_limit`, `overloaded`,
`billing_error`, `authentication_failed`, …; `SessionEnd` carries the reason.
Today an agent killed by a rate limit just looks `stalled` or `orphaned`.
**This — not latency — is the real value of idea 1.**

## 4. Your five ideas, verified

### 4.1 Hook-based live events — **YES, but for a different reason than stated**

Verified: a plugin can ship hooks (`hooks/hooks.json` or the manifest `hooks`
field, with `${CLAUDE_PLUGIN_ROOT}`), merged automatically — no `settings.json`
editing. That part of your pitch is true and is a real advantage over Coucou.

Corrections:
- **Latency is not the win.** Transcripts are appended the instant things
  happen; the 2 s lag is only your poll. A cheap `stat`-then-push (SSE) fixes
  that with no hooks at all.
- **Don't subscribe to `PreToolUse`/`PostToolUse`.** Tool calls are already in
  the transcripts, and a hook is a process spawn per call, on every session of
  everyone who installs the plugin. Subscribe only to events that carry
  information the transcript lacks: `SubagentStart/Stop`, `Notification`,
  `StopFailure`, `SessionStart/End`, `Stop`.
- **Never block Claude.** Observer hooks must be `async: true` and always exit
  `0` (exit `2` blocks on several events).
- **Don't spool under `~/.claude`.** `${CLAUDE_PLUGIN_DATA}` lives at
  `~/.claude/plugins/data/…`, which would break the README's literal promise.
  Keep using the existing state dir, make it `0700`, and run payloads through
  `redact.py` before they hit disk.
- **Transport:** hooks append JSONL to a spool; the server tails it. This works
  even if the dashboard isn't running yet (no dynamic-port/token problem) and
  keeps everything loopback/no-egress.

### 4.2 Pill mode — **YES; cheap, and a view, not a new app**

Verified: the
[Document Picture-in-Picture API](https://developer.chrome.com/docs/web-platform/document-picture-in-picture)
opens a real **always-on-top** window with arbitrary HTML from a web page —
Chrome/Edge 116+, Firefox reported at 151, **not Safari**. Limits: needs a user
gesture, the opener tab must stay open, position can't be set, secure-context
only (loopback should qualify — spike). Reuse the Work Floor sprites inside it.

Add a universal fallback: favicon + tab-title badge (`▶3 ⚠1`). A native
always-on-top app would break the "stdlib, no build" property — defer.

A single-session pill is weak. It becomes valuable only with the **fleet view**
(§5, A): count / status across *all* running sessions.

### 4.3 Sounds and stronger alerts — **YES, small; sequence after events**

- In the tab: Web Audio synthesized tones (no asset files → no-egress test still
  passes, no copyright issue). Autoplay rules need one prior click — the existing
  Notify toggle already provides it.
- Without any tab open: the hook can play an OS sound itself, and hook output
  supports a `terminalSequence` field (desktop-notification escape) — verified.
- Enterprise manners: off by default or subtle, per-event mute, quiet hours.
- Trigger them from **events** (permission wait, StopFailure, session end), not
  from a poll diff, or they will lag and double-fire.

### 4.4 Approve / deny from the dashboard — **FEASIBLE; highest value, highest risk; do it last**

Verified: `PermissionRequest` hooks can return
`{"decision": {"behavior": "allow|deny"}}`, so the mechanism exists.

Honest take: ~80 % of the value is **seeing that a session is blocked on a
permission** (read-only, zero risk, from the `Notification` hook). Only the last
20 % is *answering from the dashboard* — and that is where all the risk is. So
split them: ship the visibility in Phase 1, approvals in Phase 3.

What the docs do **not** say (must be spiked, §6): whether the hook runs before
or alongside the terminal dialog, what happens on timeout or empty output,
whether it fires for subagent calls, whether it fires in auto/bypass modes.

If built:
- **Separate opt-in plugin** (`workflow-approvals`) so the core plugin's
  "read-only" claim stays literally true and a security reviewer has a small
  surface to audit. Document it in the README as a *deliberate* change.
- **Fail to the terminal, never to "allow".** Dashboard down or no answer within
  N seconds → return no decision and let the normal prompt appear.
- Decisions bound to a per-request nonce; no `updatedInput`; **no "always allow"**
  in v1; append-only audit log (who, what, when, from where).
- The server becomes a control plane: the token is now an authorization secret,
  so re-review the existing Host/Origin/token defenses for CSRF-style
  decision forgery and add a threat-model doc.
- Unique upside over Coucou: `agent_id` is present in subagent hook input, so an
  approval can be shown *in the context of that agent's brief and place in the
  graph* — Coucou can't.

### 4.5 Other agents (Gemini CLI, Cursor) — **DEFER; the premise is weaker than it looks**

Verified both have hooks (Gemini: `BeforeTool`, `AfterTool`, `SessionStart/End`,
`Notification`, … in `.gemini/settings.json`; Cursor: `beforeShellExecution`,
`afterFileEdit`, `stop`, … in `.cursor/hooks.json`). But:

- `parent.py` isolates the format of **Claude's** parent transcript. Other tools
  don't have a parent/subagent transcript to parse, so an adapter there gives
  you a *flat session list with status* — which is exactly what Coucou already
  is. Workflow's moat (spawn edges, handoffs, critical path) does not transfer.
- Coucou itself only supports approvals for Claude Code. Ideas 4 and 5 pull in
  opposite directions.
- Every extra agent is an ongoing format-chasing cost (your own `parent.py`
  comment already says that layer is the most likely to break).

Do this: define the **agent-neutral event schema** in Phase 1 (cheap, and needed
anyway), then prove it with *one* adapter as an experiment in Phase 4. Spend the
real effort going **deeper on Claude Code multi-agent** instead (agent teams —
`TeammateIdle`/`TaskCreated`/`TaskCompleted` hooks exist — worktrees, headless
and SDK runs).

## 5. Features not on your list, ranked by enterprise value

| | Feature | Why it matters | Feasible from existing data? |
|---|---|---|---|
| **A** | **Fleet view / "needs attention" inbox** across all sessions and projects | The real Coucou-overlap and the real step up. People run many sessions in parallel; today one server = one session | Yes — hook events carry `session_id` + `cwd`; needs one multi-session server |
| **B** | **Ground-truth states** (`waiting_permission`, `waiting_input`, `api_error:<type>`, `ended:<reason>`) | Replaces the two guesses in §3 | Yes, via hooks |
| **C** | **Cost + budget alerts + runaway detection** | First question every manager asks; repeat-call loops are the classic failure | Yes — 4 token types and exact model already collected. Use a user-editable price table (prices change; don't hardcode) |
| **D** | **Run history + comparison** (stdlib `sqlite3`) | Claude Code prunes old transcripts; enterprises want trends (cost, critical path, failure rate) | Yes |
| **E** | **Replay scrubber** for post-mortems | Timestamps for every tool call already exist; reuses Work Floor | Yes |
| **F** | **Export + audit log** (JSON/CSV; approvals log) | Table stakes for security/compliance review | Yes. Check overlap with Claude Code's own telemetry before building more |
| **G** | **Engineering hygiene** (below) | Enterprises read this before they read features | Yes |

### G. Fix before building (from your own `FOLLOW-UPS.md`, which I confirmed)

- **Handoff-edge inference is superlinear and recomputed every poll** — measured
  5.2 s at 96 agents (FOLLOW-UPS #2). Faster live updates and a multi-session
  fleet would make this the first thing to fall over. **Blocks Phase 1.**
- **Builders are never evicted** (#5) — a fleet server would leak memory.
- `report` writes to the plugin dir, not the project (#3); `--cwd` is documented
  but doesn't exist (#4); thresholds only configurable by editing code (#10) —
  use plugin `userConfig` (verified: prompts the user, exported to hooks as
  `CLAUDE_PLUGIN_OPTION_<KEY>`).
- **No CI.** There is no `.github/` directory. Add: test matrix (Linux/macOS/
  Windows × Python 3.9–3.13), `claude plugin validate --strict`, the Node render
  check. 234 tests exist; they aren't gating anything.
- Add `CHANGELOG`, `SECURITY.md`, a versioned release process (`version` in
  `plugin.json` pins users — verified).
- **Naming:** the plugin is `workflow`, the package is `orchestra`, the repo is
  `claude-orchestra`, and Claude Code now has its own "workflows" feature.
  Plugin names may not start with `claude-` (reserved — verified). Pick one name
  before the user base grows.

## 6. Spikes to run before committing

1. **`PermissionRequest` semantics** — toy hook that logs and sleeps: does it run
   before/with the terminal dialog? timeout behavior? fires for subagents? in
   auto/bypass? *Gates all of Phase 3.*
2. **Hook cost** — Python cold-start per event on your OS (and `python` vs
   `python3` on Windows). Gates the choice of hook command.
3. **Hook → spool → server** — confirm `SubagentStart` `agent_id` matches the
   transcript's agent id, so events and transcripts can be joined.
4. **PiP from `http://127.0.0.1`** works in Chrome and Edge.
5. **Plugin hook load timing** — whether a newly installed plugin's hooks apply
   to already-running sessions or only after reload/restart (affects onboarding
   copy).

## 7. Phases

| Phase | Scope | Size | Gate |
|---|---|---|---|
| **0 — Foundations** | Edge-inference caching, builder eviction, CI matrix, report path, `userConfig` thresholds, naming decision | M | — |
| **1 — Event layer** | `hooks/hooks.json` (async, minimal events) → spool → SSE; agent-neutral event schema; ground-truth states (B); multi-session server + fleet inbox (A) | L | Phase 0, spikes 2–3 |
| **2 — Attention** | Pill via PiP + tab badge (4.2); synthesized sounds + terminal notifications (4.3); cost, budget, runaway detection (C) | M | Phase 1 |
| **3 — Control** | `workflow-approvals` plugin (4.4) + audit log + threat model | M | Spike 1, security review |
| **4 — Depth & breadth** | History + comparison (D), replay (E), export (F); one non-Claude adapter as an experiment (4.5) | L | Phase 2 |

## 8. Decisions needed from you

1. **Positioning.** "The observability and control plane for Claude Code
   multi-agent work" (recommended) vs "generic multi-agent dashboard".
2. **Approvals as a separate opt-in plugin** (recommended) vs a toggle in the
   core plugin.
3. **Primary OS** you and your users run — the code has Windows-specific paths
   and I'd test there first.
4. **Go-ahead for Phase 0 + the Phase 1 event layer** as the first deliverable.
