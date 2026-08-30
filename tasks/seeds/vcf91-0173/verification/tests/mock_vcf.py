"""Loopback-only VCF Operations Log Management contract mock."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "contract.json"
EXPECTED_OPERATIONS = {
    ("POST", "/api/v2/agent/secrets", "createAgentSecret"),
    ("GET", "/api/v2/agent/secrets", "listAgentSecrets"),
}


def _contract_routes() -> dict[tuple[str, str], str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    routes: dict[tuple[str, str], str] = {}
    discovered: set[tuple[str, str, str]] = set()
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation["operationId"]
            key = (method.upper(), path)
            routes[key] = operation_id
            discovered.add((method.upper(), path, operation_id))
    if discovered != EXPECTED_OPERATIONS:
        raise RuntimeError(f"contract operation drift: {sorted(discovered)!r}")
    return routes


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, poll_statuses: tuple[str, ...]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes = _contract_routes()
        self.request_log: list[dict[str, Any]] = []
        self.secret: dict[str, str] | None = None
        self.poll_count = 0
        self.poll_statuses = poll_statuses
        nonce = secrets.token_hex(12)
        self.token = f"log-token-{nonce}"
        self.secret_id = f"secret-{nonce}"
        self.generated_name = f"generated-agent-{nonce}"
        self.one_time_secret = f"one-time-{secrets.token_hex(18)}"


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _capture(self) -> tuple[str, list[tuple[str, str]], bytes]:
        parsed = urlsplit(self.path)
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b""
        try:
            json_body: Any = json.loads(body) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            json_body = None
        self.server.request_log.append(
            {
                "method": self.command,
                "target": self.path,
                "path": parsed.path,
                "query": parse_qsl(parsed.query, keep_blank_values=True),
                "headers": [
                    (key.lower(), value) for key, value in self.headers.raw_items()
                ],
                "body": body,
                "json": json_body,
            }
        )
        return parsed.path, parse_qsl(parsed.query, keep_blank_values=True), body

    def _dispatch(self) -> None:
        path, query, body = self._capture()
        operation_id = self.server.routes.get((self.command, path))
        if operation_id is None:
            self._send_json(
                404,
                {"errorCode": "API_ERROR", "errorMessage": "operation not in contract"},
            )
            return
        if self.headers.get_all("X-JWT-Token") != [self.server.token]:
            self._send_json(
                403,
                {
                    "errorCode": "SECURITY_ERROR",
                    "errorMessage": "X-JWT-Token required",
                },
            )
            return

        if operation_id == "createAgentSecret":
            self._create_secret(body)
            return
        if operation_id == "listAgentSecrets":
            self._list_secrets(query)
            return
        raise AssertionError(f"unhandled contract operation {operation_id}")

    def _create_secret(self, body: bytes) -> None:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        allowed = {"name"}
        if not isinstance(payload, dict) or not set(payload).issubset(allowed):
            self._send_json(
                400,
                {"errorCode": "JSON_FORMAT_ERROR", "errorMessage": "invalid body"},
            )
            return
        name = payload.get("name") or self.server.generated_name
        self.server.secret = {"id": self.server.secret_id, "name": name}
        self.server.poll_count = 0
        self._send_json(
            201,
            {
                "id": self.server.secret_id,
                "name": name,
                "secret": self.server.one_time_secret,
                "status": "PENDING",
            },
        )

    def _list_secrets(self, query: list[tuple[str, str]]) -> None:
        if self.server.secret is None:
            self._send_json(
                404,
                {"errorCode": "API_ERROR", "errorMessage": "secret not created"},
            )
            return
        query_map: dict[str, list[str]] = {}
        for key, value in query:
            query_map.setdefault(key, []).append(value)
        if "page" not in query_map or "size" not in query_map:
            self._send_json(
                400,
                {"errorCode": "FIELD_ERROR", "errorMessage": "pageable required"},
            )
            return
        self.server.poll_count += 1
        status_index = min(
            self.server.poll_count - 1, len(self.server.poll_statuses) - 1
        )
        status = self.server.poll_statuses[status_index]
        self._send_json(
            200,
            {
                "id": self.server.secret["id"],
                "modificationTime": "2026-07-30T12:00:00Z",
                "name": self.server.secret["name"],
                "status": status,
            },
        )

    do_GET = _dispatch
    do_POST = _dispatch


class VCFLogManagementMock:
    """Context manager exposing the loopback URL and captured request log."""

    def __init__(self, poll_statuses: tuple[str, ...] = ("PENDING", "ACTIVE")) -> None:
        if not poll_statuses or any(not status for status in poll_statuses):
            raise ValueError("poll_statuses must contain non-empty statuses")
        self._server = _Server(poll_statuses)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf-log-management-mock",
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.request_log

    @property
    def token(self) -> str:
        return self._server.token

    @property
    def secret_id(self) -> str:
        return self._server.secret_id

    @property
    def generated_name(self) -> str:
        return self._server.generated_name

    @property
    def one_time_secret(self) -> str:
        return self._server.one_time_secret

    def __enter__(self) -> "VCFLogManagementMock":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
