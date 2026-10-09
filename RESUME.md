# RESUME — read this first to continue the session

Repo: `chandan12ar/cuelight` (public) · default branch **`main`**, which changes only by a merged
PR (the owner clicks Merge on GitHub; `gh pr merge` from here is blocked). The product is
**Cuelight**; the internal package is still `orchestra`.

State (2026-10-09): **0.21.2 is on main** (#35 docs, #36 focus fix and #37 directory fix merged; CI on
main green). The directory had blocked 0.21.0 for "Secret in a shipped file"; #37 fixed it. **The owner
paused new features**: work now is fixing what exists and keeping directory findings from coming back.
**PR #38 (0.21.3, audit: graph `0` key fix, demo text, credential-read guard, every document refreshed)**
is open. The last GitHub Release is **v0.17.0**; one for 0.21.x needs the owner's yes.
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
| 0.21.1-0.21.3 | Keyboard focus kept across live refreshes; the directory block (fake Bearer token in a test) and wider secret guard; audit: graph `0` key, demo text, credential-read guard, docs refreshed |

## 4. Still open
- **Owner's direction (2026-10-09): no new features for now.** Fix what exists, keep the docs current,
  and make sure directory findings do not come back. Every finding and what it means is in
  `docs/SUBMISSION.md` ("Findings in the portal"); `CONTRIBUTING.md` says what the scanner flags.
- **Next, in order:**
  1. Owner merges #38 (0.21.3 audit) once CI is green, then clicks "Check for new commits" in the
     portal, and sends the new findings if any.
  2. GitHub Release v0.21.3 (notes from CHANGELOG 0.18.0-0.21.3), owner's yes first.
  3. Parked until the owner lifts the pause: **name the pending call on a permission prompt**
     (real-data basis in `PROGRESS.md` "Resumed"; show a design first) and agent teams (needs a
     real team run; none on this machine as of 2026-10-09).
  - Done 2026-10-09: keyboard focus across live refreshes (#36, 0.21.1); the directory block (#37,
    0.21.2); the audit (#38, 0.21.3).
- **Owner's calls:** the release above; trademark search for "Cuelight"; checking the directory
  review result for 0.21.x in the portal (every merge to main is re-scanned).
- **Unverified:** agent teams (teammates): no handling, and no team run in the local
  transcripts to test against; `bin/cuelight` launcher's permission prompt in a live Claude
  Code; the API-error hook fields `error_type`/`error_message` (docs only).
- **Known gaps:** the permission notification often carries no tool name (parked, item 3 above); history
  records only sessions that were viewed or scanned. (`agent_type` is empty on `agent_stop`, but
  nothing reads it: agent types come from the transcripts.)
- **Ideas not started:** Fleet ticker.
- **File size:** the plugin directory takes no non-image file over 256 KiB. `app.js` is ~234 KiB in
  a CRLF checkout (about 240 KB at 0.21.3); put new tab-sized views in `static/tabs.js` (`test_directory_readiness` fails
  first if a file grows past the limit).

## 5. Practical notes
- Tests: `python -m unittest discover -s tests -t .` (Node 22+ needed for the UI tests).
- UI check: `python -m orchestra --demo --no-open --port 8766 --token demo`, then
  `node docs/evidence/capture.mjs` (uses its own headless Chrome/Edge profile; `ONLY=a,b` limits
  the shots). Check against a REAL session too: `python -m orchestra --session <id>`.
- Stop servers by port or PID, never by image name (it would close the owner's Chrome).
- Reloading the page in a browser check: a navigate that changes only the `#...` part does not reload,
  so old code keeps running. Change the query string (`&reload=1`). The server itself reads static
  files on every request.
- The directory scans tests and docs too: never write credential-shaped text, even fake (use
  `tests/fake_secrets.py`, or `<fake-...>` in docs), and never the literal names of credential
  sources in the demo. The guard tests in `test_directory_readiness` fail first.
- Windows: always `encoding="utf-8"`; set mtimes explicitly in tests; files check out CRLF
  (tests about stored content read `git show :path`); use Edit, not shell rewrites, for
  any text with backslashes; no `$` inside double-quoted commit messages.
- Real data beats fixtures: check every new number against a real transcript in
  `~/.claude/projects/...` (read-only; never write under `~/.claude`).
