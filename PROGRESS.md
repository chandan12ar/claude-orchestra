# PROGRESS

State on 2026-10-09 (stopped at the owner's request): 0.21.0 on `main`, every PR through #34 merged,
CI green. Where to pick up: `RESUME.md` section 4 "Next, in order". One branch and PR
per feature; rules in `.claude/CLAUDE.md`, one-page handoff in `RESUME.md`. The tables below are
the record of each round, oldest first.

## Status

| # | Item | State |
|---|---|---|
| 0.0 | CLAUDE.md + PROGRESS.md | done |
| 0.1 | Cache handoff-edge inference (perf) | done (96 agents: 5.2s -> 0.22s cold, 0.024s warm; results proven identical to old algorithm) |
| 0.2 | Evict idle session builders | done (LRU, MAX_BUILDERS now 24, default session pinned) |
| 0.3 | `report` writes to project, not plugin dir; wire/remove `--cwd` | done (`--cwd` wired; also finds newest session when no id) |
| 0.4 | Configurable thresholds | done (ORCHESTRA_* env vars, validated; documented in README; plugin userConfig deliberately not used — not documented to reach slash commands) |
| 0.5 | Dead code + log-handle cleanup | done |
| 0.6 | CI workflow, CHANGELOG, SECURITY.md | done (CI verified on GitHub; plugin validation added later — see Notes) |
| 1.1 | Agent-neutral event schema + spool (`events.py`, `statedir.py`) | done (state dir now per-user + ownership-checked) |
| 1.2 | Hook entrypoint + `hooks/hooks.json` | done |
| 1.3a | Ground-truth states: backend (`livestate.py`, build + service wiring) | done |
| 1.3b | Ground-truth states: UI (attention banner, `waiting` status) | done (verified in real Chromium, light+dark) |
| 1.4 | Live push (SSE) | done (measured in Chromium: 613 ms event->banner; idle page 0 req/3s) |
| 1.5a | Fleet backend (`/api/fleet`, cross-project recent sessions, urgency sort) | done |
| 1.5b | Fleet UI (tab, badge, cross-session notifications) | done (verified in Chromium; fixed an empty-bar bug via global [hidden] rule) |
| 2.1 | Pill (Picture-in-Picture) + tab badge | done (title, favicon, and PiP window verified in real Chromium; PiP is Chromium-only, button hidden elsewhere) |
| 2.2 | Synthesized sounds | done (verified in Chromium: off=0 oscillators, chime=3, alert=2). Per-event mute and quiet hours added later (client-only, localStorage; verified in Chromium) |
| 2.3.0 | FIX: token double-counting (per-entry vs per-message) | done (real transcript: old overcounted 2.6-3.1x) |
| 2.3a | Cost + budget alert (incl. orchestrator tokens, per model) | done (verified in Chromium; hand-computed orchestrator cost matched) |
| 2.3b | Runaway/loop detection | done (verified in Chromium: health box + drawer) |
| 4.1 | Run history (sqlite3, opt-in, metrics only) | done (verified in Chromium: table, compare, hostile names inert, off-by-default creates no file) |
| 4.2 | Replay scrubber | done (live + static report verified in Chromium) |
| 4.3 | Export JSON/CSV | done (verified: real browser downloads, CLI, scrubbing, CSV-injection guard) |
| 5.0 | FIX (security): esc() now escapes quotes (attribute breakout) | done |
| 5.1 | CI validates plugin manifest + hooks.json (blocking) | done; a third step I added (`validate commands`) FAILED in CI and was removed — see Notes |
| 5.2 | Docs: ARCHITECTURE section 10, CLAUDE.md lessons, this file | done |

Deferred by owner decision: dashboard approve/deny; Gemini/Cursor adapters;
plugin rename.

## Open items (nothing is blocked; ordered by importance)

1. ~~Verify hook payload field names against a LIVE Claude Code session.~~ **Done
   2026-10-03.** The plugin was installed from GitHub into a real session (Claude Code
   2.1.287) and a real permission prompt was captured: `notification_type:
   "permission_prompt"`, `message: "Claude needs your permission"`, plus `idle_prompt`,
   `session_start` (`source`, `model`), `turn_end`, `agent_stop` and `session_end` (`reason`).
   The dashboard showed the banner and the Fleet badge. See `docs/evidence/live-hook-capture.md`.
   Still checked against the documentation only: `error_type`/`error_message`. Known small gap:
   `agent_type` was empty on `agent_stop`.
2. **Confirm plugin hooks reach already-running sessions** or only after reload
   (affects onboarding wording). Docs (per the same lookup, not tested by us) say
   they do NOT: a new/resumed session, `/reload-plugins` or a restart is needed.
3. ~~Merge to the default branch / release.~~ **Done** (0.2.0 merged 2026-10-03; every later
   version shipped by its own PR). Last GitHub Release: v0.10.3.
4. **Naming: decided, done (0.4.0).** The product, plugin and repo are **Cuelight**
   (`/cuelight:open`, `chandan12ar/cuelight`); a cue light is the lamp that tells a
   performer "now", which is what the dashboard is for. The internal Python package stays
   `orchestra` and the `ORCHESTRA_*` environment variables are unchanged. Config and
   history written under the old `workflow` directory are still found (see CHANGELOG).
5. **Pill browser support**: Document Picture-in-Picture is Chromium-only (a search
   result said Firefox 151; not verified). The tab-title/favicon badge works everywhere.
6. **Small gaps**: history is recorded only for
   sessions that are viewed or scanned by the fleet, not every session.
7. **Still deferred by decision**: dashboard approve/deny (needs the
   `PermissionRequest` spike first: does it run before/alongside the terminal
   dialog, on timeout, for subagents, in auto mode); Gemini/Cursor adapters.

## Commit-message inaccuracies (history is pushed; not rewriting it)

- `27815da`: shows `/bin/bash.189` / `/bin/bash.19` for `$0.189` / `$0.19` (shell expansion).
- `317d4bf`: says "603 tests pass"; the measured count was 584.
- `2ef5e09`: says "440 tests pass"; the measured count was 436.
Counts are now taken from the actual run output.

## Notes / failures

- CI (run 1, commit 8f427a9): all 9 test jobs (Linux/macOS/Windows x py3.9/3.12/3.13)
  green. The `claude plugin validate` job went green but finished in <1s. Its log
  showed it only validated the MARKETPLACE manifest. Checked locally with Claude
  Code 2.1.287: `validate .claude-plugin/plugin.json` also validates
  hooks/hooks.json (mutation-tested: a missing "hooks" wrapper, an unknown handler
  type and malformed JSON are all caught) and our real file passes with no
  warnings. CI now runs it explicitly and blocking (the earlier note that it was
  unconfirmed is therefore resolved; see the CI commit).
- Spike 2 (hook cost): one hook invocation ~46 ms of Python startup on Linux,
  async so it is off Claude's critical path. `python3 || python || true` verified
  in bash; with no Python at all it exits 0 but prints "command not found" to
  stderr (async hook, harmless).
- Hook payload field names: verified against a live session on 2026-10-03 for the
  notification, session-start, session-end, turn-end and subagent-stop events (see
  `docs/evidence/live-hook-capture.md`); only the API-error fields remain documentation-only.
- Plugin hooks only apply once the plugin is (re)loaded; not yet confirmed whether
  already-running sessions pick them up (roadmap spike 5).

### CI incident 2: the plugin-validate job I made blocking failed (run 21, e4f4812)
The new third step `claude plugin validate commands` failed in CI with "No manifest
found in directory", although the SAME CLI version (2.1.287) passed it locally,
including in a clean `env -i` shell, so it is not a dependable invocation. The
other two steps passed. Removed the step. What the remaining blocking step does
and does not cover (clean-env mutation tests): it DOES catch a hooks.json with a
missing "hooks" wrapper, an unknown handler type, or malformed JSON, and warns on
a command file with no frontmatter; it does NOT catch invalid YAML inside command
frontmatter. My commit message for e4f4812 over-claimed that it validated
`commands/`; this entry is the correction. Also: the validator warns that the
project CLAUDE.md at the plugin root "is not loaded as context" - informational,
and the reason the step was not `--strict`. Resolved: the project file moved to `.claude/CLAUDE.md`
and both validate steps now run `--strict`.

### CI incident: red from bc5121c (sounds) through b6351fc (loops)

Windows-only, all in MY TESTS (no product bug). Local runs were green because I
only run Linux; I had not checked CI between commits and should have.
1. `text=True` on subprocess output decodes with the locale encoding (cp1252 on
   Windows) while Node writes UTF-8, so assertions on `·` / `▶` (test_pill) saw
   mojibake. Fixed: every test names `encoding="utf-8"`; new
   `tests/test_encoding_hygiene.py` fails on any `text=True` or unencoded node
   run / `open(path, "w")`, so this cannot recur unnoticed on Linux.
2. `test_loads_and_reloads_when_the_file_changes` (pricing) rewrote a file with a
   same-size change and relied on the mtime moving; Windows timestamps are coarse.
   Fixed by setting the mtime explicitly. (Product note: PriceSource detects a
   change via (mtime_ns, size); an edit of identical size inside one filesystem
   tick on Windows would be missed. Not worth hashing the file for.)
Process fix: from now on CI is read after every push, before starting the next
feature. CONFIRMED FIXED: run 16 (b2c204c) green on all 9 test jobs incl. Windows.

### Commit-message defect (cannot be fixed without rewriting pushed history)
Commit 27815da ("feat: cost and budget alerts...") shows `/bin/bash.189` and
`/bin/bash.19` where `$0.189` / `$0.19` were meant: `$0` was expanded by the
shell inside a double-quoted message. The code is right; only the message text
is wrong. Dollar signs are now avoided/escaped in commit messages.

## Next step

(Historical; both steps were done on 2026-10-03.) For what is next now, see `RESUME.md`
section 4.

### CI blocked by GitHub billing (2026-10-03, from 6a77988 on) - RESOLVED
Resolved the same day: once the repository was made public the jobs ran, and every job is green on Linux, macOS and Windows (Python 3.9, 3.12, 3.13) on PRs #1 to #4 and on main. What follows is the record of the incident.

Every job on 6a77988 and later "was not started because recent account payments have
failed or your spending limit needs to be increased" (job annotation; zero steps ran).
This is an account/billing problem, NOT a test failure: 81cf685 and e96913c were green,
and the full local suite passes on every later commit (665 tests at 438d5fa). The
cross-OS result for commits after e96913c is therefore UNVERIFIED until billing is fixed
and the runs are re-run (`gh run rerun <id>`). The Windows-specific risk is low for the
latest changes but not zero (new node-based tests use explicit utf-8).

## Branch `feature/enterprise-ux` (stacked on `feature/live-events`, 2026-10-03)

Research first (what Langfuse/LangSmith/AgentOps and the open-source Claude Code
dashboards such as agents-observe and Claude-Code-Agent-Monitor offer, and what
users ask for on HN), then built the gaps. Every item has tests; the suite is at
734+ and the UI was checked in a real Chromium (light, dark, phone width).

| Item | State |
|---|---|
| `--demo` synthetic 13-agent run (+ simulator, + pending permission prompt) | done |
| Insights backend (`insights.py`) and tab (parallelism, critical path, tools, cache, spend, files) | done |
| `/api/search` + command palette + keyboard shortcuts + help | done |
| Design system: tokens, light/dark/auto, transport header, alert chips, drawer, timeline comb + critical outline + now marker | done |
| Report shell derived from `index.html` (no more drift) | done |
| Contrast guard (`tests/test_contrast.py`), empty/loading states, tab arrow keys | done |
| README + screenshots, CHANGELOG `[Unreleased]`, ARCHITECTURE section | done |

Also done later the same day: a layered, interactive Graph (tested layout functions,
pan/zoom/fit, hover focus) and a richer Work Floor (last tool call, sparkline, group by
role/status, no needless rebuilds).

Not done / ideas worth a later pass (none are blocked):
- Activity tab has no text filter of its own (the palette searches tool calls).
- Graph edges that skip columns still cross intermediate nodes (long-edge routing).
- `plugin.json` is 0.3.0 (bumped at merge).
- CI could not run on GitHub at first (account billing). After the repo went public it
  ran: all 10 jobs green on Linux, macOS and Windows (Python 3.9, 3.12, 3.13).

## Five research-led features (agreed with the owner 2026-10-03)

Picked from a review of Claude Code's changelog, the "Why Do Multi-Agent LLM Systems Fail?"
taxonomy (NeurIPS 2025), Addy Osmani's "Code Agent Orchestra", xtrace's coding-agent
observability layers and Build 2026. One branch, PR and minor version each, in this order:

| # | Feature | State |
|---|---|---|
| 1 | **Waiting on you**: time agents sat on permission/input prompts (`feature/waiting-on-you`, 0.6.0) | merged (PR #14) |
| 2 | **Did it check its work?** (`feature/verified-work`, 0.7.0): checked / failing / unchecked after the last code edit; finished unchecked and failing agents in the Health box (owner's choice) | merged (PR #15) |
| 3 | **Review the changes** (`feature/change-review`, 0.8.0): per-agent diffs from recorded patches, "What changed" card | merged (PR #16) |
| 4 | **What the run produced** (`feature/run-outcomes`, 0.9.0): commits, pushes, PRs, merges, test runs; cost per commit/PR | merged (PR #17) |
| 5 | **What each agent was told** (`feature/agent-instructions`, 0.10.0): instruction files and skills per agent, coverage of project rules; read from transcripts, so no new hook | merged (PR #18) |

## Second round of five (agreed with the owner 2026-10-08)

From a census of 200 real transcripts (records Cuelight did not read yet), the MAST failure
taxonomy and similar tools. One branch, PR and minor version each, merged by the owner:

| # | Feature | State |
|---|---|---|
| 1 | **Catch me up** (`feature/catch-me-up`, 0.12.0): session titles and Claude Code's recaps in Fleet, picker, tab, alerts, reports | merged (PR #24) |
| 2 | **Where tokens were wasted** (`feature/token-waste`, 0.13.0): cache rebuilds with cause, wait and extra cost; biggest results; unchanged re-reads. Unused plugin/MCP overhead dropped (not measurable from transcripts) | merged (PR #25) |
| 3 | **Prompts tab** (`feature/your-prompts`, 0.14.0; moved from an Insights card to its own tab at the owner's request): per prompt its duration, a waiting/agents-and-tools/Claude split, agents, files, commits, cost. `cost-state` cross-check dropped (restarts on resume, scope differs) | merged (PR #26) |
| 4 | **Context pressure** (`feature/context-pressure`, 0.15.0): Insights card "How full each context got" (main curve with compaction marks, compactions list, agents by peak), agent panel "context" line, Health box item past 80% of the window (main session's opens the card). Windows assumed (1M, Haiku 200k), `ORCHESTRA_CONTEXT_LIMITS` overrides | merged (PR #27) |
| 5 | **What went wrong** (`feature/errors-retries`, 0.16.0): Insights card with API errors and the time until the API answered again (bursts are one stall, overlaps counted once), failed calls by tool and agent, calls tried again and whether they worked, commands past their timeout; agent panel "errors" line; Health box RETRYING for a live agent failing its latest call 3 times in a row. Dropped: "background commands left running" (completion is reported in ways the transcript does not tie back reliably) | merged (PR #28) |

After the round, at the owner's go-ahead (2026-10-08): **Insights at a glance** (`feature/insights-glance`, 0.17.0). Insights had grown to 15 cards (about 6,500px tall on desktop, 10,000px on a phone), so a strip of chips at the top says what each card found, worst first, and jumps to it; cards fold and stay folded in this browser. Merged (PR #29); CI on main green on all 10 jobs.

Still to check: whether Claude Code agent teams (teammates) show up correctly; the code has no
handling for them yet, and no team run exists in the local transcripts to test against.

Known limit of #1: a wait ends at the agent's next transcript entry, so an approved command's run time
is included (Bash results record no duration; checked on real transcripts). The one real permission
prompt in the local event spool was never answered (the session was left), which is why "unanswered"
waits are counted but given no length.

## Third round (agreed with the owner 2026-10-09: all four, each new view its own tab)

GitHub Release v0.17.0 published the same day (owner's yes). One branch, PR and minor version each:

| # | Feature | State |
|---|---|---|
| 1 | **Agents tab** (`feature/agents-table`, 0.18.0): one row per agent with asked / expected / came back side by side, status, checked, time, cost, errors; flagged rows say why; "only the ones that need a look"; sortable; focus kept across live refreshes. Found and fixed on real sessions while building it: reports handed back through `SubagentHandback` (Claude Code 2.1.277+) showed as a pointer for 88 of 88 such agents, and deliverables phrased "Final message: ..." / "Write your full report to ..." were missed (41 briefs "not stated", now 10) | merged (PR #31) |
| 2 | **Spend tab** (`feature/spend-tab`, 0.19.0): running cost over the run, main session vs agents (or by model), budget line and when warn/limit were crossed; crosshair by pointer or keys; pace now; most expensive five minutes; tokens without a price file. The Insights Spend card moved here. `app.js` hit the directory's 256 KiB file limit, so the tab views moved to `static/tabs.js` | merged (PR #32) |
| 3 | **Activity search** (`feature/activity-search`, 0.20.0): a search box on the Activity tab over every tool call in the run (tool, file or command, agent) or only failed ones; repeats counted; matches marked; Enter/Shift+Enter step through; `GET /api/calls`; static reports search their baked-in details by the same rules (tested to agree). Result text dropped from the plan: per-call output is not kept | merged (PR #33) |
| 4 | **Graph edge routing** (`feature/graph-routing`, 0.21.0): an edge whose curve would pass through an agent it skips crosses those columns through the nearest free gap and bends only between columns; edges back to an earlier column leave from the left. Demo: 8 of 27 edges crossed agents (16 times), now none; a test samples every drawn segment | merged (PR #34) |

## Stopped here (2026-10-09)

The owner merged #30-#34 and asked to stop (out of credits). Main is at 0.21.0, CI green, nothing
open or unpushed. Next steps, in order, are in `RESUME.md` section 4: (1) GitHub Release v0.21.0
after the owner's yes, (2) keep keyboard focus across live refreshes in the Prompts tab and the
Insights fold buttons, (3) agent teams once the owner provides a real team run.
