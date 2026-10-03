import json
import os
import sqlite3
import stat
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from orchestra import constants as C
from orchestra import history as H
from orchestra.history import HistoryStore, metrics_from_summary
from orchestra.http import serve
from orchestra.parent import parse_timestamp
from orchestra.service import OrchestraService
from tests.fixtures import build_session, ts


def summary(sid="s1", **over):
    base = {
        "session_id": sid, "project_path": r"E:\work\api-server",
        "started_at": 1000.0, "ended_at": 1200.0, "session_live": False,
        "totals": {"agents": 3, "completed": 2, "failed": 1, "orphaned": 1,
                   "running": 0, "stalled": 0, "waiting": 0, "wall_time_s": 200.0,
                   "tokens": {"input": 100, "output": 50, "cache_read": 800, "cache_create": 100}},
        "orchestrator": {"tokens": {"output": 50}},
        "cost": {"enabled": True, "currency": "USD", "total": 1.25},
        "write_conflicts": [{"path": "a"}],
        "agents": [{"agent_id": "a1", "loop": None}, {"agent_id": "a2", "loop": {"kind": "repeat"}}],
        "edges": [{"confidence": "exact"}, {"confidence": "exact"}, {"confidence": "inferred"}],
    }
    base.update(over)
    return base


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sub", "history.sqlite")
        self.clock = [10_000.0]
        self.store = HistoryStore(self.path, now_fn=lambda: self.clock[0])


class TestMetrics(unittest.TestCase):
    def test_extracts_the_numbers(self):
        m = metrics_from_summary(summary())
        self.assertEqual((m["agents"], m["completed"], m["wall_s"]), (3, 2, 200.0))
        self.assertEqual(m["failed"], 2)                      # failed + orphaned
        self.assertEqual(m["tokens_total"], 100 + 50 + 800 + 100 + 50)   # incl. orchestrator
        self.assertAlmostEqual(m["cache_hit_ratio"], 800 / 1000)
        self.assertEqual((m["cost"], m["currency"]), (1.25, "USD"))
        self.assertEqual((m["write_conflicts"], m["loops"]), (1, 1))
        self.assertEqual((m["edges_exact"], m["edges_inferred"]), (2, 1))

    def test_project_name_comes_from_the_directory(self):
        self.assertEqual(metrics_from_summary(summary())["project_name"], "api-server")

    def test_no_prices_means_no_cost_not_zero(self):
        m = metrics_from_summary(summary(cost={"enabled": False}))
        self.assertEqual((m["cost"], m["currency"]), (None, None))

    def test_only_metrics_are_kept_never_text_from_the_run(self):
        s = summary()
        s["agents"][0].update(description="SECRET PLAN", brief="SECRET BRIEF",
                              objective="SECRET OBJ", result="SECRET RESULT")
        s["edges"][0]["evidence"] = {"snippet": "SECRET SNIPPET", "path": "/etc/shadow"}
        s["write_conflicts"] = [{"path": "/home/me/secret.txt"}]
        blob = json.dumps(metrics_from_summary(s))
        for leaked in ("SECRET", "shadow", "secret.txt"):
            self.assertNotIn(leaked, blob)

    def test_the_project_path_is_scrubbed(self):
        s = summary(project_path="/work/key=sk-ant-api03-" + "Q" * 40)
        self.assertNotIn("sk-ant-api03", metrics_from_summary(s)["project_path"])

    def test_missing_totals_do_not_throw(self):
        m = metrics_from_summary({"session_id": "x"})
        self.assertEqual((m["agents"], m["tokens_total"], m["cache_hit_ratio"]), (0, 0, None))


class TestRecording(StoreCase):
    def test_a_run_is_recorded_and_listed(self):
        self.assertTrue(self.store.maybe_record(summary()))
        rows = self.store.list()
        self.assertEqual([r["session_id"] for r in rows], ["s1"])
        self.assertEqual(rows[0]["agents"], 3)

    def test_runs_without_agents_are_not_recorded(self):
        self.assertFalse(self.store.maybe_record(summary(agents=[])))
        self.assertEqual(self.store.list(), [])

    def test_writes_are_rate_limited_per_session(self):
        self.assertTrue(self.store.maybe_record(summary()))
        self.clock[0] += 5
        self.assertFalse(self.store.maybe_record(summary()))
        self.clock[0] += H.MIN_INTERVAL_S
        self.assertTrue(self.store.maybe_record(summary()))

    def test_a_change_of_liveness_is_recorded_immediately(self):
        self.store.maybe_record(summary(session_live=True))
        self.clock[0] += 1
        self.assertTrue(self.store.maybe_record(summary(session_live=False)))

    def test_one_row_per_session_and_first_seen_is_kept(self):
        self.store.maybe_record(summary())
        first = self.store.get("s1")["first_seen_at"]
        self.clock[0] += 500
        self.store.maybe_record(summary(totals=dict(summary()["totals"], agents=9)))
        rows = self.store.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agents"], 9)
        self.assertEqual(rows[0]["first_seen_at"], first)
        self.assertGreater(rows[0]["last_seen_at"], first)

    def test_newest_run_is_listed_first(self):
        self.store.maybe_record(summary("old", started_at=1000.0))
        self.store.maybe_record(summary("new", started_at=9000.0))
        self.assertEqual([r["session_id"] for r in self.store.list()], ["new", "old"])

    def test_limit_is_honoured_and_clamped(self):
        for i in range(5):
            self.store.maybe_record(summary("s%d" % i, started_at=1000.0 + i))
        self.assertEqual(len(self.store.list(2)), 2)
        self.assertEqual(len(self.store.list(0)), 1)         # clamped up to 1


