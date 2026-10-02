# Changelog

All notable changes to Workflow. Format: [Keep a Changelog](https://keepachangelog.com/);
versions follow [SemVer](https://semver.org/) (the `version` in
`.claude-plugin/plugin.json` pins installed users until it changes).

## [Unreleased]

### Added
- Live events: async hooks (session, subagent, notification, API-failure, turn
  events) feed ground-truth state: what the session is waiting on (permission /
  input / idle / API error type), exact session end, exact subagent stop, and a
  new `waiting` agent status. `ORCHESTRA_EVENTS=off` disables recording.
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
  cleanly - for the open session and for other sessions in the fleet.
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
