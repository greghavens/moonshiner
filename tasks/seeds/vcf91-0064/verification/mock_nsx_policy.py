#!/usr/bin/env python3
"""Loopback-only mock whose routes come from the pinned two-operation contract."""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
PRECHECK_OPERATION_ID = "GetTier1State"
MUTATION_OPERATION_ID = "PatchTier1"


def load_contract() -> tuple[str, dict[str, object], dict[str, object]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    expected_ids = [PRECHECK_OPERATION_ID, MUTATION_OPERATION_ID]
    if not isinstance(operations, dict) or list(operations) != expected_ids:
        raise RuntimeError("mock contract must name only GetTier1State and PatchTier1")

    precheck = operations[PRECHECK_OPERATION_ID]
    mutation = operations[MUTATION_OPERATION_ID]
    if (
        precheck.get("operationId") != PRECHECK_OPERATION_ID
        or precheck.get("method") != "GET"
        or precheck.get("path") != "/infra/tier-1s/{tier-1-id}/state"
    ):
        raise RuntimeError("unexpected GetTier1State contract")
    if (
        mutation.get("operationId") != MUTATION_OPERATION_ID
        or mutation.get("method") != "PATCH"
        or mutation.get("path") != "/infra/tier-1s/{tier-1-id}"
    ):
        raise RuntimeError("unexpected PatchTier1 contract")
    if contract.get("basePath") != "/policy/api/v1":
        raise RuntimeError("unexpected basePath contract")
    return contract["basePath"], precheck, mutation


def compile_route(base_path: str, operation: dict[str, object]) -> re.Pattern[str]:
    escaped = re.escape(base_path + str(operation["path"]))
    escaped = escaped.replace(
        re.escape("{tier-1-id}"), r"(?P<tier1_id>[^/]+)"
    )
    return re.compile(rf"^{escaped}$")


BASE_PATH, PRECHECK_OPERATION, MUTATION_OPERATION = load_contract()
PRECHECK_ROUTE = compile_route(BASE_PATH, PRECHECK_OPERATION)
MUTATION_ROUTE = compile_route(BASE_PATH, MUTATION_OPERATION)


class ContractState:
    def __init__(self, log_path: Path, config_path: Path):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        scenarios = config.get("scenarios")
        if not isinstance(scenarios, dict):
            raise ValueError("scenario config must contain an object named scenarios")
        self.scenarios = scenarios
        self.log_path = log_path
        self.mutation_count = 0

    def scenario(self, tier1_id: str) -> dict[str, object] | None:
        scenario = self.scenarios.get(tier1_id)
        return scenario if isinstance(scenario, dict) else None

    def append_log(self, record: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractPinnedNsxPolicyMock/1"
    sys_version = ""

    @property
    def state(self) -> ContractState:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            length = 0
        return self.rfile.read(length)

    def _headers(self) -> dict[str, str]:
        return {name.lower(): value for name, value in self.headers.items()}

    def _send(self, status: int, value: object | None) -> None:
        if value is None:
            body = b""
        else:
            body = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
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

    def _record(
        self,
        operation_id: str | None,
        body: bytes,
        status: int,
    ) -> None:
        self.state.append_log(
            {
                "operationId": operation_id,
                "method": self.command,
                "raw_target": self.path,
                "headers": self._headers(),
                "body_utf8": body.decode("utf-8", errors="replace"),
                "status": status,
                "mutation_count": self.state.mutation_count,
            }
        )

    def _not_found(self) -> None:
        body = self._read_body()
        self._record(None, body, 404)
        self._send(
            404,
            {
                "error_code": 40464,
                "error_message": "operation is not present in the pinned contract",
                "module_name": "contract-mock",
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        match = PRECHECK_ROUTE.fullmatch(target.path)
        if match is None or target.fragment:
            self._not_found()
            return

        body = self._read_body()
        tier1_id = unquote(match.group("tier1_id"))
        scenario = self.state.scenario(tier1_id)
        precheck = scenario.get("precheck") if scenario is not None else None
        if not isinstance(precheck, dict):
            status = 404
            response: object | None = {
                "error_code": 40465,
                "error_message": "no runtime scenario for Tier-1",
                "module_name": "contract-mock",
            }
        else:
            status = precheck.get("status")
            if isinstance(status, bool) or not isinstance(status, int):
                status = 500
                response = {
                    "error_code": 50064,
                    "error_message": "invalid runtime precheck scenario",
                    "module_name": "contract-mock",
                }
            else:
                response = precheck.get("body")

        self._record(PRECHECK_OPERATION_ID, body, status)
        self._send(status, response)

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        match = MUTATION_ROUTE.fullmatch(target.path)
        if match is None or target.query or target.fragment:
            self._not_found()
            return

        body = self._read_body()
        tier1_id = unquote(match.group("tier1_id"))
        scenario = self.state.scenario(tier1_id)
        mutation = scenario.get("mutation") if scenario is not None else None
        if not isinstance(mutation, dict):
            status = 500
            response: object | None = {
                "error_code": 50065,
                "error_message": "mutation was not enabled by the runtime scenario",
                "module_name": "contract-mock",
            }
        else:
            status = mutation.get("status")
            if isinstance(status, bool) or not isinstance(status, int):
                status = 500
                response = {
                    "error_code": 50066,
                    "error_message": "invalid runtime mutation scenario",
                    "module_name": "contract-mock",
                }
            else:
                response = mutation.get("body")

        if status == 200:
            self.state.mutation_count += 1
        self._record(MUTATION_OPERATION_ID, body, status)
        self._send(status, response)

    do_POST = _not_found
    do_PUT = _not_found
    do_DELETE = _not_found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--scenario-file", required=True, type=Path)
    args = parser.parse_args()

    args.log_file.write_text("", encoding="utf-8")
    state = ContractState(args.log_file, args.scenario_file)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.contract_state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
