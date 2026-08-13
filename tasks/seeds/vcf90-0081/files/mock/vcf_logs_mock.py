#!/usr/bin/env python3
"""Contract-pinned loopback service for the selected VCF Logs operations."""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_operations(contract_path: Path) -> tuple[list[tuple[str, re.Pattern[str], str]], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    prefix = contract["servers"][0]["url"].rstrip("/")
    operations: list[tuple[str, re.Pattern[str], str]] = []
    for template, path_item in contract["paths"].items():
        escaped = re.escape(prefix + template)
        pattern = re.sub(r"\\\{[^{}]+\\\}", r"(?P<version>[^/]+)", escaped)
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            operations.append((method.upper(), re.compile(f"^{pattern}$"), operation["operationId"]))
    return operations, prefix


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        handler,
        operations,
        request_log,
        version,
        terminal_state,
        nonterminal_polls,
    ):
        super().__init__(address, handler)
        self.operations = operations
        self.request_log = request_log
        self.log_lock = threading.Lock()
        self.poll_count = 0
        self.version = version
        self.terminal_state = terminal_state
        self.nonterminal_polls = nonterminal_polls

    def match(self, method: str, path: str):
        for expected_method, pattern, operation_id in self.operations:
            match = pattern.fullmatch(path)
            if expected_method == method and match:
                return operation_id, match.groupdict()
        return None

    def append_log(self, entry: dict) -> None:
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def do_POST(self) -> None:
        self.handle_contract_request()

    def do_PUT(self) -> None:
        self.handle_contract_request()

    def do_GET(self) -> None:
        self.handle_contract_request()

    def do_DELETE(self) -> None:
        self.handle_contract_request()

    def do_PATCH(self) -> None:
        self.handle_contract_request()

    def handle_contract_request(self) -> None:
        target = urlsplit(self.path)
        matched = self.server.match(self.command, target.path)
        content_length = int(self.headers.get("Content-Length", "0"))
        body_bytes = self.rfile.read(content_length) if content_length else b""
        if matched is None:
            self.respond(404, {"errorMessage": "Operation is not in the pinned contract."})
            return

        operation_id, path_values = matched
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.server.append_log(
            {
                "receivedAtNs": time.monotonic_ns(),
                "operationId": operation_id,
                "method": self.command,
                "path": target.path,
                "query": target.query,
                "headers": headers,
                "body": body_bytes.decode("utf-8"),
            }
        )

        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or not authorization[7:]:
            self.respond(401, "Invalid session ID")
            return

        if operation_id == "POST_upgrades":
            body = self.read_json(body_bytes)
            if body is None or not isinstance(body.get("pakUrl"), str) or not body["pakUrl"]:
                self.respond(400, {"errorMessage": "Invalid request body.", "errorCode": "JSON_FORMAT_ERROR"})
                return
            self.respond(200, {"eula": "VCF Operations for Logs agreement", "version": self.server.version})
            return

        requested_version = unquote(path_values["version"])
        if requested_version != self.server.version:
            self.respond(404, {"errorMessage": "Upgrade version not found."})
            return

        if operation_id == "PUT_upgrades-version-eula":
            body = self.read_json(body_bytes)
            if body is None or body.get("accepted") is not True:
                self.respond(400, {"errorMessage": "Invalid request body.", "errorCode": "JSON_FORMAT_ERROR"})
                return
            self.respond(200, self.status_response("Upgrading"))
            return

        if operation_id == "GET_upgrades-version":
            self.server.poll_count += 1
            if self.server.poll_count <= self.server.nonterminal_polls:
                state = (
                    "Upgrading" if self.server.poll_count % 2 else "Verifying"
                )
            elif self.server.poll_count == self.server.nonterminal_polls + 1:
                state = self.server.terminal_state
            else:
                self.respond(409, {"errorMessage": "Polling continued after a terminal state."})
                return
            self.respond(200, self.status_response(state))
            return

        self.respond(500, {"errorMessage": "Unhandled contract operation."})

    def read_json(self, body: bytes):
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            return None
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def status_response(self, state: str) -> dict:
        return {
            "status": {
                "version": self.server.version,
                "eulaAccepted": True,
                "clusterStatus": state,
            }
        }

    def respond(self, status: int, value) -> None:
        encoded = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--terminal-state",
        choices=("Complete", "Cancelled", "Failed"),
        required=True,
    )
    parser.add_argument("--nonterminal-polls", type=int, required=True)
    args = parser.parse_args()

    if args.nonterminal_polls < 0:
        parser.error("--nonterminal-polls must be non-negative")

    operations, _prefix = load_operations(args.contract)
    operation_ids = {operation_id for _, _, operation_id in operations}
    required = {"POST_upgrades", "PUT_upgrades-version-eula", "GET_upgrades-version"}
    if operation_ids != required:
        raise SystemExit("The mock contract must contain exactly the selected upgrade operations.")

    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        operations,
        args.request_log,
        args.version,
        args.terminal_state,
        args.nonterminal_polls,
    )
    args.ready_file.write_text(
        json.dumps({"baseUri": f"http://127.0.0.1:{server.server_port}"}),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
