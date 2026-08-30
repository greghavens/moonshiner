#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the protected acceptance test."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"


def load_routes(task_id: str) -> dict[tuple[str, str], str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    routes: dict[tuple[str, str], str] = {}
    for operation in contract["operations"]:
        path = operation["path"]
        if "{id}" in path:
            path = path.replace("{id}", task_id)
        routes[(operation["method"], path)] = operation["operationId"]
    return routes


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], log_path: Path, scenario: dict[str, object]):
        super().__init__(address, FixtureHandler)
        self.routes = load_routes(str(scenario["taskId"]))
        self.log_path = log_path
        self.scenario = scenario
        self.log_lock = threading.Lock()

    def append_request(self, item: dict[str, object]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FixtureServer

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _send_json(self, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        route_key = (self.command, split.path)
        operation_id = self.server.routes.get(route_key)
        raw_body = self._read_body()
        parsed_body: object | None = None
        if raw_body:
            try:
                parsed_body = json.loads(raw_body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_body = None

        self.server.append_request(
            {
                "method": self.command,
                "path": split.path,
                "query": split.query,
                "operationId": operation_id,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "rawBody": raw_body.decode("utf-8", errors="replace"),
                "jsonBody": parsed_body,
            }
        )

        is_version_probe = self.command == "GET" and split.path == "/v1/sddc-manager"

        if split.query:
            self._send_json(
                400,
                {"errorCode": "FIXTURE_QUERY_NOT_ALLOWED", "message": "No fixture operation accepts a query string."},
            )
            return
        elif is_version_probe:
            if raw_body:
                self._send_json(
                    400,
                    {"errorCode": "FIXTURE_BODY_NOT_ALLOWED", "message": "The version probe is bodyless."},
                )
            elif self.headers.get("Authorization") != f"Bearer {self.server.scenario['accessToken']}":
                self._send_json(
                    401,
                    {"errorCode": "FIXTURE_UNAUTHORIZED", "message": "The version probe requires the SDK bearer token."},
                )
            else:
                self._send_json(
                    200,
                    {
                        "id": self.server.scenario["managerId"],
                        "fqdn": "127.0.0.1",
                        "version": "9.1.0.0",
                    },
                )
            return
        elif operation_id is None:
            self._send_json(
                404,
                {
                    "errorCode": "FIXTURE_OPERATION_NOT_SERVED",
                    "message": "The requested method and path are not in docs/contract.json.",
                },
            )
            return

        if operation_id == "createToken":
            expected = {
                "username": self.server.scenario["username"],
                "password": self.server.scenario["password"],
            }
            if parsed_body != expected or self.headers.get("Authorization") is not None:
                self._send_json(
                    400,
                    {"errorCode": "FIXTURE_INVALID_CREDENTIALS", "message": "Invalid loopback credentials."},
                )
            else:
                self._send_json(
                    201,
                    {
                        "accessToken": self.server.scenario["accessToken"],
                        "refreshToken": {"id": self.server.scenario["refreshToken"]},
                    },
                )
        elif operation_id == "updateProxyConfiguration":
            self._send_json(
                202,
                {
                    "id": self.server.scenario["taskId"],
                    "name": "Update proxy configuration",
                    "type": "PROXY_CONFIGURATION_UPDATE",
                    "status": "IN_PROGRESS",
                    "creationTimestamp": "2026-08-02T12:00:00Z",
                },
            )
        elif operation_id == "getTask":
            self._send_json(
                200,
                {
                    "id": self.server.scenario["taskId"],
                    "name": "Update proxy configuration",
                    "type": "PROXY_CONFIGURATION_UPDATE",
                    "status": "SUCCESSFUL",
                    "creationTimestamp": "2026-08-02T12:00:00Z",
                    "completionTimestamp": "2026-08-02T12:00:01Z",
                },
            )
        elif operation_id == "updateDepotSettings":
            self._send_json(
                400,
                {
                    "errorCode": "DEPOT_TOKEN_REJECTED",
                    "errorType": "VALIDATION",
                    "message": self.server.scenario["failureMessage"],
                    "remediationMessage": "Generate a current download token.",
                },
            )
        else:  # The contract-to-handler mapping is intentionally fail closed.
            self._send_json(500, {"errorCode": "FIXTURE_HANDLER_MISSING"})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()

    scenario: dict[str, object] = {
        "username": "sdk-" + secrets.token_hex(7),
        "password": "pw-" + secrets.token_urlsafe(15),
        "accessToken": "at-" + secrets.token_urlsafe(20),
        "refreshToken": "rt-" + secrets.token_urlsafe(18),
        "managerId": "sddc-manager-" + secrets.token_hex(6),
        "proxyHost": "proxy-" + secrets.token_hex(6) + ".example.test",
        "proxyPort": 1024 + secrets.randbelow(65535 - 1024),
        "proxyProtocol": secrets.choice(("HTTP", "HTTPS")),
        "depotToken": secrets.token_hex(16),
        "taskId": "task-proxy-" + secrets.token_hex(8),
        "failureMessage": "The supplied depot download token was rejected (" + secrets.token_hex(6) + ").",
    }
    args.request_log.write_text("", encoding="utf-8")
    server = FixtureServer(("127.0.0.1", args.port), args.request_log, scenario)
    host, port = server.server_address
    ready = {"host": host, "port": port, **scenario}
    temporary_ready = args.ready_file.with_name(args.ready_file.name + ".tmp")
    temporary_ready.write_text(json.dumps(ready, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary_ready, args.ready_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
