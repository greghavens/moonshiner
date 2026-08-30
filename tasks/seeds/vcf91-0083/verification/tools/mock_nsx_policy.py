#!/usr/bin/env python3
"""Loopback-only NSX Policy mock whose route allow-list comes from the contract."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def compile_routes(contract: dict[str, Any]) -> list[dict[str, Any]]:
    base_path = contract["basePath"].rstrip("/")
    routes: list[dict[str, Any]] = []
    for operation in contract["operations"]:
        template = base_path + operation["path"]
        parts = re.split(r"(\{[^{}]+\})", template)
        pattern = "".join(
            r"[^/?]+"
            if part.startswith("{") and part.endswith("}")
            else re.escape(part)
            for part in parts
        )
        routes.append(
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "pattern": re.compile(rf"^{pattern}$"),
                "responses": operation["responses"],
            }
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, Any]],
        log_path: Path,
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_GET(self) -> None:  # noqa: N802 - explicit deny path
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - explicit deny path
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - explicit deny path
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - explicit deny path
        self._handle()

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        route = next(
            (
                item
                for item in self.server.routes
                if item["method"] == self.command
                and item["pattern"].fullmatch(parsed.path)
                and not parsed.query
            ),
            None,
        )
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except ValueError:
            length = 0
        body = self.rfile.read(max(length, 0))

        header_log: dict[str, list[str]] = {}
        for name in self.headers:
            lower = name.lower()
            if lower not in header_log:
                header_log[lower] = self.headers.get_all(name, [])
        entry = {
            "sequence": self.server_request_count(),
            "operationId": None if route is None else route["operationId"],
            "method": self.command,
            "raw_target": self.path,
            "headers": header_log,
            "body_base64": base64.b64encode(body).decode("ascii"),
        }
        with self.server.log_path.open("a", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(entry, separators=(",", ":"), ensure_ascii=False))
            log.write("\n")
            log.flush()

        if route is None:
            self._respond(
                404,
                b'{"error_code":40400,"error_message":"route not in contract"}',
            )
            return

        operation_id = route["operationId"]
        if operation_id == "PatchGroupForDomain":
            self._respond(200, b"")
            return
        if operation_id == "PatchSecurityPolicyForDomain":
            if "503" not in route["responses"]:
                self._respond(500, b'{"error_code":50000}')
                return
            self._respond(
                503,
                b'{"error_code":73001,"error_message":"policy engine unavailable",'
                b'"module_name":"policy"}',
            )
            return
        self._respond(404, b'{"error_code":40401}')

    def server_request_count(self) -> int:
        try:
            with self.server.log_path.open("r", encoding="utf-8") as log:
                return sum(1 for line in log if line.strip()) + 1
        except FileNotFoundError:
            return 1

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    routes = compile_routes(contract)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")

    server = ContractServer(("127.0.0.1", 0), routes, args.log)
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
