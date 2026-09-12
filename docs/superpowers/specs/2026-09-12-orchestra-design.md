# Orchestra — Subagent Orchestration Observability

**Date:** 2026-09-12
**Status:** Approved design, ready for implementation planning
**Command:** `/orchestra`

---

## 1. Problem

Claude Code's subagent-driven workflows spawn many agents that run in parallel and in
series. The built-in display shows that agents are running, but not enough to reason
about an orchestration: how many agents exist, what each was asked to do, what each was
expected to produce, which are still running, which are stuck, and — most importantly —
which agent's output became which other agent's input.

Orchestra reconstructs the full picture from the transcripts Claude Code already writes
to disk, and renders it as a local web dashboard: live while agents run, and as a
shareable report afterwards.

## 2. Goals

- Show every subagent in a session: count, type, model, status, duration, token cost.
- Show each agent's brief, its extracted objective, and its extracted expected output.
- Show the handoff structure — which agent's result fed which agent's prompt.
- Work live during a run, and as a post-run report.
- Default to the current session; allow selecting any other run in the project.
- Install and run on any machine with nothing but a Python 3.9+ interpreter.

## 3. Non-goals

- Modifying, controlling, pausing, or resuming agents. Orchestra is read-only.
- Instrumenting Claude Code via hooks or settings changes.
- Indexing or browsing other users' data, or any project outside the caller's own
  `~/.claude` tree.
- Any LLM call at runtime. All analysis is deterministic.

## 4. Hard constraints

These are requirements, not preferences. Each one rules out designs.

| Constraint | Consequence |
|---|---|
| Runs on any machine, zero setup | Python standard library only. No pip, no npm, no build step. |
| Entirely local | Binds `127.0.0.1` only. Never `0.0.0.0`. |
| No data leaves the machine | No CDN, no web fonts, no telemetry, no outbound request of any kind. Every asset ships in the package. |
| Reads only the caller's own data | Scoped to `~/.claude/projects/<project>` for the invoking project. |
| Read-only | Never writes into transcripts or any Claude Code state. |
| Safe to share output | Secret redaction before any rendering path. |
| Python floor 3.9 | Checked at startup with a clear error message. |

## 5. Data sources (verified on disk)

All facts below were confirmed against real transcripts before this design was written.

| Source | Path | Provides |
|---|---|---|
| Agent metadata | `~/.claude/projects/<proj>/<session>/subagents/agent-<id>.meta.json` | `agentType`, `description`, `model`, `spawnDepth`, `toolUseId`, `requestShape` |
| Agent transcript | `.../subagents/agent-<id>.jsonl` | every tool call, token usage, timestamps, final assistant message |
| Launch record | parent `<session>.jsonl`, `tool_use` with `name` in `{Agent, Task}` | full prompt, description, model, launch timestamp, `toolUseId` |
| Launch result | parent `.jsonl`, matching `tool_result` | `agentId`, background-vs-inline, live output file path |
| Termination | parent `.jsonl`, `<task-notification>` block | `<task-id>`, `<status>`, `<result>`, finish timestamp |
| Session identity | env `CLAUDE_CODE_SESSION_ID` | exact current session; no guessing |

Observed in the reference corpus (96 agents): `spawnDepth` always `1`; `requestShape`
either `background` or absent (inline); agent types `general-purpose`,
`claude-code-guide`, `statusline-setup`.

The project directory name is the absolute project path with separators and colons
replaced by `-` (e.g. `E:\god_ai\claude-SA` becomes `E--god-ai-claude-SA`).

## 6. Architecture

Layers, each testable without the layer above it:

```
locate.py      resolve project dir + session file from CLAUDE_CODE_SESSION_ID / cwd
     |
transcript.py  incremental JSONL reader with per-file byte-offset cache
     |
build.py       PURE: transcripts in -> Run out.  All intelligence lives here.
     |         uses extract.py, edges.py, redact.py
model.py       dataclasses, no I/O
     |
http.py        stdlib ThreadingHTTPServer: JSON API + static files, 127.0.0.1 only
     |
static/        index.html, app.js, style.css — zero external references
```

`build.py` is a pure function from file contents to a `Run` object. This is the
load-bearing decision: the entire correctness of the tool is testable against fixture
transcripts with no server, no browser, and no timing dependence.

### File layout

