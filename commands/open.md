---
description: Open the Cuelight dashboard for this session's subagents
allowed-tools: Bash
argument-hint: "[stop | report [path]]"
---

Run Cuelight for the current Claude Code session.

The plugin directory is `${CLAUDE_PLUGIN_ROOT}`. Run every command from there so
`python -m orchestra` resolves, and pass the directory you started in as `--cwd`
so reports land in the project, not in the plugin directory.

Dispatch on `$ARGUMENTS`:

- **empty** — start the dashboard and print its URL:
  ```bash
  P="$PWD"; cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --cwd "$P"
  ```
- **`stop`** — shut the server down:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --stop
  ```
- **`report`** or **`report <path>`** — write a self-contained HTML snapshot:
  ```bash
  P="$PWD"; cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --cwd "$P" --report "<path or .>"
  ```

Report back to the user exactly what the command printed — the URL, the report
path, or the failure text. Do not paraphrase a failure as success, and do not
retry a failed start more than once.

If the command prints "no session id", tell the user Cuelight could not detect
the session and they can pass one explicitly with `--session`.
