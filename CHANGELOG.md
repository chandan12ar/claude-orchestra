# Changelog

All notable changes to Workflow. Format: [Keep a Changelog](https://keepachangelog.com/);
versions follow [SemVer](https://semver.org/) (the `version` in
`.claude-plugin/plugin.json` pins installed users until it changes).

## [Unreleased]

### Added
- Insights tab: parallelism over time, the critical path (the exact-evidence chain
  of agents that set the run's length, overlap counted once), tool use, prompt-cache
  hit rate, fresh-token share by agent and model, contended and widely-read files,
  longest-running agents, and a Spend card with burn rate and a budget forecast.
  Computed server-side (`orchestra/insights.py`) and carried in the summary payload,
  so reports and exports include it.
- Command palette (`Ctrl/Cmd+K` or `/`) with instant agent and command results and
  `GET /api/search` for tool calls and files. The search matches the redacted text
  only (it cannot be used to probe a secret); repeated identical calls collapse into
  one row with a count, so a loop reads as x10. Static reports search the details
  baked into them.
- Keyboard shortcuts (`1`-`7` views, `L`, `R`, `T`, `F`, `?`, `Esc`) with a help
  dialog; they never fire while typing or with a modifier key. Tabs support arrow
  keys.
- Timeline: a comb of ticks on each bar showing when the agent was calling tools,
  an outline on the critical path, durations inside bars and a "now" marker.
- `python -m orchestra --demo`: a scripted 13-agent run served from a throwaway
  directory, with a simulator that keeps the running agents moving.
- Theme: automatic, light or dark, remembered per browser, applied before first
  paint. Deep links now carry the view (`#view=insights&agent=<id>`).
- Empty and loading states, and a visible "can't reach the dashboard" message.

### Changed
- A new design system: a monochrome interface where status is the only colour, a
  transport-style header (live clock, stat strip, budget meter), alerts as status
  chips, a redesigned agent drawer, and a responsive layout down to phone width.
  Text and status colours are held to WCAG 4.5:1 by `tests/test_contrast.py`.
- The static report's page shell is now derived from `index.html` instead of being
  a hand-kept copy, so the two cannot drift apart.

### Fixed
- `fmtDuration` shows hours and never rounds to "1m 60s"; the main-transcript
  reader drops launches that a truncated file no longer contains; the page declares
  its own icon (no `/favicon.ico` request).

## [0.2.0] - 2026-10-03

### Added
- Live events: async hooks (session, subagent, notification, API-failure, turn
  events) feed ground-truth state: what the session is waiting on (permission /
  input / idle / API error type), exact session end, exact subagent stop, and a
  new `waiting` agent status. `ORCHESTRA_EVENTS=off` disables recording.
- Run history (opt-in, `ORCHESTRA_HISTORY=on`): a History tab to list past runs and
  compare any two (agents, failures, wall time, tokens, cost, loops). Stores
  metrics only - never prompts, results, or file paths - in a per-user 0600 database
  pruned by age and count; off by default.
- Replay: scrub or play back the run to see who had launched, who was running and
  who had finished at any moment - in the live dashboard and in a static report,
  so a post-mortem needs no server. Statuses are reconstructed from start/end
  times; token totals are shown as unavailable rather than wrong.
- Export: `Export` menu (CSV one-row-per-agent, JSON everything), `GET /api/export`,
  and `python -m orchestra --export csv|json [--out PATH]`. CSV cells that a
  spreadsheet would run as a formula are neutralised; output is the same scrubbed
  data the dashboard shows.
- Possible-loop detection: an agent that is still running and has repeated the
  same tool call (or strictly alternated between two) is listed as a *possible*
  loop with the evidence; thresholds configurable. Never a verdict.
- Cost and budget: with a user-supplied price file, the header shows what the run
  cost - the orchestrator's own usage included, priced per model - with a partial
  flag for unpriced models and a budget (`ORCHESTRA_BUDGET`) that warns at 80 %
  and alerts when exceeded. No prices are built in.
- Sounds (off by default, synthesized with Web Audio, no audio files): a distinct
  tone when Claude needs you, when something fails, and when everything finishes
  cleanly - for the open session and for other sessions in the fleet. An *Options*
  panel mutes each event separately and sets quiet hours (overnight spans work).
- Pill: a small always-on-top window (Document Picture-in-Picture, Chromium) showing
  what needs you across all sessions, plus a tab-title count and a favicon badge
  that work in every browser.
- Fleet view: every recently active session across all projects, most urgent
  first, with a badge and notifications for sessions you are not viewing.
- Live push (Server-Sent Events): the dashboard updates when something changes
  instead of every 2 s; polling remains as a 15 s safety net and as the fallback.
- `--cwd` flag: base for relative report paths; fallback session discovery.
- `ORCHESTRA_*` environment variables for every threshold (see README).
- CI (Linux/macOS/Windows x Python 3.9/3.12/3.13), `SECURITY.md`, this file.

### Changed
- Handoff-edge inference is cached across polls and skips provably-failing
  searches: 96 agents went from 5.2 s to 0.22 s cold, 0.024 s warm.
- Session builders are held in a bounded LRU (default 8) instead of forever.

### Fixed
- **Security:** the page's `esc()` helper did not escape quotes, so a value containing
  one could break out of a quoted HTML attribute and inject another. It now does.
- **Token counts were inflated 2.6-3.1x on real transcripts.** Claude Code writes
  one entry per content block, each repeating the message's usage; usage is now
  counted once per API message id (and tracked per model).
- `/workflow:open report` wrote into the plugin directory; it now writes into
  the project.
- A leaked log file handle in `cmd_start`.

### Removed
- Dead code: `IncrementalReader.reset`, `parent.content_text`,
  `SessionInfo.size_bytes`.

## [0.1.0]
- Initial release: timeline, graph, activity, Work Floor, drawer, filters,
  health and write-conflict boxes, deep links, notifications, copy summary,
  static report.
