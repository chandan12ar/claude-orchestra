---
description: Open the Cuelight dashboard for this session's subagents
argument-hint: "[stop | report [path]]"
---

Run Cuelight for the current Claude Code session.

Each action is one command that runs Cuelight's own file from the plugin
directory, `${CLAUDE_PLUGIN_ROOT}`. Run it from the current directory as it is:
do not `cd` first, so a report lands in the project.

Dispatch on `$ARGUMENTS`:

- **empty** — start the dashboard and print its URL:
  ```bash
  python "${CLAUDE_PLUGIN_ROOT}/orchestra/__main__.py" --session "$CLAUDE_CODE_SESSION_ID" --cwd "$PWD"
  ```
- **`stop`** — shut the server down:
  ```bash
  python "${CLAUDE_PLUGIN_ROOT}/orchestra/__main__.py" --session "$CLAUDE_CODE_SESSION_ID" --stop
  ```
- **`report`** or **`report <path>`** — write a self-contained HTML snapshot:
  ```bash
  python "${CLAUDE_PLUGIN_ROOT}/orchestra/__main__.py" --session "$CLAUDE_CODE_SESSION_ID" --cwd "$PWD" --report "<path or .>"
  ```

If `python` is not found, run the same command with `python3`.

Report back to the user exactly what the command printed — the URL, the report
path, or the failure text. Do not paraphrase a failure as success, and do not
retry a failed start more than once.

If the command prints "no session id", tell the user Cuelight could not detect
the session and they can pass one explicitly with `--session`.
