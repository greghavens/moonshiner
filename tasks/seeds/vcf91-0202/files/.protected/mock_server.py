#!/usr/bin/env python3
"""Contract-pinned loopback VCF Installer used by protected verification."""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATION_IDS = ["validateSddcSpec", "deploySddc"]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"runtime scenario {name} is invalid")
    return value


def load_routes(contract_path: Path) -> list[Route]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    if source.get("repositoryCommitSha") != PINNED_COMMIT:
        raise RuntimeError("contract repository commit is not pinned")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract specification path is not pinned")
    operations = contract.get("operations", [])
    if [item.get("operationId") for item in operations] != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")
    routes = [
        Route(item["operationId"], item["method"].upper(), item["path"])
        for item in operations
    ]
    if [(route.method, route.path) for route in routes] != [
        ("POST", "/v1/sddcs/validations"),
        ("POST", "/v1/sddcs"),
    ]:
        raise RuntimeError("contract route projection changed")
    return routes


class MockState:
    def __init__(
        self,
        routes: list[Route],
        request_log: Path,
        scenario: dict[str, Any],
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        self.token = require_text(scenario, "token")
        self.task_id = require_text(scenario, "taskId")
        self.passed_validation_id = require_text(scenario, "passedValidationId")
        self.rejected_cases = scenario.get("rejectedCases")
        self.passed_spec = scenario.get("passedSpec")
        if (
            not isinstance(self.rejected_cases, list)
            or not self.rejected_cases
            or not all(isinstance(item, dict) for item in self.rejected_cases)
            or not isinstance(self.passed_spec, dict)
        ):
            raise RuntimeError("runtime scenario specifications are invalid")
        self.successful_precheck_seen = False
        self.deployment_count = 0
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> Route | None:
        return next(
            (
                route
                for route in self.routes
                if route.method == method and route.path == path
            ),
            None,
        )

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, ContractHandler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        route = self.server.state.match(self.command, target.path)
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        if route is None:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif route.operation_id == "validateSddcSpec":
            status, response = self._validate_sddc(target.query, body)
        elif route.operation_id == "deploySddc":
            status, response = self._deploy_sddc(target.query, body)
        else:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )

        header_values = {
            name.lower(): self.headers.get_all(name) or []
            for name in self.headers.keys()
        }
        self.server.state.record(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "headerValues": header_values,
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    def _common_request_error(self, raw_query: str, body: bytes) -> tuple[int, Any] | None:
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query string must be absent")
        if self.headers.get("Authorization") != f"Bearer {self.server.state.token}":
            return 403, error_body("AUTHORIZATION", "unexpected bearer token")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if media_type.lower() != "application/json":
            return 415, error_body("MEDIA_TYPE", "request must be JSON")
        if not body:
            return 400, error_body("WIRE_SHAPE", "request body must be present")
        return None

    @staticmethod
    def _decode_body(body: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _validate_sddc(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        common_error = self._common_request_error(raw_query, body)
        if common_error:
            return common_error
        value = self._decode_body(body)
        state = self.server.state
        for rejected_case in state.rejected_cases:
            if value == rejected_case.get("spec"):
                response = {
                    "id": require_text(rejected_case, "validationId"),
                    "description": "Protected deployment precheck",
                }
                for name in ("executionStatus", "resultStatus"):
                    if name in rejected_case:
                        response[name] = rejected_case[name]
                return 200, response
        if value == state.passed_spec:
            state.successful_precheck_seen = True
            return 200, {
                "id": state.passed_validation_id,
                "description": "Protected deployment precheck",
                "executionStatus": " completed ",
                "resultStatus": " succeeded ",
            }
        return 400, error_body(
            "WIRE_SHAPE", "body is not an exact protected SddcSpec"
        )

    def _deploy_sddc(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        common_error = self._common_request_error(raw_query, body)
        if common_error:
            return common_error
        state = self.server.state
        value = self._decode_body(body)
        if not state.successful_precheck_seen:
            return 409, error_body(
                "PRECHECK_NOT_PASSED", "deployment was attempted before a passed precheck"
            )
        if value != state.passed_spec:
            return 400, error_body(
                "WIRE_SHAPE", "deployment did not reuse the validated SddcSpec"
            )
        if state.deployment_count:
            return 409, error_body("DUPLICATE_DEPLOYMENT", "deployment was repeated")
        state.deployment_count += 1
        return 202, {
            "id": state.task_id,
            "name": "Protected validated deployment",
            "deploymentType": "VCF",
            "status": "IN_PROGRESS",
            "creationTimestamp": "2026-08-02T18:00:00Z",
        }

    def _send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"errorCode": code, "message": message, "arguments": []}


def write_port(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(str(port))
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        raise SystemExit(
            "usage: mock_server.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )
    port_file, log_file, contract_file, scenario_file = map(Path, argv[1:])
    routes = load_routes(contract_file)
    state = MockState(routes, log_file, read_json(scenario_file))
    server = ContractServer(("127.0.0.1", 0), state)
    write_port(port_file, int(server.server_address[1]))
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
