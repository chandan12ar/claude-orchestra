"""The local server. Loopback only, token-gated, no egress."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from orchestra import constants as C
from orchestra.service import NotFound, OrchestraService

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                  ".js": "text/javascript; charset=utf-8",
                  ".css": "text/css; charset=utf-8"}

_LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


def _host_is_loopback(header: Optional[str]) -> bool:
    if not header:
        return False
    host = header.rsplit(":", 1)[0] if header.count(":") == 1 else header
    return host.strip("[]") in [h.strip("[]") for h in _LOOPBACK]


def _origin_is_allowed(header: Optional[str]) -> bool:
    """A cross-site page must not be able to read the dashboard's JSON.

    No Origin at all is fine: that is a same-origin navigation or a direct
    fetch. An Origin naming somewhere other than loopback is a cross-site
    read attempt and is refused.
    """
    if not header or header == "null":
        return True
    return _host_is_loopback(urlparse(header).netloc)


def make_handler(service: OrchestraService, state: Dict[str, float]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Orchestra"

        def log_message(self, fmt, *args):  # silence the default stderr spam
            pass

        # -- helpers ----------------------------------------------------
        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Dict[str, Any]) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, code: int, message: str) -> None:
            self._json(code, {"error": message})

        def _authorized(self, query: Dict[str, list]) -> bool:
            supplied = (query.get("k", [""])[0]
                        or self.headers.get("X-Orchestra-Token", ""))
            return bool(service.token) and supplied == service.token

        # -- routing ----------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            state["last_request"] = time.time()
            if not _host_is_loopback(self.headers.get("Host")):
                self._error(403, "non-loopback host")
                return
            if not _origin_is_allowed(self.headers.get("Origin")):
                self._error(403, "cross-site origin")
                return
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path.startswith("/api/"):
                self._api(path, query)
            else:
                self._static(path)

        def _api(self, path: str, query: Dict[str, list]) -> None:
            if path == "/api/health":
                self._json(200, {"ok": True})
                return
            if not self._authorized(query):
                self._error(403, "bad or missing token")
                return
            session = query.get("session", [""])[0]
            try:
                if path == "/api/run":
                    self._json(200, service.run_summary(session))
                elif path.startswith("/api/agent/"):
                    agent_id = path[len("/api/agent/"):]
                    self._json(200, service.agent_detail(agent_id, session))
                elif path == "/api/sessions":
                    self._json(200, service.session_list(session))
                else:
                    self._error(404, "no such route")
            except NotFound as exc:
                self._error(404, str(exc))

        def _static(self, path: str) -> None:
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = os.path.normpath(os.path.join(STATIC_DIR, name))
            if not target.startswith(STATIC_DIR + os.sep) or not os.path.isfile(target):
                self._error(404, "not found")
                return
            extension = os.path.splitext(target)[1]
            if extension not in _CONTENT_TYPES:
                self._error(403, "unsupported asset")
                return
            with open(target, "rb") as fh:
                self._send(200, fh.read(), _CONTENT_TYPES[extension])

    return Handler


def make_server(service: OrchestraService, port: int) -> ThreadingHTTPServer:
    state = {"last_request": time.time()}
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service, state))
    server.orchestra_state = state  # type: ignore[attr-defined]
    return server


def serve(service: OrchestraService, port: int = C.DEFAULT_PORT
          ) -> Tuple[ThreadingHTTPServer, threading.Thread]:
    """Start on a background thread. Returns (server, thread)."""
    server = make_server(service, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def start_idle_watchdog(server: ThreadingHTTPServer,
                        idle_seconds: int = C.IDLE_SHUTDOWN_S) -> threading.Thread:
    """Shut down after a long silence so no server is left running for days."""
    state = server.orchestra_state  # type: ignore[attr-defined]

    def watch() -> None:
        while True:
            time.sleep(30)
            if time.time() - state["last_request"] > idle_seconds:
                server.shutdown()
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread
