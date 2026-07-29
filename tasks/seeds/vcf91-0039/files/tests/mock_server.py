#!/usr/bin/env python3
"""Loopback-only mock pinned to getDomains in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PAGE_SIZE = 2
TOTAL_PAGES = 3


def load_route(contract_path: Path) -> dict:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if len(operations) != 1 or operations[0]["operationId"] != "getDomains":
        raise ValueError("mock is pinned to exactly the getDomains operation")
    operation = operations[0]
    if (operation["method"], operation["path"]) != ("GET", "/v1/domains"):
        raise ValueError("getDomains route changed in the pinned contract")
    return operation


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, operation: dict, log_path: Path):
        super().__init__(address, handler)
        self.operation = operation
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.sequence = 0
        self.page_requests = 0
        suffix = secrets.token_hex(8)
        self.access_token = "domain_inventory_" + secrets.token_hex(20)
        self.domains = [
            {
                "id": f"domain-z-{suffix}",
                "name": 'Zulu "Core"',
                "status": "ACTIVE",
                "type": "MANAGEMENT",
                "orgName": "Example Organization",
            },
            {
                "id": f"domain-b-{suffix}",
                "name": "alpha\\Edge",
                "status": "ACTIVE",
                "type": "VI",
                "orgName": "Example Organization",
            },
            {
                "id": f"domain-c-{suffix}",
                "name": "Beta",
                "status": "ACTIVE",
                "type": "VI",
                "orgName": "Example Organization",
            },
            {
                "id": f"domain-a-{suffix}",
                "name": "alpha\\Edge",
                "status": "ACTIVE",
                "type": "VI",
                "orgName": "Example Organization",
            },
            {
                "id": f"domain-e-{suffix}",
                "name": "Éclair",
                "status": "ACTIVE",
                "type": "VI",
                "orgName": "Example Organization",
            },
            {
                "id": f"domain-d-{suffix}",
                "name": "Delta Ω",
                "status": "ACTIVE",
                "type": "VI",
                "orgName": "Example Organization",
            },
        ]
        for index, domain in enumerate(self.domains):
            domain["owners"] = [f"owner-{index}", "svc-inventory"]
            domain["upgradeStatus"] = {
                "status": "UP_TO_DATE",
                "completedResources": index,
                "totalResources": len(self.domains),
            }

    def append_log(self, entry: dict) -> None:
        with self.log_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")

    def page(self, page_number: int) -> list[dict]:
        with self.state_lock:
            traversal = self.page_requests // TOTAL_PAGES
            self.page_requests += 1
        start = page_number * PAGE_SIZE
        elements = list(self.domains[start : start + PAGE_SIZE])
        if traversal % 2 == 1:
            elements.reverse()
        return elements


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self.dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self.dispatch()

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = body_bytes.decode("utf-8")
        matches_contract = (
            self.command == self.server.operation["method"]
            and split.path == self.server.operation["path"]
        )
        self.server.append_log(
            {
                "operationId": (
                    self.server.operation["operationId"] if matches_contract else None
                ),
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )

        if not matches_contract:
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        query = parse_qs(split.query, keep_blank_values=True, strict_parsing=True)
        if set(query) != {"pageNumber", "pageSize"}:
            self.send_json(400, {"message": "Unexpected getDomains query parameters"})
            return
        try:
            page_number = int(query["pageNumber"][0])
            requested_size = int(query["pageSize"][0])
        except (ValueError, IndexError):
            self.send_json(400, {"message": "Invalid pagination parameter"})
            return
        if (
            len(query["pageNumber"]) != 1
            or len(query["pageSize"]) != 1
            or requested_size != PAGE_SIZE
            or page_number not in range(TOTAL_PAGES)
        ):
            self.send_json(400, {"message": "Invalid pagination request"})
            return
        if self.headers.get("Authorization") != "Bearer " + self.server.access_token:
            self.send_json(401, {"message": "Missing or invalid bearer token"})
            return

        elements = self.server.page(page_number)
        self.send_json(
            200,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": len(elements),
                    "totalElements": len(self.server.domains),
                    "totalPages": TOTAL_PAGES,
                },
            },
        )

    def send_json(self, status: int, payload: object) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    operation = load_route(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0), Handler, operation, args.log
    )
    args.port_file.write_text(
        json.dumps(
            {
                "port": server.server_port,
                "access_token": server.access_token,
                "domains": server.domains,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
