#!/usr/bin/env python3
"""Contract-pinned loopback fixture for one vCenter Automation operation."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def append_log(path: Path, entry: dict) -> None:
    payload = json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_json(sys.argv[3])
    scenario = load_json(sys.argv[4])

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("contract must name exactly one operation")
    operation = operations[0]
    if operation.get("operationId") != "Vcenter.Authorization.Roles_list":
        raise ValueError("unexpected contract operationId")
    if operation.get("method") != "GET":
        raise ValueError("contract operation must be GET")
    allowed_path = operation.get("path")
    if not isinstance(allowed_path, str) or not allowed_path.startswith("/api/"):
        raise ValueError("contract operation path is invalid")

    page_size = scenario["page_size"]
    old_token = scenario["old_token"]
    fresh_token = scenario["fresh_token"]
    expiry_marker = scenario["expiry_marker"]
    pages = scenario["pages"]
    page_by_marker = {
        page.get("incoming_marker"): page
        for page in pages
    }
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
            operation_match = self.command == "GET" and split.path == allowed_path
            status = 404
            response: dict = {
                "error_type": "NOT_FOUND",
                "messages": [],
            }

            query = parse_qs(
                split.query,
                keep_blank_values=True,
                strict_parsing=False,
            )
            marker_values = query.get("marker")
            incoming_marker = marker_values[0] if marker_values else None
            api_session_id = self.headers.get("vmware-api-session-id")

            if operation_match:
                allowed_query_keys = {"page_size", "marker"}
                exact_keys = set(query)
                valid_page_size = query.get("page_size") == [str(page_size)]
                valid_marker = (
                    marker_values is None
                    or (
                        len(marker_values) == 1
                        and isinstance(incoming_marker, str)
                        and incoming_marker != ""
                    )
                )
                if (
                    exact_keys - allowed_query_keys
                    or not valid_page_size
                    or not valid_marker
                    or incoming_marker not in page_by_marker
                ):
                    status = 400
                    response = {
                        "error_type": "INVALID_ARGUMENT",
                        "messages": [],
                    }
                elif api_session_id == old_token:
                    if incoming_marker == expiry_marker:
                        status = 401
                        response = {
                            "error_type": "UNAUTHENTICATED",
                            "messages": [],
                        }
                    elif incoming_marker is None:
                        status = 200
                        response = {
                            "items": page_by_marker[None]["items"],
                            "marker": page_by_marker[None]["outgoing_marker"],
                        }
                    else:
                        status = 401
                        response = {
                            "error_type": "UNAUTHENTICATED",
                            "messages": [],
                        }
                elif api_session_id == fresh_token:
                    page = page_by_marker[incoming_marker]
                    status = 200
                    response = {"items": page["items"]}
                    outgoing = page.get("outgoing_marker")
                    if outgoing is not None:
                        response["marker"] = outgoing
                else:
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [],
                    }

            entry = {
                "operationId": (
                    operation["operationId"] if operation_match else None
                ),
                "method": self.command,
                "rawTarget": self.path,
                "path": split.path,
                "rawQuery": split.query,
                "query": query,
                "vmwareApiSessionId": api_session_id,
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
