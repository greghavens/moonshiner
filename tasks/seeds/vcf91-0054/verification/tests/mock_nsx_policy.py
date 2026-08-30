#!/usr/bin/env python3
"""Contract-pinned loopback service for a guarded NSX Policy group update.

All fixture values are supplied at runtime by the protected verifier. The
service exposes no control or log endpoint; it writes every request directly
to the JSONL path supplied on the command line.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_contract(
    contract_path: Path,
) -> tuple[dict[tuple[str, str], str], re.Pattern[str]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    expected = {
        (
            "GET",
            "/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}",
        ): "ReadGroupForDomain",
        (
            "PUT",
            "/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}",
        ): "UpdateGroupForDomain",
    }
    routes = {
        (operation["method"], operation["path"]): operation["operationId"]
        for operation in operations
    }
    if routes != expected:
        raise RuntimeError(
            "mock contract must name exactly ReadGroupForDomain and "
            "UpdateGroupForDomain"
        )

    read_parameters = [item["name"] for item in operations[0]["parameters"]]
    update_parameters = [item["name"] for item in operations[1]["parameters"]]
    if read_parameters != ["domain-id", "group-id"]:
        raise RuntimeError("read operation parameter projection is incomplete")
    if update_parameters != ["domain-id", "group-id", "Group"]:
        raise RuntimeError("update operation parameter projection is incomplete")

    template = re.escape(operations[0]["path"])
    template = template.replace(
        re.escape("{domain-id}"), r"(?P<domain>[^/]+)"
    ).replace(re.escape("{group-id}"), r"(?P<group>[^/]+)")
    return routes, re.compile(rf"^{template}$")


class ContractServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: dict[tuple[str, str], str],
        route_pattern: re.Pattern[str],
        log_path: Path,
        scenario: dict[str, object],
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.route_pattern = route_pattern
        self.log_path = log_path
        self.scenario = scenario

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
        self.end_headers()
        self.wfile.write(body)

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
            "authorization": self.headers.get("Authorization"),
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "contentLength": int(self.headers.get("Content-Length", "0")),
            "bodyHex": body.hex(),
            "body": body.decode("utf-8", "replace"),
        }

    def authenticate_and_match(
        self, body: bytes
    ) -> tuple[re.Match[str] | None, str | None]:
        parsed = urlsplit(self.path)
        match = self.contract_server.route_pattern.fullmatch(parsed.path)
        template = (
            "/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}"
        )
        operation_id = self.contract_server.routes.get((self.command, template))
        if match is None:
            operation_id = None
        self.contract_server.record(
            self.request_record(operation_id, parsed.path, parsed.query, body)
        )

        if match is None or operation_id is None:
            self.send_json(404, {"error_message": "Operation not served"})
            return None, None

        scenario = self.contract_server.scenario
        expected_auth = "Basic " + base64.b64encode(
            f"{scenario['username']}:{scenario['password']}".encode("utf-8")
        ).decode("ascii")
        if self.headers.get("Authorization") != expected_auth:
            self.send_json(401, {"error_message": "Basic authorization required"})
            return None, None
        if urlsplit(self.path).query:
            self.send_json(400, {"error_message": "Query parameters are not allowed"})
            return None, None
        if (
            unquote(match.group("domain")) != scenario["domain_id"]
            or unquote(match.group("group")) != scenario["group_id"]
        ):
            self.send_json(404, {"error_message": "Group not found"})
            return None, None
        return match, operation_id

    def current_group(self) -> dict[str, object]:
        scenario = self.contract_server.scenario
        group: dict[str, object] = {
            "_revision": scenario["current_revision"],
            "display_name": scenario["current_display_name"],
            "id": scenario["group_id"],
            "resource_type": "Group",
        }
        return group

    def do_GET(self) -> None:
        body = self.read_body()
        match, operation_id = self.authenticate_and_match(body)
        if match is None or operation_id is None:
            return
        if operation_id != "ReadGroupForDomain":
            self.send_json(405, {"error_message": "Method not served"})
            return
        if body:
            self.send_json(400, {"error_message": "GET body is not allowed"})
            return
        self.send_json(200, self.current_group())

    def do_PUT(self) -> None:
        body = self.read_body()
        match, operation_id = self.authenticate_and_match(body)
        if match is None or operation_id is None:
            return
        if operation_id != "UpdateGroupForDomain":
            self.send_json(405, {"error_message": "Method not served"})
            return
        if not self.headers.get("Content-Type", "").lower().startswith(
            "application/json"
        ):
            self.send_json(415, {"error_message": "JSON content type required"})
            return
        try:
            request = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error_message": "Malformed JSON"})
            return
        if not isinstance(request, dict):
            self.send_json(400, {"error_message": "Group body must be an object"})
            return

        scenario = self.contract_server.scenario
        if request.get("_revision") != scenario["current_revision"]:
            self.send_json(412, {"error_message": "Stale group revision"})
            return
        if not isinstance(request.get("display_name"), str):
            self.send_json(400, {"error_message": "display_name is required"})
            return

        scenario["current_display_name"] = request["display_name"]
        scenario["current_revision"] = int(scenario["current_revision"]) + 1
        response = self.current_group()
        if "description" in request:
            response["description"] = request["description"]
        self.send_json(200, response)

    def reject_unserved_method(self) -> None:
        parsed = urlsplit(self.path)
        body = self.read_body()
        self.contract_server.record(
            self.request_record(None, parsed.path, parsed.query, body)
        )
        self.send_json(405, {"error_message": "Method not served"})

    do_POST = reject_unserved_method
    do_PATCH = reject_unserved_method
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
    routes, route_pattern = load_contract(contract_path)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes,
        route_pattern,
        log_path,
        scenario,
    )

    temporary = port_file.with_suffix(port_file.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_file)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
