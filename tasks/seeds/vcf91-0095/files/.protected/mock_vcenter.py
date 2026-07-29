#!/usr/bin/env python3
"""Contract-pinned loopback fixture for vCenter credential cutover."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


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


def compact_json(value: dict) -> bytes:
    return json.dumps(
        value, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


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
    if not isinstance(operation.get("operationId"), str):
        raise ValueError("the focused operation must name its operationId")
    if operation.get("method") != "GET":
        raise ValueError("the focused operation must use GET")
    allowed_path = operation.get("path")
    if not isinstance(allowed_path, str) or not allowed_path.startswith("/api/"):
        raise ValueError("the focused operation has an invalid API path")
    if operation.get("requestBody") is not False:
        raise ValueError("the focused operation must be bodyless")

    old_token = scenario["old_token"]
    new_token = scenario["new_token"]
    old_item = scenario["old_item"]
    new_item = scenario["new_item"]
    release_file = Path(scenario["release_file"])
    if not isinstance(old_token, str) or not old_token:
        raise ValueError("scenario old_token must be non-empty")
    if not isinstance(new_token, str) or not new_token:
        raise ValueError("scenario new_token must be non-empty")
    if old_token == new_token:
        raise ValueError("scenario session tokens must differ")
    if not isinstance(old_item, dict) or not isinstance(new_item, dict):
        raise ValueError("scenario role items must be objects")

    state_lock = threading.Lock()
    next_request = 0

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, value: dict) -> None:
            body = compact_json(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            nonlocal next_request

            split = urlsplit(self.path)
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(content_length) if content_length else b""
            operation_match = (
                self.command == operation["method"]
                and split.path == allowed_path
            )

            with state_lock:
                sequence_index = next_request
                expected_token = (
                    old_token if sequence_index == 0 else new_token
                )
                request_valid = (
                    operation_match
                    and sequence_index < 2
                    and self.path == allowed_path
                    and split.query == ""
                    and self.headers.get("vmware-api-session-id")
                    == expected_token
                    and self.headers.get("Accept") == "application/json"
                    and self.headers.get("Authorization") is None
                    and self.headers.get("Content-Type") is None
                    and body == b""
                )
                if not operation_match:
                    status = 404
                elif not request_valid:
                    status = 400
                else:
                    status = 200
                    next_request += 1

                entry = {
                    "operationId": (
                        operation["operationId"] if operation_match else None
                    ),
                    "sequenceIndex": sequence_index,
                    "requestValid": request_valid,
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
                append_log(log_file, entry)

            if status != 200:
                error_type = (
                    "NOT_FOUND" if status == 404 else "INVALID_ARGUMENT"
                )
                self._send_json(
                    status,
                    {"error_type": error_type, "messages": []},
                )
                return

            if sequence_index == 0:
                deadline = time.monotonic() + 20
                while not release_file.is_file():
                    if time.monotonic() >= deadline:
                        self._send_json(
                            503,
                            {
                                "error_type": "SERVICE_UNAVAILABLE",
                                "messages": [],
                            },
                        )
                        return
                    time.sleep(0.01)
                self._send_json(200, {"items": [old_item]})
                return

            self._send_json(200, {"items": [new_item]})

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
