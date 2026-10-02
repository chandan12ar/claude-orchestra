# Changelog

All notable changes to Workflow. Format: [Keep a Changelog](https://keepachangelog.com/);
versions follow [SemVer](https://semver.org/) (the `version` in
`.claude-plugin/plugin.json` pins installed users until it changes).

## [Unreleased]

### Added
- `--cwd` flag: base for relative report paths; fallback session discovery.
- `ORCHESTRA_*` environment variables for every threshold (see README).
- CI (Linux/macOS/Windows x Python 3.9/3.12/3.13), `SECURITY.md`, this file.

### Changed
- Handoff-edge inference is cached across polls and skips provably-failing
  searches: 96 agents went from 5.2 s to 0.22 s cold, 0.024 s warm.
- Session builders are held in a bounded LRU (default 8) instead of forever.

### Fixed
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
