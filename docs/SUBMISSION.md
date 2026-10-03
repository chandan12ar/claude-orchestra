# Submitting Cuelight to Anthropic's directory

Status of Cuelight against Anthropic's published plugin requirements, plus ready-to-paste answers for the
submission form. Sources: the [pre-submission checklist](https://claude.com/docs/plugins/pre-submission-checklist),
[Submit your plugin](https://claude.com/docs/plugins/submit) and the
[plugin manifest reference](https://code.claude.com/docs/en/plugins-reference), read on 2026-10-03.
Anthropic's rules change; re-check them before each submission.

## Where to submit

The developer portal at <https://claude.ai/directory/manage> (**Submit new**, then **Plugin bundle**).
The older Claude Console form at `platform.claude.com/plugins/submit` is no longer supported.

You need a paid claude.ai plan (Pro, Max, Team or Enterprise) and your GitHub account connected on claude.ai.
The repository is already public, which the listing requires before it goes live.

## Checklist

`tests/test_directory_readiness.py` checks the rows marked **test** on every run, so they cannot regress.

| Requirement | Status | How it is checked |
|---|---|---|
| `.claude-plugin/plugin.json` at the plugin root, valid | Done | `claude plugin validate --strict` in CI, and **test** |
| `name` is lowercase kebab-case, at most 64 characters, not reserved | Done (`cuelight`) | **test** |
| `description`, `author`, `version` set | Done | **test** |
| `license` set and a `LICENSE` file present | Done (MIT) | **test** |
| README of at least 40 words, outside code blocks | Done | **test** |
| Directory listing fields: `icon`, `documentationUrl`, `supportUrl`, `privacyPolicyUrl` | Done; URLs are `https://` and the icon is a PNG in the plugin | **test** |
| `hooks/hooks.json` valid, with a top-level `hooks` object and only real hook events | Done | **test** |
| Hook commands name each path in full from `${CLAUDE_PLUGIN_ROOT}` | Done | **test** |
| No `npx`, `uvx` or other package launchers, and no package install | Done (standard library only) | **test** |
| No file over 256 KiB (images and fonts aside) | Done | **test** |
| At most 512 files | Done | **test** |
| Only text, PNG, JPEG, GIF, WebP and font files | Done | **test** |
| No system files (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `__MACOSX`) | Done | **test** |
| No symlinks, submodules or Git LFS | Done | **test** |
| No real credentials in any file | Done | `tests/test_redact.py` covers the redactor; review by hand before each release |
| Commit readable source, not minified or packed code | Done | by construction (no build step) |
| Describe in the README everything the plugin runs, sends or fetches | Done | README section "What Cuelight runs and touches" |
| Component files use the exact names Claude Code expects | Done | `claude plugin validate` |

## What to expect from review

- **Held for a reviewer (not a rejection).** Cuelight installs hooks and starts a local server, so a person
  will read it. Everything it does is in the README and PRIVACY.md, and the code is plain Python.
- **Name check.** `cuelight` was checked only informally. The closest name found is Cuelux (stage-lighting
  software). A look-alike can be held for a reviewer; an exact clash blocks. Run a proper trademark search first.
- **Claude Code only.** The directory shows the surfaces a plugin supports. Cuelight needs a local Python and
  hooks, so it is meant for Claude Code. Hooks are ignored in claude.ai chat, and the local server cannot run there.

## Answers for the form

**Repository:** `https://github.com/chandan12ar/cuelight` (plugin path: leave empty; tracked branch: `main`).

**Short description:** Know the moment your Claude Code agents need you: a local, read-only dashboard of subagent runs.

**Example use cases:**
1. You start five subagents and switch to another window. A small pill shows when one is waiting for your
   permission, so you answer it instead of finding out ten minutes later.
2. A long run is slower than expected. The timeline, critical path and cost view show which agent held everything up.
3. Several sessions across different projects are open. The Fleet view lists which one needs you first.

**Data handling:**
- *Does it read or store personal data?* It reads Claude Code transcripts on the user's own machine, which may
  contain whatever the user discussed. It shows them only to the user, in a local browser tab. It stores only
  redacted hook events (7 days) and, if the user opts in, metrics-only run history (90 days).
- *Does it send data to services other than its declared connectors?* No. It makes no outside network
  connections and has no telemetry.
- *How long is data kept?* Hook events 7 days; optional history 90 days; reports until the user deletes them.
- *Intended for people under 18?* No.

## After it is listed

- New versions arrive from the tracked branch: merge to `main`, and the directory scans and publishes it.
- Raise `version` in `.claude-plugin/plugin.json` with every release (see [../CONTRIBUTING.md](../CONTRIBUTING.md)).
- Do not rename the plugin. Use `displayName` for a different label.
