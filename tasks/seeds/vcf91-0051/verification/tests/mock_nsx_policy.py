#!/usr/bin/env python3
"""Contract-pinned loopback service for ListGroupForDomain.

The verifier generates all credentials, resource identifiers, cursors, and
group records at runtime. This process exposes no test-control or log route;
requests are written directly to the JSONL path supplied on the command line.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def load_contract(contract_path: Path) -> tuple[str, re.Pattern[str]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if len(operations) != 1:
        raise RuntimeError("mock contract must name exactly one operation")
    operation = operations[0]
    if (
        operation["operationId"] != "ListGroupForDomain"
        or operation["method"] != "GET"
        or operation["path"]
        != "/policy/api/v1/infra/domains/{domain-id}/groups"
    ):
        raise RuntimeError("mock contract operation does not match the pinned route")

    names = [item["name"] for item in operation["parameters"]]
    if names != [
        "domain-id",
        "cursor",
        "include_mark_for_delete_objects",
        "included_fields",
        "member_types",
        "page_size",
        "sort_ascending",
        "sort_by",
    ]:
        raise RuntimeError("mock contract parameter projection is incomplete")

    template = re.escape(operation["path"]).replace(
        re.escape("{domain-id}"), r"(?P<domain>[^/]+)"
    )
    return operation["operationId"], re.compile(rf"^{template}$")


class ContractServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        operation_id: str,
        route_pattern: re.Pattern[str],
        log_path: Path,
        scenario: dict[str, object],
    ) -> None:
        super().__init__(address, handler)
        self.operation_id = operation_id
        self.route_pattern = route_pattern
        self.log_path = log_path
        self.scenario = scenario

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

    def record_request(
        self,
        operation_id: str | None,
        path: str,
        raw_query: str,
        body: bytes,
    ) -> None:
        self.contract_server.record(
            {
                "operationId": operation_id,
                "method": self.command,
                "path": path,
                "rawQuery": raw_query,
                "query": {
                    key: values
                    for key, values in sorted(
                        parse_qs(raw_query, keep_blank_values=True).items()
                    )
                },
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "contentType": self.headers.get("Content-Type"),
                "contentLength": int(self.headers.get("Content-Length", "0")),
                "body": body.decode("utf-8", "replace"),
            }
        )

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        match = self.contract_server.route_pattern.fullmatch(parsed.path)
        operation_id = (
            self.contract_server.operation_id if match is not None else None
        )
        body = self.read_body()
        self.record_request(operation_id, parsed.path, parsed.query, body)

        if match is None:
            self.send_json(404, {"error_message": "Operation not served"})
            return

        scenario = self.contract_server.scenario
        expected_auth = "Basic " + base64.b64encode(
            f"{scenario['username']}:{scenario['password']}".encode("utf-8")
        ).decode("ascii")
        if self.headers.get("Authorization") != expected_auth:
            self.send_json(401, {"error_message": "Basic authorization required"})
            return
        if unquote(match.group("domain")) != scenario["domain_id"]:
            self.send_json(404, {"error_message": "Domain not found"})
            return
        if body:
            self.send_json(400, {"error_message": "GET body is not allowed"})
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) - {"cursor", "page_size"}:
            self.send_json(400, {"error_message": "Unexpected optional query"})
            return
        if query.get("page_size") != [str(scenario["page_size"])]:
            self.send_json(400, {"error_message": "page_size is required"})
            return

        incoming = query.get("cursor")
        incoming_cursor = incoming[0] if incoming and len(incoming) == 1 else None
        pages = scenario["pages"]
        assert isinstance(pages, list)
        page = next(
            (
                item
                for item in pages
                if isinstance(item, dict)
                and item["incoming_cursor"] == incoming_cursor
            ),
            None,
        )
        if page is None:
            self.send_json(400, {"error_message": "Unknown cursor"})
            return

        response: dict[str, object] = {"results": page["results"]}
        if incoming_cursor is None:
            response["result_count"] = sum(
                len(item["results"])
                for item in pages
                if isinstance(item, dict)
            )
        if page["outgoing_cursor"] is not None:
            response["cursor"] = page["outgoing_cursor"]
        self.send_json(200, response)

    def reject_unserved_method(self) -> None:
        parsed = urlsplit(self.path)
        body = self.read_body()
        self.record_request(None, parsed.path, parsed.query, body)
        self.send_json(405, {"error_message": "Method not served"})

    do_POST = reject_unserved_method
    do_PUT = reject_unserved_method
    do_PATCH = reject_unserved_method
    do_DELETE = reject_unserved_method


def main() -> int:
    if len(sys.argv) != 5:
        print(
            "usage: mock_nsx_policy.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE",
            file=sys.stderr,
        )
        return 2

    port_file = Path(sys.argv[1])
    log_path = Path(sys.argv[2])
    contract_path = Path(sys.argv[3])
    scenario_path = Path(sys.argv[4])
    operation_id, route_pattern = load_contract(contract_path)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        operation_id,
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
