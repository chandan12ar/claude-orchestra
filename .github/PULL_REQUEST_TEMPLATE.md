## What and why

<!-- One or two sentences. -->

## Checks

- [ ] `python -m unittest discover -s tests -t .` passes
- [ ] Checked in a real browser if `orchestra/static/` changed
- [ ] `CHANGELOG.md` updated under `Unreleased`
- [ ] `version` bumped in `.claude-plugin/plugin.json` if users should receive this
- [ ] No new network access, nothing written under `~/.claude`, output still passes through `redact.py`
