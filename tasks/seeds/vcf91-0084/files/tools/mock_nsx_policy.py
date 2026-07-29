#!/usr/bin/env python3
"""Loopback NSX Policy mock whose complete route table comes from the contract."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


PRECHECK_ID = "GetTier1State"
MUTATION_ID = "PatchTier1"


def load_routes(contract_path: Path) -> tuple[str, dict[str, re.Pattern[str]]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, list):
        raise RuntimeError("contract operations must be an array")
    if [item.get("operationId") for item in operations] != [
        PRECHECK_ID,
        MUTATION_ID,
    ]:
        raise RuntimeError("contract must name only GetTier1State and PatchTier1")
    base_path = contract.get("basePath")
    if base_path != "/policy/api/v1":
        raise RuntimeError("unexpected NSX Policy basePath")

    routes: dict[str, re.Pattern[str]] = {}
    expected = {
        PRECHECK_ID: ("GET", "/infra/tier-1s/{tier-1-id}/state"),
        MUTATION_ID: ("PATCH", "/infra/tier-1s/{tier-1-id}"),
    }
    for operation in operations:
        operation_id = operation["operationId"]
        if (operation.get("method"), operation.get("path")) != expected[operation_id]:
            raise RuntimeError(f"unexpected contract for {operation_id}")
        escaped = re.escape(base_path + operation["path"])
        escaped = escaped.replace(
            re.escape("{tier-1-id}"),
            r"(?P<tier1_id>[^/]+)",
        )
        routes[operation_id] = re.compile(rf"^{escaped}$")
    return base_path, routes


def b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


class State:
    def __init__(
        self,
        contract_path: Path,
        scenario_path: Path,
        log_path: Path,
        effect_path: Path,
    ) -> None:
        _base_path, self.routes = load_routes(contract_path)
        fixture = json.loads(scenario_path.read_text(encoding="utf-8"))
        scenarios = fixture.get("scenarios")
        if not isinstance(scenarios, dict):
            raise RuntimeError("scenario fixture must contain a scenarios object")
        self.scenarios = scenarios
        self.log_path = log_path
        self.effect_path = effect_path
        self.mutation_count = 0
        log_path.write_text("", encoding="utf-8")
        effect_path.write_text("0\n", encoding="ascii")

    def scenario(self, encoded_id: str) -> dict[str, object] | None:
        value = self.scenarios.get(unquote(encoded_id))
        return value if isinstance(value, dict) else None

    def append_log(
        self,
        handler: BaseHTTPRequestHandler,
        operation_id: str,
        body: bytes,
        status: int,
    ) -> None:
        def header(name: str) -> str:
            return handler.headers.get(name, "")

        def count(name: str) -> int:
            return len(handler.headers.get_all(name, []))

        fields = [
            operation_id,
            handler.command,
            b64(handler.path),
            b64(header("Authorization")),
            str(count("Authorization")),
            b64(header("Accept")),
            str(count("Accept")),
            b64(header("Content-Type")),
            str(count("Content-Type")),
            b64(header("Content-Length")),
            b64(body.decode("utf-8", errors="replace")),
            str(status),
            str(self.mutation_count),
        ]
        with self.log_path.open("a", encoding="ascii", newline="\n") as stream:
            stream.write("\t".join(fields))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def record_effect(self) -> None:
        self.mutation_count += 1
        with self.effect_path.open("w", encoding="ascii", newline="\n") as stream:
            stream.write(f"{self.mutation_count}\n")
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractPinnedNsxPolicyMock/1"
    sys_version = ""

    @property
    def state(self) -> State:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(max(0, length))

    def send_json(self, status: int, value: object | None) -> None:
        if value is None:
            body = b""
        else:
            body = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        self.send_response(status)
        if value is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def not_found(self) -> None:
        body = self.read_body()
        self.state.append_log(self, "", body, 404)
        self.send_json(
            404,
            {
                "error_code": 40484,
                "error_message": "route is absent from the pinned contract",
                "module_name": "contract-mock",
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        match = self.state.routes[PRECHECK_ID].fullmatch(target.path)
        if match is None or target.fragment:
            self.not_found()
            return

        body = self.read_body()
        scenario = self.state.scenario(match.group("tier1_id"))
        precheck = scenario.get("precheck") if scenario is not None else None
        if not isinstance(precheck, dict):
            status = 404
            response: object | None = {
                "error_code": 40485,
                "error_message": "no fixture for Tier-1",
                "module_name": "contract-mock",
            }
        else:
            status_value = precheck.get("status")
            if isinstance(status_value, bool) or not isinstance(status_value, int):
                status = 500
                response = {
                    "error_code": 50084,
                    "error_message": "invalid precheck fixture",
                    "module_name": "contract-mock",
                }
            else:
                status = status_value
                response = precheck.get("body")
        self.state.append_log(self, PRECHECK_ID, body, status)
        self.send_json(status, response)

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        match = self.state.routes[MUTATION_ID].fullmatch(target.path)
        if match is None or target.query or target.fragment:
            self.not_found()
            return

        body = self.read_body()
        scenario = self.state.scenario(match.group("tier1_id"))
        mutation = scenario.get("mutation") if scenario is not None else None
        if not isinstance(mutation, dict):
            status = 409
            response: object | None = {
                "error_code": 40984,
                "error_message": "fixture does not permit mutation",
                "module_name": "contract-mock",
            }
        else:
            status_value = mutation.get("status")
            if isinstance(status_value, bool) or not isinstance(status_value, int):
                status = 500
                response = {
                    "error_code": 50085,
                    "error_message": "invalid mutation fixture",
                    "module_name": "contract-mock",
                }
            else:
                status = status_value
                response = mutation.get("body")
        if status == 200:
            self.state.record_effect()
        self.state.append_log(self, MUTATION_ID, body, status)
        self.send_json(status, response)

    do_DELETE = not_found
    do_POST = not_found
    do_PUT = not_found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--effects", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    args = parser.parse_args()

    state = State(args.contract, args.scenarios, args.log, args.effects)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.contract_state = state  # type: ignore[attr-defined]
    args.port_file.write_text(f"{server.server_port}\n", encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
