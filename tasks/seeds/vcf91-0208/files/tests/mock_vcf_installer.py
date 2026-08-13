"""Contract-pinned loopback server used by the protected acceptance check."""

from __future__ import annotations

import contextlib
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RequestRecord:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes
    operation_id: str | None


@dataclass
class Scenario:
    result_status: str
    complete_after: int | None = 1
    validate_response: str = "json"
    validation_id: str = "123e4567-e89b-42d3-a456-556642440000"
    poll_count: int = 0
    deployment_count: int = 0
    request_log: list[RequestRecord] = field(default_factory=list)


class ContractMockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, contract_path: Path, scenario: Scenario):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.contract = contract
        self.scenario = scenario
        self.routes = tuple(self._compile_route(operation) for operation in contract["operations"].values())
        super().__init__(("127.0.0.1", 0), ContractRequestHandler)

    @staticmethod
    def _compile_route(operation: dict[str, object]) -> tuple[str, re.Pattern[str], str]:
        path = str(operation["path"])
        escaped = re.escape(path)
        pattern = re.sub(r"\\\{[^{}]+\\\}", r"[^/]+", escaped)
        return str(operation["method"]), re.compile(f"^{pattern}$"), str(operation["operationId"])

    def operation_for(self, method: str, target: str) -> str | None:
        path = urlsplit(target).path
        for route_method, route_pattern, operation_id in self.routes:
            if method == route_method and route_pattern.fullmatch(path):
                return operation_id
        return None

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"


class ContractRequestHandler(BaseHTTPRequestHandler):
    server: ContractMockServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        operation_id = self.server.operation_for(self.command, self.path)
        self.server.scenario.request_log.append(
            RequestRecord(
                method=self.command,
                target=self.path,
                headers={name.lower(): value for name, value in self.headers.items()},
                body=body,
                operation_id=operation_id,
            )
        )

        if operation_id is None:
            self._json_response(404, {"message": "operation is outside the pinned contract"})
            return

        if body:
            try:
                json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json_response(400, {"message": "request body is not JSON"})
                return

        if operation_id == "validateSddcSpec":
            if self.server.scenario.validate_response == "http_error":
                self._json_response(503, {"message": "validation service unavailable"})
            elif self.server.scenario.validate_response == "malformed_json":
                self._raw_response(202, b"{not-json", "application/json")
            elif self.server.scenario.validate_response == "json_array":
                self._raw_response(202, b"[]", "application/json")
            elif self.server.scenario.validate_response == "disconnect":
                self.close_connection = True
            else:
                self._json_response(202, self._validation("IN_PROGRESS", "UNKNOWN"))
        elif operation_id == "getSddcSpecValidation":
            self.server.scenario.poll_count += 1
            complete_after = self.server.scenario.complete_after
            if complete_after is None or self.server.scenario.poll_count < complete_after:
                self._json_response(200, self._validation("IN_PROGRESS", "UNKNOWN"))
            else:
                self._json_response(200, self._validation("COMPLETED", self.server.scenario.result_status))
        elif operation_id == "deploySddc":
            self.server.scenario.deployment_count += 1
            self._json_response(
                202,
                {
                    "id": "deployment-chi01-m01",
                    "name": "chi01-m01",
                    "status": "IN_PROGRESS",
                    "creationTimestamp": "2026-08-02T12:00:00Z",
                },
            )
        else:  # The contract and dispatcher must evolve together.
            self._json_response(500, {"message": "unimplemented contract operation"})

    def _validation(self, execution_status: str, result_status: str) -> dict[str, object]:
        return {
            "id": self.server.scenario.validation_id,
            "description": "VCF Installer specification validation",
            "executionStatus": execution_status,
            "resultStatus": result_status,
            "validationChecks": [],
        }

    def _json_response(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._raw_response(status, data, "application/json")

    def _raw_response(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextlib.contextmanager
def running_mock(
    contract_path: Path,
    result_status: str,
    *,
    complete_after: int | None = 1,
    validate_response: str = "json",
) -> Iterator[ContractMockServer]:
    scenario = Scenario(
        result_status=result_status,
        complete_after=complete_after,
        validate_response=validate_response,
    )
    server = ContractMockServer(contract_path, scenario)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
