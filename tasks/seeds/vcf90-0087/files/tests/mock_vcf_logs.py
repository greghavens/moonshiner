"""Loopback-only VCF Operations for Logs mock pinned to docs/contract.json."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
CONTRACT_OPERATION_IDS = frozenset(CONTRACT["operations"])
EXPECTED_OPERATION_IDS = frozenset({"POST_sessions", "GET_events-+path"})
if CONTRACT_OPERATION_IDS != EXPECTED_OPERATION_IDS:
    raise RuntimeError("mock contract contains an unexpected operation set")
SESSION_TARGET = (
    CONTRACT["serverBasePath"] + CONTRACT["operations"]["POST_sessions"]["path"]
)
EVENTS_PREFIX = (
    CONTRACT["serverBasePath"]
    + CONTRACT["operations"]["GET_events-+path"]["path"].replace("{+path}", "")
)


class _State:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.session_generation = 0
        self.first_session_successes = 0


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: _State) -> None:
        self.state = state
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _record(self, body: bytes) -> None:
        self.server.state.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "accept": self.headers.get("Accept"),
                "authorization": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "content_length": self.headers.get("Content-Length"),
                "body": body,
            }
        )

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, status: int, payload: str) -> None:
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._body()
        self._record(body)
        if self.path != SESSION_TARGET:
            self._json(404, {"errorMessage": "operation is not in the contract"})
            return
        try:
            credentials = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"errorMessage": "invalid JSON"})
            return
        if set(credentials) != {"username", "password", "provider"}:
            self._json(400, {"errorMessage": "invalid session fields"})
            return
        self.server.state.session_generation += 1
        generation = self.server.state.session_generation
        self._json(
            200,
            {
                "userId": "00000000-0000-0000-0000-000000000001",
                "sessionId": f"session-{generation}",
                "ttl": 1,
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._body()
        self._record(body)
        target = urlsplit(self.path)
        prefix = EVENTS_PREFIX
        if not target.path.startswith(prefix) or target.path == prefix:
            self._json(404, {"errorMessage": "operation is not in the contract"})
            return

        generation = self.server.state.session_generation
        supplied = self.headers.get("Authorization")
        if supplied != f"Bearer session-{generation}":
            self._text(401, "Invalid session ID")
            return
        if generation == 1 and self.server.state.first_session_successes == 1:
            self._json(440, "Login Timeout")
            return
        if generation == 1:
            self.server.state.first_session_successes += 1

        decoded_path = unquote(target.path[len(prefix) :])
        segments = decoded_path.split("/")
        constraint = segments[1] if len(segments) >= 2 else ""
        _operator, separator, operand = constraint.partition(" ")
        term = operand if separator and operand else "matching"
        event = {"text": f"{term} event", "timestamp": 1700000000000 + len(term)}
        query = parse_qs(target.query, keep_blank_values=True)
        collection_name = "results" if query.get("view") == ["SIMPLE"] else "events"
        self._json(200, {"complete": True, "duration": 1, collection_name: [event]})


class MockVcfLogs:
    """Context manager exposing an in-process server and its request log."""

    contract_operation_ids = CONTRACT_OPERATION_IDS

    def __init__(self) -> None:
        self._state = _State()
        self._server = _Server(("127.0.0.1", 0), self._state)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return list(self._state.requests)

    def __enter__(self) -> "MockVcfLogs":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
