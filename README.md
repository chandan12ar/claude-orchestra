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

- **Timeline** — one row per agent, with parallel waves banded together.
- **Graph** — who fed whom. Solid edges are exact (spawn, file handoff, direct
  message); dashed edges are inferred from text reuse and carry the snippet that
  produced them, so you can judge them yourself.
- **Drawer** — each agent's brief, its extracted objective and expected output,
  its returned result, its tool calls, and the files it read and wrote.
- **Health** — stalled, failed, and orphaned agents surfaced instead of buried.

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
