#!/usr/bin/env python3
"""Loopback NSX Policy mock whose route allow-list comes from contract.json."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


NORMAL_PAGES: dict[str, dict[str, Any]] = {
    "": {
        "results": [
            {
                "id": "zeta",
                "display_name": "web",
                "path": "/infra/segments/zeta",
            },
            {
                "id": "b",
                "display_name": "app",
                "path": "/infra/segments/b",
            },
        ],
        "cursor": "next +/=",
        "result_count": 4,
    },
    "next +/=": {
        "results": [],
        "cursor": "empty-page",
    },
    "empty-page": {
        "results": [
            {
                "id": "db",
                "display_name": "database",
                "path": "/infra/segments/db",
            },
            {
                "id": "a",
                "display_name": "app",
                "path": "/infra/segments/a",
            },
        ]
    },
}

SET_PAGES: dict[str, dict[str, Any]] = {
    "": {
        "results": [
            {
                "id": "zeta",
                "display_name": "web",
                "path": "/infra/segments/zeta",
            },
            {
                "id": "b",
                "display_name": "app",
                "path": "/infra/segments/b",
            },
        ],
        "cursor": "page-2",
        "result_count": 4,
    },
    "page-2": {
        "results": [
            {
                "id": "db",
                "display_name": "database",
                "path": "/infra/segments/db",
            },
            {
                "id": "a",
                "display_name": "app",
                "path": "/infra/segments/a",
            },
        ]
    },
}

REPEATED_PAGES: dict[str, dict[str, Any]] = {
    "": {
        "results": [
            {
                "id": "a",
                "display_name": "app",
                "path": "/infra/segments/a",
            }
        ],
        "cursor": "repeat-me",
    },
    "repeat-me": {
        "results": [
            {
                "id": "b",
                "display_name": "app",
                "path": "/infra/segments/b",
            }
        ],
        "cursor": "repeat-me",
    },
}


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("swagger") != "2.0":
        raise ValueError("mock requires an OpenAPI 2.0 extraction")
    base_path = contract.get("basePath")
    operations = contract.get("operations")
    if not isinstance(base_path, str) or not isinstance(operations, list):
        raise ValueError("contract is missing basePath or operations")
    if not operations:
        raise ValueError("contract names no operations")

    seen_ids: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()
    for operation in operations:
        operation_id = operation.get("operationId")
        method = operation.get("method")
        operation_path = operation.get("path")
        if not all(
            isinstance(value, str) and value
            for value in (operation_id, method, operation_path)
        ):
            raise ValueError("contract contains an incomplete operation")
        route = (method.upper(), base_path + operation_path)
        if operation_id in seen_ids or route in seen_routes:
            raise ValueError("contract contains a duplicate operation or route")
        seen_ids.add(operation_id)
        seen_routes.add(route)
    return contract


class ContractServer(HTTPServer):
    def __init__(
        self,
        contract: dict[str, Any],
        scenario: str,
        log_path: Path,
    ) -> None:
        super().__init__(("127.0.0.1", 0), ContractHandler)
        self.scenario = scenario
        self.log_path = log_path
        self.log_path.write_text("", encoding="utf-8")
        base_path = contract["basePath"]
        self.routes: dict[tuple[str, str], str] = {}
        self.known_paths: set[str] = set()
        for operation in contract["operations"]:
            full_path = base_path + operation["path"]
            self.routes[(operation["method"].upper(), full_path)] = operation[
                "operationId"
            ]
            self.known_paths.add(full_path)

    def record(self, request: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(request, sort_keys=True) + "\n")


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        split = urlsplit(self.path)
        method = self.command.upper()
        operation_id = self.server.routes.get((method, split.path))
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        headers = {
            name.lower(): self.headers.get_all(name)
            for name in self.headers.keys()
        }
        self.server.record(
            {
                "operationId": operation_id,
                "method": method,
                "target": self.path,
                "path": split.path,
                "raw_query": split.query,
                "headers": headers,
                "body": body,
            }
        )

        if operation_id is None:
            status = 405 if split.path in self.server.known_paths else 404
            self._send_json(status, {"error": "operation not allowed"})
            return
        if operation_id != "ListAllInfraSegments":
            self._send_json(501, {"error": "operation has no fixture"})
            return
        if self.server.scenario == "http-error":
            self._send_json(503, {"error": "fixture unavailable"})
            return
        if self.server.scenario == "malformed":
            payload = b'{"results": ['
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.server.scenario == "set":
            pages = SET_PAGES
        elif self.server.scenario == "repeated":
            pages = REPEATED_PAGES
        else:
            pages = NORMAL_PAGES

        query = parse_qs(split.query, keep_blank_values=True)
        cursors = query.get("cursor", [""])
        if len(cursors) != 1 or cursors[0] not in pages:
            self._send_json(400, {"error": "unknown or duplicate cursor"})
            return
        self._send_json(200, pages[cursors[0]])

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(
            value, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()

    contract = load_contract(args.contract)
    server = ContractServer(contract, args.scenario, args.log)
    host, port = server.server_address
    args.ready.write_text(f"http://{host}:{port}", encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
