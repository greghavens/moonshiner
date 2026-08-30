#!/usr/bin/env python3
"""Contract-pinned loopback service for vcf91-0166."""

from __future__ import annotations

import argparse
import copy
import json
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "getAllLogForwarders",
    "createLogForwarder",
}


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def load_routes(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for route_path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            key = (method.upper(), route_path)
            if key in routes:
                raise ValueError("duplicate focused method and path")
            routes[key] = operation
    if {item["operationId"] for item in routes.values()} != EXPECTED_OPERATION_IDS:
        raise ValueError("unexpected focused operationId set")
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: dict[tuple[str, str], dict[str, Any]],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.forwarders = copy.deepcopy(config["initial_forwarders"])
        self.successful_creates = 0
        self.old_token_expired = False
        self.lock = threading.Lock()

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")

    def list_forwarders(self, token: str | None) -> tuple[int, Any]:
        with self.lock:
            if token == self.config["old_token"] and self.old_token_expired:
                return 403, self.auth_error()
            if token not in (self.config["old_token"], self.config["new_token"]):
                return 403, self.auth_error()
            return 200, copy.deepcopy(self.forwarders)

    def create_forwarder(
        self, token: str | None, body: Any
    ) -> tuple[int, Any]:
        with self.lock:
            if token == self.config["old_token"]:
                if self.successful_creates >= 1:
                    self.old_token_expired = True
                    return 403, self.auth_error()
            elif token != self.config["new_token"]:
                return 403, self.auth_error()

            if not isinstance(body, dict):
                return 400, {
                    "errorCode": "JSON_FORMAT_ERROR",
                    "errorMessage": "request body must be an object",
                }
            name = body.get("name")
            if not isinstance(name, str) or not name:
                return 400, {
                    "errorCode": "FIELD_ERROR",
                    "errorMessage": "name must be nonblank",
                }
            if any(item.get("name") == name for item in self.forwarders):
                return 400, {
                    "errorCode": "FIELD_ERROR",
                    "errorMessage": "name already exists",
                }
            created = copy.deepcopy(body)
            created["id"] = self.config["created_ids"][name]
            self.forwarders.append(created)
            self.successful_creates += 1
            return 201, copy.deepcopy(created)

    @staticmethod
    def auth_error() -> dict[str, str]:
        return {
            "errorCode": "SECURITY_ERROR",
            "errorMessage": "access token expired",
        }


class ContractUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The identical HTTP handler over a local Unix socket when AF_INET is denied."""

    daemon_threads = True

    def __init__(
        self,
        address: str,
        routes: dict[tuple[str, str], dict[str, Any]],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.forwarders = copy.deepcopy(config["initial_forwarders"])
        self.successful_creates = 0
        self.old_token_expired = False
        self.lock = threading.Lock()

    append_log = ContractServer.append_log
    list_forwarders = ContractServer.list_forwarders
    create_forwarder = ContractServer.create_forwarder
    auth_error = staticmethod(ContractServer.auth_error)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _read_body(self) -> tuple[bytes, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length else 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            return raw, None

    def _headers(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for key, value in self.headers.raw_items():
            grouped.setdefault(key.lower(), []).append(value)
        return grouped

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw, body = self._read_body()
        route = self.server.routes.get((method, split.path))
        token_values = self._headers().get("x-jwt-token", [])
        token = token_values[0] if len(token_values) == 1 else None

        status = 404
        payload: Any = {
            "errorCode": "API_ERROR",
            "errorMessage": "operation is outside the focused contract",
        }
        if route is not None and not split.query:
            if route["operationId"] == "getAllLogForwarders":
                status, payload = self.server.list_forwarders(token)
            elif route["operationId"] == "createLogForwarder":
                status, payload = self.server.create_forwarder(token, body)

        headers = self._headers()
        self.server.append_log(
            {
                "operationId": route["operationId"] if route else None,
                "method": method,
                "raw_target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": headers,
                "body_raw": raw.decode("utf-8", errors="replace"),
                "body_bytes": len(raw),
                "body": body,
                "status": status,
            }
        )
        self._json(status, payload)

    def _json(self, status: int, value: Any) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    routes = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    unix_path = args.ready.with_suffix(".sock")
    mode = "tcp"
    try:
        server: ContractServer | ContractUnixServer = ContractServer(
            ("127.0.0.1", 0), routes, args.log, config
        )
        ready_value: dict[str, Any] = {
            "mode": mode,
            "host": "127.0.0.1",
            "port": server.server_port,
        }
    except PermissionError:
        mode = "unix"
        if unix_path.exists():
            unix_path.unlink()
        try:
            server = ContractUnixServer(str(unix_path), routes, args.log, config)
            ready_value = {
                "mode": mode,
                "host": "127.0.0.1",
                "port": 1,
                "socket": str(unix_path),
            }
        except PermissionError:
            mode = "inprocess"
            server = None
            ready_value = {
                "mode": mode,
                "host": "127.0.0.1",
                "port": 1,
            }
    durable_write(
        args.ready,
        json.dumps(ready_value, separators=(",", ":")),
        "w",
    )
    try:
        if server is None:
            threading.Event().wait()
        else:
            server.serve_forever(poll_interval=0.05)
    finally:
        if server is not None:
            server.server_close()
        if mode == "unix" and unix_path.exists():
            unix_path.unlink()


if __name__ == "__main__":
    main()
