#!/usr/bin/env python3
"""Contract-pinned loopback NSX Policy service for credential cutover tests.

Fixture values are supplied at runtime by the protected verifier. The service
exposes only the operation named by the focused contract and writes every
request to the supplied JSONL log path.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROUTE_TEMPLATE = "/policy/api/v1/infra/domains/{domain-id}/groups"


def load_contract(
    contract_path: Path,
) -> tuple[dict[tuple[str, str], str], re.Pattern[str]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    expected = {
        ("GET", ROUTE_TEMPLATE): "ListGroupForDomain",
    }
    routes = {
        (operation["method"], operation["path"]): operation["operationId"]
        for operation in operations
    }
    if routes != expected:
        raise RuntimeError(
            "mock contract must name exactly ListGroupForDomain"
        )

    operation = operations[0]
    parameter_names = [item["name"] for item in operation["parameters"]]
    if parameter_names != [
        "domain-id",
        "cursor",
        "include_mark_for_delete_objects",
        "included_fields",
        "member_types",
        "page_size",
        "sort_ascending",
        "sort_by",
    ]:
        raise RuntimeError("ListGroupForDomain parameter projection is incomplete")
    optional_parameters = operation["parameters"][1:]
    if not all(item.get("omitWhenUnset") is True for item in optional_parameters):
        raise RuntimeError("optional query omission contract is incomplete")

    template = re.escape(operation["path"]).replace(
        re.escape("{domain-id}"), r"(?P<domain>[^/]+)"
    )
    return routes, re.compile(rf"^{template}$")


class ContractServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: dict[tuple[str, str], str],
        route_pattern: re.Pattern[str],
        log_path: Path,
        release_path: Path,
        scenario: dict[str, str],
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.route_pattern = route_pattern
        self.log_path = log_path
        self.release_path = release_path
        self.scenario = scenario
        self.phase = "old"

    def record(self, record: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
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
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def request_record(
        self,
        operation_id: str | None,
        parsed_path: str,
        raw_query: str,
        body: bytes,
    ) -> dict[str, object]:
        return {
            "operationId": operation_id,
            "method": self.command,
            "path": parsed_path,
            "rawQuery": raw_query,
            "host": self.headers.get("Host"),
            "authorization": self.headers.get("Authorization"),
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "contentLength": int(self.headers.get("Content-Length", "0")),
            "bodyHex": body.hex(),
            "body": body.decode("utf-8", "replace"),
        }

    def do_GET(self) -> None:
        body = self.read_body()
        parsed = urlsplit(self.path)
        match = self.contract_server.route_pattern.fullmatch(parsed.path)
        operation_id = self.contract_server.routes.get(
            ("GET", ROUTE_TEMPLATE)
        )
        if match is None:
            operation_id = None
        self.contract_server.record(
            self.request_record(operation_id, parsed.path, parsed.query, body)
        )

        if match is None or operation_id != "ListGroupForDomain":
            self.send_json(404, {"error_message": "Operation not served"})
            return
        if parsed.query:
            self.send_json(
                400, {"error_message": "Query parameters are not allowed"}
            )
            return
        if body:
            self.send_json(400, {"error_message": "GET body is not allowed"})
            return
        scenario = self.contract_server.scenario
        if unquote(match.group("domain")) != scenario["domain_id"]:
            self.send_json(404, {"error_message": "Domain not found"})
            return

        phase = self.contract_server.phase
        username = scenario[f"{phase}_username"]
        password = scenario[f"{phase}_password"]
        expected_auth = "Basic " + base64.b64encode(
            f"{username}:{password}".encode("utf-8")
        ).decode("ascii")
        if self.headers.get("Authorization") != expected_auth:
            self.send_json(401, {"error_message": "Incorrect credential phase"})
            return

        if phase == "old":
            deadline = time.monotonic() + 15.0
            while not self.contract_server.release_path.exists():
                if time.monotonic() >= deadline:
                    self.send_json(
                        504, {"error_message": "Old request was not released"}
                    )
                    return
                time.sleep(0.01)
            self.contract_server.phase = "new"

        group_id = scenario[f"{phase}_group_id"]
        display_name = scenario[f"{phase}_display_name"]
        response = {
            "results": [
                {
                    "id": group_id,
                    "display_name": display_name,
                    "path": (
                        f"/infra/domains/{scenario['domain_id']}/groups/"
                        f"{group_id}"
                    ),
                    "resource_type": "Group",
                }
            ],
            "result_count": 1,
        }
        self.send_json(200, response)

    def reject_unserved_method(self) -> None:
        body = self.read_body()
        parsed = urlsplit(self.path)
        self.contract_server.record(
            self.request_record(None, parsed.path, parsed.query, body)
        )
        self.send_json(405, {"error_message": "Method not served"})

    do_POST = reject_unserved_method
    do_PUT = reject_unserved_method
    do_PATCH = reject_unserved_method
    do_DELETE = reject_unserved_method


def main() -> int:
    if len(sys.argv) != 6:
        print(
            "usage: mock_nsx_policy.py PORT_FILE LOG_FILE CONTRACT_FILE "
            "SCENARIO_FILE RELEASE_FILE",
            file=sys.stderr,
        )
        return 2

    port_path = Path(sys.argv[1])
    log_path = Path(sys.argv[2])
    contract_path = Path(sys.argv[3])
    scenario_path = Path(sys.argv[4])
    release_path = Path(sys.argv[5])
    routes, route_pattern = load_contract(contract_path)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes,
        route_pattern,
        log_path,
        release_path,
        scenario,
    )

    temporary = port_path.with_suffix(port_path.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
