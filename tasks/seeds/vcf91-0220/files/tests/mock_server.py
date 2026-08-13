#!/usr/bin/env python3
"""Loopback-only mock for the three operations named by docs/contract.json."""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


SUCCESS_VALIDATION_ID = "11111111-1111-4111-8111-111111111111"
FAILURE_VALIDATION_ID = "22222222-2222-4222-8222-222222222222"
TASK_ID = "33333333-3333-4333-8333-333333333333"
TIMEOUT_VALIDATION_ID = "44444444-4444-4444-8444-444444444444"


def load_routes(contract_path: Path) -> dict[tuple[str, str], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes = {
        (operation["method"], operation["path"]): operation["operationId"]
        for operation in contract["operations"]
    }
    expected = {
        ("POST", "/v1/sddcs/validations"): "validateSddcSpec",
        ("GET", "/v1/sddcs/validations/{id}"): "getSddcSpecValidation",
        ("POST", "/v1/sddcs"): "deploySddc",
    }
    if routes != expected:
        raise RuntimeError("mock contract does not contain the pinned operation set")
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, scenario: str, log_path: Path, routes: dict[tuple[str, str], str]):
        super().__init__(("127.0.0.1", 0), Handler)
        self.scenario = scenario
        self.log_path = log_path
        self.routes = routes
        self.poll_count = 0

    def append_log(self, entry: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        split = urlsplit(self.path)
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        operation_id = self._operation_id(method, split.path)
        self.server.append_log(
            {
                "method": method,
                "rawPath": self.path,
                "path": split.path,
                "query": split.query,
                "operationId": operation_id,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "headerItems": [
                    [key.lower(), value] for key, value in self.headers.items()
                ],
                "bodyUtf8": body.decode("utf-8"),
                "bodyHex": body.hex(),
            }
        )

        if operation_id is None:
            self._json_response(404, {"error": "operation not in contract"})
        elif operation_id == "validateSddcSpec":
            if self.server.scenario == "http-error":
                self._json_response(500, {"error": "validation unavailable"})
                return
            if self.server.scenario == "immediate-success":
                self._json_response(
                    200,
                    {
                        "id": SUCCESS_VALIDATION_ID,
                        "description": "installation precheck",
                        "executionStatus": "COMPLETED",
                        "resultStatus": "SUCCEEDED",
                    },
                )
                return
            if self.server.scenario == "immediate-failure":
                self._json_response(
                    202,
                    {
                        "id": FAILURE_VALIDATION_ID,
                        "description": "installation precheck",
                        "executionStatus": "FAILED",
                        "resultStatus": "UNKNOWN",
                    },
                )
                return
            if self.server.scenario.startswith("success-optionals") or self.server.scenario == "success":
                validation_id = SUCCESS_VALIDATION_ID
            elif self.server.scenario == "failure":
                validation_id = FAILURE_VALIDATION_ID
            elif self.server.scenario == "poll-error":
                validation_id = TIMEOUT_VALIDATION_ID
            else:
                validation_id = TIMEOUT_VALIDATION_ID
            self._json_response(
                202,
                {
                    "id": validation_id,
                    "description": "installation precheck",
                    "executionStatus": "IN_PROGRESS",
                    "resultStatus": "UNKNOWN",
                },
            )
        elif operation_id == "getSddcSpecValidation":
            self.server.poll_count += 1
            if self.server.scenario == "poll-error":
                self._json_response(500, {"error": "poll unavailable"})
                return
            if self.server.scenario == "timeout":
                execution_status, result_status = "IN_PROGRESS", "UNKNOWN"
                validation_id = TIMEOUT_VALIDATION_ID
            elif (
                self.server.scenario == "success"
                or self.server.scenario.startswith("success-optionals")
            ) and self.server.poll_count == 1:
                execution_status, result_status = "IN_PROGRESS", "UNKNOWN"
                validation_id = SUCCESS_VALIDATION_ID
            elif self.server.scenario == "success" or self.server.scenario.startswith(
                "success-optionals"
            ):
                execution_status, result_status = "COMPLETED", "SUCCEEDED"
                validation_id = SUCCESS_VALIDATION_ID
            else:
                execution_status, result_status = "COMPLETED", "FAILED"
                validation_id = FAILURE_VALIDATION_ID
            self._json_response(
                200,
                {
                    "id": validation_id,
                    "description": "installation precheck",
                    "executionStatus": execution_status,
                    "resultStatus": result_status,
                },
            )
        elif operation_id == "deploySddc":
            if not (
                self.server.scenario in ("success", "immediate-success")
                or self.server.scenario.startswith("success-optionals")
            ):
                self._json_response(409, {"error": "precheck failed"})
            else:
                self._json_response(
                    202,
                    {
                        "id": TASK_ID,
                        "status": "IN_PROGRESS",
                        "creationTimestamp": "2026-08-03T12:00:00Z",
                    },
                )

    def _operation_id(self, method: str, path: str) -> str | None:
        direct = self.server.routes.get((method, path))
        if direct is not None:
            return direct
        template_operation = self.server.routes.get(
            (method, "/v1/sddcs/validations/{id}")
        )
        if template_operation and re.fullmatch(
            r"/v1/sddcs/validations/[0-9a-fA-F-]{36}", path
        ):
            return template_operation
        return None

    def _json_response(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=(
            "success",
            "success-optionals-true",
            "success-optionals-false",
            "immediate-success",
            "immediate-failure",
            "failure",
            "timeout",
            "http-error",
            "poll-error",
        ),
        required=True,
    )
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    server = ContractServer(args.scenario, args.log, routes)
    args.ready.write_text(str(server.server_address[1]), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
