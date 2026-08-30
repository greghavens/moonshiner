"""Loopback VCF Networks mock whose routes are loaded from docs/contract.json."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ContractMock:
    """Serve only the operation routes named by a derived contract."""

    def __init__(
        self,
        contract_path: Path,
        *,
        fail_operation_id: str | None = None,
        disconnect_operation_id: str | None = None,
    ) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.fail_operation_id = fail_operation_id
        self.disconnect_operation_id = disconnect_operation_id
        self.request_log: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMock":
        server_path = self.contract["servers"][0]["url"]
        operations = self.contract["operations"]
        routes = {
            (operation["method"].upper(), server_path + operation["path"]): operation
            for operation in operations
        }
        if len(routes) != len(operations):
            raise AssertionError("contract contains duplicate method/path routes")
        if self.fail_operation_id is not None and self.fail_operation_id not in {
            operation["operationId"] for operation in operations
        }:
            raise AssertionError("failure operation is not named by the contract")
        if self.disconnect_operation_id is not None and self.disconnect_operation_id not in {
            operation["operationId"] for operation in operations
        }:
            raise AssertionError("disconnect operation is not named by the contract")

        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def do_GET(self) -> None:  # noqa: N802 - used to prove unknown routes fail
                self._dispatch()

            def log_message(self, _format: str, *args: object) -> None:
                del args

            def _dispatch(self) -> None:
                parsed = urlsplit(self.path)
                operation = routes.get((self.command, parsed.path))
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                owner.request_log.append(
                    {
                        "operation_id": operation["operationId"] if operation else None,
                        "method": self.command,
                        "path": parsed.path,
                        "query": parsed.query,
                        "headers": {key.lower(): value for key, value in self.headers.items()},
                        "body": body,
                    }
                )

                if operation is None:
                    self._respond(404, {"code": 404, "message": "route not in contract"})
                    return

                if operation["operationId"] == owner.disconnect_operation_id:
                    self.close_connection = True
                    return

                if operation["operationId"] == owner.fail_operation_id:
                    if "500" not in operation["responses"]:
                        raise AssertionError("contract does not define the injected failure")
                    self._respond(
                        500,
                        {
                            "code": 500,
                            "message": f"forced failure for {operation['operationId']}",
                        },
                    )
                    return

                success_statuses = sorted(
                    int(code) for code in operation["responses"] if code.startswith("2")
                )
                if not success_statuses:
                    raise AssertionError("contract operation has no success response")
                response: dict[str, Any] | None = None
                if operation["operationId"] == "updateSyslogStatus":
                    response = {"enabled": True}
                self._respond(success_statuses[0], response)

            def _respond(self, status: int, payload: dict[str, Any] | None) -> None:
                encoded = (
                    json.dumps(payload, separators=(",", ":")).encode("utf-8")
                    if payload is not None
                    else b""
                )
                self.send_response(status)
                if encoded:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                if encoded:
                    self.wfile.write(encoded)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
