# Contributing to Cuelight

Thanks for helping. Cuelight is small on purpose: Python standard library only, a vanilla JavaScript front end,
no build step and no network access. Please keep it that way.

## Set up

```bash
git clone https://github.com/chandan12ar/cuelight
cd cuelight
python -m unittest discover -s tests -t . -v      # the whole suite; needs Python 3.9+ and, optionally, Node
python -m orchestra --demo                        # a scripted run to look at, no real session needed
```

Node is optional: tests that render the page under Node skip themselves without it. To check the plugin files
the way the directory does, install Claude Code and run
`claude plugin validate --strict .claude-plugin/plugin.json` (and the same for `marketplace.json`).

## Rules that are not negotiable

These are enforced by tests, so a change that breaks one fails CI. The reasons are in [SECURITY.md](SECURITY.md).

- The server binds `127.0.0.1` only and checks `Host`, `Origin` and the token.
- No outside network access from the program or the page (no CDN, fonts, analytics).
- Nothing is ever written under `~/.claude`.
- Everything leaving the process passes through `orchestra/redact.py`.
- Hooks never block or fail Claude Code: async, always exit 0.
- Cuelight observes only. Approving or denying from the dashboard is out of scope for this plugin.

## Making a change

1. Branch from `main` (`feature/...` or `fix/...`).
2. Write the test first when you can, then the change.
3. Run the full suite. Check the page in a real browser if you touched `orchestra/static/`; the stubbed DOM in
   some tests cannot show layout or contrast problems.
4. Update [CHANGELOG.md](CHANGELOG.md) under `Unreleased`.
5. Open a pull request. CI runs Linux, macOS and Windows on Python 3.9, 3.12 and 3.13, and the plugin
   validator. `main` only changes by merged pull request.

## Releasing (maintainers)

Installed copies stay on the `version` in `.claude-plugin/plugin.json` until it changes, so any user-visible
change needs a bump (patch for fixes and look-and-feel, minor for features). Move the `Unreleased` notes under a
dated version heading, merge, then tag `vX.Y.Z` and publish a GitHub Release with the same notes.
Never rename the plugin: installs are recorded under its name. Use `displayName` for a different label.

## Reporting problems

Bugs and ideas: open an issue. Security problems: do not open a public issue; see [SECURITY.md](SECURITY.md).
