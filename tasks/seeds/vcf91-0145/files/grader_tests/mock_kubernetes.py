"""Loopback Kubernetes API fixture for pod, Event, and log evidence."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class MockKubernetesState:
    pods: list[dict[str, Any]]
    events: list[dict[str, Any]]
    container_log: str


class MockKubernetes:
    """Context manager for a loopback-only core V1 Kubernetes surface."""

    def __init__(self, state: MockKubernetesState, request_log: Path):
        self._state = state
        self.request_log = request_log
        self._log_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "MockKubernetes":
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "KubernetesEvidenceFixture/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                return

            def _read_body(self) -> bytes:
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError:
                    length = 0
                return self.rfile.read(length) if length else b""

            def _record(self, body: bytes) -> None:
                record = {
                    "method": self.command,
                    "raw_path": self.path,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                    "body": body.decode("utf-8"),
                }
                encoded = json.dumps(
                    record, sort_keys=True, separators=(",", ":")
                )
                with fixture._log_lock:
                    with fixture.request_log.open("a", encoding="utf-8") as stream:
                        stream.write(encoded + "\n")

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            def _json_response(self, status: int, value: Any) -> None:
                payload = json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                self._send(status, payload, "application/json")

            def do_GET(self) -> None:
                body = self._read_body()
                self._record(body)
                target = urlsplit(self.path)
                pods_match = re.fullmatch(
                    r"/api/v1/namespaces/[^/]+/pods",
                    target.path,
                )
                events_match = re.fullmatch(
                    r"/api/v1/namespaces/[^/]+/events",
                    target.path,
                )
                log_match = re.fullmatch(
                    r"/api/v1/namespaces/[^/]+/pods/[^/]+/log",
                    target.path,
                )
                if pods_match:
                    self._json_response(
                        200,
                        {"apiVersion": "v1", "kind": "PodList", "items": fixture._state.pods},
                    )
                elif events_match:
                    self._json_response(
                        200,
                        {
                            "apiVersion": "v1",
                            "kind": "EventList",
                            "items": fixture._state.events,
                        },
                    )
                elif log_match:
                    query = parse_qs(target.query, keep_blank_values=True)
                    if "container" not in query and len(fixture._state.pods) == 1:
                        # Direct client checks may intentionally omit every option.
                        pass
                    payload = fixture._state.container_log.encode("utf-8")
                    self._send(200, payload, "text/plain; charset=utf-8")
                else:
                    self._json_response(
                        404,
                        {
                            "kind": "Status",
                            "status": "Failure",
                            "reason": "NotFound",
                        },
                    )

        self.request_log.write_text("", encoding="utf-8")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mock-kubernetes",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def read_request_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