```
.claude-plugin/plugin.json
.claude-plugin/marketplace.json
commands/orchestra.md
orchestra/
  __main__.py     CLI entry: --session --cwd --port --no-open --report
  locate.py
  transcript.py
  model.py
  build.py
  extract.py
  edges.py
  redact.py
  report.py       self-contained single-file HTML snapshot
  http.py
  static/index.html, app.js, style.css
tests/
  fixtures/
  test_*.py
README.md
LICENSE
```

## 7. Data model

```
Run
  session_id, project_path, started_at, ended_at, status
  agents[]  edges[]  batches[]  diagnostics{}

Agent
  agent_id                 e.g. "a132adbf5b25c6de5"
  tool_use_id              e.g. "toolu_01..."
  parent_agent_id          None => launched by the orchestrator (main session)
  spawn_depth
  agent_type, description, model, launch_mode (background|inline)
  brief                    full prompt text as given
  objective                extracted intent, 1-3 lines
  objective_source         which extraction tier produced it
  expected_output          extracted deliverable / success criteria
  expected_output_source   which extraction tier produced it
  status                   running|completed|failed|stalled|orphaned|unknown
  rounds[]                 [{started_at, ended_at, status, result}]
  started_at, ended_at, duration_s, last_activity_at
  result                   final returned text (latest round)
  tokens                   {input, output, cache_read, cache_create} per model
  tool_calls[]             {name, target, timestamp}
  files_written[], files_read[]
  transcript_path

Edge
  src, dst                 agent ids; "main" denotes the orchestrator
  kind                     spawn|artifact|message|handoff
  confidence               exact|inferred
  evidence                 {path, written_at, read_at} | {snippet, score} | {tool_use_id}

Batch
  assistant_turn_uuid, agent_ids[], launched_at
```

`Batch` groups agents launched within the same assistant turn. It is what lets the
timeline distinguish "four agents running as one parallel wave" from "four agents whose
runtimes happen to overlap".

`rounds[]` rather than a single start/end pair, because a `task-notification` can fire
more than once for the same agent: an agent that finishes can be resumed with
`SendMessage`, so an agent legitimately transitions `completed -> running -> completed`.

## 8. Status state machine

| Status | Determined by |
|---|---|
| `completed` | terminal notification, or non-error inline `tool_result` |
| `failed` | notification status != completed, `is_error` on the tool_result, or transcript ends mid-tool-call |
| `running` | launched, no terminal record, last activity within the stall threshold |
| `stalled` | launched, no terminal record, silent longer than the stall threshold, parent session still live |
| `orphaned` | launched, no terminal record, and the parent session is not live |
| `unknown` | `meta.json` present with no matching launch record |

