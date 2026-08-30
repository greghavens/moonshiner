#!/usr/bin/env python3
"""Contract-pinned loopback service for NSX realization failure evidence.

The verifier creates every credential, identifier, cursor, alarm, and message
at runtime. This process exposes no test-control or request-log HTTP route; the
complete request log is written directly to the JSONL path supplied on launch.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


EXPECTED_OPERATIONS = {
    "ReadIntentStatus": {
        "method": "GET",
        "path": "/policy/api/v1/infra/realized-state/status",
        "parameters": [
            "include_enforced_status",
            "intent_path",
            "site_path",
        ],
    },
    "ListAlarms": {
        "method": "GET",
        "path": "/policy/api/v1/infra/realized-state/alarms",
        "parameters": [
            "cursor",
            "included_fields",
            "page_size",
            "sort_ascending",
            "sort_by",
        ],
    },
}


def load_contract(contract_path: Path) -> dict[str, dict[str, object]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if [operation["operationId"] for operation in operations] != [
        "ReadIntentStatus",
        "ListAlarms",
    ]:
        raise RuntimeError("mock contract must name exactly the two evidence operations")

    routes: dict[str, dict[str, object]] = {}
    for operation in operations:
        operation_id = operation["operationId"]
        expected = EXPECTED_OPERATIONS[operation_id]
        if (
            operation["method"] != expected["method"]
            or operation["path"] != expected["path"]
            or [item["name"] for item in operation["parameters"]]
            != expected["parameters"]
        ):
            raise RuntimeError(f"incomplete contract projection for {operation_id}")
        routes[operation["path"]] = {
            "operationId": operation_id,
            "method": operation["method"],
        }
    return routes


class ContractServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: dict[str, dict[str, object]],
        log_path: Path,
        scenario: dict[str, object],
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
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
        route = self.contract_server.routes.get(parsed.path)
        operation_id = (
            str(route["operationId"])
            if route is not None and route["method"] == "GET"
            else None
        )
        body = self.read_body()
        self.record_request(operation_id, parsed.path, parsed.query, body)

        if operation_id is None:
            self.send_json(404, {"error_message": "Operation not served"})
            return

        scenario = self.contract_server.scenario
        if self.headers.get("Authorization") != f"Bearer {scenario['token']}":
            self.send_json(401, {"error_message": "Bearer authorization required"})
            return
        if body:
            self.send_json(400, {"error_message": "GET body is not allowed"})
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        if operation_id == "ReadIntentStatus":
            self.handle_status(query)
        else:
            self.handle_alarms(query)

    def handle_status(self, query: dict[str, list[str]]) -> None:
        scenario = self.contract_server.scenario
        if set(query) != {"intent_path", "include_enforced_status"}:
            self.send_json(400, {"error_message": "Incorrect status query members"})
            return
        if query["intent_path"] != [str(scenario["intent_path"])]:
            self.send_json(404, {"error_message": "Intent not found"})
            return
        if query["include_enforced_status"] != ["true"]:
            self.send_json(400, {"error_message": "Enforced details are required"})
            return
        self.send_json(200, scenario["status"])

    def handle_alarms(self, query: dict[str, list[str]]) -> None:
        scenario = self.contract_server.scenario
        if set(query) - {"cursor", "page_size"}:
            self.send_json(400, {"error_message": "Unexpected optional alarm query"})
            return
        if query.get("page_size") != [str(scenario["page_size"])]:
            self.send_json(400, {"error_message": "page_size is required"})
            return

        cursor_values = query.get("cursor")
        if cursor_values is not None and len(cursor_values) != 1:
            self.send_json(400, {"error_message": "cursor must be singular"})
            return
        incoming_cursor = cursor_values[0] if cursor_values else None
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
    routes = load_contract(contract_path)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    log_path.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes,
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
