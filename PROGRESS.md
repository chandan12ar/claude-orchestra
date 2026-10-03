# PROGRESS

Branch: `feature/live-events`. Rules: test -> commit -> push after every
feature; on failure record it here and stop. See `CLAUDE.md`.

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

1. **Verify hook payload field names against a LIVE Claude Code session.**
   *Update (2026-10-03): cross-checked against the official hooks reference
   (code.claude.com/docs/en/hooks) via a docs lookup — every field name and the
   `notification_type` values we map (`permission_prompt`, `idle_prompt`,
   `agent_needs_input`, `elicitation_dialog`, `elicitation_url_dialog`) match, as do
   `StopFailure` `error_type`/`error_message`, `SessionEnd` `reason` and
   `last_assistant_message` on Stop/SubagentStop. This is documentation, still NOT a
   captured payload, so a live capture remains worth doing.* This is
   the single biggest unverified assumption: ground-truth states (waiting on
   permission, API error type, session end reason) read `notification_type`,
   `message`, `error_type`, `error_message`, `reason`, `last_assistant_message`
   from the docs summary, not from a captured payload. The code degrades to empty
   fields rather than failing, but the headline feature would then show nothing.
   `hooks/hooks.json` itself IS verified valid by Claude Code 2.1.287's validator.
   How: install the plugin in a real session, trigger a permission prompt, read
   `<state dir>/events/<session>.jsonl`. Needs a human at a real Claude Code.
2. **Confirm plugin hooks reach already-running sessions** or only after reload
   (affects onboarding wording). Docs (per the same lookup, not tested by us) say
   they do NOT: a new/resumed session, `/reload-plugins` or a restart is needed.
3. **Merge to the default branch / release.** Not done: `feature/live-events`
   is unmerged; `.claude-plugin/plugin.json` was bumped to `0.2.0` for the PR (the `version`
   pins installed users). Suggest 0.2.0 at merge; CHANGELOG `[Unreleased]` is ready.
4. **Naming.** Plugin `workflow` / package `orchestra` / repo `claude-orchestra`,
   and Claude Code now has its own "workflows" feature. `claude-` cannot start a
   plugin name. Decide before the user base grows (owner deferred it).
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
- **UNVERIFIED against a live Claude Code session:** the exact hook payload field
  names (`notification_type`, `message`, `error_type`, `error_message`, `reason`,
  `last_assistant_message`). They come from the docs summary, not a captured
  payload. `normalize_claude_hook` degrades to empty fields rather than failing,
  but ground-truth states (1.3) depend on `notification_type`. First thing to do
  with a real session: install the plugin, trigger a permission prompt, and
  inspect `<state dir>/events/<session>.jsonl`.
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
and the reason the step is not `--strict`.

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

Everything planned is built and pushed; CI is green through 1960490. Do, in order:
(1) Open item 1 with a real Claude Code session, (2) open a PR from
`feature/live-events` and merge, bumping `plugin.json` to 0.2.0.

### CI blocked by GitHub billing (2026-10-03, from 6a77988 on)
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
