#!/usr/bin/env python3
"""Contract-pinned loopback service for the protected verifier."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


PAGES = {
    None: {
        "results": [
            {
                "entity_id": "incident-zeta",
                "start_entity_id": "vm-107",
                "name": "Zeta path",
                "status": "OPEN",
            },
            {
                "entity_id": "incident-alpha",
                "start_entity_id": "vm-101",
                "name": "Alpha path",
                "status": "CLOSED",
            },
        ],
        "total_count": 6,
        "cursor": "page-2",
    },
    "page-2": {
        "results": [
            {
                "entity_id": "incident-Echo",
                "start_entity_id": "vm-105",
                "name": "Echo path",
                "status": "OPEN",
            },
            {
                "entity_id": "incident-bravo",
                "start_entity_id": "vm-102",
                "name": "Bravo path",
                "status": "CLOSED",
            },
        ],
        "total_count": 6,
        "cursor": "page+3/=",
    },
    "page+3/=": {
        "results": [
            {
                "entity_id": "incident-delta",
                "start_entity_id": "vm-104",
                "name": "Delta path",
                "status": "OPEN",
            },
            {
                "entity_id": "incident-Charlie",
                "start_entity_id": "vm-103",
                "name": "Charlie path",
                "status": "CLOSED",
            },
        ],
        "total_count": 6,
    },
}


def load_operation(contract_path: Path) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations", [])
    if len(operations) != 1:
        raise ValueError("mock requires exactly one named contract operation")
    operation = operations[0]
    if operation.get("operationId") != "listTroubleshootingIncidents":
        raise ValueError("mock contract operation mismatch")
    if operation.get("method") != "GET":
        raise ValueError("mock contract method mismatch")
    if operation.get("wire_path") != (
        contract.get("server_base_path", "") + operation.get("path", "")
    ):
        raise ValueError("mock contract path mismatch")
    parameter_names = [item.get("name") for item in operation["query_parameters"]]
    if parameter_names != ["size", "cursor", "start_entity_id"]:
        raise ValueError("mock contract query parameter mismatch")
    return operation


class ContractServer(ThreadingHTTPServer):
    operation: dict
    log_path: Path


class Handler(BaseHTTPRequestHandler):
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        query_pairs = parse_qsl(split.query, keep_blank_values=True)
        entry = {
            "operationId": self.server.operation["operationId"],
            "method": self.command,
            "raw_target": self.path,
            "path": split.path,
            "query": query_pairs,
            "headers": {
                "accept": self.headers.get_all("Accept") or [],
                "authorization": self.headers.get_all("Authorization") or [],
                "content-length": self.headers.get_all("Content-Length") or [],
                "content-type": self.headers.get_all("Content-Type") or [],
                "transfer-encoding": self.headers.get_all("Transfer-Encoding") or [],
            },
            "content_length": length,
            "body": body.decode("utf-8", errors="replace"),
        }
        with self.server.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

        if split.path != self.server.operation["wire_path"]:
            self._write_json(404, {"error": "operation not served"})
            return

        allowed = {item["name"] for item in self.server.operation["query_parameters"]}
        if any(name not in allowed for name, _value in query_pairs):
            self._write_json(400, {"error": "unknown query parameter"})
            return

        values: dict[str, list[str]] = {}
        for name, value in query_pairs:
            values.setdefault(name, []).append(value)
        if any(len(items) != 1 for items in values.values()):
            self._write_json(400, {"error": "duplicate query parameter"})
            return

        cursor = values.get("cursor", [None])[0]
        if cursor not in PAGES:
            self._write_json(400, {"error": "unknown cursor"})
            return
        self._write_json(200, PAGES[cursor])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()

    operation = load_operation(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler)
    server.operation = operation
    server.log_path = args.log
    args.ready.write_text(str(server.server_port), encoding="utf-8")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
