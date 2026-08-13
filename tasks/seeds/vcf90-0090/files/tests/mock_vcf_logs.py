"""Loopback-only VCF Operations for Logs mock, pinned to docs/contract.json."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"


class MockVCFLogs:
    """Context manager exposing only the operations named by the contract."""

    def __init__(self) -> None:
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        base_path = self.contract["server_base_path"]
        self.routes = {
            (operation["method"], base_path + operation["path"]): operation["operationId"]
            for operation in self.contract["operations"]
        }
        expected = {
            ("POST", "/api/v2/sessions"): "POST_sessions",
            ("POST", "/api/v2/log-forwarder"): "POST_log-forwarder",
        }
        if self.routes != expected:
            raise ValueError("mock routes no longer match the pinned contract")
        self.request_log: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._created = 0

    def __enter__(self) -> "MockVCFLogs":
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                fixture._handle(self)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                fixture._handle(self)

            def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                fixture._handle(self)

            def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                fixture._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self._server is not None
        assert self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length", "0"))
        raw = handler.rfile.read(length)
        path = urlsplit(handler.path).path
        record = {
            "method": handler.command,
            "path": path,
            "query": urlsplit(handler.path).query,
            "content_type": handler.headers.get("Content-Type"),
            "accept": handler.headers.get("Accept"),
            "authorization": handler.headers.get("Authorization"),
            "content_length": handler.headers.get("Content-Length"),
            "raw_body": raw.decode("utf-8"),
        }
        with self._lock:
            self.request_log.append(record)

        operation_id = self.routes.get((handler.command, path))
        if operation_id is None:
            self._send(handler, 404, {"errorMessage": "Operation is not in the pinned contract."})
            return

        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            self._send(handler, 400, {"errorMessage": "Invalid request body."})
            return

        if operation_id == "POST_sessions":
            self._session(handler, body)
        elif operation_id == "POST_log-forwarder":
            self._forwarder(handler, body)
        else:  # The route table is checked in __init__, so this is defensive only.
            self._send(handler, 404, {"errorMessage": "Operation is not implemented."})

    def _operation(self, operation_id: str) -> dict[str, Any]:
        return next(
            operation
            for operation in self.contract["operations"]
            if operation["operationId"] == operation_id
        )

    def _session(self, handler: BaseHTTPRequestHandler, body: Any) -> None:
        schema = self._operation("POST_sessions")["requestBody"]["schema"]
        if not isinstance(body, dict) or set(schema["required"]) - body.keys():
            self._send(handler, 400, {"errorMessage": "Invalid request body."})
            return
        self._send(
            handler,
            200,
            {
                "userId": "00000000-0000-0000-0000-000000000090",
                "sessionId": "vcf90-loopback-session",
                "ttl": 1800,
            },
        )

    def _forwarder(self, handler: BaseHTTPRequestHandler, body: Any) -> None:
        operation = self._operation("POST_log-forwarder")
        schema = operation["requestBody"]["schema"]
        allowed = set(schema["properties"])
        required = set(schema["required"])
        if handler.headers.get("Authorization") != "Bearer vcf90-loopback-session":
            self._send(handler, 401, {"errorMessage": "Authentication required."})
            return
        if not isinstance(body, dict) or required - body.keys() or body.keys() - allowed:
            self._send(handler, 400, {"errorMessage": "Invalid request body."})
            return
        if body["name"] == "edge-dr":
            self._send(handler, 409, operation["responses"]["409"]["example"])
            return

        with self._lock:
            self._created += 1
            identifier = f"forwarder-{self._created:03d}"
        response = {
            "name": body["name"],
            "host": body["host"],
            "port": body["port"],
            "protocol": body["protocol"],
            "sslEnabled": body["sslEnabled"],
            "workerCount": body.get("workerCount", 4),
            "connectionRefreshInterval": body.get("connectionRefreshInterval", 60),
            "diskCacheSize": body.get("diskCacheSize", 1000000000),
            "tags": body.get("tags", {}),
            "filter": body.get("filter", ""),
            "transportProtocol": body.get("transportProtocol", "TCP"),
            "forwardComplementaryFields": body.get("forwardComplementaryFields", False),
            "id": identifier,
        }
        self._send(handler, 201, response)

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
