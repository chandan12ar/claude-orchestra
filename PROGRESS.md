# PROGRESS

Branch: `feature/live-events`. Rules: test -> commit -> push after every
feature; on failure record it here and stop. See `CLAUDE.md`.

## Status

| # | Item | State |
|---|---|---|
| 0.0 | CLAUDE.md + PROGRESS.md | done |
| 0.1 | Cache handoff-edge inference (perf) | done (96 agents: 5.2s -> 0.22s cold, 0.024s warm; results proven identical to old algorithm) |
| 0.2 | Evict idle session builders | done (LRU, MAX_BUILDERS=8, default session pinned) |
| 0.3 | `report` writes to project, not plugin dir; wire/remove `--cwd` | done (`--cwd` wired; also finds newest session when no id) |
| 0.4 | Configurable thresholds | done (ORCHESTRA_* env vars, validated; documented in README; plugin userConfig deliberately not used — not documented to reach slash commands) |
| 0.5 | Dead code + log-handle cleanup | done |
| 0.6 | CI workflow, CHANGELOG, SECURITY.md | done (CI result verified after push — see Notes) |
| 1.1 | Agent-neutral event schema + spool (`events.py`, `statedir.py`) | done (state dir now per-user + ownership-checked) |
| 1.2 | Hook entrypoint + `hooks/hooks.json` | done |
| 1.3a | Ground-truth states: backend (`livestate.py`, build + service wiring) | done |
| 1.3b | Ground-truth states: UI (attention banner, `waiting` status) | done (verified in real Chromium, light+dark) |
| 1.4 | Live push (SSE) | done (measured in Chromium: 613 ms event->banner; idle page 0 req/3s) |
| 1.5a | Fleet backend (`/api/fleet`, cross-project recent sessions, urgency sort) | done |
| 1.5b | Fleet UI (tab, badge, cross-session notifications) | done (verified in Chromium; fixed an empty-bar bug via global [hidden] rule) |
| 2.1 | Pill (Picture-in-Picture) + tab badge | done (title, favicon, and PiP window verified in real Chromium; PiP is Chromium-only, button hidden elsewhere) |
| 2.2 | Synthesized sounds | done (verified in Chromium: off=0 oscillators, chime=3, alert=2). Not built yet: per-event mute, quiet hours (client-only follow-up) |
| 2.3.0 | FIX: token double-counting (per-entry vs per-message) | done (real transcript: old overcounted 2.6-3.1x) |
| 2.3a | Cost + budget alert (incl. orchestrator tokens, per model) | done (verified in Chromium; hand-computed orchestrator cost matched) |
| 2.3b | Runaway/loop detection | done (verified in Chromium: health box + drawer) |
| 4.1 | Run history (sqlite3) | todo |
| 4.2 | Replay scrubber | done (live + static report verified in Chromium) |
| 4.3 | Export JSON/CSV | done (verified: real browser downloads, CLI, scrubbing, CSV-injection guard) |

Deferred by owner decision: dashboard approve/deny; Gemini/Cursor adapters;
plugin rename.

## Notes / failures

- CI (run 1, commit 8f427a9): all 9 test jobs (Linux/macOS/Windows x py3.9/3.12/3.13)
  green. The `claude plugin validate` job went green but finished in <1s, so treat
  it as *unconfirmed* until its log is read.
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

Start at the first `todo` row above.
