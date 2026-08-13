#!/usr/bin/env python3
"""Loopback-only VCF Operations mock derived from docs/contract.json."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
BASE_PATH = CONTRACT["base_path"]
OPERATIONS = CONTRACT["operations"]
ALLOWED = {
    (operation["method"], BASE_PATH + operation["path"]): operation_id
    for operation_id, operation in OPERATIONS.items()
}

RESOURCE_IDS = [
    "33333333-3333-4333-8333-333333333333",
    "11111111-1111-4111-8111-111111111111",
    "55555555-5555-4555-8555-555555555555",
    "22222222-2222-4222-8222-222222222222",
    "44444444-4444-4444-8444-444444444444",
]


def resource(identifier: str, name: str) -> dict:
    return {
        "creationTime": 1744473856401,
        "resourceKey": {
            "name": name,
            "adapterKindKey": "VMWARE",
            "resourceKindKey": "VirtualMachine",
            "resourceIdentifiers": [],
        },
        "resourceStatusStates": [],
        "resourceHealth": "GREEN",
        "resourceHealthValue": 100.0,
        "identifier": identifier,
    }


class State:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.auth_count = 0

    def append(self, record: dict) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VCFOperationsContractMock/9.0"

    def _handle(self) -> None:
        split = urlsplit(self.path)
        operation_id = ALLOWED.get((self.command, split.path))
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": split.path,
            "query": parse_qs(split.query, keep_blank_values=True),
            "headers": {
                name.lower(): self.headers.get(name)
                for name in ("Accept", "Content-Type", "Authorization")
                if self.headers.get(name) is not None
            },
            "body": body.decode("utf-8"),
            "operationId": operation_id,
        }
        self.server.state.append(record)  # type: ignore[attr-defined]

        if operation_id is None:
            self._json(404, {"message": "operation is outside the pinned contract"})
            return
        if operation_id == "acquireToken":
            self._acquire(body)
            return
        if operation_id == "getResources":
            self._resources(parse_qs(split.query, keep_blank_values=True))
            return
        self._json(404, {"message": "operation is not implemented"})

    def _acquire(self, body: bytes) -> None:
        try:
            credentials = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"message": "invalid JSON"})
            return
        if credentials.get("username") != "ops-user" or credentials.get("password") != 'p@ss"word':
            self._json(401, {"message": "authentication failed"})
            return
        with self.server.state.lock:  # type: ignore[attr-defined]
            self.server.state.auth_count += 1  # type: ignore[attr-defined]
            count = self.server.state.auth_count  # type: ignore[attr-defined]
        self._json(200, {"token": f"token-{count}", "validity": 4102444800000})

    def _resources(self, query: dict[str, list[str]]) -> None:
        authorization = self.headers.get("Authorization")
        try:
            page = int(query.get("page", ["0"])[0])
            page_size = int(query.get("pageSize", ["1000"])[0])
        except ValueError:
            self._json(400, {"message": "invalid pagination"})
            return

        if authorization == "token-1" and page == 1:
            self._json(401, {"message": "token expired"})
            return
        if authorization not in {"token-1", "token-2"}:
            self._json(401, {"message": "invalid token"})
            return

        start = page * page_size
        selected = RESOURCE_IDS[start : start + page_size]
        payload = {
            "pageInfo": {
                "totalCount": len(RESOURCE_IDS),
                "page": page,
                "pageSize": page_size,
            },
            "resourceList": [resource(value, f"vm-{start + index + 1}")
                             for index, value in enumerate(selected)],
        }
        self._json(200, payload)

    def _json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    args = parser.parse_args()

    args.log_file.write_text("", encoding="utf-8")
    state = State(args.log_file)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