class TestRetention(StoreCase):
    def test_old_runs_are_deleted(self):
        self.store.maybe_record(summary("ancient"))
        self.clock[0] += C.HISTORY_DAYS * 86400 + 10
        self.store.maybe_record(summary("fresh"))
        self.assertEqual([r["session_id"] for r in self.store.list()], ["fresh"])

    def test_the_row_count_is_capped_keeping_the_newest(self):
        with mock.patch.object(C, "HISTORY_MAX_RUNS", 3):
            for i in range(6):
                self.clock[0] += 1
                self.store.maybe_record(summary("s%d" % i, started_at=1000.0 + i))
            ids = {r["session_id"] for r in self.store.list()}
        self.assertEqual(ids, {"s3", "s4", "s5"})


class TestCompare(StoreCase):
    def setUp(self):
        super().setUp()
        self.store.maybe_record(summary("a", totals=dict(
            summary()["totals"], agents=4, wall_time_s=100.0)))
        self.store.maybe_record(summary("b", totals=dict(
            summary()["totals"], agents=6, wall_time_s=150.0)))

    def test_reports_b_relative_to_a(self):
        d = self.store.compare("a", "b")["delta"]
        self.assertEqual((d["agents"]["a"], d["agents"]["b"], d["agents"]["change"]), (4, 6, 2))
        self.assertAlmostEqual(d["agents"]["pct"], 50.0)
        self.assertAlmostEqual(d["wall_s"]["pct"], 50.0)

    def test_percent_is_none_when_the_baseline_is_zero(self):
        self.store.maybe_record(summary("z", totals=dict(summary()["totals"], agents=0),
                                        agents=[{"agent_id": "x"}]))
        d = self.store.compare("z", "b")["delta"]
        self.assertIsNone(d["agents"]["pct"])
        self.assertEqual(d["agents"]["change"], 6)

    def test_an_unknown_metric_is_none_not_zero(self):
        self.store.maybe_record(summary("n", cost={"enabled": False}))
        self.assertIsNone(self.store.compare("a", "n")["delta"]["cost"])

    def test_unknown_run_is_none(self):
        self.assertIsNone(self.store.compare("a", "nope"))


