"""Loopback-only VCF Installer mock for the pinned four-operation contract."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_PATH = _ROOT / "docs" / "contract.json"
_TASK_ID = "123e4567-e89b-42d3-a456-556642440000"
_EXPECTED_OPERATIONS = {
    ("POST", "/v1/tokens"): "createToken",
    ("POST", "/v1/sddcs"): "deploySddc",
    ("PATCH", "/v1/tokens/access-token/refresh"): "refreshAccessToken",
    ("GET", "/v1/sddcs/{id}"): "getSddcTaskByID",
}


class ContractPinnedInstallerMock:
    """Context-managed local HTTP server with a readable request log."""

    def __init__(self) -> None:
        contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        actual = {
            (operation["method"], operation["path"]): operation["operationId"]
            for operation in contract["operations"]
        }
        if actual != _EXPECTED_OPERATIONS:
            raise RuntimeError("mock routes no longer match docs/contract.json")

        self.request_log: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._old_token_polls = 0
        self._new_token_polls = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractPinnedInstallerMock":
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)
        self._server.server_close()

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
                self._route()

            def do_PATCH(self) -> None:  # noqa: N802 - stdlib hook name
                self._route()

            def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
                self._route()

            def do_PUT(self) -> None:  # noqa: N802 - reject unnamed operations
                self._route()

            def do_DELETE(self) -> None:  # noqa: N802 - reject unnamed operations
                self._route()

            def log_message(self, format: str, *args: object) -> None:
                return

            def _route(self) -> None:
                split = urlsplit(self.path)
                route_path = split.path
                operation_id: str | None = None
                if self.command == "POST" and route_path == "/v1/tokens":
                    operation_id = "createToken"
                elif self.command == "POST" and route_path == "/v1/sddcs":
                    operation_id = "deploySddc"
                elif (
                    self.command == "PATCH"
                    and route_path == "/v1/tokens/access-token/refresh"
                ):
                    operation_id = "refreshAccessToken"
                elif self.command == "GET" and route_path == f"/v1/sddcs/{_TASK_ID}":
                    operation_id = "getSddcTaskByID"

                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length) if length else b""
                try:
                    json_body = json.loads(raw_body) if raw_body else None
                except json.JSONDecodeError:
                    json_body = None

                entry: dict[str, Any] = {
                    "method": self.command,
                    "target": self.path,
                    "path": route_path,
                    "query": split.query,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "raw_body": raw_body,
                    "json_body": json_body,
                    "operationId": operation_id,
                }

                status, response = owner._response_for(entry)
                entry["response_status"] = status
                with owner._lock:
                    owner.request_log.append(entry)

                encoded = b"" if response is None else json.dumps(response).encode("utf-8")
                self.send_response(status)
                if response is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                if encoded:
                    self.wfile.write(encoded)

        return Handler

    def _response_for(self, request: dict[str, Any]) -> tuple[int, Any]:
        operation_id = request["operationId"]
        if operation_id is None:
            return 404, {"message": "operation not in pinned contract"}

        headers = request["headers"]
        if headers.get("accept") != "application/json":
            return 406, {"message": "Accept must be application/json"}
        if request["method"] in {"POST", "PATCH"} and headers.get(
            "content-type"
        ) != "application/json":
            return 415, {"message": "Content-Type must be application/json"}

        if operation_id == "createToken":
            return 201, {
                "accessToken": "expired-access",
                "refreshToken": {"id": "refresh-token-1"},
            }

        if operation_id == "deploySddc":
            if headers.get("authorization") != "Bearer expired-access":
                return 401, {"message": "access token rejected"}
            return 202, {
                "id": _TASK_ID,
                "name": "VCF deployment",
                "status": "IN_PROGRESS",
                "creationTimestamp": "2025-06-20T10:00:00Z",
            }

        if operation_id == "refreshAccessToken":
            if request["json_body"] != "refresh-token-1":
                return 400, {"message": "refresh body must be the token ID string"}
            return 200, "fresh-access"

        authorization = headers.get("authorization")
        if authorization == "Bearer expired-access":
            self._old_token_polls += 1
            if self._old_token_polls == 1:
                return 200, {
                    "id": _TASK_ID,
                    "name": "VCF deployment",
                    "status": "IN_PROGRESS",
                    "creationTimestamp": "2025-06-20T10:00:00Z",
                }
            return 401, {"message": "access token expired"}
        if authorization == "Bearer fresh-access":
            self._new_token_polls += 1
            status = (
                "IN_PROGRESS"
                if self._new_token_polls == 1
                else "COMPLETED_WITH_SUCCESS"
            )
            return 200, {
                "id": _TASK_ID,
                "name": "VCF deployment",
                "status": status,
                "creationTimestamp": "2025-06-20T10:00:00Z",
            }
        return 401, {"message": "access token rejected"}
