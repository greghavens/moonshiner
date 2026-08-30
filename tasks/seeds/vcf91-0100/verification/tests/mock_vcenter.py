#!/usr/bin/env python3
"""Contract-pinned loopback fixture for an expiring vCenter API session."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


EXPECTED_OPERATIONS = {
    "Vcenter.Vm.Hardware.Cpu_update": "PATCH",
    "Vcenter.Vm.Hardware.Memory_update": "PATCH",
    "Vcenter.Vm.Power_start": "POST",
}


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_fsynced(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def append_log(path: Path, entry: dict) -> None:
    encoded = json.dumps(
        entry, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def contract_routes(contract: dict, vm: str) -> dict[tuple[str, str, str], str]:
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 3:
        raise ValueError("the focused contract must name exactly three operations")

    seen: set[str] = set()
    routes: dict[tuple[str, str, str], str] = {}
    encoded_vm = quote(vm, safe="")
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("every focused operation must be an object")
        operation_id = operation.get("operationId")
        method = operation.get("method")
        target = operation.get("path")
        if (
            operation_id not in EXPECTED_OPERATIONS
            or method != EXPECTED_OPERATIONS[operation_id]
            or not isinstance(target, str)
            or target.count("{vm}") != 1
            or not target.startswith("/api/")
        ):
            raise ValueError("the focused contract names an unexpected operation")
        if operation_id in seen:
            raise ValueError("the focused contract repeats an operation")
        seen.add(operation_id)

        rendered = target.replace("{vm}", encoded_vm)
        split = urlsplit(rendered)
        route = (method, split.path, split.query)
        if route in routes:
            raise ValueError("focused contract routes must be unique")
        routes[route] = operation_id

    if seen != set(EXPECTED_OPERATIONS):
        raise ValueError("the focused contract operation set is incomplete")

    scheme = contract.get("securitySchemes", {}).get("api_key_auth")
    if scheme != {
        "type": "apiKey",
        "in": "header",
        "name": "vmware-api-session-id",
    }:
        raise ValueError("the focused contract has an unexpected security scheme")
    return routes


def positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE "
            "SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_object(Path(sys.argv[3]))
    scenario = load_object(Path(sys.argv[4]))

    vm = scenario.get("vm")
    initial_token = scenario.get("initial_token")
    refreshed_token = scenario.get("refreshed_token")
    if not isinstance(vm, str) or not vm:
        raise ValueError("scenario vm must be a non-empty string")
    if not isinstance(initial_token, str) or not initial_token:
        raise ValueError("scenario initial_token must be a non-empty string")
    if (
        not isinstance(refreshed_token, str)
        or not refreshed_token
        or refreshed_token == initial_token
    ):
        raise ValueError("scenario refreshed_token must be non-empty and distinct")
    cpu_count = positive_integer(scenario.get("cpu_count"), "cpu_count")
    memory_mib = positive_integer(scenario.get("memory_mib"), "memory_mib")
    expired_message = scenario.get("expired_message")
    if not isinstance(expired_message, str) or not expired_message:
        raise ValueError("scenario expired_message must be a non-empty string")

    routes = contract_routes(contract, vm)
    expected_bodies = {
        "Vcenter.Vm.Hardware.Cpu_update": json.dumps(
            {"count": cpu_count},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
        "Vcenter.Vm.Hardware.Memory_update": json.dumps(
            {"size_mib": memory_mib},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
    }
    state = {"phase": 0}
    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def send_json(self, status: int, value: dict) -> None:
            body = json.dumps(
                value, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def handle_contract_request(self) -> None:
            split = urlsplit(self.path)
            content_length_text = self.headers.get("Content-Length", "0")
            try:
                content_length = int(content_length_text or "0")
            except ValueError:
                content_length = -1
            body = (
                self.rfile.read(content_length)
                if content_length > 0
                else b""
            )
            operation_id = routes.get(
                (self.command, split.path, split.query)
            )
            session_token = self.headers.get("vmware-api-session-id")

            request_json = None
            if body:
                try:
                    request_json = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    request_json = None

            status = 404
            response: dict | None = {
                "error_type": "NOT_FOUND",
                "messages": [],
            }

            relevant_headers_valid = (
                self.headers.get("Accept") == "application/json"
                and self.headers.get("Authorization") is None
            )
            shape_valid = False
            if operation_id in expected_bodies:
                shape_valid = (
                    relevant_headers_valid
                    and self.headers.get("Content-Type") == "application/json"
                    and body == expected_bodies[operation_id]
                )
            elif operation_id == "Vcenter.Vm.Power_start":
                shape_valid = (
                    relevant_headers_valid
                    and self.headers.get("Content-Type") is None
                    and body == b""
                )

            with state_lock:
                phase_before = state["phase"]
                if operation_id is not None and not shape_valid:
                    status = 400
                    response = {
                        "error_type": "INVALID_ARGUMENT",
                        "messages": [],
                    }
                elif (
                    operation_id == "Vcenter.Vm.Hardware.Cpu_update"
                    and phase_before == 0
                    and session_token == initial_token
                ):
                    status = 204
                    response = None
                    state["phase"] = 1
                elif (
                    operation_id == "Vcenter.Vm.Hardware.Memory_update"
                    and phase_before == 1
                    and session_token == initial_token
                ):
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [
                            {
                                "args": [],
                                "default_message": expired_message,
                                "id": "com.vmware.vapi.std.errors.unauthenticated",
                            }
                        ],
                    }
                    state["phase"] = 2
                elif (
                    operation_id == "Vcenter.Vm.Hardware.Memory_update"
                    and phase_before == 2
                    and session_token == refreshed_token
                ):
                    status = 204
                    response = None
                    state["phase"] = 3
                elif (
                    operation_id == "Vcenter.Vm.Power_start"
                    and phase_before == 3
                    and session_token == refreshed_token
                ):
                    status = 204
                    response = None
                    state["phase"] = 4
                elif operation_id is not None and session_token not in {
                    initial_token,
                    refreshed_token,
                }:
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [],
                    }
                elif operation_id is not None:
                    status = 409
                    response = {
                        "error_type": "ALREADY_IN_DESIRED_STATE",
                        "messages": [],
                    }

                entry = {
                    "sequence": phase_before,
                    "operationId": operation_id,
                    "method": self.command,
                    "rawTarget": self.path,
                    "path": split.path,
                    "rawQuery": split.query,
                    "vmwareApiSessionId": session_token,
                    "authorization": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                    "contentType": self.headers.get("Content-Type"),
                    "contentLength": len(body),
                    "bodyHex": body.hex(),
                    "bodyJson": request_json,
                    "headerNames": [
                        name.lower() for name, _value in self.headers.items()
                    ],
                    "status": status,
                }
                append_log(log_file, entry)

            if response is None:
                self.send_empty(status)
            else:
                self.send_json(status, response)

        do_GET = handle_contract_request
        do_PATCH = handle_contract_request
        do_POST = handle_contract_request
        do_PUT = handle_contract_request
        do_DELETE = handle_contract_request

    log_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.parent.mkdir(parents=True, exist_ok=True)
    write_fsynced(log_file, "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    write_fsynced(port_file, str(server.server_port))
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