A parent session is **live** when its transcript file has been modified within the last
10 minutes. This single definition separates `stalled` (the agent is quiet but the
orchestration is still going, so it may be genuinely working) from `orphaned` (Claude
Code exited or crashed, and the agent's outcome will never be recorded).

**Stall threshold: 300 seconds (5 minutes)**, configurable.

Status is computed from the *latest* record for an agent, not the first.

The `stalled` / `orphaned` distinction is required for correctness of the post-run
report: without it, every crashed or interrupted run renders its agents as perpetually
"running".

## 9. Extraction of `objective` and `expected_output`

Deterministic, no LLM. Tiered, first match wins:

1. **Heading scan** of the brief for `## Expected Output`, `## Deliverable`, `## Output`,
   `## Success Criteria`, `## Return`, `## Definition of Done`, `## Acceptance`
   (case-insensitive, any heading level) — take that section.
2. **Imperative scan** for lines beginning `Return`, `Report`, `Produce`, `Output`,
   `Your job is`.
3. **Fallback**: the `description` field plus the first substantive paragraph of the brief.

The tier that fired is stored in `*_source` and displayed in the UI
(`from "## Deliverable"` vs `inferred from opening paragraph`), so the tool never claims
precision it does not have. The full brief is always available in the drawer.

## 10. Edge inference

| Edge | Rule | Confidence |
|---|---|---|
| `spawn` | launcher's `tool_use.id` matches `meta.json.toolUseId` | exact |
| `artifact` | A's `Write`/`Edit`/`NotebookEdit` target paths intersect B's `Read`/`Grep`/`Glob` targets, where B's read timestamp is later than A's write | exact |
| `message` | `SendMessage` tool_use in A naming B's agent id | exact |
| `handoff` | 8-word shingle containment between A's returned result and B's brief, at or above threshold | inferred |

**Causality guard:** no edge is drawn backwards in time. For `handoff`, A must have ended
before B started. This eliminates most false positives at no cost.

**Hub-file collapsing:** files read by more than 3 agents and not written during the run
(plan documents, `CLAUDE.md`, `package.json`) are collapsed into a single "shared
context" node rather than generating a quadratic number of artifact edges.

**Path normalisation** before intersection: case-insensitive comparison, separator
normalisation, worktree prefix stripping. Without this, artifact edges silently never
match on Windows.

**Deduplication:** when an exact edge already exists between a pair, a `handoff` match is
folded in as additional evidence rather than drawn as a second edge.

**Handoff threshold:** containment score >= 0.15, or any single contiguous match of >= 40
words. Evidence stores the longest matching snippet and the score, so every inferred edge
can be judged by the user.

## 11. Redaction

A single scrub pass applied at the serialization boundary — one chokepoint, so no code
path can bypass it. Patterns: `sk-` keys, `ghp_`/`gho_` tokens, `AKIA` access key ids,
`Bearer <token>`, JWTs, PEM private key blocks, and `password=`/`api_key=` assignments.
Matches are replaced with a `redacted:<type>` marker.

This exists because the dashboard renders raw prompt text and the output is intended to
be shareable.

## 12. Command surface

`commands/orchestra.md` defines the slash command. Its frontmatter permits only `Bash`,
and its body dispatches on the argument:

| Invocation | Behaviour |
|---|---|
| `/orchestra` | Start the server if not already running for this session, then print the tokenised `127.0.0.1` URL. Idempotent — a second call reuses the running server and reprints the URL. |
| `/orchestra stop` | Terminate the server for this session and remove its portfile. |
| `/orchestra report [path]` | Write a self-contained HTML snapshot. Defaults to `orchestra-report-<session>.html` in the project root. Does not require a running server. |

The underlying CLI is `python -m orchestra`, with `--session`, `--cwd`, `--port`,
`--no-open`, `--report`, and `--stop`. The slash command passes
`--session "$CLAUDE_CODE_SESSION_ID" --cwd .`, so session identification is exact.

**Detached launch.** The server must outlive the Bash call that starts it, on Windows as
well as POSIX. The command starts it via `subprocess.Popen` with detached creation flags
and redirected stdio, then polls the portfile for up to 5 seconds to obtain the URL. If
the portfile does not appear, the command reports the server's captured stderr rather
than a generic failure — a silent non-start is the worst possible failure mode for a
tool whose whole job is visibility.

The browser is opened with `webbrowser.open` unless `--no-open` is passed. Failure to
open a browser is not an error; the URL is printed either way.

## 13. Server and security

`ThreadingHTTPServer` bound to `127.0.0.1`, default port 7717, falling back to the next
free port. Started detached by the slash command. A portfile in the session scratch
directory means a second `/orchestra` reuses the running server rather than starting a
twin. **Idle auto-shutdown after 30 minutes** with no polling.

**Localhost is not a security boundary.** Any other local process — and any web page the
user has open, via DNS rebinding — can reach `127.0.0.1:7717` and read prompts, source
paths, and results. Therefore:

- Each launch mints a random token, carried in the URL and required on every `/api/*` call.
- `Host` and `Origin` headers are validated; requests with a non-loopback `Host` are rejected.
- Missing or wrong token returns `403`.

## 14. HTTP API

```
GET /api/run?session=<id>      Run summary: agents (light), edges, batches, diagnostics
GET /api/agent/<agent_id>      brief, objective, expected_output, result, tool log, files
GET /api/sessions?project=<p>  run picker: session id, start time, agent count, status
GET /api/health                liveness
```

Heavy per-agent fields are served only by `/api/agent/<id>`, on click. The 2-second poll
therefore stays small even with 30 agents in a run.

## 15. User interface

Single page, vanilla JavaScript, no build step, no external references.

- **Header** — the auto-detected current session as the default view, plus a dropdown of
  other runs in the project; totals (agents, running, done, failed, tokens, wall time);
  live/pause toggle.
- **Timeline** — SVG Gantt, one row per agent on a shared time axis, batch bands drawn
  behind to show parallel waves, resumed agents drawn as multiple segments on one row.
- **Graph** — layered DAG. Solid edges are exact, dashed edges are inferred. Clicking an
  edge shows its evidence: the file path and timestamps, or the matching snippet and score.
- **Drawer** — per agent: status, type, model, duration, tokens, objective,
  `expected_output` labelled with its extraction tier, collapsed full brief, returned
  result, tool call log, files written and read.
- **Health strip** — stalled, failed, and orphaned agents surfaced at the top level.
- **Diagnostics** — count of unparsable transcript lines, if any.

Status is conveyed by shape and label in addition to colour, so the view survives
colourblindness and greyscale screenshots. Light and dark are both supported via
`prefers-color-scheme`.

The DAG layout is hand-rolled in SVG (rank by spawn depth and start time, order within
rank to reduce crossings). This is the most intricate piece of work in the project, and
it is forced by the no-CDN constraint. That trade is accepted deliberately.

## 16. Report mode

`/orchestra report [path]` renders the same `Run` model to a **self-contained single-file
HTML snapshot** — inline CSS and JS, no server required, no external references. It can
be committed, attached, or emailed. It reuses the renderer, so the cost over the live
dashboard is small.

## 17. Error handling

Transcripts are appended to while being read. The design assumes this rather than hoping
against it.

- **Torn final line** — skipped, and the byte offset is *not* advanced past it; the next
  poll re-reads it whole.
- **Unparsable lines** — counted and surfaced in the diagnostics panel. Never fatal.
- **Encoding** — UTF-8 with `errors="replace"`. The reference corpus already contains
  mojibake; a crash on a user's encoding is not acceptable.
- **Large transcripts** — an 11 MB session transcript exists in the reference corpus. The
  byte-offset cache means it is fully parsed once and only its tail is read thereafter.
- **Missing session, empty history, busy port** — a clear message and the run picker,
  never a stack trace.
- **Browser poll failure** — "reconnecting" indicator, last good state retained,
  exponential backoff.

## 18. Testing

`build.py` is pure, so the intelligence layer is tested by golden files: fixture
transcripts in, expected `Run` JSON out.

Fixture corpus covers: single agent; parallel batch; **resumed agent with two
notifications**; failed agent; orphaned run; nested depth-2 spawn; corrupt line;
offset-resume after append.

Unit tests cover each extraction tier, each of the four edge inferencers, every redaction
pattern, and every transition of the status state machine.

One smoke test boots the server on an ephemeral port, exercises every endpoint, and
asserts that a request with a bad token receives `403`.

**No test reads the real `~/.claude` tree.** The suite must pass on a fresh machine with
an empty Claude Code history. Tests use `unittest`, not pytest, so contributing requires
zero installs.

## 19. Decisions and rejected alternatives

| Decision | Rejected alternative | Reason |
|---|---|---|
| Read-only transcript scanner | Hook-instrumented event stream | Hooks require editing each user's `settings.json`, capture nothing retroactively, and a buggy hook degrades the user's real session. The only gain is sub-second latency on agents that run for minutes. |
| Python standard library | FastAPI/uvicorn, or Node + React | A pip install or npm build is real friction on a fresh machine and one more thing to break. |
| Deterministic extraction | LLM-based summarisation of briefs | Must run offline, cost nothing, and return the same answer twice. |
| Hand-rolled SVG rendering | A charting or graph library from a CDN | A CDN is outbound network traffic, which the local-only constraint forbids. |
| Token-gated API | Plain localhost binding | Localhost is reachable by any local process and by web pages via DNS rebinding. |
| Keep the inferred `handoff` edge | Exact edges only | The handoff edge answers the original question directly; labelling it and attaching its evidence preserves trust. |

## 20. Known risks

- **DAG layout quality.** Hand-rolled layout may produce crossings in dense runs.
  Mitigation: rank-and-reorder heuristic, plus the timeline view as an alternative reading
  of the same data.
- **Handoff false positives.** Shared boilerplate between briefs could trip the shingle
  match. Mitigation: causality guard, threshold, and visible evidence so the user can
  dismiss a bad edge.
- **Format drift.** Claude Code's transcript format may change between versions.
  Mitigation: the parser tolerates unknown fields and missing keys; the diagnostics panel
  makes silent degradation visible rather than invisible.
- **Nesting is untested against real data.** `spawnDepth > 1` does not appear in the
  reference corpus. The model supports it and a synthetic fixture covers it, but the first
  real nested run should be verified by hand.
