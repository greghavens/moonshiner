#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the focused vCenter collection."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


def load_object(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def append_log(path: Path, entry: dict) -> None:
    encoded = json.dumps(
        entry, separators=(",", ":"), ensure_ascii=False
    ) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_object(sys.argv[3])
    scenario = load_object(sys.argv[4])

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("the focused contract must name exactly one operation")
    operation = operations[0]
    if operation.get("operationId") != "Vcenter.Authorization.Roles_list":
        raise ValueError("the focused contract names an unexpected operationId")
    if operation.get("method") != "GET":
        raise ValueError("the focused operation must use GET")
    allowed_path = operation.get("path")
    if not isinstance(allowed_path, str) or not allowed_path.startswith("/api/"):
        raise ValueError("the focused operation has an invalid API path")

    token = scenario["session_token"]
    page_size = scenario["page_size"]
    pages = scenario["pages"]
    if not isinstance(token, str) or not token:
        raise ValueError("scenario session_token must be a non-empty string")
    if not isinstance(page_size, int) or page_size < 1:
        raise ValueError("scenario page_size must be positive")
    if not isinstance(pages, list) or not pages:
        raise ValueError("scenario pages must be a non-empty array")

    page_by_marker = {}
    for page in pages:
        incoming = page.get("incoming_marker")
        if incoming in page_by_marker:
            raise ValueError("scenario incoming markers must be unique")
        page_by_marker[incoming] = page

    state_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, value: dict) -> None:
            body = json.dumps(
                value, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            split = urlsplit(self.path)
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length else b""
            marker = None
            marker_known = True
            raw_query = f"page_size={quote(str(page_size), safe='')}"
            query_parts = split.query.split("&") if split.query else []
            if len(query_parts) == 2 and query_parts[1].startswith("marker="):
                encoded_marker = query_parts[1][len("marker=") :]
                marker_known = False
                for candidate in page_by_marker:
                    if (
                        isinstance(candidate, str)
                        and quote(candidate, safe="") == encoded_marker
                    ):
                        marker = candidate
                        marker_known = True
                        break
                raw_query += "&marker=" + encoded_marker
            elif len(query_parts) != 1:
                marker_known = False

            operation_match = (
                self.command == operation["method"]
                and split.path == allowed_path
            )
            exact_target = operation_match and split.query == raw_query
            request_valid = (
                exact_target
                and marker_known
                and marker in page_by_marker
                and self.headers.get("vmware-api-session-id") == token
                and self.headers.get("Accept") == "application/json"
                and self.headers.get("Authorization") is None
                and self.headers.get("Content-Type") is None
                and len(body) == 0
            )

            status = 404
            response: dict = {
                "error_type": "NOT_FOUND",
                "messages": [],
            }
            if operation_match and not request_valid:
                status = 400
                response = {
                    "error_type": "INVALID_ARGUMENT",
                    "messages": [],
                }
            elif request_valid:
                page = page_by_marker[marker]
                status = 200
                response = {"items": page["items"]}
                outgoing = page.get("outgoing_marker")
                if outgoing is not None:
                    response["marker"] = outgoing

            entry = {
                "operationId": (
                    operation["operationId"] if operation_match else None
                ),
                "method": self.command,
                "rawTarget": self.path,
                "path": split.path,
                "rawQuery": split.query,
                "vmwareApiSessionId": self.headers.get(
                    "vmware-api-session-id"
                ),
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "contentType": self.headers.get("Content-Type"),
                "contentLength": len(body),
                "bodyHex": body.hex(),
                "status": status,
            }
            with state_lock:
                append_log(log_file, entry)
            self._send_json(status, response)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle

    log_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
