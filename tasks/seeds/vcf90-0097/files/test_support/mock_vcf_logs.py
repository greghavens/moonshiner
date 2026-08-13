#!/usr/bin/env python3
"""Loopback-only mock for the selected VCF Operations for Logs 9.0 operations."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit


USER_ID = "12345678-1234-1234-1234-123456789abc"


class MockState:
    def __init__(self, contract_path: Path, request_log: Path) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            (operation["method"], operation["path"]): operation["operationId"]
            for operation in contract["operations"]
        }
        expected = {
            ("POST", "/sessions"): "POST_sessions",
            ("GET", "/events/{+path}"): "GET_events-+path",
        }
        if operations != expected:
            raise ValueError(f"mock contract operations changed: {operations!r}")
        if contract["serverBasePath"] != "/api/v2":
            raise ValueError("mock requires the 9.0 /api/v2 base path")

        self.base_path = contract["serverBasePath"]
        self.request_log = request_log
        self.request_log.write_text("", encoding="utf-8")
        self.lock = threading.Lock()
        self.sequence = 0
        self.sessions_issued = 0

    def append_request(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        split = urlsplit(handler.path)
        record = {
            "sequence": 0,
            "method": handler.command,
            "target": handler.path,
            "path": split.path,
            "query": split.query,
            "headers": {key.lower(): value for key, value in handler.headers.items()},
            "body": body.decode("utf-8"),
        }
        with self.lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")

    def issue_session(self) -> str:
        with self.lock:
            self.sessions_issued += 1
            return f"session-{self.sessions_issued}-token"


def make_handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = self._read_body()
            state.append_request(self, body)
            split = urlsplit(self.path)
            if split.path != state.base_path + "/sessions" or split.query:
                self._send_json(404, {"errorMessage": "Not Found"})
                return
            content_type = self.headers.get("Content-Type", "")
            if content_type.partition(";")[0].strip().lower() != "application/json":
                self._send_json(400, {"errorMessage": "Content-Type must be application/json"})
                return
            try:
                credentials = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"errorMessage": "Invalid request body"})
                return
            required_credentials = {"username", "password", "provider"}
            if not isinstance(credentials, dict) or not required_credentials <= credentials.keys():
                self._send_json(400, {"errorMessage": "Invalid credentials shape"})
                return
            token = state.issue_session()
            self._send_json(200, {"userId": USER_ID, "sessionId": token, "ttl": 1800})

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = self._read_body()
            state.append_request(self, body)
            split = urlsplit(self.path)
            prefix = state.base_path + "/events/"
            if not split.path.startswith(prefix) or split.path == prefix:
                self._send_json(404, {"errorMessage": "Not Found"})
                return

            token = self.headers.get("Authorization")
            constraint = unquote(split.path[len(prefix) :])
            if token == "Bearer session-1-token" and "CONTAINS alpha" in constraint:
                self._send_json(
                    200,
                    {
                        "complete": True,
                        "duration": 1,
                        "events": [
                            {"text": "alpha \"one\"\t雪", "timestamp": 101},
                            {"text": "path\\node/\b\f\r", "timestamp": 102},
                        ],
                    },
                )
                return
            if token == "Bearer session-1-token" and "CONTAINS beta" in constraint:
                self._send_json(440, "Login Timeout")
                return
            if token == "Bearer session-2-token" and "CONTAINS beta" in constraint:
                self._send_json(
                    200,
                    {
                        "complete": True,
                        "duration": 1,
                        "events": [{"text": "beta\nline 🚀", "timestamp": 103}],
                    },
                )
                return
            self._send_json(401, "Invalid session ID")

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length else b""

        def _send_json(self, status: int, payload: object) -> None:
            serialized = json.dumps(payload, separators=(",", ":")).replace("/", "\\/")
            body = serialized.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    state = MockState(args.contract, args.request_log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    server.daemon_threads = True
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
