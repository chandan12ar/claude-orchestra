# CLAUDE.md — working context for Workflow (repo: claude-orchestra)

Read this first, then `PROGRESS.md` (what is done / what is next), then
`docs/ROADMAP.md` (why).

## What this project is

A Claude Code **plugin** (`/workflow:open`) that serves a local, read-only
dashboard of a session's subagent orchestration. Python 3.9+ **stdlib only**,
vanilla JS front end, **no build step, no network egress**. Package name is
`orchestra/`; plugin name is `workflow`. Architecture: `docs/ARCHITECTURE.md`.

## Current direction (decided with the owner)

Positioning: **the observability and control plane for Claude Code multi-agent
work** = the record (transcripts) + the live truth (hooks), across all sessions.

- Branch for this work: `feature/live-events` (merge to the default branch later).
- **Out of scope for now (owner decision): dashboard approve/deny** and any
  other-agent adapter (Gemini/Cursor). Do not build them; revisit later.
  Approvals, if ever built, are a *separate opt-in plugin* with an audit log.
- Phases (details in `docs/ROADMAP.md`, live status in `PROGRESS.md`):
  0 Foundations/bug fixes -> 1 Event layer (hooks) -> 2 Attention (pill, sounds,
  cost) -> 4 History/replay/export.

## Working rules the owner set (follow exactly)

1. After **each feature**: run the full test suite, then commit with a clear
   message, then **push immediately**. Never batch commits.
2. If something fails, **write the failure into the commit message and/or
   `PROGRESS.md`, push, and stop.** Do not paper over it.
3. If the owner says "stop": commit and push all work, update `PROGRESS.md`
   with exactly where we stopped and what is next, push, and end.
4. Keep this file and `PROGRESS.md` current — they are how context survives.
5. Push only to `feature/live-events` (never force-push, never rewrite history).

## Invariants that must not regress

- Server binds `127.0.0.1` only; Host/Origin checks; token on every `/api/*`
  except `/api/health`.
- **No network egress** from the app or its static assets
  (`tests/test_static_assets.py` enforces it).
- Never write under `~/.claude`. Runtime state goes in the state dir
  (`ORCHESTRA_STATE_DIR` or the OS temp dir), created `0700`, files `0600`.
- Everything leaving the process passes through `redact.py`.
- Observer hooks must **never block or fail Claude Code**: async, always exit 0.
- Tests: `python -m unittest discover -s tests -t . -v` (stdlib only; Node is
  used by `tests/test_report_renders.py` if available).

## Commands

```bash
python -m unittest discover -s tests -t .        # full suite
python -m orchestra --session <id>               # run the dashboard
python -m orchestra --session <id> --report .    # static report
```
