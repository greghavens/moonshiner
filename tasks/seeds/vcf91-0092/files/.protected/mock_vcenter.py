#!/usr/bin/env python3
"""Contract-pinned loopback fixture for retry-safe vCenter CPU updates."""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


def load_object(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_fsynced(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def append_log(path: Path, entry: dict) -> None:
    encoded = json.dumps(
        entry, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE "
            "SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_object(sys.argv[3])
    scenario = load_object(sys.argv[4])

    operations = contract.get("operations")
    expected = {
        "Vcenter.Vm.Hardware.Cpu_get": "GET",
        "Vcenter.Vm.Hardware.Cpu_update": "PATCH",
    }
    if not isinstance(operations, list) or len(operations) != len(expected):
        raise ValueError("the focused contract must name exactly two operations")

    operation_by_method = {}
    path_template = None
    for operation in operations:
        operation_id = operation.get("operationId")
        method = operation.get("method")
        path = operation.get("path")
        if operation_id not in expected or expected[operation_id] != method:
            raise ValueError("the focused contract names an unexpected operation")
        if (
            not isinstance(path, str)
            or not path.startswith("/api/")
            or path.count("{vm}") != 1
        ):
            raise ValueError("the focused contract has an invalid API path")
        if path_template is None:
            path_template = path
        elif path != path_template:
            raise ValueError("the focused operations must share one path")
        if method in operation_by_method:
            raise ValueError("contract methods must be unique")
        operation_by_method[method] = operation

    if set(operation_by_method) != set(expected.values()):
        raise ValueError("the focused contract method set is incomplete")

    token = scenario["session_token"]
    vm = scenario["vm"]
    desired_count = scenario["desired_count"]
    successful_count = scenario["successful_count"]
    initial_info = scenario["initial_info"]
    if not isinstance(token, str) or not token:
        raise ValueError("scenario session_token must be a non-empty string")
    if not isinstance(vm, str) or not vm:
        raise ValueError("scenario vm must be a non-empty string")
    if (
        not isinstance(desired_count, int)
        or isinstance(desired_count, bool)
        or desired_count < 1
    ):
        raise ValueError("scenario desired_count must be positive")
    if (
        not isinstance(successful_count, int)
        or isinstance(successful_count, bool)
        or successful_count < 1
        or successful_count == desired_count
    ):
        raise ValueError(
            "scenario successful_count must be positive and distinct"
        )
    if not isinstance(initial_info, dict):
        raise ValueError("scenario initial_info must be an object")

    allowed_path = path_template.replace("{vm}", quote(vm, safe=""))
    state = {
        "info": copy.deepcopy(initial_info),
        "patch_attempts": 0,
    }
    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def _send_json(self, status: int, value: dict) -> None:
            body = json.dumps(
                value, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            split = urlsplit(self.path)
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length else b""
            operation = operation_by_method.get(self.command)
            operation_match = (
                operation is not None and split.path == allowed_path
            )

            common_valid = (
                operation_match
                and split.query == ""
                and self.headers.get("vmware-api-session-id") == token
                and self.headers.get("Accept") == "application/json"
                and self.headers.get("Authorization") is None
            )

            status = 404
            response = {
                "error_type": "NOT_FOUND",
                "messages": [],
            }
            request_json = None

            if operation_match and not common_valid:
                status = 400
                response = {
                    "error_type": "INVALID_ARGUMENT",
                    "messages": [],
                }
            elif common_valid and self.command == "GET":
                if self.headers.get("Content-Type") is not None or body:
                    status = 400
                    response = {
                        "error_type": "INVALID_ARGUMENT",
                        "messages": [],
                    }
                else:
                    with state_lock:
                        response = copy.deepcopy(state["info"])
                    status = 200
            elif common_valid and self.command == "PATCH":
                try:
                    request_json = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    request_json = None
                patch_valid = (
                    self.headers.get("Content-Type") == "application/json"
                    and isinstance(request_json, dict)
                    and list(request_json) == ["count"]
                    and isinstance(request_json["count"], int)
                    and not isinstance(request_json["count"], bool)
                    and request_json["count"]
                    in {desired_count, successful_count}
                )
                if not patch_valid:
                    status = 400
                    response = {
                        "error_type": "INVALID_ARGUMENT",
                        "messages": [],
                    }
                else:
                    with state_lock:
                        state["patch_attempts"] += 1
                        state["info"]["count"] = request_json["count"]
                        attempt = state["patch_attempts"]
                    if attempt == 1:
                        status = 503
                        response = {
                            "error_type": "SERVICE_UNAVAILABLE",
                            "messages": [],
                        }
                    else:
                        status = 204
                        response = None

            entry = {
                "operationId": (
                    operation.get("operationId")
                    if operation_match
                    else None
                ),
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
                "bodyJson": request_json,
                "status": status,
            }
            with state_lock:
                append_log(log_file, entry)

            if response is None:
                self._send_empty(status)
            else:
                self._send_json(status, response)

        do_GET = _handle
        do_PATCH = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle

    log_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    write_fsynced(port_file, str(server.server_port))
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
