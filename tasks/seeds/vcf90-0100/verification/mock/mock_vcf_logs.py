#!/usr/bin/env python3
"""Contract-pinned loopback mock for the two VCF Operations for Logs calls."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = ["POST_sessions", "PATCH_log-forwarder-id"]


def load_routes(contract_path: Path) -> tuple[str, list[dict[str, Any]]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    operation_ids = [operation["operationId"] for operation in operations]
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise ValueError(f"mock contract operationIds changed: {operation_ids!r}")

    routes: list[dict[str, Any]] = []
    base_path = contract["basePath"]
    for operation in operations:
        route_path = base_path + operation["path"]
        pattern = re.escape(route_path).replace(re.escape("{id}"), r"(?P<id>[^/]+)")
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "pattern": re.compile(rf"^{pattern}$"),
            }
        )
    return base_path, routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], routes: list[dict[str, Any]], request_log: Path):
        super().__init__(address, ContractHandler)
        self.routes = routes
        self.request_log = request_log
        self.sequence = 0
        self.patch_count = 0

    def identify(self, method: str, path: str) -> tuple[str | None, re.Match[str] | None]:
        for route in self.routes:
            match = route["pattern"].fullmatch(path)
            if route["method"] == method and match:
                return route["operationId"], match
        return None, None

    def record(self, operation_id: str | None, method: str, raw_path: str, body: bytes, headers: Any) -> None:
        self.sequence += 1
        entry = {
            "sequence": self.sequence,
            "operationId": operation_id,
            "method": method,
            "rawPath": raw_path,
            "headers": {key.lower(): value for key, value in headers.items()},
            "body": body.decode("utf-8", errors="strict"),
        }
        with self.request_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n")
            handle.flush()


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def _handle(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        parsed_path = urlsplit(self.path)
        operation_id, _ = self.server.identify(self.command, parsed_path.path)
        self.server.record(operation_id, self.command, self.path, body, self.headers)

        if operation_id is None:
            self._json_response(404, {"errorMessage": "No contract operation for request."})
            return

        if operation_id == "POST_sessions":
            self._json_response(
                200,
                {
                    "userId": "90000000-0000-4000-8000-000000000000",
                    "sessionId": "session-token/9.0+mock",
                    "ttl": 1800,
                },
            )
            return

        if self.headers.get("Authorization") != "Bearer session-token/9.0+mock":
            self._json_response(401, {"errorMessage": "Invalid session ID"})
            return

        self.server.patch_count += 1
        if self.server.patch_count == 1:
            self._empty_response(204)
        elif self.server.patch_count == 2:
            self._json_response(
                400,
                {
                    "errorMessage": 'Forwarder "dr" is unreachable.',
                    "errorCode": "FIELD_ERROR",
                    "errorDetails": {"reason": "connection refused"},
                },
            )
        else:
            self._json_response(
                200,
                {
                    "name": "checkout-primary",
                    "host": "logs-primary.example.com",
                    "port": 6514,
                    "protocol": "SYSLOG",
                    "sslEnabled": True,
                    "workerCount": 6,
                    "diskCacheSize": 1000000000,
                    "tags": {},
                    "filter": "app=checkout",
                    "forwardComplementaryFields": False,
                    "id": "33333333-3333-4333-8333-333333333333",
                },
            )

    def _empty_response(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--request-log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    args = parser.parse_args()

    _, routes = load_routes(args.contract)
    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), routes, args.request_log)
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
