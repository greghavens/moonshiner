#!/usr/bin/env python3
"""Contract-derived IPv4-loopback vCenter fixture for protected verification."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def compile_template(template: str) -> re.Pattern[str]:
    cursor = 0
    pieces: list[str] = ["^"]
    for match in re.finditer(r"\{[^{}]+\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(r"[^/?#]+")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    pieces.append("$")
    return re.compile("".join(pieces))


def derive_routes(contract: dict[str, object]) -> list[dict[str, object]]:
    source = contract.get("source")
    if not isinstance(source, dict):
        raise ValueError("contract source is missing")
    if source.get("spec_path") != (
        "specifications/vsphere/openapi/automation/vcenter.yaml"
    ):
        raise ValueError("contract is not the vCenter Automation specification")
    commit = source.get("repository_commit_sha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("contract repository commit is not immutable")
    if contract.get("server_base_path") != "/api":
        raise ValueError("contract server base must be /api")

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 2:
        raise ValueError("contract must contain exactly two operations")

    routes: list[dict[str, object]] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("contract operation must be an object")
        operation_id = operation.get("operationId")
        method = operation.get("method")
        path = operation.get("path")
        status = operation.get("successStatus")
        if not isinstance(operation_id, str) or operation_id in seen:
            raise ValueError("contract operationId is missing or duplicated")
        if method != "GET" or not isinstance(path, str) or not path.startswith("/api/"):
            raise ValueError("focused fixture permits only contract GET routes")
        if status != 200:
            raise ValueError("focused fixture requires HTTP 200 operations")
        seen.add(operation_id)
        routes.append(
            {
                "operation_id": operation_id,
                "method": method,
                "path": path,
                "pattern": compile_template(path),
                "success_status": status,
            }
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: list[dict[str, object]],
        responses: dict[str, list[object]],
        log_path: Path,
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.responses = responses
        self.log_path = log_path
        self.sequence = 0
        self.response_indexes = {
            str(route["operation_id"]): 0 for route in routes
        }
        self.state_lock = threading.Lock()

    def route_for(self, method: str, path: str) -> dict[str, object] | None:
        for route in self.routes:
            pattern = route["pattern"]
            if route["method"] == method and isinstance(pattern, re.Pattern):
                if pattern.fullmatch(path):
                    return route
        return None

    def log_request_record(self, record: dict[str, object]) -> None:
        with self.state_lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def next_response(self, operation_id: str) -> object:
        with self.state_lock:
            index = self.response_indexes[operation_id]
            values = self.responses[operation_id]
            if index >= len(values):
                raise IndexError("no configured response remains")
            self.response_indexes[operation_id] = index + 1
            return values[index]


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        route = self.server.route_for(self.command, split.path)
        content_length_text = self.headers.get("Content-Length")
        try:
            content_length = int(content_length_text) if content_length_text else 0
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length) if content_length > 0 else b""
        self.server.log_request_record(
            {
                "operation_id": (
                    route["operation_id"] if route is not None else None
                ),
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "request_version": self.request_version,
                "headers": [
                    {"name": name, "value": value}
                    for name, value in self.headers.raw_items()
                ],
                "body_length": len(body),
                "body_base64": base64.b64encode(body).decode("ascii"),
            }
        )

        if route is None:
            self.send_json(404, {"error_type": "NOT_FOUND", "messages": []})
            return
        operation_id = str(route["operation_id"])
        try:
            payload = self.server.next_response(operation_id)
        except IndexError:
            self.send_json(500, {"error_type": "NO_RESPONSE", "messages": []})
            return
        self.send_json(int(route["success_status"]), payload)

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_PATCH = dispatch
    do_DELETE = dispatch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()

    contract = load_json(args.contract)
    config = load_json(args.config)
    if not isinstance(contract, dict) or not isinstance(config, dict):
        raise ValueError("contract and config must be JSON objects")
    routes = derive_routes(contract)
    responses = config.get("responses")
    route_ids = {str(route["operation_id"]) for route in routes}
    if not isinstance(responses, dict) or set(responses) != route_ids:
        raise ValueError("response keys must exactly match contract operationIds")
    for operation_id, values in responses.items():
        if not isinstance(operation_id, str) or not isinstance(values, list):
            raise ValueError("every operation requires a response list")

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes,
        responses,
        args.log,
    )
    ready = {
        "host": "127.0.0.1",
        "port": server.server_address[1],
        "operation_ids": sorted(route_ids),
    }
    ready_temp = args.ready_file.with_name(
        f".{args.ready_file.name}.{os.getpid()}.tmp"
    )
    with ready_temp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(ready, separators=(",", ":")))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(ready_temp, args.ready_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"mock startup failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
