import http.client
import os
import tempfile
import time
import unittest
from unittest import mock

from orchestra import http as H
from orchestra.events import Event, EventSpool
from orchestra.parent import parse_timestamp
from orchestra.service import NotFound, OrchestraService
from tests.fixtures import build_session, ts

TOKEN = "tok-stream"


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.paths = build_session(self.root, "s1")
        self.spool_dir = os.path.join(tempfile.mkdtemp(), "events")
        self.service = OrchestraService(
            root=self.root, token=TOKEN, default_session="s1",
            now_fn=lambda: parse_timestamp(ts(150)),
            spool_factory=lambda: EventSpool(self.spool_dir))


class TestChangeToken(ServiceCase):
    def test_is_stable_when_nothing_changes(self):
        self.assertEqual(self.service.change_token("s1"),
                         self.service.change_token("s1"))

    def test_changes_when_the_main_transcript_grows(self):
        before = self.service.change_token("s1")
        with open(self.paths.session_jsonl, "a") as fh:
            fh.write("\n")
        self.assertNotEqual(self.service.change_token("s1"), before)

    def test_changes_when_a_subagent_transcript_grows(self):
        before = self.service.change_token("s1")
        with open(os.path.join(self.paths.subagents_dir, "agent-a3.jsonl"), "a") as fh:
            fh.write("\n")
        self.assertNotEqual(self.service.change_token("s1"), before)

    def test_changes_when_a_new_agent_appears(self):
        before = self.service.change_token("s1")
        open(os.path.join(self.paths.subagents_dir, "agent-zz.jsonl"), "w").close()
        self.assertNotEqual(self.service.change_token("s1"), before)

    def test_changes_when_a_hook_event_arrives(self):
        before = self.service.change_token("s1")
        EventSpool(self.spool_dir).append(Event(kind="turn_end", session_id="s1", ts=1.0))
        self.assertNotEqual(self.service.change_token("s1"), before)

    def test_ignores_other_sessions_events(self):
        before = self.service.change_token("s1")
        EventSpool(self.spool_dir).append(Event(kind="turn_end", session_id="other", ts=1.0))
        self.assertEqual(self.service.change_token("s1"), before)

    def test_unknown_session_is_not_found(self):
        with self.assertRaises(NotFound):
            self.service.change_token("nope")


class StreamCase(ServiceCase):
    def setUp(self):
        super().setUp()
        for name, value in (("STREAM_POLL_S", 0.05), ("STREAM_HEARTBEAT_S", 0.3)):
            patcher = mock.patch.object(H, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server, _ = H.serve(self.service, port=0)
        self.addCleanup(self.server.shutdown)
        self.conns = []
        self.addCleanup(lambda: [c.close() for c in self.conns])

    def open(self, path="/api/stream?session=s1&k=" + TOKEN, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port,
                                          timeout=5)
        self.conns.append(conn)
        conn.request("GET", path, headers=headers or {})
        return conn.getresponse()

    def read_event(self, resp):
        """Next (event, data) pair, or ('keep-alive', '') for a comment."""
        event, data = None, ""
        while True:
            line = resp.readline().decode("utf-8")
            if line.startswith(":"):
                return "keep-alive", ""
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
            elif line in ("\n", "\r\n") and event:
                return event, data


class TestStream(StreamCase):
    def test_streams_as_event_stream_and_says_hello(self):
        resp = self.open()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.getheader("Content-Type"))
        self.assertEqual(self.read_event(resp)[0], "hello")

    def test_a_transcript_write_produces_a_tick_quickly(self):
        resp = self.open()
        self.read_event(resp)
        with open(self.paths.session_jsonl, "a") as fh:
            fh.write("\n")
        started = time.time()
        self.assertEqual(self.read_event(resp)[0], "tick")
        self.assertLess(time.time() - started, 2.0)

    def test_a_hook_event_produces_a_tick(self):
        resp = self.open()
        self.read_event(resp)
        EventSpool(self.spool_dir).append(
            Event(kind="notification", session_id="s1", ts=1.0))
        self.assertEqual(self.read_event(resp)[0], "tick")

    def test_a_quiet_session_sends_keep_alives_not_ticks(self):
        resp = self.open()
        self.read_event(resp)
        self.assertEqual(self.read_event(resp)[0], "keep-alive")

    def test_the_stream_carries_no_run_data(self):
        resp = self.open()
        _, hello = self.read_event(resp)
        with open(self.paths.session_jsonl, "a") as fh:
            fh.write("\n")
        _, tick = self.read_event(resp)
        for payload in (hello, tick):
            for leaked in ("agent", "brief", "result", "Plan the work", "PLAN.md"):
                self.assertNotIn(leaked, payload)

    def test_requires_the_token(self):
        self.assertEqual(self.open("/api/stream?session=s1").status, 403)
        self.assertEqual(self.open("/api/stream?session=s1&k=wrong").status, 403)

    def test_refuses_a_foreign_host_and_a_cross_site_origin(self):
        self.assertEqual(self.open(headers={"Host": "evil.example.com"}).status, 403)
        self.assertEqual(
            self.open(headers={"Origin": "https://evil.example.com"}).status, 403)

    def test_unknown_session_is_a_404_not_an_endless_stream(self):
        self.assertEqual(self.open("/api/stream?session=nope&k=" + TOKEN).status, 404)

    def test_stream_count_is_capped_and_released(self):
        with mock.patch.object(H, "MAX_STREAMS", 1):
            first = self.open()
            self.read_event(first)
            self.assertEqual(self.open().status, 429)
            first.close()
            self.conns[0].close()
            deadline, status = time.time() + 3, None
            while time.time() < deadline:
                resp = self.open()
                status = resp.status
                if status == 200:
                    break
                time.sleep(0.1)
            self.assertEqual(status, 200)


class TestStreamIsStaticallyWired(unittest.TestCase):
    def test_client_uses_event_source_with_a_polling_fallback(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "orchestra", "static", "app.js")
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("new EventSource(", js)
        self.assertIn('typeof EventSource === "undefined"', js)
        self.assertIn("STREAM_POLL_MS", js)
        # Every restart of the loop (session switch, Live toggle, page load)
        # must also restart the stream, or the stream follows a stale session.
        self.assertEqual(js.count("startStream();"), 3)
        self.assertIn("stopStream();", js)


if __name__ == "__main__":
    unittest.main()
