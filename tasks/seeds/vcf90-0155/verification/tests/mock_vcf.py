"""Contract-pinned loopback VCF Automation service used by acceptance tests."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_PATH = _ROOT / "docs" / "contract.json"


class _ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, drop_first_response: bool) -> None:
        contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        operations = contract["operations"]
        if len(operations) != 1:
            raise AssertionError("the loopback fixture requires exactly one contract operation")
        operation = operations[0]
        if operation["method"] != "PATCH":
            raise AssertionError("the pinned operation must use PATCH")

        super().__init__(address, _Handler)
        self.operation: dict[str, Any] = operation
        self.drop_first_response = drop_first_response
        self.dropped_response = False
        self.request_log: list[dict[str, Any]] = []
        self.deployments: dict[str, dict[str, Any]] = {}
        self.effect_count = 0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def contract_server(self) -> _ContractServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", self.contract_server.operation["method"])
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _method_not_allowed
    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed

    def do_PATCH(self) -> None:
        template = self.contract_server.operation["path"]
        prefix, suffix = template.split("{deploymentId}")
        if not self.path.startswith(prefix) or not self.path.endswith(suffix):
            self._send_json(404, {"error": "operation not in contract"})
            return
        encoded_id = self.path[len(prefix) : len(self.path) - len(suffix) if suffix else None]
        if not encoded_id or "/" in encoded_id:
            self._send_json(404, {"error": "operation not in contract"})
            return

        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text or "")
        except ValueError:
            self._send_json(400, {"error": "Content-Length required"})
            return
        body = self.rfile.read(length)
        self.contract_server.request_log.append(
            {
                "request_line": self.requestline,
                "method": self.command,
                "path": self.path,
                "headers": list(self.headers.raw_items()),
                "body": body,
            }
        )

        if self.headers.get("Content-Type") != "application/json":
            self._send_json(415, {"error": "application/json required"})
            return
        try:
            update = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return
        allowed = set(self.contract_server.operation["request"]["fields"])
        if not isinstance(update, dict) or not set(update) <= allowed:
            self._send_json(400, {"error": "body does not match DeploymentUpdate"})
            return
        if any(not isinstance(value, str) for value in update.values()):
            self._send_json(400, {"error": "DeploymentUpdate values must be strings"})
            return

        deployment = self.contract_server.deployments.setdefault(
            encoded_id,
            {
                "id": encoded_id,
                "name": "Original deployment",
                "description": "Original description",
                "iconId": "00000000-0000-0000-0000-000000000000",
            },
        )
        changed = any(deployment.get(key) != value for key, value in update.items())
        deployment.update(update)
        if changed:
            self.contract_server.effect_count += 1

        if self.contract_server.drop_first_response and not self.contract_server.dropped_response:
            self.contract_server.dropped_response = True
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return

        self._send_json(200, deployment)


class MockVCFAutomation:
    """Context manager exposing a loopback endpoint and readable request log."""

    def __init__(self, *, drop_first_response: bool = True) -> None:
        self._drop_first_response = drop_first_response
        self._server: _ContractServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "MockVCFAutomation":
        self._server = _ContractServer(
            ("127.0.0.1", 0), drop_first_response=self._drop_first_response
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        assert self._thread is not None
        self._thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def request_log(self) -> list[dict[str, Any]]:
        assert self._server is not None
        return self._server.request_log

    @property
    def effect_count(self) -> int:
        assert self._server is not None
        return self._server.effect_count
