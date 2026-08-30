#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager for protected verification.

The HTTP surface is intentionally limited to the two operations named by
docs/contract.json plus the session calls the VMware SDK itself makes while
connecting and disconnecting (GET /v1/sddc-manager and
DELETE /v1/tokens/refresh-token). Requests are written to a JSONL file
supplied by the test; there is no extra log/control HTTP route. Domain
element order alternates on every collection response.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DUMMY_USERNAME = "svc-domain-inventory"
DUMMY_PASSWORD = "fixture-password"
ACCESS_TOKEN = "fixture-access-token"

DOMAINS = [
    {
        "id": "aa000000-0000-4000-8000-000000000300",
        "name": "Zulu-Compute",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": False,
    },
    {
        "id": "aa000000-0000-4000-8000-000000000220",
        "name": "alpha-analytics",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": True,
    },
    {
        "id": "aa000000-0000-4000-8000-000000000210",
        "name": "Bravo-Edge",
        "type": "VI",
        "status": "EXPANDING",
        "isManagementSsoDomain": False,
    },
    {
        "id": "aa000000-0000-4000-8000-000000000100",
        "name": "Alpha-Mgmt",
        "type": "MANAGEMENT",
        "status": "ACTIVE",
        "isManagementSsoDomain": True,
    },
    {
        "id": "aa000000-0000-4000-8000-000000000200",
        "name": "Bravo-Edge",
        "type": "VI",
        "status": "ACTIVE",
        "isManagementSsoDomain": False,
    },
]


def load_routes(contract_path: Path) -> dict[tuple[str, str], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    routes = {
        (entry["method"], entry["path"]): entry["operationId"]
        for entry in contract["operations"]
    }
    expected = {
        ("POST", "/v1/tokens"): "createToken",
        ("GET", "/v1/domains"): "getDomains",
    }
    if routes != expected:
        raise RuntimeError(
            "mock contract must name exactly createToken and getDomains")
    return routes


class ContractServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: dict[tuple[str, str], str],
        log_path: Path,
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.collection_responses = 0

    def record(self, record: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            stream.flush()


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

    def send_contract_error(self, status: int, code: str, message: str) -> None:
        self.send_json(
            status,
            {
                "errorCode": code,
                "message": message,
                "referenceToken": f"fixture-{status}",
            },
        )

    def request_record(
        self,
        operation_id: str | None,
        path: str,
        query: dict[str, list[str]],
        body: bytes,
    ) -> dict[str, object]:
        return {
            "operationId": operation_id,
            "method": self.command,
            "path": path,
            "query": {key: values for key, values in sorted(query.items())},
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "body": body.decode("utf-8", "replace"),
        }

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        route = (self.command, parsed.path)
        operation_id = self.contract_server.routes.get(route)
        body = self.read_body()
        record = self.request_record(
            operation_id, parsed.path, parse_qs(parsed.query), body)
        self.contract_server.record(record)

        if operation_id != "createToken":
            self.send_contract_error(404, "NOT_FOUND", "Operation not served")
            return
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_contract_error(400, "BAD_REQUEST", "JSON body required")
            return
        if (
            payload.get("username") != DUMMY_USERNAME
            or payload.get("password") != DUMMY_PASSWORD
        ):
            self.send_contract_error(
                400, "INVALID_CREDENTIALS", "Dummy credentials do not match")
            return
        self.send_json(
            201,
            {
                "accessToken": ACCESS_TOKEN,
                "refreshToken": {"id": "fixture-refresh-token"},
            },
        )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        route = (self.command, parsed.path)
        operation_id = self.contract_server.routes.get(route)
        query = parse_qs(parsed.query)

        if parsed.path == "/v1/sddc-manager":
            # Connect-VcfSddcManagerServer probes this appliance-info
            # endpoint after createToken; it is SDK session plumbing, not a
            # contract operation exercised by the task.
            record = self.request_record(None, parsed.path, query, b"")
            self.contract_server.record(record)
            if self.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
                self.send_contract_error(
                    401, "UNAUTHORIZED", "Bearer token required")
                return
            self.send_json(
                200,
                {
                    "id": "fixture-sddc-manager",
                    "fqdn": "127.0.0.1",
                    "version": "9.1.0.0",
                },
            )
            return

        if operation_id != "getDomains":
            record = self.request_record(operation_id, parsed.path, query, b"")
            self.contract_server.record(record)
            self.send_contract_error(404, "NOT_FOUND", "Operation not served")
            return
        if self.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            record = self.request_record(operation_id, parsed.path, query, b"")
            self.contract_server.record(record)
            self.send_contract_error(401, "UNAUTHORIZED", "Bearer token required")
            return

        try:
            page_size = int(query.get("pageSize", ["100"])[0])
            page_number = int(query.get("pageNumber", ["1"])[0])
        except ValueError:
            self.send_contract_error(
                400, "BAD_REQUEST", "Paging values must be integers")
            return
        if page_size <= 0 or page_number <= 0:
            self.send_contract_error(
                400, "BAD_REQUEST", "Paging values must be positive")
            return

        domains = list(DOMAINS)
        if "type" in query:
            requested_type = query["type"][0]
            domains = [
                domain for domain in domains
                if domain["type"] == requested_type
            ]

        offset = (page_number - 1) * page_size
        page_elements = domains[offset: offset + page_size]
        self.contract_server.collection_responses += 1
        if self.contract_server.collection_responses % 2 == 0:
            page_elements.reverse()

        total_elements = len(domains)
        total_pages = (
            (total_elements + page_size - 1) // page_size
            if total_elements else 0
        )
        record = self.request_record(
            operation_id, parsed.path, query, b"")
        record["responseElementIds"] = [
            element["id"] for element in page_elements
        ]
        self.contract_server.record(record)
        self.send_json(
            200,
            {
                "elements": page_elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": len(page_elements),
                    "totalElements": total_elements,
                    "totalPages": total_pages,
                },
            },
        )

    def do_PUT(self) -> None:
        self.reject_unserved_method()

    def do_PATCH(self) -> None:
        self.reject_unserved_method()

    def do_DELETE(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/v1/tokens/refresh-token":
            # Disconnect-VcfSddcManagerServer invalidates the refresh token
            # (spec operationId invalidateRefreshToken); SDK session plumbing.
            body = self.read_body()
            record = self.request_record(
                "invalidateRefreshToken", parsed.path,
                parse_qs(parsed.query), body)
            self.contract_server.record(record)
            self.send_json(200, {})
            return
        self.reject_unserved_method()

    def reject_unserved_method(self) -> None:
        parsed = urlsplit(self.path)
        body = self.read_body()
        record = self.request_record(
            None, parsed.path, parse_qs(parsed.query), body)
        self.contract_server.record(record)
        self.send_contract_error(405, "METHOD_NOT_ALLOWED", "Method not served")


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: mock_sddc_manager.py PORT_FILE LOG_FILE CONTRACT_FILE",
            file=sys.stderr,
        )
        return 2

    port_file = Path(sys.argv[1])
    log_path = Path(sys.argv[2])
    contract_path = Path(sys.argv[3])
    routes = load_routes(contract_path)
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, log_path)

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
