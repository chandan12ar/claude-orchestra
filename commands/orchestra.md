---
description: Open the Orchestra dashboard for this session's subagents
allowed-tools: Bash
argument-hint: "[stop | report [path]]"
---

Run Orchestra for the current Claude Code session.

The plugin directory is `${CLAUDE_PLUGIN_ROOT}`. Run every command from there so
`python -m orchestra` resolves.

Dispatch on `$ARGUMENTS`:

- **empty** — start the dashboard and print its URL:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID"
  ```
- **`stop`** — shut the server down:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --stop
  ```
- **`report`** or **`report <path>`** — write a self-contained HTML snapshot:
  ```bash
  cd "${CLAUDE_PLUGIN_ROOT}" && python -m orchestra --session "$CLAUDE_CODE_SESSION_ID" --report "<path or .>"
  ```

Report back to the user exactly what the command printed — the URL, the report
path, or the failure text. Do not paraphrase a failure as success, and do not
retry a failed start more than once.

If the command prints "no session id", tell the user Orchestra could not detect
the session and they can pass one explicitly with `--session`.
