"""Contract-pinned loopback SDDC Manager used by the protected verifier."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


EXPECTED_OPERATION_IDS = {
    "createToken",
    "updateSystemConfiguration",
    "updateProxyConfiguration",
}


class MockSddcManager:
    """Serve only the operation set projected into the protected contract."""

    def __init__(
        self,
        contract_path: Path,
        log_path: Path,
        *,
        access_token: str,
        refresh_token: str,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract["operations"]
        operation_ids = {item["operationId"] for item in operations}
        if operation_ids != EXPECTED_OPERATION_IDS or len(operations) != 3:
            raise AssertionError(
                f"unexpected mock contract operations: {sorted(operation_ids)}"
            )

        self.routes = {
            (item["method"], item["path"]): item["operationId"]
            for item in operations
        }
        if len(self.routes) != len(operations):
            raise AssertionError("contract contains duplicate method/path routes")

        self.log_path = log_path
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._log_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def service_root(self) -> str:
        if self._server is None:
            raise RuntimeError("mock server is not running")
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> "MockSddcManager":
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                self._handle()

            def do_PATCH(self) -> None:  # noqa: N802
                self._handle()

            def do_GET(self) -> None:  # noqa: N802
                self._handle()

            def do_PUT(self) -> None:  # noqa: N802
                self._handle()

            def do_DELETE(self) -> None:  # noqa: N802
                self._handle()

            def _handle(self) -> None:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                body = self.rfile.read(max(content_length, 0))
                record = {
                    "method": self.command,
                    "target": self.path,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                    "body": body.decode("utf-8", errors="replace"),
                }
                with parent._log_lock:
                    with parent.log_path.open(
                        "a", encoding="utf-8", newline="\n"
                    ) as log_file:
                        log_file.write(
                            json.dumps(
                                record,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )

                operation_id = parent.routes.get((self.command, self.path))
                if operation_id == "createToken":
                    self._send_json(
                        201,
                        {
                            "accessToken": parent.access_token,
                            "refreshToken": {"id": parent.refresh_token},
                        },
                    )
                elif operation_id == "updateSystemConfiguration":
                    self._send_empty(200)
                elif operation_id == "updateProxyConfiguration":
                    # The pinned specification declares no content for this 500.
                    self._send_empty(500)
                else:
                    self._send_empty(404)

            def _send_empty(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                encoded = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, _format: str, *args: object) -> None:
                del args

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf91-contract-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
