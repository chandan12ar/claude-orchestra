# Workflow — known follow-ups

Everything here was raised by the final whole-branch review and judged
non-blocking. The branch ships without these; they are recorded so the decision
is visible rather than forgotten. Ranked roughly by value.

## Worth doing next

1. **Add a table view.** `expected_output` is extracted for every agent but is
   only visible in the drawer, one agent at a time — and neither the timeline
   nor the graph surfaces it. Of the five questions Workflow answers, "what was
   this agent supposed to produce" is the one whose answer is least certain
   (deterministic extraction from prose), so it is the one that most needs to be
   scannable side by side, where a reader can spot the row whose deliverable
   does not match what came back. A compact table of
   description / objective / expected output / result would close this; the
   extraction work behind it is already done.

2. **Cache handoff edge inference.** `_handoff_edges` runs
   `SequenceMatcher.find_longest_match` for every ordered pair passing the
   causality guard, before the score short-circuit, and the whole edge set is
   recomputed on every 2-second poll. Measured on ~2000-word briefs: 0.17s at 21
   agents, 1.40s at 50, 5.24s at 96 — and 4-10x worse on the boilerplate-heavy
   briefs a subagent-driven run actually produces. Long sequential
   orchestrations are both the worst case and the common case. Cache results
   keyed on the `(result, brief)` pairs that actually changed.

3. **`/workflow:open report` writes to the plugin directory, not the project.**
   `commands/open.md` does `cd "${CLAUDE_PLUGIN_ROOT}"` and passes a
   relative path, which `write_report` resolves against that cwd. Spec section
   12 says the project root, and `.gitignore` already carries
   `orchestra-report-*.html` as though it lands there. Pass an absolute path.

4. **`--cwd` does not exist.** Spec sections 6 and 12 both name it; `argparse`
   rejects it. Consequently `find_project_dir` and `encode_project_dir` are
   dead code, reachable only from their own tests. Either wire up the flag or
   delete both functions — the current state is the worst of the two.

5. **Evict idle builders.** `OrchestraService._builders` retains a full
   `RunBuilder` per session the picker visits, each holding every agent's
   digest, for the life of the process (bounded only by the 30-minute idle
   shutdown).

## Smaller

6. **Status is conveyed by label and colour, not shape.** Spec section 15 says
   "shape and label". Timeline bars and graph dots are the same shape for every
   status; only open rounds differ, by dash pattern. The label does carry the
   status, so greyscale and colourblind readers are served — but the spec's
   literal claim is not met.

7. **Two undocumented spec divergences, both defensible.** Hub files are listed
   as footer text rather than drawn as a "shared context" node; and the DAG
   ranks by longest path over exact edges rather than by `spawn_depth` (which is
   shipped in the light payload and read by nothing). Longest-path is the better
   rank — amend the spec rather than the code.

8. **`/api/sessions` takes `?session=`, not `?project=`** as spec section 14
   specifies. The shipped shape is better, since it derives the project from the
   session. Again: amend the spec.

9. **Dead code left by fix rounds.** `_assemble` is typed `Optional[Agent]` and
    never returns `None`, so `build.py`'s `if a is not None` filter is dead.
    `IncrementalReader.reset()` and `parent.content_text` have no production
    caller. `_OUTPUT_HEADINGS` lists `"report format"` twice.
    `SessionInfo.size_bytes` is served over `/api/sessions` and read by nothing —
    drop the field rather than test it.

10. **Stall threshold is "configurable" only by editing `constants.py`** — no
    flag, no environment variable (spec section 8).

11. **`cmd_start` opens the log file handle and never closes it** in the parent
    process.

## Resolved

- **Thresholds only configurable by editing code (#10).** Every threshold in
  `constants.py` now reads an `ORCHESTRA_*` env var (validated, falls back to
  the default); table in README.

- **Idle builders never evicted (#5).** `OrchestraService` keeps an LRU of
  `MAX_BUILDERS` (8); the default session is pinned; evicted sessions are
  rebuilt from disk on next visit.
- **`report` wrote to the plugin dir (#3) and `--cwd` did not exist (#4).**
  `--cwd` now exists: base for relative report paths and fallback session
  discovery (newest session of that project). `commands/open.md` passes it.

- **Handoff-edge inference cost (#2).** `HandoffCache` in `edges.py` keys scores
  by text content so unchanged pairs are never rescored across polls, and a
  provably exact prefilter skips `SequenceMatcher` when too few 8-word shingles
  are shared for a 40-word run to exist. 96 agents: 5.2s -> 0.22s cold, 0.024s
  warm. `TestHandoffCache` checks results against the old algorithm.

- **`AgentDigest`'s docstring claim.** Was false: "safe to ingest repeatedly"
  didn't hold, because `transcript.py` resetting its offset to 0 on a
  truncated/replaced file left `build.py`'s accumulated per-agent digest in
  place, double-counting tokens and tool calls on re-read. Fixed by adding
  `IncrementalReader.consume_reset()`, which `RunBuilder._scan_subagents` now
  checks to drop the stale digest before re-ingesting — the same fix
  `ParentIndex` already had. See §4 and §5 (`transcript.py`) in
  `ARCHITECTURE.md`.

## Deliberately not doing

- **`scrub_obj` converting tuples to lists.** Every value originates from
  `json.loads`, which never produces tuples.
- **`status.py`'s `digest.last_activity_at or final.started_at` treating `0.0`
  as falsy.** Domain-impossible: every timestamp is a 2020s epoch, and both
  branches converge on the same answer anyway.
