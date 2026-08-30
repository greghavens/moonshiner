#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the focused vCenter workflow."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


EXPECTED_OPERATION_IDS = [
    "Vcenter.Vm.Hardware.Cpu_update",
    "Vcenter.Vm.Hardware.Memory_update",
    "Vcenter.Vm.Power_start",
]


def load_object(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def append_log(path: Path, entry: dict) -> None:
    encoded = json.dumps(
        entry, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def compact_json(value: dict) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_object(sys.argv[3])
    scenario = load_object(sys.argv[4])

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 3:
        raise ValueError("the focused contract must name exactly three operations")
    operation_ids = [item.get("operationId") for item in operations]
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise ValueError("the focused contract operation order is invalid")

    expected_methods = ["PATCH", "PATCH", "POST"]
    for index, operation in enumerate(operations):
        if operation.get("method") != expected_methods[index]:
            raise ValueError("the focused contract contains an invalid method")
        path = operation.get("path")
        if not isinstance(path, str) or not path.startswith("/api/"):
            raise ValueError("the focused contract contains an invalid API path")

    token = scenario["session_token"]
    vm = scenario["vm"]
    cpu_count = scenario["cpu_count"]
    memory_mib = scenario["memory_mib"]
    power_error_message = scenario["power_error_message"]
    if not isinstance(token, str) or not token:
        raise ValueError("scenario session_token must be a non-empty string")
    if not isinstance(vm, str) or not vm:
        raise ValueError("scenario vm must be a non-empty string")
    if not isinstance(cpu_count, int) or cpu_count < 1:
        raise ValueError("scenario cpu_count must be positive")
    if not isinstance(memory_mib, int) or memory_mib < 1:
        raise ValueError("scenario memory_mib must be positive")
    if not isinstance(power_error_message, str) or not power_error_message:
        raise ValueError("scenario power_error_message must be non-empty")

    encoded_vm = quote(vm, safe="")
    routes: list[dict] = []
    for index, operation in enumerate(operations):
        raw_target = operation["path"].replace("{vm}", encoded_vm)
        split = urlsplit(raw_target)
        expected_body = b""
        expected_content_type = None
        if index == 0:
            expected_body = compact_json({"count": cpu_count})
            expected_content_type = "application/json"
        elif index == 1:
            expected_body = compact_json({"size_mib": memory_mib})
            expected_content_type = "application/json"
        routes.append(
            {
                "index": index,
                "operationId": operation["operationId"],
                "method": operation["method"],
                "path": split.path,
                "query": split.query,
                "rawTarget": raw_target,
                "body": expected_body,
                "contentType": expected_content_type,
            }
        )

    state_lock = threading.Lock()
    next_index = 0

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, value: dict) -> None:
            body = compact_json(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def _handle(self) -> None:
            nonlocal next_index

            split = urlsplit(self.path)
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length else b""
            route = next(
                (
                    candidate
                    for candidate in routes
                    if self.command == candidate["method"]
                    and split.path == candidate["path"]
                    and split.query == candidate["query"]
                ),
                None,
            )

            with state_lock:
                status = 404
                response: dict = {
                    "error_type": "NOT_FOUND",
                    "messages": [],
                }
                request_valid = False
                sequence_valid = False
                if route is not None:
                    sequence_valid = route["index"] == next_index
                    request_valid = (
                        sequence_valid
                        and self.path == route["rawTarget"]
                        and self.headers.get("vmware-api-session-id") == token
                        and self.headers.get("Accept") == "application/json"
                        and self.headers.get("Authorization") is None
                        and self.headers.get("Content-Type")
                        == route["contentType"]
                        and body == route["body"]
                    )
                    if not sequence_valid:
                        status = 409
                        response = {
                            "error_type": "NOT_ALLOWED_IN_CURRENT_STATE",
                            "messages": [],
                        }
                    elif not request_valid:
                        status = 400
                        response = {
                            "error_type": "INVALID_ARGUMENT",
                            "messages": [],
                        }
                    elif route["operationId"] == "Vcenter.Vm.Power_start":
                        status = 503
                        response = {
                            "error_type": "SERVICE_UNAVAILABLE",
                            "messages": [
                                {
                                    "args": [],
                                    "default_message": power_error_message,
                                    "id": "com.vmware.vcenter.power.unavailable",
                                }
                            ],
                        }
                        next_index += 1
                    else:
                        status = 204
                        response = {}
                        next_index += 1

                entry = {
                    "operationId": (
                        route["operationId"] if route is not None else None
                    ),
                    "sequenceIndex": (
                        route["index"] if route is not None else None
                    ),
                    "sequenceValid": sequence_valid,
                    "requestValid": request_valid,
                    "method": self.command,
                    "rawTarget": self.path,
                    "path": split.path,
                    "rawQuery": split.query,
                    "vmwareApiSessionId": self.headers.get(
                        "vmware-api-session-id"
                    ),
                    "authorization": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                    "contentType": self.headers.get("Content-Type"),
                    "contentLength": len(body),
                    "bodyHex": body.hex(),
                    "status": status,
                }
                append_log(log_file, entry)

            if status == 204:
                self._send_empty(status)
            else:
                self._send_json(status, response)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle

    log_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
