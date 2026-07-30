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
        "/vcenter/trusted-infrastructure/hosts/{host}/hardware/tpm",
        "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm_list",
    ),
    (
        "GET",
        "/vcenter/trusted-infrastructure/hosts/{host}/hardware/tpm/{tpm}/event-log",
        "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get",
    ),
    (
        "POST",
        "/appliance/support-bundle?vmw-task=true",
        "Appliance.SupportBundle_create$Task",
    ),
}


@dataclass(frozen=True)
class MockState:
    tpms: list[dict[str, Any]]
    event_log: dict[str, Any]
    support_bundle_task: str


def _load_and_check_contract(contract_path: Path) -> None:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = {
        (item["method"], item["path"], item["operationId"])
        for item in contract["operations"]
    }
    if operations != EXPECTED_OPERATIONS:
        raise RuntimeError("loopback fixture and docs/contract.json are out of sync")


class MockVCenter:
    """Context manager for the contract-pinned loopback HTTP service."""

    def __init__(self, root: Path, state: MockState, request_log: Path):
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

            def _record(self, body: bytes) -> None:
                record = {
                    "method": self.command,
                    "raw_path": self.path,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                    "body": body.decode("utf-8"),
                }
                line = json.dumps(record, sort_keys=True, separators=(",", ":"))
                with fixture._log_lock:
                    with fixture.request_log.open("a", encoding="utf-8") as stream:
                        stream.write(line + "\n")

            def _read_body(self) -> bytes:
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError:
                    length = 0
                return self.rfile.read(length) if length else b""

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

            def _not_found(self) -> None:
                self._json_response(
                    404,
                    {
                        "error_type": "NOT_FOUND",
                        "messages": [
                            {
                                "id": "fixture.operation.not_found",
                                "default_message": "operation is outside the pinned contract",
                            }
                        ],
                    },
                )

            def do_GET(self) -> None:
                body = self._read_body()
                self._record(body)
                target = urlsplit(self.path)
                list_match = re.fullmatch(
                    r"/api/vcenter/trusted-infrastructure/hosts/"
                    r"[^/]+/hardware/tpm",
                    target.path,
                )
                event_match = re.fullmatch(
                    r"/api/vcenter/trusted-infrastructure/hosts/"
                    r"[^/]+/hardware/tpm/[^/]+/event-log",
                    target.path,
                )
                if list_match:
                    self._json_response(200, fixture._state.tpms)
                elif event_match and not target.query:
                    self._json_response(200, fixture._state.event_log)
                else:
                    self._not_found()

            def do_POST(self) -> None:
                body = self._read_body()
                self._record(body)
                target = urlsplit(self.path)
                if (
                    target.path == "/api/appliance/support-bundle"
                    and target.query == "vmw-task=true"
                ):
                    self._json_response(202, fixture._state.support_bundle_task)
                else:
                    self._not_found()

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
