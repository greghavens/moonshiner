#!/usr/bin/env python3
"""Contract-pinned loopback fixture for a partial-failure vCenter workflow."""

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
EXPECTED_ORDER = list(EXPECTED_OPERATIONS)


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def compact_json(value: dict) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def append_log(path: Path, entry: dict) -> None:
    encoded = json.dumps(
        entry, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def contract_routes(contract: dict, scenario: dict) -> list[dict]:
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 3:
        raise ValueError("the focused contract must name exactly three operations")
    if [item.get("operationId") for item in operations] != EXPECTED_ORDER:
        raise ValueError("the focused contract operation order is invalid")

    security = contract.get("securitySchemes", {}).get("api_key_auth")
    if security != {
        "type": "apiKey",
        "in": "header",
        "name": "vmware-api-session-id",
    }:
        raise ValueError("the focused contract security scheme is invalid")

    vm = scenario["vm"]
    encoded_vm = quote(vm, safe="")
    expected_bodies = {
        "Vcenter.Vm.Hardware.Cpu_update": compact_json(
            {"count": scenario["cpu_count"]}
        ),
        "Vcenter.Vm.Hardware.Memory_update": compact_json(
            {"size_mib": scenario["memory_mib"]}
        ),
    }
    routes: list[dict] = []
    unique_routes: set[tuple[str, str, str]] = set()
    for index, operation in enumerate(operations):
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
        rendered = target.replace("{vm}", encoded_vm)
        split = urlsplit(rendered)
        route_key = (method, split.path, split.query)
        if route_key in unique_routes:
            raise ValueError("focused contract routes must be unique")
        unique_routes.add(route_key)
        body = expected_bodies.get(operation_id, b"")
        routes.append(
            {
                "index": index,
                "operationId": operation_id,
                "method": method,
                "path": split.path,
                "query": split.query,
                "rawTarget": rendered,
                "body": body,
                "contentType": (
                    "application/json" if operation_id in expected_bodies else None
                ),
            }
        )
    return routes


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

    token = scenario.get("session_token")
    vm = scenario.get("vm")
    if not isinstance(token, str) or not token:
        raise ValueError("scenario session_token must be a non-empty string")
    if not isinstance(vm, str) or not vm:
        raise ValueError("scenario vm must be a non-empty string")
    scenario["cpu_count"] = positive_integer(
        scenario.get("cpu_count"), "cpu_count"
    )
    scenario["memory_mib"] = positive_integer(
        scenario.get("memory_mib"), "memory_mib"
    )
    failure_message = scenario.get("power_error_message")
    if not isinstance(failure_message, str) or not failure_message:
        raise ValueError("scenario power_error_message must be non-empty")

    routes = contract_routes(contract, scenario)
    state = {"next_index": 0}
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
            body = compact_json(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def handle_request(self) -> None:
            split = urlsplit(self.path)
            try:
                content_length = int(
                    self.headers.get("Content-Length", "0") or "0"
                )
            except ValueError:
                content_length = -1
            body = (
                self.rfile.read(content_length)
                if content_length > 0
                else b""
            )
            route = next(
                (
                    item
                    for item in routes
                    if (
                        self.command,
                        split.path,
                        split.query,
                    )
                    == (item["method"], item["path"], item["query"])
                ),
                None,
            )

            with state_lock:
                status = 404
                response = {"error_type": "NOT_FOUND", "messages": []}
                sequence_valid = False
                request_valid = False
                if route is not None:
                    sequence_valid = route["index"] == state["next_index"]
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
                                    "default_message": failure_message,
                                    "id": "com.vmware.vcenter.power.unavailable",
                                }
                            ],
                        }
                        state["next_index"] += 1
                    else:
                        status = 204
                        response = {}
                        state["next_index"] += 1

                header_names = sorted(
                    {name.lower() for name in self.headers.keys()}
                )
                append_log(
                    log_file,
                    {
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
                        "headerNames": header_names,
                        "sessionHeaderCount": len(
                            self.headers.get_all("vmware-api-session-id") or []
                        ),
                        "authorizationHeaderCount": len(
                            self.headers.get_all("Authorization") or []
                        ),
                        "acceptHeaderCount": len(
                            self.headers.get_all("Accept") or []
                        ),
                        "contentTypeHeaderCount": len(
                            self.headers.get_all("Content-Type") or []
                        ),
                        "vmwareApiSessionId": self.headers.get(
                            "vmware-api-session-id"
                        ),
                        "authorization": self.headers.get("Authorization"),
                        "accept": self.headers.get("Accept"),
                        "contentType": self.headers.get("Content-Type"),
                        "declaredContentLength": self.headers.get(
                            "Content-Length"
                        ),
                        "contentLength": len(body),
                        "bodyHex": body.hex(),
                        "status": status,
                    },
                )

            if status == 204:
                self.send_empty(status)
            else:
                self.send_json(status, response)

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request
        do_PATCH = handle_request
        do_DELETE = handle_request

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
