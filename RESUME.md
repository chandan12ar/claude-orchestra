# RESUME — read this first to continue the session

Repo: `chandan12ar/cuelight` (public) · default branch **`main`**, which changes only by a merged
PR (the owner clicks Merge on GitHub; `gh pr merge` from here is blocked). The product is
**Cuelight**; the internal package is still `orchestra`.

State (2026-10-09, **stopped here at the owner's request**, out of credits): **0.21.0 is on main**.
PRs #30-#34 are all merged and CI on main is green (run 37889257756, after the #34 merge). Local
suite: **1,148 tests, 3 skipped**. The last GitHub Release is **v0.17.0**; one for 0.21.0 needs the
owner's yes. Nothing is in flight: no open PRs, no unpushed work.
Details: `PROGRESS.md` (status by round), `.claude/CLAUDE.md` (rules + lessons), `CHANGELOG.md`
(per version), `docs/ARCHITECTURE.md` (how it works), `docs/FOLLOW-UPS.md` (older review items).

## 1. What the product is
A Claude Code plugin (`/cuelight:open`) serving a local dashboard of a session's subagent
orchestration. Python 3.9+ stdlib only, vanilla JS, no build step, no network egress,
loopback + token. Direction agreed with the owner: **observability + control plane for Claude
Code multi-agent work** = the record (transcripts) + the live truth (hooks), across all sessions.
Tabs: Timeline, Graph, Insights, Prompts, Activity, Work Floor, Fleet, History.

## 2. Owner's rules (follow exactly)
1. One feature = one branch, one PR, one minor version (bump `plugin.json` + a CHANGELOG
   heading; installed copies only update when the version changes).
2. After each feature: full test suite -> commit -> push -> **read the PR's CI and say it is
   green before asking the owner to merge**.
3. If something fails: note it in the commit message or `PROGRESS.md`, push, and stop.
4. If the owner says "stop": commit + push everything, update the docs with where we stopped.
5. New feature work: show a short design first and wait for the go-ahead. Never force-push.
6. Out of scope by owner decision: dashboard approve/deny, Gemini/Cursor adapters.
7. Every merge to `main` becomes the version the Claude plugin directory reviews (it
   re-scans about every 6 hours), so keep `main` clean.

## 3. What is built
| Versions | Delivered |
|---|---|
| 0.2-0.5 | Live layer (async hooks -> spool -> SSE), Fleet, pill + sounds, cost/budget, loops, replay, export, history; Insights, Graph, Work Floor, palette, `--demo`; rename to Cuelight; Pulse strip; directory-readiness docs |
| 0.6-0.10 | Waiting on you; Did they check their work?; Review the changes (diffs); What the run produced (commits/PRs/tests); What each agent was told |
| 0.10.1-0.11 | Directory review fixes (no `allowed-tools`, removed an uninspectable image); `bin/cuelight` launcher so "don't ask again" covers Cuelight only |
| 0.12-0.16 | Catch me up (titles, recaps); Where tokens were wasted; Prompts tab; Context pressure; What went wrong (API stalls, failed calls, retries, timeouts) |
| 0.17 | Insights at a glance: chip strip (worst first) + foldable cards |
| 0.18-0.21 (merged) | Agents tab (+ SubagentHandback reports, deliverable phrasings); Spend tab (running cost, budget crossings; tab views moved to `static/tabs.js` for the 256 KiB limit); Activity search (`/api/calls`); graph edge routing |

## 4. Still open
- **Next, in order (when work resumes):**
  1. GitHub Release v0.21.0 (notes from CHANGELOG 0.18.0-0.21.0), owner's yes first.
  2. Keyboard focus lost on live refresh in the Prompts tab and the Insights fold buttons/chips
     (the Agents and Spend tabs already keep it; copy `renderAgents`' keep/restore block). A
     ready brief exists as a proposed session card "Keep keyboard focus across live refreshes".
  3. Agent teams (teammates): needs one real team run from the owner to build against.
- **Owner's calls:** the release above; trademark search for "Cuelight"; checking the directory
  review result for 0.21.0 in the portal (every merge to main is re-scanned).
- **Unverified:** agent teams (teammates): no handling, and no team run in the local
  transcripts to test against; `bin/cuelight` launcher's permission prompt in a live Claude
  Code; the API-error hook fields `error_type`/`error_message` (docs only).
- **Known gaps:** `agent_type` is empty on `agent_stop`; the permission notification carries no
  tool name; history records only sessions that were viewed or scanned.
- **Ideas not started:** Fleet ticker; the Prompts tab and Insights fold buttons lose keyboard
  focus on each live refresh (the Agents and Spend tabs already keep it; same fix applies).
- **File size:** the plugin directory takes no non-image file over 256 KiB. `app.js` is ~234 KB in
  a CRLF checkout; put new tab-sized views in `static/tabs.js` (`test_directory_readiness` fails
  first if a file grows past the limit).

## 5. Practical notes
- Tests: `python -m unittest discover -s tests -t .` (Node 22+ needed for the UI tests).
- UI check: `python -m orchestra --demo --no-open --port 8766 --token demo`, then
  `node docs/evidence/capture.mjs` (uses its own headless Chrome/Edge profile; `ONLY=a,b` limits
  the shots). Check against a REAL session too: `python -m orchestra --session <id>`.
- Stop servers by port or PID, never by image name (it would close the owner's Chrome).
- Windows: always `encoding="utf-8"`; set mtimes explicitly in tests; files check out CRLF
  (tests about stored content read `git show :path`); use Edit, not shell rewrites, for
  any text with backslashes; no `$` inside double-quoted commit messages.
- Real data beats fixtures: check every new number against a real transcript in
  `~/.claude/projects/...` (read-only; never write under `~/.claude`).
