#!/usr/bin/env python3
"""Contract-pinned loopback NSX Policy service for protected verification.

The service derives its entire route allow-list from docs/contract.json.
Fixture values and response statuses are supplied by the verifier at runtime.
Requests are recorded directly to an fsynced JSONL file; there is no log or
control HTTP endpoint.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATIONS = {
    (
        "PATCH",
        "/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}",
    ): "PatchGroupForDomain",
    (
        "PATCH",
        "/policy/api/v1/infra/domains/{domain-id}/security-policies/"
        "{security-policy-id}",
    ): "PatchSecurityPolicyForDomain",
}


def compile_routes(contract_path: Path) -> list[dict[str, Any]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    base_path = contract["basePath"].rstrip("/")
    actual = {
        (
            operation["method"],
            base_path + operation["path"],
        ): operation["operationId"]
        for operation in contract["operations"]
    }
    if actual != EXPECTED_OPERATIONS:
        raise RuntimeError(
            "mock contract must name only PatchGroupForDomain and "
            "PatchSecurityPolicyForDomain"
        )

    routes: list[dict[str, Any]] = []
    for operation in contract["operations"]:
        template = base_path + operation["path"]
        parts = re.split(r"(\{[^{}]+\})", template)
        pattern_parts: list[str] = []
        for part in parts:
            if part.startswith("{") and part.endswith("}"):
                name = part[1:-1].replace("-", "_")
                pattern_parts.append(rf"(?P<{name}>[^/?]+)")
            else:
                pattern_parts.append(re.escape(part))
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "path": template,
                "pattern": re.compile("^" + "".join(pattern_parts) + "$"),
                "responses": operation["responses"],
            }
        )
    return routes


class ContractServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, Any]],
        log_path: Path,
        scenario: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.scenario = scenario
        self.sequence = 0
        self.effects: list[str] = []

    def record(self, entry: dict[str, Any]) -> None:
        self.sequence += 1
        entry["sequence"] = self.sequence
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    entry,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def contract_server(self) -> ContractServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    def send_json(self, status: int, payload: object | None) -> None:
        if payload is None:
            body = b""
        else:
            body = json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def route_for(
        self, path: str, query: str
    ) -> tuple[dict[str, Any] | None, re.Match[str] | None]:
        if query:
            return None, None
        for route in self.contract_server.routes:
            if route["method"] != self.command:
                continue
            match = route["pattern"].fullmatch(path)
            if match is not None:
                return route, match
        return None, None

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        route, match = self.route_for(parsed.path, parsed.query)
        body = self.read_body()
        headers = {
            name.lower(): self.headers.get_all(name, [])
            for name in self.headers
        }
        self.contract_server.record(
            {
                "operationId": (
                    None if route is None else route["operationId"]
                ),
                "method": self.command,
                "path": parsed.path,
                "rawQuery": parsed.query,
                "headers": headers,
                "bodyBase64": base64.b64encode(body).decode("ascii"),
                "effectsBefore": list(self.contract_server.effects),
            }
        )

        if route is None or match is None:
            self.send_json(
                404,
                {
                    "error_code": 40400,
                    "error_message": "operation is not in the contract",
                },
            )
            return

        scenario = self.contract_server.scenario
        expected_auth = "Basic " + base64.b64encode(
            (
                str(scenario["username"])
                + ":"
                + str(scenario["password"])
            ).encode("utf-8")
        ).decode("ascii")
        if self.headers.get("Authorization") != expected_auth:
            self.send_json(
                403,
                {
                    "error_code": 40300,
                    "error_message": "authorization rejected",
                },
            )
            return
        if not self.headers.get("Accept", "").lower().startswith(
            "application/json"
        ):
            self.send_json(
                400,
                {
                    "error_code": 40001,
                    "error_message": "JSON Accept header required",
                },
            )
            return
        if not self.headers.get("Content-Type", "").lower().startswith(
            "application/json"
        ):
            self.send_json(
                400,
                {
                    "error_code": 40002,
                    "error_message": "JSON Content-Type required",
                },
            )
            return
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(
                400,
                {
                    "error_code": 40003,
                    "error_message": "malformed JSON",
                },
            )
            return
        if not isinstance(payload, dict):
            self.send_json(
                400,
                {
                    "error_code": 40004,
                    "error_message": "request body must be an object",
                },
            )
            return

        if unquote(match.group("domain_id")) != scenario["domain_id"]:
            self.send_json(
                404,
                {
                    "error_code": 40401,
                    "error_message": "domain not found",
                },
            )
            return

        operation_id = route["operationId"]
        if operation_id == "PatchGroupForDomain":
            if (
                unquote(match.group("group_id"))
                != scenario["group_id"]
            ):
                self.send_json(
                    404,
                    {
                        "error_code": 40402,
                        "error_message": "group not found",
                    },
                )
                return
            status = int(scenario["group_status"])
            error_code = int(scenario["group_error_code"])
            effect_name = "source-group"
        elif operation_id == "PatchSecurityPolicyForDomain":
            if (
                unquote(match.group("security_policy_id"))
                != scenario["security_policy_id"]
            ):
                self.send_json(
                    404,
                    {
                        "error_code": 40403,
                        "error_message": "security policy not found",
                    },
                )
                return
            status = int(scenario["policy_status"])
            error_code = int(scenario["policy_error_code"])
            effect_name = "security-policy"
        else:
            self.send_json(
                404,
                {
                    "error_code": 40404,
                    "error_message": "operation is not served",
                },
            )
            return

        if str(status) not in route["responses"]:
            self.send_json(
                500,
                {
                    "error_code": 50000,
                    "error_message": "scenario status is outside contract",
                },
            )
            return
        if status == 200:
            self.contract_server.effects.append(effect_name)
            self.send_json(200, None)
            return
        error_payload = {
            "error_message": "runtime fixture failure",
            "module_name": "policy",
        }
        include_error_code = bool(
            scenario[
                "group_include_error_code"
                if operation_id == "PatchGroupForDomain"
                else "policy_include_error_code"
            ]
        )
        if include_error_code:
            error_payload["error_code"] = error_code
        self.send_json(status, error_payload)

    def reject_unserved_method(self) -> None:
        parsed = urlsplit(self.path)
        body = self.read_body()
        self.contract_server.record(
            {
                "operationId": None,
                "method": self.command,
                "path": parsed.path,
                "rawQuery": parsed.query,
                "headers": {
                    name.lower(): self.headers.get_all(name, [])
                    for name in self.headers
                },
                "bodyBase64": base64.b64encode(body).decode("ascii"),
                "effectsBefore": list(self.contract_server.effects),
            }
        )
        self.send_json(
            405,
            {
                "error_code": 40500,
                "error_message": "method is not served",
            },
        )

    do_GET = reject_unserved_method
    do_POST = reject_unserved_method
    do_PUT = reject_unserved_method
    do_DELETE = reject_unserved_method


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: mock_nsx_policy.py PORT_FILE LOG_FILE CONTRACT_FILE "
            "SCENARIO_FILE",
            file=sys.stderr,
        )
        return 2

    port_file = Path(sys.argv[1])
    log_path = Path(sys.argv[2])
    contract_path = Path(sys.argv[3])
    scenario_path = Path(sys.argv[4])
    routes = compile_routes(contract_path)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    required_scenario_keys = {
        "username",
        "password",
        "domain_id",
        "group_id",
        "security_policy_id",
        "group_status",
        "group_error_code",
        "group_include_error_code",
        "policy_status",
        "policy_error_code",
        "policy_include_error_code",
    }
    if set(scenario) != required_scenario_keys:
        raise RuntimeError("runtime scenario keys are incomplete")

    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        routes,
        log_path,
        scenario,
    )
    temporary = port_file.with_suffix(port_file.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
