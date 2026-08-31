"""Loopback mock for the focused VCF 9.1 bootstrap operations."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Mapping
from urllib.parse import urlsplit


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs" / "contract.json"


@dataclass(frozen=True)
class RequestRecord:
    method: str
    target: str
    path: str
    query: str
    headers: Mapping[str, str]
    body: bytes


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        owner: ContractMock = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        parsed = urlsplit(self.path)
        record = RequestRecord(
            self.command,
            self.path,
            parsed.path,
            parsed.query,
            {key.lower(): value for key, value in self.headers.items()},
            body,
        )
        with owner._lock:
            owner.request_log.append(record)

        operation_id = owner.routes.get((self.command, parsed.path))
        if operation_id is None:
            self._json(404, {"errorCode": "UNSUPPORTED_OPERATION", "message": "not in contract"})
            return
        if operation_id == owner.fail_operation:
            self._json(500, owner.error_responses[operation_id])
            return
        status = owner.status_overrides.get(operation_id, owner.success_statuses[operation_id])
        self._json(status, owner.success_responses[operation_id])

    def _json(self, status: int, document: object) -> None:
        encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ContractMock:
    def __init__(
        self,
        *,
        fail_operation: str | None = "syncDepotMetadata",
        status_overrides: Mapping[str, int] | None = None,
    ) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        operations = contract["operations"]
        expected = {
            "updateProxyConfiguration",
            "updateServicesConfig",
            "syncDepotMetadata",
        }
        if set(operations) != expected:
            raise AssertionError("mock and operation contract diverged")
        self.routes = {
            (operation["method"], operation["path"]): operation_id
            for operation_id, operation in operations.items()
        }
        self.success_statuses = {
            operation_id: operation["successStatus"]
            for operation_id, operation in operations.items()
        }
        if fail_operation is not None and fail_operation not in expected:
            raise ValueError(f"unknown failure operation {fail_operation}")
        self.fail_operation = fail_operation
        self.status_overrides = dict(status_overrides or {})
        self.success_responses = {
            "updateProxyConfiguration": {
                "id": "proxy-task-live-shape",
                "name": "Update Proxy Configuration",
                "status": "COMPLETED_WITH_SUCCESS",
                "creationTimestamp": "2026-08-30T12:00:00Z",
            },
            "updateServicesConfig": {
                "services": [{
                    "name": "VCF Depot",
                    "type": "VCF_DEPOT",
                    "key": "depot-service-key",
                    "nodes": [{
                        "name": "VCF Depot",
                        "addresses": [{"type": "Fqdn", "value": "vcf-flt01.vcf.lab"}],
                    }],
                }],
            },
            "syncDepotMetadata": {"syncStatus": "SYNC_IN_PROGRESS"},
        }
        self.error_responses = {
            operation_id: {
                "errorCode": f"VCF_{operation_id.upper()}_FAILED",
                "errorType": "INTERNAL_SERVER_ERROR",
                "message": f"{operation_id} failed",
                "remediationMessage": "Retry after correcting the service condition.",
                "referenceToken": f"ref-{operation_id}",
            }
            for operation_id in expected
        }
        self.request_log: list[RequestRecord] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMock":
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