class TestSafety(StoreCase):
    def test_the_database_is_private_to_the_user(self):
        self.store.maybe_record(summary())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode) & 0o077, 0)
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode) & 0o077, 0)

    def test_the_schema_has_no_text_columns_for_run_content(self):
        names = set(H.NAMES)
        for banned in ("description", "brief", "result", "objective", "prompt", "files", "path"):
            self.assertFalse(any(n == banned for n in names), banned)
        # the only free-text columns are the project directory and its name
        text_cols = {n for n, t in H.COLUMNS if t.startswith("TEXT")}
        self.assertEqual(text_cols, {"session_id", "project_path", "project_name", "currency"})

    def test_a_corrupt_database_is_reported_and_never_raises(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as fh:
            fh.write(b"this is not a sqlite database" * 50)
        self.assertFalse(self.store.maybe_record(summary()))
        self.assertIn("history unavailable", self.store.error)
        self.assertEqual(self.store.list(), [])

    def test_an_unwritable_location_is_reported_and_never_raises(self):
        blocker = os.path.join(self.dir, "file")
        open(blocker, "w").close()
        store = HistoryStore(os.path.join(blocker, "x", "h.sqlite"))
        self.assertFalse(store.maybe_record(summary()))
        self.assertIn("history unavailable", store.error)

    def test_it_recovers_when_the_problem_goes_away(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as fh:
            fh.write(b"garbage" * 100)
        self.store.maybe_record(summary())
        self.assertTrue(self.store.error)
        os.remove(self.path)
        self.store._ready = False
        self.clock[0] += 100
        self.assertTrue(self.store.maybe_record(summary()))
        self.assertEqual(self.store.error, "")

    def test_sql_in_a_value_is_just_data(self):
        evil = "x'); DROP TABLE runs; --"
        self.store.maybe_record(summary(evil, project_path=evil))
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.get(evil)["session_id"], evil)

    def test_history_is_off_unless_asked_for(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORCHESTRA_HISTORY", None)
            self.assertFalse(H.enabled())
        for value in ("on", "1", "TRUE", "yes"):
            with mock.patch.dict(os.environ, {"ORCHESTRA_HISTORY": value}):
                self.assertTrue(H.enabled(), value)
        for value in ("off", "0", "", "maybe"):
            with mock.patch.dict(os.environ, {"ORCHESTRA_HISTORY": value}):
                self.assertFalse(H.enabled(), value)

    def test_default_location_is_not_under_dot_claude_or_the_temp_dir(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORCHESTRA_HISTORY_DB", None)
            path = H.default_path()
        self.assertNotIn(os.sep + ".claude" + os.sep, path)
        self.assertNotIn(tempfile.gettempdir(), path)


class TestServiceAndApi(unittest.TestCase):
    TOKEN = "tok-hist"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        build_session(self.root, "s1")
        build_session(self.root, "s2")
        self.store = HistoryStore(os.path.join(tempfile.mkdtemp(), "h.sqlite"))

    def service(self, history="store"):
        return OrchestraService(
            root=self.root, token=self.TOKEN, default_session="s1",
            now_fn=lambda: parse_timestamp(ts(150)),
            history=self.store if history == "store" else None)

    def test_viewing_a_session_records_it(self):
        self.service().run_summary("s1")
        self.assertEqual([r["session_id"] for r in self.store.list()], ["s1"])

    def test_the_fleet_scan_records_the_sessions_it_builds(self):
        service = self.service()
        for sid in ("s1", "s2"):
            os.utime(os.path.join(self.root, "projects", "E--proj", sid + ".jsonl"),
                     (parse_timestamp(ts(150)),) * 2)
        service.fleet()
        self.assertEqual({r["session_id"] for r in self.store.list()}, {"s1", "s2"})

    def test_without_a_store_nothing_is_written_and_the_api_says_so(self):
        service = self.service(history=None)
        service.run_summary("s1")
        self.assertEqual(service.history_list(), {"enabled": False, "runs": [], "error": ""})
        self.assertIsNone(service.history_compare("s1", "s2"))

    def get(self, base, path):
        return urllib.request.urlopen(base + path + ("&" if "?" in path else "?") +
                                      "k=" + self.TOKEN, timeout=5)

    def test_endpoints(self):
        service = self.service()
        service.run_summary("s1")
        service.run_summary("s2")
        server, _ = serve(service, port=0)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_port
        data = json.loads(self.get(base, "/api/history").read())
        self.assertTrue(data["enabled"])
        self.assertEqual({r["session_id"] for r in data["runs"]}, {"s1", "s2"})
        cmp_ = json.loads(self.get(base, "/api/history/compare?a=s1&b=s2").read())
        self.assertEqual(cmp_["delta"]["agents"]["change"], 0)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(base, "/api/history/compare?a=s1&b=ghost")
        self.assertEqual(ctx.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(base + "/api/history", timeout=5)
        self.assertEqual(ctx.exception.code, 403)

    def test_a_non_numeric_limit_falls_back_instead_of_erroring(self):
        service = self.service()
        service.run_summary("s1")
        server, _ = serve(service, port=0)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_port
        self.assertEqual(self.get(base, "/api/history?limit=abc").status, 200)


if __name__ == "__main__":
    unittest.main()


class TestRenamedDataDirectory(unittest.TestCase):
    """History recorded before the 0.4.0 rename (as "Workflow") keeps being used."""

    def path_with(self, base, new=False, legacy=False):
        for flag, name in ((new, "cuelight"), (legacy, "workflow")):
            if flag:
                os.makedirs(os.path.join(base, name), exist_ok=True)
                open(os.path.join(base, name, "history.sqlite"), "wb").close()
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": base, "XDG_DATA_HOME": base}):
            os.environ.pop("ORCHESTRA_HISTORY_DB", None)
            return H.default_path()

    def test_a_new_install_uses_the_new_name(self):
        path = self.path_with(tempfile.mkdtemp())
        self.assertEqual(os.path.basename(os.path.dirname(path)), "cuelight")

    def test_existing_history_under_the_old_name_is_kept(self):
        path = self.path_with(tempfile.mkdtemp(), legacy=True)
        self.assertEqual(os.path.basename(os.path.dirname(path)), "workflow")

    def test_the_new_location_wins_once_it_exists(self):
        path = self.path_with(tempfile.mkdtemp(), new=True, legacy=True)
        self.assertEqual(os.path.basename(os.path.dirname(path)), "cuelight")
