#!/usr/bin/env python3
"""Contract-pinned loopback server used by the protected verifier."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlsplit


def load_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("contract must name exactly one operation")
    operation = operations[0]
    required = {"id", "documentedName", "method", "path", "modes", "requestBody"}
    if not required.issubset(operation):
        raise ValueError("contract operation is incomplete")
    modes = operation["modes"]
    if set(modes) != {"precheck", "mutate"}:
        raise ValueError("contract must define only precheck and mutate modes")
    return operation


def query_pairs(mode: dict) -> tuple[tuple[str, str], ...]:
    values = mode.get("query", {})
    return tuple((str(key), str(value).lower() if isinstance(value, bool) else str(value))
                 for key, value in values.items())


class ContractServer(HTTPServer):
    def __init__(self, address, handler, *, operation, log_path, token,
                 precheck_status, mutation_status):
        super().__init__(address, handler)
        self.operation = operation
        self.log_path = log_path
        self.token = token
        self.precheck_status = precheck_status
        self.mutation_status = mutation_status
        self.log_lock = Lock()
        self.log_records: list[dict] = []

    def append_log(self, record: dict) -> None:
        with self.log_lock:
            self.log_records.append(record)
            contents = "".join(
                json.dumps(item, separators=(",", ":")) + "\n"
                for item in self.log_records
            )
            pending_path = self.log_path.with_name(self.log_path.name + ".pending")
            pending_path.write_text(contents, encoding="utf-8")
            pending_path.replace(self.log_path)


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        operation = self.server.operation
        supplied_query = tuple(parse_qsl(parsed.query, keep_blank_values=True))
        mode_name = None
        if self.command == operation["method"] and parsed.path == operation["path"]:
            for candidate in ("precheck", "mutate"):
                if supplied_query == query_pairs(operation["modes"][candidate]):
                    mode_name = candidate
                    break

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            json_body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            json_body = None

        if mode_name is None:
            status = 404
        elif self.headers.get("Authorization") != f"Bearer {self.server.token}":
            status = 401
        elif self.headers.get_content_type() != "application/json":
            status = 415
        elif not isinstance(json_body, dict) or "typeId" not in json_body:
            status = 400
        elif mode_name == "precheck":
            status = self.server.precheck_status
        else:
            status = self.server.mutation_status

        self.server.append_log({
            "method": self.command,
            "target": self.path,
            "path": parsed.path,
            "query": list(supplied_query),
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "rawBody": raw_body,
            "jsonBody": json_body,
            "mode": mode_name,
            "responseStatus": status,
        })

        payload = json.dumps({"mode": mode_name, "status": status},
                             separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    do_POST = _handle
    do_GET = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--precheck-status", type=int, default=200)
    parser.add_argument("--mutation-status", type=int, default=201)
    args = parser.parse_args()

    operation = load_contract(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0), Handler, operation=operation, log_path=args.log,
        token=args.token, precheck_status=args.precheck_status,
        mutation_status=args.mutation_status,
    )
    pending_port_file = args.port_file.with_name(args.port_file.name + ".pending")
    pending_port_file.write_text(str(server.server_port), encoding="ascii")
    pending_port_file.replace(args.port_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
