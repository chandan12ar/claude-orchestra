import os
import tempfile
import unittest

from orchestra.locate import (SessionPaths, encode_project_dir, find_session,
                              list_sessions)


class TestEncodeProjectDir(unittest.TestCase):
    def test_windows_path(self):
        self.assertEqual(encode_project_dir(r"E:\god_ai\claude-SA"),
                         "E--god-ai-claude-SA")

    def test_windows_user_path(self):
        self.assertEqual(encode_project_dir(r"C:\Users\Chandan"), "C--Users-Chandan")

    def test_posix_path(self):
        self.assertEqual(encode_project_dir("/home/dev/my_app"), "-home-dev-my-app")

    def test_existing_dashes_survive(self):
        self.assertEqual(encode_project_dir(r"E:\project-boss"), "E--project-boss")


class LocateTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.projects = os.path.join(self.root, "projects")
        os.makedirs(self.projects)

    def make_session(self, project, session_id, agents=0):
        pdir = os.path.join(self.projects, project)
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, session_id + ".jsonl"), "w") as fh:
            fh.write('{"type": "user"}\n')
        if agents:
            sub = os.path.join(pdir, session_id, "subagents")
            os.makedirs(sub, exist_ok=True)
            for i in range(agents):
                base = os.path.join(sub, "agent-a{}".format(i))
                open(base + ".jsonl", "w").close()
                open(base + ".meta.json", "w").close()
        return pdir


class TestFindSession(LocateTestCase):
    def test_finds_session_in_any_project(self):
        self.make_session("E--god-ai-claude-SA", "sess-1", agents=2)
        found = find_session("sess-1", root=self.root)
        self.assertIsInstance(found, SessionPaths)
        self.assertTrue(found.session_jsonl.endswith("sess-1.jsonl"))
        self.assertTrue(found.subagents_dir.endswith(os.path.join("sess-1", "subagents")))
        self.assertTrue(os.path.isdir(found.subagents_dir))

    def test_missing_session_returns_none(self):
        self.make_session("E--p", "sess-1")
        self.assertIsNone(find_session("nope", root=self.root))

    def test_session_without_subagents_dir_still_resolves(self):
        self.make_session("E--p", "sess-2", agents=0)
        found = find_session("sess-2", root=self.root)
        self.assertIsNotNone(found)
        self.assertFalse(os.path.isdir(found.subagents_dir))

    def test_missing_root_returns_none(self):
        self.assertIsNone(find_session("sess-1", root=os.path.join(self.root, "gone")))


class TestListSessions(LocateTestCase):
    def test_lists_newest_first_with_agent_counts(self):
        pdir = self.make_session("E--p", "old", agents=1)
        self.make_session("E--p", "new", agents=3)
        os.utime(os.path.join(pdir, "old.jsonl"), (1000, 1000))
        sessions = list_sessions(pdir)
        self.assertEqual([s.session_id for s in sessions], ["new", "old"])
        self.assertEqual(sessions[0].agent_count, 3)
        self.assertEqual(sessions[1].agent_count, 1)

    def test_missing_dir_returns_empty(self):
        self.assertEqual(list_sessions(os.path.join(self.root, "gone")), [])


if __name__ == "__main__":
    unittest.main()
