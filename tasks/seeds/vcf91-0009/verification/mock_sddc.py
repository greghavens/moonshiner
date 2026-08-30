#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager for the protected acceptance test."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ACCESS_TOKEN = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
    "eyJzdWIiOiJtb29uc2hpbmVyLXRlc3QiLCJpYXQiOjE3NjcyMjU2MDAsImV4cCI6NDEwMjQ0NDgwMH0."
)


def compile_contract(contract_path: Path) -> list[tuple[str, re.Pattern[str], str]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes: list[tuple[str, re.Pattern[str], str]] = []
    for operation in contract["operations"]:
        pattern = re.sub(r"\{[^/{}]+\}", r"[^/]+", operation["path"])
        routes.append(
            (
                operation["method"].upper(),
                re.compile("^" + pattern + "$"),
                operation["operationId"],
            )
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, routes, log_path: Path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.lock = threading.Lock()
        self.bundle_reads = 0
        self.task_reads: dict[str, int] = {}

    def operation_for(self, method: str, path: str) -> str | None:
        for route_method, pattern, operation_id in self.routes:
            if route_method == method and pattern.fullmatch(path):
                return operation_id
        return None

    def record(self, value: dict) -> None:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")


class Handler(BaseHTTPRequestHandler):
    server_version = "MoonshinerContractMock/1.0"
    sys_version = ""

    def log_message(self, _format, *_args):
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int, value: dict) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        body_bytes = self._read_body()

        # Connect-VcfSddcManagerServer (VMware.Sdk.Vcf.SddcManager 13.5.x)
        # validates its session with GET /v1/sddc-manager before handing the
        # connection back. That probe belongs to the SDK's connect handshake,
        # not to the bundle lifecycle under test, so it is answered here and
        # kept out of the contract audit log.
        if self.command == "GET" and parsed.path == "/v1/sddc-manager":
            if self.headers.get("Authorization") != "Bearer " + ACCESS_TOKEN:
                self._send_json(
                    401,
                    {
                        "errorCode": "AUTHENTICATION_FAILED",
                        "message": "Bearer token required",
                    },
                )
                return
            self._send_json(
                200,
                {
                    "id": "sddc-manager-001",
                    "fqdn": "127.0.0.1",
                    "version": "9.1.0.0.25372366",
                    "ipAddress": "127.0.0.1",
                },
            )
            return

        operation_id = self.server.operation_for(self.command, parsed.path)
        body_value = None
        if body_bytes:
            try:
                body_value = json.loads(body_bytes)
            except json.JSONDecodeError:
                body_value = body_bytes.decode("utf-8", errors="replace")

        self.server.record(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "operationId": operation_id,
                "authorization": self.headers.get("Authorization"),
                "contentType": self.headers.get("Content-Type"),
                "body": body_value,
            }
        )

        if operation_id is None:
            self._send_json(
                404,
                {
                    "errorCode": "MOCK_OPERATION_NOT_IN_CONTRACT",
                    "message": "The requested operation is not in docs/contract.json",
                },
            )
            return

        if operation_id != "createToken":
            if self.headers.get("Authorization") != "Bearer " + ACCESS_TOKEN:
                self._send_json(
                    401,
                    {
                        "errorCode": "AUTHENTICATION_FAILED",
                        "message": "Bearer token required",
                    },
                )
                return

        if operation_id == "createToken":
            if not isinstance(body_value, dict):
                self._send_json(400, {"errorCode": "INVALID_TOKEN_SPEC"})
                return
            self._send_json(
                201,
                {
                    "accessToken": ACCESS_TOKEN,
                    "refreshToken": {"id": "refresh-token-001"},
                },
            )
            return

        if operation_id == "getApplianceInfo":
            self._send_json(
                200,
                {
                    "role": "SddcManager",
                    "version": "9.1.0.0.25372366",
                    "dnsDomain": "loopback.local",
                    "dnsServers": [],
                    "ntpServers": [],
                },
            )
            return

        if operation_id == "getBundles":
            bundles = [
                {
                    "id": "Bundle-case",
                    "type": "VMWARE_SOFTWARE",
                    "version": "9.1.0.0",
                    "downloadStatus": "PENDING",
                },
                {
                    "id": "bundle-alpha",
                    "type": "VMWARE_SOFTWARE",
                    "version": "9.1.0.1",
                    "downloadStatus": "PENDING",
                },
                {
                    "id": "bundle-zulu",
                    "type": "SDDC_MANAGER",
                    "version": "9.1.0.2",
                    "downloadStatus": "PENDING",
                },
            ]
            with self.server.lock:
                self.server.bundle_reads += 1
                if self.server.bundle_reads % 2 == 1:
                    bundles.reverse()
            self._send_json(
                200,
                {
                    "elements": bundles,
                    "pageMetadata": {
                        "pageNumber": 0,
                        "pageSize": 3,
                        "totalElements": 3,
                        "totalPages": 1,
                    },
                },
            )
            return

        if operation_id == "startBundleDownloadByID":
            if body_value != {"bundleDownloadSpec": {"downloadNow": True}}:
                self._send_json(
                    400,
                    {
                        "errorCode": "INVALID_BUNDLE_UPDATE_SPEC",
                        "message": "Expected an immediate bundle download specification",
                    },
                )
                return
            bundle_id = parsed.path.rsplit("/", 1)[-1]
            task_ids = {
                "bundle-alpha": "task-download-001",
                "bundle-zulu": "task-download-fail",
                "bundle-stall": "task-download-stall",
                "bundle-weird": "task-download-weird",
            }
            task_id = task_ids.get(bundle_id, "task-download-fail")
            with self.server.lock:
                self.server.task_reads[task_id] = 0
            self._send_json(
                202,
                {
                    "id": task_id,
                    "name": "Download bundle",
                    "type": "BUNDLE_DOWNLOAD",
                    "status": "PENDING",
                    "creationTimestamp": "2026-07-28T00:00:00Z",
                    "isCancellable": True,
                    "isRetryable": False,
                },
            )
            return

        if operation_id == "getTask":
            task_id = parsed.path.rsplit("/", 1)[-1]
            with self.server.lock:
                if task_id not in self.server.task_reads:
                    self._send_json(
                        404,
                        {
                            "errorCode": "TASK_NOT_FOUND",
                            "message": "Task not found",
                        },
                    )
                    return
                self.server.task_reads[task_id] += 1
                read_number = self.server.task_reads[task_id]

            if task_id == "task-download-fail":
                status = "Failed"
            elif task_id == "task-download-stall":
                status = "Queued"
            elif task_id == "task-download-weird":
                status = "Waiting For Depot"
            else:
                status = "In Progress" if read_number == 1 else "Successful"
            value = {
                "id": task_id,
                "name": "Download bundle",
                "type": "BUNDLE_DOWNLOAD",
                "status": status,
                "creationTimestamp": "2026-07-28T00:00:00Z",
                "isCancellable": status == "In Progress",
                "isRetryable": status == "Failed",
            }
            if status in {"Successful", "Failed"}:
                value["completionTimestamp"] = "2026-07-28T00:00:01Z"
            self._send_json(200, value)
            return

        raise AssertionError(f"Unhandled contract operation: {operation_id}")

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    args = parser.parse_args()

    routes = compile_contract(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, args.log)
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
