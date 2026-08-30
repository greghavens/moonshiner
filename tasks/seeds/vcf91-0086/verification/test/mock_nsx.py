#!/usr/bin/env python3
"""Contract-pinned loopback NSX Policy fixture used only by the verifier."""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class ContractMock(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        contract_path: Path,
        alarms_path: Path,
        observations_path: Path,
        request_log: Path,
    ) -> None:
        contract = load_json(contract_path)
        if not isinstance(contract, dict):
            raise ValueError("contract must be a JSON object")
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations must be an array")

        named = {
            operation.get("operationId"): operation
            for operation in operations
            if isinstance(operation, dict)
        }
        if set(named) != {"ListAlarms", "ListTraceflowObservations"}:
            raise ValueError("mock only supports the two contract operations")
        if any(operation.get("method") != "GET" for operation in named.values()):
            raise ValueError("mock contract operations must be GET")

        base_path = contract.get("basePath")
        if base_path != "/policy/api/v1":
            raise ValueError("unexpected contract base path")
        self.alarm_target = base_path + named["ListAlarms"]["path"]
        self.observation_template = (
            base_path + named["ListTraceflowObservations"]["path"]
        )

        alarms = load_json(alarms_path)
        observations = load_json(observations_path)
        self._validate_fixture(
            alarms,
            expected_schema="PolicyAlarmResourceListResult",
            operation=named["ListAlarms"],
        )
        self._validate_fixture(
            observations,
            expected_schema="TraceflowObservationListResult",
            operation=named["ListTraceflowObservations"],
        )

        self.responses = {
            "ListAlarms": json.dumps(
                alarms, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
            "ListTraceflowObservations": json.dumps(
                observations, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
        }
        self.request_log = request_log
        super().__init__(("127.0.0.1", 0), MockHandler)

    @staticmethod
    def _validate_fixture(
        payload: object,
        *,
        expected_schema: str,
        operation: dict[str, object],
    ) -> None:
        success = operation.get("success_response")
        if not isinstance(success, dict) or success.get("schema") != expected_schema:
            raise ValueError("fixture schema is not pinned to the contract operation")
        resource_types = success.get("resource_types")
        if resource_types is None:
            resource_types = [success.get("resource_type")]
        if (
            not isinstance(resource_types, list)
            or not resource_types
            or any(not isinstance(item, str) for item in resource_types)
        ):
            raise ValueError("contract response must name its resource types")
        allowed_types = set(resource_types)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("fixture must contain a results array")
        for item in payload["results"]:
            if not isinstance(item, dict) or item.get("resource_type") not in allowed_types:
                raise ValueError("fixture result has a resource_type outside the contract")

    def append_request(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        entry = {
            "method": handler.command,
            "target": handler.path,
            "headers": {
                name.lower(): value for name, value in handler.headers.items()
            },
            "body": body.decode("utf-8"),
        }
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
        fd = os.open(
            self.request_log,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def operation_for(self, raw_target: str) -> str | None:
        split = urlsplit(raw_target)
        if split.query or split.fragment:
            return None
        if split.path == self.alarm_target:
            return "ListAlarms"

        prefix, suffix = self.observation_template.split("{traceflow-id}")
        if not split.path.startswith(prefix) or not split.path.endswith(suffix):
            return None
        encoded_id = split.path[len(prefix) : len(split.path) - len(suffix)]
        if not encoded_id or "/" in encoded_id:
            return None
        if unquote(encoded_id) != "tf incident/42":
            return None
        return "ListTraceflowObservations"


class MockHandler(BaseHTTPRequestHandler):
    server: ContractMock

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def _handle(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        self.server.append_request(self, body)

        operation = self.server.operation_for(self.path)
        if self.command != "GET" or operation is None:
            payload = b'{"error":"route is not in the pinned contract"}'
            self.send_response(404)
        else:
            payload = self.server.responses[operation]
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--alarms", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    server = ContractMock(
        args.contract,
        args.alarms,
        args.observations,
        args.request_log,
    )
    args.port_file.write_text(
        str(server.server_address[1]),
        encoding="ascii",
    )
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
