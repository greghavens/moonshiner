"""Loopback VCF Installer mock whose route table is pinned to docs/contract.json."""

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
    server_version = "VCFInstallerContractMock/9.1"
    protocol_version = "HTTP/1.1"

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_GET(self) -> None:  # noqa: N802 - unsupported by this reduced contract
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - unsupported by this reduced contract
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - unsupported by this reduced contract
        self._dispatch()

    def _dispatch(self) -> None:
        owner: ContractMock = self.server.owner  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        parsed = urlsplit(self.path)
        record = RequestRecord(
            method=self.command,
            target=self.path,
            path=parsed.path,
            query=parsed.query,
            headers={key.lower(): value for key, value in self.headers.items()},
            body=body,
        )
        with owner._lock:
            owner.request_log.append(record)

        operation_id = owner.routes.get((self.command, parsed.path))
        if operation_id is None:
            self._json_response(
                404,
                {
                    "errorCode": "UNSUPPORTED_CONTRACT_OPERATION",
                    "message": "The reduced contract does not expose this operation",
                },
            )
            return

        if operation_id == owner.fail_operation:
            status = 500
            document = owner.error_responses[operation_id]
        else:
            status = 202
            document = owner.success_responses[operation_id]
        status = owner.status_overrides.get(operation_id, status)
        self._json_response(status, document)

    def _json_response(self, status: int, document: object) -> None:
        encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ContractMock:
    """Context-managed loopback server exposing exactly the pinned operations."""

    def __init__(
        self,
        *,
        fail_operation: str | None = "syncDepotMetadata",
        status_overrides: Mapping[str, int] | None = None,
    ) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        operations = contract["operations"]
        expected_ids = {
            "updateProxyConfiguration",
            "updateDepotSettings",
            "syncDepotMetadata",
        }
        if set(operations) != expected_ids:
            raise AssertionError("mock and protected operation contract diverged")
        self.routes = {
            (operation["method"], operation["path"]): operation_id
            for operation_id, operation in operations.items()
        }
        if len(self.routes) != len(operations):
            raise AssertionError("contract contains duplicate method/path routes")
        if fail_operation is not None and fail_operation not in expected_ids:
            raise ValueError(f"unknown failure operation {fail_operation}")
        self.fail_operation = fail_operation
        self.status_overrides = dict(status_overrides or {})
        unknown_overrides = set(self.status_overrides) - expected_ids
        if unknown_overrides:
            raise ValueError(f"unknown status overrides {sorted(unknown_overrides)}")
        for operation_id, operation in operations.items():
            if "202" not in operation["responses"]:
                raise AssertionError(
                    f"mock success status is outside the contract for {operation_id}"
                )
            if operation_id == fail_operation and "500" not in operation["responses"]:
                raise AssertionError(
                    f"mock failure status is outside the contract for {operation_id}"
                )
        self.success_responses = {
            "updateProxyConfiguration": {
                "id": "task-proxy-91",
                "name": "Update Proxy Configuration",
                "status": "IN_PROGRESS",
                "creationTimestamp": "2026-05-13T12:00:00Z",
            },
            "updateDepotSettings": {
                "vmwareAccount": {
                    "status": "DEPOT_CONNECTION_SUCCESSFUL",
                    "message": "Credentials accepted",
                },
                "depotConfiguration": {"isOfflineDepot": False},
            },
            "syncDepotMetadata": {
                "syncStatus": "IN_PROGRESS",
            },
        }
        self.error_responses = {
            operation_id: {
                "errorCode": f"VCF_{operation_id.upper()}_FAILED",
                "errorType": "INTERNAL_SERVER_ERROR",
                "message": f"{operation_id} failed in the contract mock",
                "referenceToken": f"ref-{operation_id}",
            }
            for operation_id in expected_ids
        }
        self.error_responses["syncDepotMetadata"] = {
            "errorCode": "VCF_DEPOT_SYNC_FAILED",
            "errorType": "INTERNAL_SERVER_ERROR",
            "message": "Depot metadata index could not be refreshed",
            "remediationMessage": "Retry after depot connectivity is restored.",
            "referenceToken": "ref-sync-91",
            "causes": [
                {
                    "type": "DepotConnectionException",
                    "message": "Upstream depot returned an inconsistent manifest",
                }
            ],
        }
        self.sync_error = self.error_responses["syncDepotMetadata"]
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
