# PROGRESS

Branch: `feature/live-events`. Rules: test -> commit -> push after every
feature; on failure record it here and stop. See `CLAUDE.md`.

## Status

| # | Item | State |
|---|---|---|
| 0.0 | CLAUDE.md + PROGRESS.md | done |
| 0.1 | Cache handoff-edge inference (perf) | todo |
| 0.2 | Evict idle session builders | todo |
| 0.3 | `report` writes to project, not plugin dir; wire/remove `--cwd` | todo |
| 0.4 | Configurable thresholds (env vars + plugin userConfig) | todo |
| 0.5 | Dead code + log-handle cleanup | todo |
| 0.6 | CI workflow, CHANGELOG, SECURITY.md | todo |
| 1.1 | Agent-neutral event schema + spool (`events.py`) | todo |
| 1.2 | Hook entrypoint + `hooks/hooks.json` | todo |
| 1.3 | Ground-truth session states from events | todo |
| 1.4 | Live push (SSE) | todo |
| 1.5 | Fleet view / attention inbox | todo |
| 2.1 | Pill (Picture-in-Picture) + tab badge | todo |
| 2.2 | Synthesized sounds | todo |
| 2.3 | Cost, budget alert, runaway detection | todo |
| 4.1 | Run history (sqlite3) | todo |
| 4.2 | Replay scrubber | todo |
| 4.3 | Export JSON/CSV | todo |

Deferred by owner decision: dashboard approve/deny; Gemini/Cursor adapters;
plugin rename.

## Notes / failures

(none yet)

## Next step

Start at the first `todo` row above.
