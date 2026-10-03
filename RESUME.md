# RESUME — read this first to continue the session

Repo: `chandan12ar/claude-orchestra` (public) · Branch: **`main`**, version **0.3.0**.
State (2026-10-03): PR #1 (live events, 0.2.0) and PR #2 (Insights, palette, design system,
graph, work floor, `--demo`; 0.3.0) are merged; CI is green on main (Linux/macOS/Windows x
Python 3.9/3.12/3.13 + plugin validation); **766 local tests pass**. Proof of the last run
(test list + 18 screenshots) is in `docs/evidence/`.
Details: `PROGRESS.md` (status + open items), `CLAUDE.md` (rules + lessons), `docs/ROADMAP.md`
(why), `docs/ARCHITECTURE.md` (how it works; §10 live layer, then insights/search/demo and
the design system).

**Open:** (1) plugin name `workflow` vs `orchestra` vs `claude-orchestra` (a plugin name
cannot start with `claude-`), the owner has not decided; (2) hook payload field names are
verified against the docs only, never a captured live payload (needs a human at a real
Claude Code session); (3) small ideas: Activity-tab text filter, routing graph edges around
columns they skip.

## 1. What the product is
A Claude Code plugin (`/workflow:open`) serving a local dashboard of a session's subagent
orchestration. Python 3.9+ stdlib only, vanilla JS, no build step, no network egress,
loopback + token. Package `orchestra/`, plugin name `workflow`.
Direction agreed with the owner: **observability + control plane for Claude Code multi-agent
work** = the record (transcripts) + the live truth (hooks), across all sessions.

## 2. Owner's rules (follow exactly)
1. After each feature: run tests -> commit with a clear message -> push immediately. No batching.
2. If something fails: note it in the commit message or `PROGRESS.md`, push, and stop.
3. If the owner says "stop": commit + push everything, update docs with where we stopped.
4. Work on a branch and open a PR; `main` changes only by merged PR. Never force-push.
5. Out of scope by owner decision: dashboard approve/deny, Gemini/Cursor adapters, plugin rename.

## 3. What was built (all pushed)
| Phase | Delivered |
|---|---|
| 0 Foundations | Handoff-edge cache (5.2s -> 0.22s @96 agents); LRU session cache; `--cwd`; `ORCHESTRA_*` env tunables; dead-code cleanup; CI matrix (3 OS x py3.9/3.12/3.13); CHANGELOG, SECURITY.md |
| 1 Event layer | Async hooks (`hooks/hooks.json`, `orchestra/hook.py`) -> per-session JSONL spool; ground-truth states (waiting on permission/input/idle/API error, exact session end/subagent stop, new `waiting` status); live push via SSE (~0.6s); Fleet tab across all projects |
| 2 Attention | Pill (Picture-in-Picture, Chromium) + tab-title count + favicon badge; synthesized sounds (off by default); cost + budget alerts (user price file, orchestrator included, per model); possible-loop detection |
| 4 Depth | Replay (works in static reports); CSV/JSON export (UI, API, CLI); opt-in run history (sqlite, metrics only) with compare |
| Fixes found on the way | **Token counts were inflated 2.6-3.1x** (one transcript entry per content block repeats usage; now counted once per `message.id`); **`esc()` didn't escape quotes** (attribute breakout); empty-bar `[hidden]` CSS bug; collapsed replay axis |

## 4. Verified vs NOT verified
Verified: all features in a real Chromium (Playwright) against live servers; token fix against a
real transcript; `hooks.json` valid per Claude Code 2.1.287's validator (mutation-tested);
CI green on Linux/macOS/Windows through `1960490` (run 23).

**NOT verified — do this first:** the hook payload **field names** (`notification_type`,
`message`, `error_type`, `error_message`, `reason`, `last_assistant_message`) came from the docs,
not a captured payload. If wrong, the headline "waiting on permission" feature shows nothing
(code degrades to empty fields, it doesn't crash). Needs a human at a real Claude Code session:
install plugin -> trigger a permission prompt -> read `<state dir>/events/<session>.jsonl`.
(State dir: `ORCHESTRA_STATE_DIR`, else `<tmp>/orchestra-<uid>`.)

Update 2026-10-03: the field names were cross-checked against the official hooks docs and all
match (docs only, still no captured payload). The docs also say plugin hooks do not reach
already-running sessions (need a new session, `/reload-plugins` or restart); not tested by us.
CI run 23 (`1960490`) finished **green** (full test matrix on 3 OS x 3 Pythons + plugin validation).

## 5. Incidents (honest log)
- CI red on Windows for 3 commits (bc5121c..b6351fc): my tests used `text=True` (cp1252 vs Node
  UTF-8) and relied on fine mtimes. Fixed in `b2c204c`; `tests/test_encoding_hygiene.py` guards it.
- I added a CI step `claude plugin validate commands`; it failed in CI (passed locally, same
  version) and was removed (`1960490`). Remaining blocking steps validate the marketplace and
  plugin manifest (which covers `hooks.json`).
- Wrong commit messages (pushed, not rewritten): `27815da` (`/bin/bash.189` for a dollar amount),
  `317d4bf` (says 603 tests, was 584), `2ef5e09` (says 440, was 436).

## 6. Next steps, in order
1. Verify hook field names with a real Claude Code session (section 4).
2. Open a PR `feature/live-events` -> default branch; bump `.claude-plugin/plugin.json`
   `version` 0.1.0 -> 0.2.0 (CHANGELOG `[Unreleased]` is ready); merge.
3. Decide the plugin name (`workflow` vs package `orchestra` vs repo `claude-orchestra`;
   a plugin name can't start with `claude-`).
4. Later, owner's call: approvals (spike `PermissionRequest` first: runs before/alongside the
   terminal dialog? timeout? subagents? auto mode?), other-agent adapters.

## 7. Practical notes for the next session
- Tests: `python -m unittest discover -s tests -t .` (Node needed for the UI tests).
- Real-browser checks: Playwright at `/opt/node22/lib/node_modules/playwright`, Chromium at
  `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (launch with `--no-sandbox`).
- Windows pitfalls: always `encoding="utf-8"` for subprocess/open; set mtimes explicitly in tests.
- Read CI after every push, before starting the next feature.
- In shell commit messages avoid `$` in double quotes (it gets expanded).
- Known small gap: history records only viewed/scanned sessions.
