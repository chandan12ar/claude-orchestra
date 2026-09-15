# Orchestra

See what your Claude Code subagents are actually doing.

Orchestra reads the transcripts Claude Code already writes and reconstructs the
whole orchestration: how many agents ran, what each was asked to do, what each
was expected to produce, which are still going, which are stuck, and which
agent's output became which other agent's input.

## Install

```bash
/plugin marketplace add <this repo>
/plugin install orchestra
```

Requires Python 3.9 or newer. Nothing else — no pip install, no npm, no build.

## Use

| Command | What it does |
|---|---|
| `/orchestra` | Start the dashboard and open it |
| `/orchestra stop` | Shut the server down |
| `/orchestra report` | Write a self-contained HTML snapshot you can share |

## What you get

- **Timeline** — one row per agent: a status dot, a duration bar, parallel
  waves banded together. A still-running agent's dot pulses and its bar
  breathes, so "in progress" is never just a color you have to notice.
- **Graph** — who fed whom. Solid edges are exact (spawn, file handoff, direct
  message); dashed edges are inferred from text reuse and carry the snippet
  that produced them, so you can judge them yourself. The longest
  duration-weighted chain — the actual bottleneck, not just the longest hop
  count — is highlighted, and a newly-detected handoff flashes a dot
  traveling the edge the moment it happens.
- **Activity** — a merged, live, newest-first feed of tool calls across every
  running agent, click-through to the agent it came from.
- **Work Floor** — every agent as a small pixel-art sprite, animated by its
  status (idle, running, waving on completion, a one-shot jump burst the
  moment it finishes), tinted a stable per-agent hue so a busy floor still
  reads as distinct agents at a glance.
- **Drawer** — each agent's brief, its extracted objective and expected
  output, its returned result, its exact model version, a cache-hit
  breakdown, a tool-mix fingerprint (Read/Edit/Bash/Task at a glance), every
  tool call, and the files it read and wrote.
- **Filter bar** — free-text search plus status chips; narrows every view
  (Timeline, Graph, Activity) at once without ever hiding or reflowing
  anything.
- **Health & write-conflict boxes** — stalled, failed, and orphaned agents
  surfaced instead of buried, plus a flag for any file two agents wrote
  independently — a real correctness risk, not just informational.
- **Deep links** — every agent has a `#agent=<id>` URL, pasteable into a PR
  or a Slack thread, that opens straight to its drawer.
- **Notifications** — an optional desktop alert when an agent fails or the
  session ends, for when you're not watching the tab.
- **Copy summary** — one click produces a paste-ready markdown summary for a
  PR description or a status update.

## Privacy

Orchestra is local and read-only.

- The server binds `127.0.0.1` only, and every API call requires a token minted
  at launch.
- There is no network egress of any kind: no CDN, no fonts, no telemetry. Every
  asset ships in the package.
- It never writes to any file under `~/.claude`.
- Anything that looks like a credential is redacted before it reaches the page.

## Development

```bash
python -m unittest discover -s tests -t . -v
```

Standard library only, tests included.

## How it works

For the full architecture — how transcripts are turned into a dashboard, the
exact formulas behind the status states and the graph's edges, and what every
feature shows and why — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
