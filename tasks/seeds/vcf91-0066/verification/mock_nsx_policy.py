#!/usr/bin/env python3
"""Loopback NSX Policy mock whose routes come only from the pinned contract."""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
COLLECTOR_OPERATION = "GetFirewallIdentityStoreEventLogServer"
EVENTS_OPERATION = "GetUserLoginEvents"


def compile_path(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^{}]+\})", template)
    expression = "".join(
        r"[^/]+" if part.startswith("{") else re.escape(part)
        for part in parts
    )
    return re.compile(rf"^{expression}$")


def load_routes() -> list[tuple[str, str, re.Pattern[str]]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    expected = [COLLECTOR_OPERATION, EVENTS_OPERATION]
    if not isinstance(operations, dict) or list(operations) != expected:
        raise RuntimeError("contract must name exactly the two IDFW evidence operations")
    if contract.get("basePath") != "/policy/api/v1":
        raise RuntimeError("unexpected contract basePath")

    routes: list[tuple[str, str, re.Pattern[str]]] = []
    for operation_id, operation in operations.items():
        if (
            not isinstance(operation, dict)
            or operation.get("operationId") != operation_id
            or operation.get("method") != "GET"
            or not isinstance(operation.get("path"), str)
        ):
            raise RuntimeError(f"invalid contract operation {operation_id}")
        routes.append(
            (
                operation_id,
                "GET",
                compile_path(contract["basePath"] + operation["path"]),
            )
        )
    return routes


ROUTES = load_routes()


class RuntimeState:
    def __init__(self, log_file: Path, scenario_file: Path):
        scenario = json.loads(scenario_file.read_text(encoding="utf-8"))
        if not isinstance(scenario, dict):
            raise ValueError("runtime scenario must be a JSON object")
        self.scenario = scenario
        self.log_file = log_file

    def append(self, value: dict[str, object]) -> None:
        with self.log_file.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def response_for(self, operation_id: str) -> tuple[int, object]:
        if operation_id == COLLECTOR_OPERATION:
            status = self.scenario.get("collector_http_status", 200)
            body = self.scenario.get("collector")
        elif operation_id == EVENTS_OPERATION:
            status = self.scenario.get("events_http_status", 200)
            body = self.scenario.get("user_stats")
        else:
            raise AssertionError("route is not present in the pinned contract")
        if isinstance(status, bool) or not isinstance(status, int):
            return 500, {
                "error_code": 50066,
                "error_message": "invalid runtime status",
                "module_name": "contract-mock",
            }
        return status, body


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractPinnedNsxPolicyMock/1"
    sys_version = ""

    @property
    def state(self) -> RuntimeState:
        return self.server.runtime_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            length = 0
        return self.rfile.read(length)

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def record(
        self,
        operation_id: str | None,
        body: bytes,
        status: int,
    ) -> None:
        self.state.append(
            {
                "operationId": operation_id,
                "method": self.command,
                "raw_target": self.path,
                "headers": {
                    name.lower(): value for name, value in self.headers.items()
                },
                "body_utf8": body.decode("utf-8", errors="replace"),
                "status": status,
            }
        )

    def not_found(self) -> None:
        body = self.read_body()
        self.record(None, body, 404)
        self.send_json(
            404,
            {
                "error_code": 40466,
                "error_message": "operation is not in the pinned contract",
                "module_name": "contract-mock",
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        operation_id = next(
            (
                operation
                for operation, method, route in ROUTES
                if method == "GET" and route.fullmatch(target.path)
            ),
            None,
        )
        if operation_id is None or target.fragment:
            self.not_found()
            return

        body = self.read_body()
        status, response = self.state.response_for(operation_id)
        self.record(operation_id, body, status)
        self.send_json(status, response)

    do_POST = not_found
    do_PUT = not_found
    do_PATCH = not_found
    do_DELETE = not_found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--scenario-file", required=True, type=Path)
    args = parser.parse_args()

    args.log_file.write_text("", encoding="utf-8")
    state = RuntimeState(args.log_file, args.scenario_file)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.runtime_state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
