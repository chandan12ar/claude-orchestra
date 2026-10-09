# CLAUDE.md — working context for Cuelight (repo: cuelight)

Read `RESUME.md` first (one-page session handoff), then `PROGRESS.md` (what is done / what is next), then
`docs/ROADMAP.md` (why).

## What this project is

A Claude Code **plugin** (`/cuelight:open`) that serves a local, read-only
dashboard of a session's subagent orchestration. Python 3.9+ **stdlib only**,
vanilla JS front end, **no build step, no network egress**. Package name is
`orchestra/`; plugin name is `cuelight`. Architecture: `docs/ARCHITECTURE.md`.

## Current direction (decided with the owner)

Positioning: **the observability and control plane for Claude Code multi-agent
work** = the record (transcripts) + the live truth (hooks), across all sessions.

- One branch, one PR and one minor version per feature; `main` changes only by a
  PR the owner merges. Show a short design for new feature work before building it.
- **Out of scope for now (owner decision): dashboard approve/deny** and any
  other-agent adapter (Gemini/Cursor). Do not build them; revisit later.
  Approvals, if ever built, are a *separate opt-in plugin* with an audit log.
- Phases (details in `docs/ROADMAP.md`, live status in `PROGRESS.md`):
  0 Foundations/bug fixes -> 1 Event layer (hooks) -> 2 Attention (pill, sounds,
  cost) -> 4 History/replay/export, then two research-led rounds of five features
  and "Insights at a glance", then a third round (Agents tab, Spend tab, Activity
  search, graph routing). **All merged (0.21.0).** What is left: `RESUME.md`
  section 4.

## Working rules the owner set (follow exactly)

1. After **each feature**: run the full test suite, then commit with a clear
   message, then **push immediately**. Never batch commits.
2. If something fails, **write the failure into the commit message and/or
   `PROGRESS.md`, push, and stop.** Do not paper over it.
3. If the owner says "stop": commit and push all work, update `PROGRESS.md`
   with exactly where we stopped and what is next, push, and end.
4. Keep this file and `PROGRESS.md` current — they are how context survives.
5. Push only to your feature branch, never to `main` (never force-push, never
   rewrite history). Read the PR's CI and say it is green before asking for a merge.

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

## Lessons from this build (so they are not repeated)

- **Read CI after every push, before the next feature.** It was red on Windows
  for three commits because only Linux was run locally.
- Windows pitfalls that bit: `subprocess` `text=True` decodes with cp1252 while
  Node writes UTF-8 (always pass `encoding="utf-8"`; `tests/test_encoding_hygiene.py`
  enforces it); file mtimes are coarse (set them explicitly in tests).
- Fixtures hid a real bug for months: they had no `message.id`, so per-entry
  token summing looked fine. When a number matters, check it against a REAL
  transcript (`~/.claude/projects/...`), not only the synthetic fixtures.
- Verify UI in a real browser (Playwright + `/opt/pw-browsers/chromium-1194`), not
  only under the node DOM stub: it caught an empty-bar CSS bug, a collapsed
  replay axis, and the attribute-escaping hole.
- In shell commit messages avoid `$`: `$0.19` in double quotes became `/bin/bash.19`.

## Commands

```bash
python -m unittest discover -s tests -t .        # full suite
python -m orchestra --session <id>               # run the dashboard
python -m orchestra --session <id> --report .    # static report
```
