"""Loopback-only HTTP fixture driven by docs/contract.json."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


def _match_template(template: str, actual: str) -> dict[str, str] | None:
    expected_parts = template.strip("/").split("/")
    actual_parts = actual.strip("/").split("/")
    if len(expected_parts) != len(actual_parts):
        return None

    parameters: dict[str, str] = {}
    for expected, received in zip(expected_parts, actual_parts):
        if expected.startswith("{") and expected.endswith("}"):
            value = unquote(received)
            if not value:
                return None
            parameters[expected[1:-1]] = value
        elif expected != received:
            return None
    return parameters


class ContractMock:
    """Serve only operations named by the compact checked-in contract."""

    def __init__(self, contract_path: Path, request_log_path: Path, token: str) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.request_log_path = request_log_path
        self.token = token
        self.state: dict[str, dict[str, Any]] = {}
        self.effect_counts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def origin(self) -> str:
        if self._server is None:
            raise RuntimeError("mock server is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMock":
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _record(self, raw_body: bytes) -> None:
                record = {
                    "method": self.command,
                    "path": urlsplit(self.path).path,
                    "query": urlsplit(self.path).query,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": raw_body.decode("utf-8"),
                }
                with fixture._lock:
                    with fixture.request_log_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, sort_keys=True) + "\n")

            def _reply(self, status: int, body: dict[str, Any]) -> None:
                raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _dispatch(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                self._record(raw_body)

                path = urlsplit(self.path).path
                selected: tuple[dict[str, Any], dict[str, str]] | None = None
                for operation in fixture.contract["operations"]:
                    if self.command != operation["method"]:
                        continue
                    parameters = _match_template(operation["fullPath"], path)
                    if parameters is not None:
                        selected = operation, parameters
                        break

                if selected is None:
                    self._reply(404, {"errorMessage": "Not found"})
                    return
                operation, parameters = selected
                if operation["operationId"] not in {
                    item["operationId"] for item in fixture.contract["operations"]
                }:
                    self._reply(404, {"errorMessage": "Not found"})
                    return
                if self.headers.get("Authorization") != f"Bearer {fixture.token}":
                    self._reply(401, {"errorMessage": "Invalid session ID"})
                    return
                if self.headers.get_content_type() != operation["requestBody"]["contentType"]:
                    self._reply(400, {"errorMessage": "Invalid request body."})
                    return

                try:
                    body = json.loads(raw_body)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._reply(400, {"errorMessage": "Invalid request body."})
                    return
                properties = operation["requestBody"]["schema"]["properties"]
                if not isinstance(body, dict) or not set(body).issubset(properties):
                    self._reply(400, {"errorMessage": "Invalid request body."})
                    return

                webhook_id = parameters["webhookId"]
                with fixture._lock:
                    if fixture.state.get(webhook_id) != body:
                        fixture.effect_counts[webhook_id] = fixture.effect_counts.get(webhook_id, 0) + 1
                    fixture.state[webhook_id] = body
                self._reply(200, {"id": webhook_id, **body})

            do_PUT = _dispatch
            do_GET = _dispatch
            do_POST = _dispatch
            do_PATCH = _dispatch
            do_DELETE = _dispatch

            def log_message(self, format: str, *args: object) -> None:
                return

        self.request_log_path.write_text("", encoding="utf-8")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
