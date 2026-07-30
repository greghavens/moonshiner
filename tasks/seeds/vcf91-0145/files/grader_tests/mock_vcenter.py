"""Loopback-only vCenter fixture constrained by docs/contract.json."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_OPERATIONS = {
    (
        "GET",
        "/vcenter/namespaces/instances/v2/{namespace}",
        "Vcenter.Namespaces.Instances_getV2",
    ),
    (
        "GET",
        "/vcenter/namespace-management/supervisors/{supervisor}/summary",
        "Vcenter.NamespaceManagement.Supervisors.Summary_get",
    ),
}


@dataclass(frozen=True)
class MockVCenterState:
    namespace_info: dict[str, Any]
    supervisor_summary: dict[str, Any]


def _load_and_check_contract(contract_path: Path) -> None:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = {
        (item["method"], item["path"], item["operationId"])
        for item in contract["operations"]
    }
    if operations != EXPECTED_OPERATIONS:
        raise RuntimeError("loopback fixture and docs/contract.json are out of sync")


class MockVCenter:
    """Context manager for the contract-pinned loopback vCenter service."""

    def __init__(
        self,
        root: Path,
        state: MockVCenterState,
        request_log: Path,
    ):
        _load_and_check_contract(root / "docs" / "contract.json")
        self._state = state
        self.request_log = request_log
        self._log_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "MockVCenter":
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "VCFContractFixture/1"
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

            def _json_response(self, status: int, value: Any) -> None:
                payload = json.dumps(
                    value, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                body = self._read_body()
                self._record(body)
                target = urlsplit(self.path)
                namespace_match = re.fullmatch(
                    r"/api/vcenter/namespaces/instances/v2/[^/]+",
                    target.path,
                )
                supervisor_match = re.fullmatch(
                    r"/api/vcenter/namespace-management/supervisors/"
                    r"[^/]+/summary",
                    target.path,
                )
                if namespace_match and not target.query:
                    self._json_response(200, fixture._state.namespace_info)
                elif supervisor_match and not target.query:
                    self._json_response(200, fixture._state.supervisor_summary)
                else:
                    self._json_response(
                        404,
                        {
                            "error_type": "NOT_FOUND",
                            "messages": [
                                {
                                    "id": "fixture.operation.not_found",
                                    "default_message": (
                                        "operation is outside the pinned contract"
                                    ),
                                }
                            ],
                        },
                    )

        self.request_log.write_text("", encoding="utf-8")
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}/api"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mock-vcenter",
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
