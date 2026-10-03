# Live hook capture: the field names, checked against a real session

On 2026-10-03 the plugin was installed from GitHub into a real Claude Code session
(version 2.1.287, Windows) and the events its hooks wrote were read back from the spool.
Until then every field name the "waiting on you" feature reads came from the documentation.
These are real events, in order. Session ids and file paths are removed; nothing else is changed.

| # | kind | what Claude Code sent (kept fields) |
|---|---|---|
| 1 | `session_start` | `{"source": "startup", "model": "claude-sonnet-5-5"}` |
| 2 | `turn_end` | `(no extra fields)` |
| 3 | `turn_end` | `(no extra fields)` |
| 4 | `notification` | `{"notification_type": "idle_prompt", "message": "Claude is waiting for your input"}` |
| 5 | `turn_end` | `(no extra fields)` |
| 6 | `agent_stop` | `{"message": "stop the cuelight dashboard"}` (and an `agent_id`) |
| 7 | `turn_end` | `(no extra fields)` |
| 8 | `notification` | `{"notification_type": "permission_prompt", "message": "Claude needs your permission"}` |

The same install also recorded the end of an earlier session:

| kind | what Claude Code sent |
|---|---|
| `turn_end` | (no extra fields) |
| `session_end` | `{"reason": "prompt_input_exit"}` |

## What this confirms

- `notification_type` and `message` are the right names, for both `idle_prompt` and
  **`permission_prompt`**, so the purple "Waiting for your permission" banner reads real data.
  The permission message is exactly "Claude needs your permission": Claude Code does not put the
  tool name in it in this version, so the banner cannot show which tool is asking.
- `session_start` carries `source` and `model`; `agent_stop` carries the subagent's final text
  as `message` (from `last_assistant_message`); `turn_end` needs no fields.
- `session_end` carries `reason`, so the "Ended (prompt_input_exit)" label in the Fleet list is
  read from real data.
- The dashboard showed the banner, a badge on the Fleet tab and a Fleet row for the waiting
  session. The screenshot of that live session is not committed because it shows unrelated
  browser tabs and a private project name; `screenshots/02-timeline-dark.png` shows the same
  banner on the demo run.

## What it does not confirm

- `error_type` / `error_message` (API failures) were not triggered, so those two names are
  still checked against the documentation only.
- `agent_type` on `agent_stop` was empty in this capture. It is only a label and nothing
  depends on it, but it means either the field is absent from that payload or it has another
  name. Not investigated further.
- Whether plugin hooks reach an already-running session was not tested: the capture came from a
  session started after the install.
