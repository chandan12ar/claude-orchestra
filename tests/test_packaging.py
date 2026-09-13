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
        self.assertEqual(data["name"], "orchestra")
        self.assertIn("description", data)
        self.assertRegex(data["version"], r"^\d+\.\d+\.\d+$")

    def test_marketplace_lists_the_plugin(self):
        data = json.loads(read(".claude-plugin", "marketplace.json"))
        names = [p["name"] for p in data["plugins"]]
        self.assertIn("orchestra", names)


class TestSlashCommand(unittest.TestCase):
    def test_frontmatter_limits_tools_to_bash(self):
        text = read("commands", "orchestra.md")
        front = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(front, "command file needs YAML frontmatter")
        self.assertIn("allowed-tools: Bash", front.group(1))

    def test_documents_all_three_invocations(self):
        text = read("commands", "orchestra.md")
        for fragment in ("--stop", "--report", "CLAUDE_CODE_SESSION_ID"):
            self.assertIn(fragment, text)


class TestReadme(unittest.TestCase):
    def test_states_the_local_only_guarantee(self):
        text = read("README.md").lower()
        self.assertIn("127.0.0.1", text)
        self.assertIn("standard library", text)


if __name__ == "__main__":
    unittest.main()
