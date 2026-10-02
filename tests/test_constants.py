import importlib
import os
import unittest
from unittest import mock

from orchestra import constants


class TestTunables(unittest.TestCase):
    def tearDown(self):
        importlib.reload(constants)

    def _load(self, **env):
        with mock.patch.dict(os.environ, env, clear=False):
            return importlib.reload(constants)

    def test_defaults_are_unchanged_when_nothing_is_set(self):
        for key in list(os.environ):
            if key.startswith("ORCHESTRA_") and key != "ORCHESTRA_STATE_DIR":
                os.environ.pop(key)
        c = importlib.reload(constants)
        self.assertEqual((c.STALL_THRESHOLD_S, c.SESSION_LIVE_THRESHOLD_S,
                          c.HUB_FILE_THRESHOLD, c.HANDOFF_CONTAINMENT,
                          c.HANDOFF_RUN_WORDS, c.SHINGLE_SIZE,
                          c.IDLE_SHUTDOWN_S, c.DEFAULT_PORT, c.MAX_BUILDERS),
                         (300, 600, 3, 0.15, 40, 8, 1800, 7717, 8))

    def test_environment_overrides_a_threshold(self):
        c = self._load(ORCHESTRA_STALL_SECONDS="45",
                       ORCHESTRA_HANDOFF_CONTAINMENT="0.4")
        self.assertEqual(c.STALL_THRESHOLD_S, 45)
        self.assertEqual(c.HANDOFF_CONTAINMENT, 0.4)

    def test_garbage_falls_back_to_the_default(self):
        c = self._load(ORCHESTRA_STALL_SECONDS="soon", ORCHESTRA_PORT="  ")
        self.assertEqual(c.STALL_THRESHOLD_S, 300)
        self.assertEqual(c.DEFAULT_PORT, 7717)

    def test_out_of_range_falls_back_to_the_default(self):
        # A 0-second stall threshold would mark every agent stalled instantly.
        c = self._load(ORCHESTRA_STALL_SECONDS="0",
                       ORCHESTRA_HANDOFF_CONTAINMENT="7",
                       ORCHESTRA_PORT="80")
        self.assertEqual(c.STALL_THRESHOLD_S, 300)
        self.assertEqual(c.HANDOFF_CONTAINMENT, 0.15)
        self.assertEqual(c.DEFAULT_PORT, 7717)

    def test_every_tunable_is_documented_in_the_readme(self):
        readme = open(os.path.join(os.path.dirname(__file__), "..", "README.md"),
                      encoding="utf-8").read()
        for env, _, _, _ in constants.TUNABLES.values():
            self.assertIn(env, readme)


if __name__ == "__main__":
    unittest.main()
