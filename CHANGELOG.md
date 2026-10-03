# Changelog

All notable changes to Cuelight (called Workflow before 0.4.0). Format: [Keep a Changelog](https://keepachangelog.com/);
versions follow [SemVer](https://semver.org/) (the `version` in
`.claude-plugin/plugin.json` pins installed users until it changes).

## [Unreleased]

## [0.10.0] - 2026-10-03

### Added
- **What each agent was told**: the instruction files (CLAUDE.md, rules, memory) and skills each agent
  had, and which agents ran without the project instructions the main session had. An Insights card
  shows how many agents loaded your project instructions, names the ones that did not (with their agent
  type, since some types may be meant to run without them), and lists every instruction file with its
  type, size and how many agents loaded it; the agent panel lists that agent's files. Read from the
  `instructions`, `nested_memory` and `skill_listing` records Claude Code writes in every transcript,
  subagents' included, so it needs no new hook and works on past sessions. Only paths, types and sizes
  are kept, never the files' content. Worktree copies of a file count as the same file.
- The demo's read-only audit agent runs without the project instructions; the payment agent picks up a
  nested CLAUDE.md when it touches `src/payments`.

### Fixed
- Found while checking this release in a real browser: the main session's instruction files were
  cleared on every refresh after the first (a misplaced line in the builder), so a live dashboard lost
  them within seconds. Fixed before release, with a test that refreshes after the transcript grows.

## [0.9.0] - 2026-10-03

### Added
- **What the run produced**: commits, pushes, pull requests, branch merges and test runs, from the main
  session and every agent. An Insights card shows the counts, how many test, build and lint runs passed,
  the whole run's cost (or fresh tokens) per commit and per pull request, the pull requests as links
  (https only, opened in a new tab without a referrer) and the latest commits with their first message
  line, branch, author agent and time. The agent panel says what that agent produced. Read from the git
  results Claude Code records on a Bash call (`toolUseResult.gitOperation`) and its `pr-link` entries.
  A `git commit -q` prints no commit id, so Claude Code records none: it is still counted, without a sha.
  A forked agent replays its parent's history, so commits and pull requests are counted once.
- The demo's orchestrator commits, pushes and opens a pull request; one agent commits its own work.

## [0.8.0] - 2026-10-03

### Added
- **Review the changes**: every agent's file changes as real diffs. The agent panel has a Changes
  section: one folded entry per file with lines added and removed, "new" for created files, and the
  diff with old and new line numbers and the time of each edit. An Insights card "What changed" totals
  files, lines added and removed and new files, ranks agents by lines changed, and lists the most
  changed files (worktree copies of a file count as one). Diffs come from the patch Claude Code records
  for each successful edit (`toolUseResult.structuredPatch`, or a new file's content), so nothing is
  re-diffed; transcripts without it fall back to the edit's own old and new text. Failed edits are not
  counted. Bounded at 1,500 diff lines per agent (counts stay complete) and redacted as one text per
  hunk, so a key spread over several lines is caught. Files in scratch or temp folders are shown but not
  counted. Static reports carry the diffs too.
- The demo records realistic patches for its new files and edits.

## [0.7.0] - 2026-10-03

### Added
- **Did they check their work?** For every agent that edited code, whether it ran a test, build,
  type check or lint (or ran the very file it edited) after its last edit, and whether that check
  passed. Finished agents that are unchecked or whose last check failed are listed in the Health box;
  an Insights card counts checked, failing and unchecked agents and lists the ones worth a look with
  the evidence (last file edited, last check command); the agent panel says it in one line. Docs and
  files in scratch or temp folders do not count as code. Rule-based (`orchestra/verify.py`), no model
  calls. Add your own check commands with `ORCHESTRA_VERIFY_PATTERN` (a regular expression; an
  invalid one is ignored and the card says so).
- Tool calls now record whether their result was an error (`ok` in the agent detail payload).
- The demo has a failing test run, an agent that edited after its last type check, and checked ones.

## [0.6.0] - 2026-10-03

### Added
- **Waiting on you**: how long agents sat on permission and input prompts only you could answer.
  A new Insights card shows your time (overlapping waits counted once), agent time lost (every
  wait added), the number of waits, the longest, a per-agent ranking and the latest prompts with
  their text. A wait still open counts up live, and the agent panel says how long that agent
  waited on you. A wait ends at the agent's next transcript entry, so an approved command's own
  run time is included (transcripts record no separate time for it). A prompt still up when the
  session ended is listed as never answered and given no length. Backed by `livestate.waits`,
  `insights.waits` and per-transcript activity times; no new hook.
- The demo has answered prompts (two of them overlapping) and one agent waiting on you now.
- README section "Example use cases" (Anthropic's directory policy asks for at least three working examples).

### Fixed
- The directory's validation blocked the plugin for "secret in a shipped file": the redaction tests
  held fake keys as text. They are now built from pieces at run time (`tests/fake_secrets.py`), the old
  planning document was cleaned the same way, and a test keeps secret-shaped text out of every file.

## [0.5.0] - 2026-10-03

### Added
- Plugin metadata for Anthropic's directory listing: `displayName`, `homepage`, `repository`, `license`,
  `icon` (new `assets/icon.png`), `documentationUrl`, `supportUrl` and `privacyPolicyUrl`.
- `PRIVACY.md` (what it reads, writes and never sends), `CONTRIBUTING.md`, issue and pull request templates.
- `docs/WORKFLOW.md` (the whole flow on one page) and `docs/SUBMISSION.md` (checklist status and the
  answers for the submission form).
- README section "What Cuelight runs and touches".
- `tests/test_directory_readiness.py`: Anthropic's mechanical pre-submission checks, run on every build.
- **Pulse**: a live strip above every view with charts for agents running, tool calls per
  minute and fresh tokens spent, change arrows against the previous minute, a "needs you"
  tile and a tape of recent starts, finishes, failures and stalls (click one to open the
  agent). Backed by a new `insights.pulse` block and per-message token timestamps. Charts
  update in place, hover shows exact values, it can be collapsed, and it stops moving under
  reduced motion.

### Changed
- The project's own `CLAUDE.md` moved to `.claude/CLAUDE.md` (a `CLAUDE.md` at the plugin root is not
  loaded and draws a validator warning). CI now runs `claude plugin validate --strict`.

## [0.4.1] - 2026-10-03

### Changed
- The always-on-top pill has a new look: a small cue-light mascot whose face and motion show the
  state (asleep when quiet, bobbing with an orbiting ring when agents run, hopping with ripples when
  something needs you, shaking on an error), a count badge when several sessions wait, and a
  segmented bar for the agents. It is drawn in the window itself (no images, no network), follows
  the light/dark setting and stops moving when reduced motion is requested. The window is now built
  once and updated in place, so the animation no longer restarts on every poll.
- The browser tab icon now shows the same cue-light face for the current state, with the count of
  waiting sessions in a red badge, so the state is visible while the dashboard tab is in the background.

### Fixed
- The marketplace entry still showed the old description; the README install line now names
  the marketplace (`/plugin install cuelight@cuelight-marketplace`).

## [0.4.0] - 2026-10-03

### Changed
- **Renamed from Workflow to Cuelight.** A cue light is the lamp that tells a performer
  "now", which is what the dashboard is for. What changes for you:
  - The command is `/cuelight:open` (was `/workflow:open`), and the plugin is `cuelight`:
    `/plugin marketplace add chandan12ar/cuelight`, then `/plugin install cuelight`.
  - Downloaded exports are named `cuelight-<session>.csv|json` (were `workflow-...`).
  - New config and history live under a `cuelight` directory
    (`~/.config/cuelight/prices.json`, `~/.local/share/cuelight/history.sqlite`; Windows:
    `%APPDATA%\cuelight`, `%LOCALAPPDATA%\cuelight`). A price file or history already
    saved under the old `workflow` directory is still found and used until a `cuelight`
    one exists, so nothing is lost. `ORCHESTRA_PRICES` and `ORCHESTRA_HISTORY_DB` still
    override both.
  - The GitHub repository is now `chandan12ar/cuelight` (the old address redirects).
  - Unchanged: the internal Python package (`orchestra`), every `ORCHESTRA_*` environment
    variable, the API and the on-disk event format.

## [0.3.0] - 2026-10-03

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

- Graph: a layered layout (ranks by exact edges, up/down ordering sweeps that keep the
  best crossing count, exact vertical alignment with neighbours), pan and zoom (drag,
  Ctrl/Cmd+wheel, buttons, `+`/`-`/`0`), hover focus that fades unrelated agents, a
  duration on every card, and the orchestrator's launch fan drawn only where it is the
  explanation (hover the orchestrator for the rest).
- Work Floor: each card shows what the agent is doing right now (its last tool call)
  and an activity sparkline; group by role or by status, attention first; the floor is
  no longer rebuilt (restarting every sprite) when nothing visible changed.

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
