"""Anthropic's plugin-directory checklist, as tests, so a later commit cannot quietly break it.

Source: https://claude.com/docs/plugins/pre-submission-checklist (read 2026-10-03). The portal's own
validation is the authority; these tests catch the mechanical rows early. docs/SUBMISSION.md lists
which rows are covered here.
"""

import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hook events Claude Code documents; the directory rejects a hooks.json that names anything else.
KNOWN_HOOK_EVENTS = {
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "PermissionRequest", "PermissionDenied", "Notification", "SubagentStart", "SubagentStop", "Stop",
    "StopFailure", "TeammateIdle", "TaskCreated", "TaskCompleted", "PreCompact", "PostCompact",
    "ConfigChange", "CwdChanged", "FileChanged", "WorktreeCreate", "WorktreeRemove", "Elicitation",
    "ElicitationResult", "InstructionsLoaded", "Setup",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
FONT_EXT = {".woff", ".woff2", ".ttf", ".otf"}
SYSTEM_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
LAUNCHERS = re.compile(r"\b(npx|bunx|pnpm\s+dlx|yarn\s+dlx|uvx|pipx\s+run|uv\s+run|pip\s+install|npm\s+install)\b")


def load_json(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return json.load(fh)


def tracked_files():
    """Files the plugin ships: what git tracks, else a walk that skips junk."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, timeout=30,
                             check=True).stdout.decode("utf-8")
        names = sorted({n for n in out.split("\0") if n})   # a set: a file mid-merge is listed once per stage
        if names:
            return [n for n in names if os.path.isfile(os.path.join(ROOT, n))]
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for name in files:
            found.append(os.path.relpath(os.path.join(base, name), ROOT).replace(os.sep, "/"))
    return found


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.plugin = load_json(".claude-plugin", "plugin.json")

    def test_name_is_lowercase_kebab_case_and_not_reserved(self):
        name = self.plugin["name"]
        self.assertRegex(name, r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
        self.assertLessEqual(len(name), 64)
        self.assertNotRegex(name, r"^(claude|anthropic|anthropics|cc-plugin)[-_]|^(claude|anthropic|official|plugin|mcp|test)$")

    def test_entry_name_in_the_marketplace_matches_the_manifest(self):
        market = load_json(".claude-plugin", "marketplace.json")
        self.assertIn(self.plugin["name"], [p["name"] for p in market["plugins"]])

    def test_required_metadata_is_present(self):
        for key in ("description", "version", "license", "homepage", "repository"):
            self.assertTrue(self.plugin.get(key), key)
        self.assertTrue(self.plugin["author"].get("name"))

    def test_license_file_exists_and_matches(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "LICENSE")))
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            self.assertIn("MIT License", fh.read())
        self.assertEqual(self.plugin["license"], "MIT")

    def test_directory_listing_urls_are_https(self):
        for key in ("documentationUrl", "supportUrl", "privacyPolicyUrl"):
            self.assertTrue(self.plugin.get(key, "").startswith("https://"), key)

    def test_icon_is_a_real_png_inside_the_plugin(self):
        icon = self.plugin["icon"]
        self.assertTrue(icon.startswith("./") and ".." not in icon)
        with open(os.path.join(ROOT, icon), "rb") as fh:
            self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_privacy_policy_url_points_at_a_file_in_this_repository(self):
        url = self.plugin["privacyPolicyUrl"]
        self.assertTrue(url.endswith("/PRIVACY.md"))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "PRIVACY.md")))

    def test_no_claude_md_at_the_plugin_root(self):
        # `claude plugin validate --strict` fails on it; the project's own copy lives in .claude/.
        self.assertFalse(os.path.exists(os.path.join(ROOT, "CLAUDE.md")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".claude", "CLAUDE.md")))


class TestReadme(unittest.TestCase):
    def test_readme_has_at_least_forty_words_outside_code_blocks(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            text = re.sub(r"```.*?```", "", fh.read(), flags=re.S)
        self.assertGreaterEqual(len(re.findall(r"\w+", text)), 40)

    def test_readme_discloses_what_the_plugin_runs_and_touches(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("## What Cuelight runs and touches", readme)
        for word in ("Network", "Files it writes", "Programs it runs"):
            self.assertIn(word, readme)


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.hooks = load_json("hooks", "hooks.json")

    def test_top_level_hooks_object_and_known_events_only(self):
        self.assertIsInstance(self.hooks.get("hooks"), dict)
        self.assertLessEqual(set(self.hooks["hooks"]), KNOWN_HOOK_EVENTS)

    def test_hooks_json_is_not_also_declared_in_the_manifest(self):
        self.assertNotIn("hooks", load_json(".claude-plugin", "plugin.json"))

    def commands(self):
        for groups in self.hooks["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    yield hook["command"]

    def test_every_hook_is_async_and_never_fails_the_session(self):
        for groups in self.hooks["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertTrue(hook.get("async"))
                    self.assertTrue(hook["command"].rstrip().endswith("|| true"))

    def test_paths_are_written_in_full_from_the_plugin_root(self):
        for command in self.commands():
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/", command)
            self.assertNotIn("$(", command)
            self.assertNotRegex(command, r"python3?\s+-c")

    def test_no_package_launchers_or_installs(self):
        for command in self.commands():
            self.assertNotRegex(command, LAUNCHERS)
        with open(os.path.join(ROOT, "commands", "open.md"), encoding="utf-8") as fh:
            self.assertNotRegex(fh.read(), LAUNCHERS)


class TestFilesShipped(unittest.TestCase):
    def setUp(self):
        self.files = tracked_files()

    def test_at_most_512_files(self):
        self.assertLessEqual(len(self.files), 512)

    def test_no_non_image_file_over_256_kib(self):
        for name in self.files:
            ext = os.path.splitext(name)[1].lower()
            if ext in IMAGE_EXT or ext in FONT_EXT:
                continue
            self.assertLess(os.path.getsize(os.path.join(ROOT, name)), 256 * 1024, name)

    def test_only_text_images_and_fonts(self):
        for name in self.files:
            ext = os.path.splitext(name)[1].lower()
            if ext in IMAGE_EXT or ext in FONT_EXT:
                continue
            with open(os.path.join(ROOT, name), "rb") as fh:
                chunk = fh.read(8192)
            self.assertNotIn(b"\0", chunk, name + " looks binary")

    def test_images_are_complete_files(self):
        magic = {".png": b"\x89PNG\r\n\x1a\n", ".gif": b"GIF8", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8"}
        for name in self.files:
            ext = os.path.splitext(name)[1].lower()
            if ext in magic:
                with open(os.path.join(ROOT, name), "rb") as fh:
                    self.assertTrue(fh.read(8).startswith(magic[ext]), name)

    def test_no_system_files(self):
        for name in self.files:
            parts = [p.lower() for p in name.split("/")]
            self.assertNotIn(parts[-1], SYSTEM_FILES, name)
            self.assertNotIn("__macosx", parts, name)

    def test_names_are_valid_on_windows_and_macos(self):
        reserved = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}
        seen = {}
        for name in self.files:
            for part in name.split("/"):
                self.assertNotRegex(part, r"[:<>\"|?*]", name)
                self.assertFalse(part.endswith((".", " ")), name)
                self.assertNotIn(part.split(".")[0].lower(), reserved, name)
            self.assertNotIn(name.lower(), seen, "differs only by case from " + seen.get(name.lower(), ""))
            seen[name.lower()] = name

    def test_no_symlinks_submodules_or_lfs(self):
        for name in self.files:
            self.assertFalse(os.path.islink(os.path.join(ROOT, name)), name)
        self.assertFalse(os.path.exists(os.path.join(ROOT, ".gitmodules")))
        attrs = os.path.join(ROOT, ".gitattributes")
        if os.path.exists(attrs):
            with open(attrs, encoding="utf-8") as fh:
                text = fh.read()
            for bad in ("filter=", "export-ignore", "export-subst"):
                self.assertNotIn(bad, text)


if __name__ == "__main__":
    unittest.main()
