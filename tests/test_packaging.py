import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TestPluginManifest(unittest.TestCase):
    def test_plugin_json_is_valid_and_named(self):
        data = json.loads(read(".claude-plugin", "plugin.json"))
        self.assertEqual(data["name"], "cuelight")
        self.assertIn("description", data)
        self.assertRegex(data["version"], r"^\d+\.\d+\.\d+$")

    def test_marketplace_lists_the_plugin(self):
        data = json.loads(read(".claude-plugin", "marketplace.json"))
        names = [p["name"] for p in data["plugins"]]
        self.assertIn("cuelight", names)


class TestSlashCommand(unittest.TestCase):
    def test_frontmatter_pre_approves_no_tools(self):
        # The directory holds a plugin whose command pre-approves broad shell access.
        text = read("commands", "open.md")
        front = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(front, "command file needs YAML frontmatter")
        self.assertNotIn("allowed-tools", front.group(1))

    def test_each_action_is_one_command_from_the_plugin_root(self):
        text = read("commands", "open.md")
        blocks = re.findall(r"```bash\n(.*?)\n\s*```", text, re.DOTALL)
        self.assertEqual(len(blocks), 3)
        for block in blocks:
            command = block.strip()
            self.assertTrue(command.startswith('python "${CLAUDE_PLUGIN_ROOT}/orchestra/__main__.py" '), command)
            for joiner in ("&&", ";", "|", "cd "):
                self.assertNotIn(joiner, command)

    def test_the_cli_runs_as_a_file_from_any_directory(self):
        # How the slash command starts it: by path, from the user's project.
        import subprocess
        import sys
        import tempfile
        out = subprocess.run([sys.executable, os.path.join(ROOT, "orchestra", "__main__.py"), "--help"],
                             cwd=tempfile.gettempdir(), capture_output=True, encoding="utf-8", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--session", out.stdout)

    def test_documents_all_three_invocations(self):
        text = read("commands", "open.md")
        for fragment in ("--stop", "--report", "CLAUDE_CODE_SESSION_ID"):
            self.assertIn(fragment, text)


class TestReadme(unittest.TestCase):
    def test_states_the_local_only_guarantee(self):
        text = read("README.md").lower()
        self.assertIn("127.0.0.1", text)
        self.assertIn("standard library", text)


if __name__ == "__main__":
    unittest.main()
