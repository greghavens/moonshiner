#!/usr/bin/env python3
"""Contract-pinned loopback fixture for one declarative vCenter mutation."""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


OPERATION_ID = "Vcenter.Vm.Hardware.Cpu_update"
SPEC_PATH = "/vcenter/vm/{vm}/hardware/cpu"
METHOD = "PATCH"
BEHAVIORS = {"disconnect_once", "disconnect_always", "http_error"}


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


def append_fsynced(path: Path, value: dict) -> None:
    text = json.dumps(
        value,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def require_positive_int64(value: object, name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > 2**63 - 1
    ):
        raise ValueError(f"{name} must be a positive signed-int64")
    return value


def contract_route(contract: dict, vm: str) -> tuple[str, str]:
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("focused contract must contain exactly one operation")
    operation = operations[0]
    if (
        not isinstance(operation, dict)
        or operation.get("operationId") != OPERATION_ID
        or operation.get("method") != METHOD
        or operation.get("specPathItem") != SPEC_PATH
        or operation.get("path") != f"/api{SPEC_PATH}"
        or operation.get("security") != ["api_key_auth"]
    ):
        raise ValueError("focused operation projection is not the expected one")

    parameters = operation.get("parameters")
    if parameters != [
        {
            "name": "vm",
            "in": "path",
            "required": True,
            "type": "string",
            "resourceType": "VirtualMachine",
        }
    ]:
        raise ValueError("focused path parameter projection changed")
    request_body = operation.get("requestBody")
    if request_body != {
        "required": True,
        "contentType": "application/json",
        "schema": "Vcenter.Vm.Hardware.Cpu.UpdateSpec",
    }:
        raise ValueError("focused request body projection changed")
    if operation.get("responses", {}).get("204") != {"content": False}:
        raise ValueError("focused success response projection changed")

    security = contract.get("securitySchemes", {}).get("api_key_auth")
    if security != {
        "type": "apiKey",
        "in": "header",
        "name": "vmware-api-session-id",
    }:
        raise ValueError("focused security projection changed")

    schema = contract.get("schemas", {}).get(
        "Vcenter.Vm.Hardware.Cpu.UpdateSpec"
    )
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or list(properties) != [
        "count",
        "cores_per_socket",
        "hot_add_enabled",
        "hot_remove_enabled",
    ]:
        raise ValueError("focused CPU update schema projection changed")
    for name in properties:
        if properties[name].get("required") is not False:
            raise ValueError("CPU update properties must be optional")

    path = operation["path"].replace("{vm}", quote(vm, safe=""))
    split = urlsplit(path)
    if split.query or split.fragment:
        raise ValueError("focused route must not contain a query or fragment")
    return operation["method"], split.path


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
    token = scenario.get("session_token")
    error_secret = scenario.get("error_secret")
    behavior = scenario.get("behavior")
    if not isinstance(vm, str) or not vm:
        raise ValueError("scenario vm must be a non-empty string")
    if not isinstance(token, str) or not token:
        raise ValueError("scenario token must be a non-empty string")
    if not isinstance(error_secret, str) or not error_secret:
        raise ValueError("scenario error secret must be a non-empty string")
    if behavior not in BEHAVIORS:
        raise ValueError("scenario behavior is not supported")
    initial_count = require_positive_int64(
        scenario.get("initial_count"), "initial_count"
    )
    desired_count = require_positive_int64(
        scenario.get("desired_count"), "desired_count"
    )
    if desired_count == initial_count:
        raise ValueError("scenario CPU counts must be distinct")

    route = contract_route(contract, vm)
    expected_body = json.dumps(
        {"count": desired_count},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    state = {
        "request_count": 0,
        "current_count": initial_count,
        "effect_count": 0,
    }
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
                value,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def disconnect_without_response(self) -> None:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()

        def handle_contract_request(self) -> None:
            split = urlsplit(self.path)
            lengths = self.headers.get_all("Content-Length", [])
            try:
                content_length = (
                    int(lengths[0]) if len(lengths) == 1 else -1
                )
            except ValueError:
                content_length = -1
            if 0 <= content_length <= 1024 * 1024:
                body = self.rfile.read(content_length)
            else:
                body = b""

            operation_id = (
                OPERATION_ID
                if (self.command, split.path) == route and not split.query
                else None
            )
            try:
                body_json = json.loads(body.decode("utf-8")) if body else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                body_json = None

            relevant_headers_valid = (
                self.headers.get("Accept") == "application/json"
                and self.headers.get("Content-Type") == "application/json"
                and self.headers.get("vmware-api-session-id") == token
                and self.headers.get("Authorization") is None
                and self.headers.get("Transfer-Encoding") is None
                and len(self.headers.get_all("vmware-api-session-id", [])) == 1
                and len(self.headers.get_all("Accept", [])) == 1
                and len(self.headers.get_all("Content-Type", [])) == 1
                and len(lengths) == 1
                and content_length == len(expected_body)
            )
            shape_valid = relevant_headers_valid and body == expected_body

            with state_lock:
                request_index = state["request_count"]
                state["request_count"] += 1
                status: int | None
                response: dict | None = None
                response_action = "response"
                applied_change = False

                if operation_id is None:
                    status = 404
                    response = {
                        "error_type": "NOT_FOUND",
                        "messages": [],
                    }
                elif not shape_valid:
                    status = 400
                    response = {
                        "error_type": "INVALID_ARGUMENT",
                        "messages": [],
                    }
                elif behavior == "http_error":
                    status = 503
                    response = {
                        "error_type": "SERVICE_UNAVAILABLE",
                        "messages": [
                            {
                                "args": [],
                                "default_message": error_secret,
                                "id": (
                                    "com.vmware.vapi.std.errors."
                                    "service_unavailable"
                                ),
                            }
                        ],
                    }
                else:
                    if state["current_count"] != desired_count:
                        state["current_count"] = desired_count
                        state["effect_count"] += 1
                        applied_change = True
                    must_disconnect = (
                        behavior == "disconnect_always"
                        or (
                            behavior == "disconnect_once"
                            and request_index == 0
                        )
                    )
                    if must_disconnect:
                        status = None
                        response_action = "disconnect"
                    else:
                        status = 204

                entry = {
                    "requestIndex": request_index,
                    "operationId": operation_id,
                    "method": self.command,
                    "rawTarget": self.path,
                    "rawPath": split.path,
                    "rawQuery": split.query,
                    "vmwareApiSessionId": self.headers.get(
                        "vmware-api-session-id"
                    ),
                    "authorization": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                    "contentType": self.headers.get("Content-Type"),
                    "transferEncoding": self.headers.get("Transfer-Encoding"),
                    "declaredContentLength": (
                        content_length if content_length >= 0 else None
                    ),
                    "bodyLength": len(body),
                    "bodyHex": body.hex(),
                    "bodyJson": body_json,
                    "headerNames": [
                        name.lower() for name, _value in self.headers.items()
                    ],
                    "responseAction": response_action,
                    "status": status,
                    "appliedChange": applied_change,
                    "effectCount": state["effect_count"],
                    "currentCount": state["current_count"],
                }
                append_fsynced(log_file, entry)

            if response_action == "disconnect":
                self.disconnect_without_response()
            elif response is None:
                self.send_empty(status)
            else:
                self.send_json(status, response)

        do_PATCH = handle_contract_request
        do_GET = handle_contract_request
        do_POST = handle_contract_request
        do_PUT = handle_contract_request
        do_DELETE = handle_contract_request

    log_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.parent.mkdir(parents=True, exist_ok=True)
    write_fsynced(log_file, "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True

    def stop_server(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    write_fsynced(port_file, str(server.server_address[1]) + "\n")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
