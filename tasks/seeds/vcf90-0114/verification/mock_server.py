#!/usr/bin/env python3
"""Loopback-only VCF Installer fixture pinned to docs/contract.json."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getTasks": ("GET", "/v1/tasks"),
    "refreshAccessToken": ("PATCH", "/v1/tokens/access-token/refresh"),
}


def load_routes(contract_path: Path) -> dict[tuple[str, str], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = {
        item["operationId"]: (item["method"], item["path"])
        for item in contract["operations"]
    }
    if operations != EXPECTED_OPERATIONS:
        raise ValueError(f"contract operation mismatch: {operations!r}")
    return {wire: operation_id for operation_id, wire in operations.items()}


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, routes, log_path, scenario):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.scenario = scenario
        self.log_lock = threading.Lock()
        self.sequence = 0
        self.refreshed = False

    def record(self, entry: dict) -> None:
        with self.log_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FixtureServer

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def log_message(self, _format: str, *_args) -> None:
        return

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        body_bytes = self._read_body()
        body = body_bytes.decode("utf-8")
        operation_id = self.server.routes.get((self.command, parsed.path))
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.server.record({
            "method": self.command,
            "rawTarget": self.path,
            "path": parsed.path,
            "query": parse_qsl(parsed.query, keep_blank_values=True),
            "headers": headers,
            "body": body,
            "operationId": operation_id,
        })

        if operation_id == "createToken":
            if self.server.scenario == "create-status":
                self._json(200, {
                    "accessToken": "access-token-1",
                    "refreshToken": {"id": "refresh-token-1"},
                })
                return
            if self.server.scenario == "create-malformed":
                self._json(201, {
                    "accessToken": 1,
                    "refreshToken": {"id": "refresh-token-1"},
                })
                return
            self._json(201, {
                "accessToken": "access-token-1",
                "refreshToken": {"id": "refresh-token-1"},
            })
            return

        if operation_id == "getTasks":
            query = parse_qsl(parsed.query, keep_blank_values=True)
            if len(query) == 2 and dict(query) == {"pageNumber": "0", "pageSize": "2"}:
                if self.headers.get("Authorization") != "Bearer access-token-1":
                    self._json(401, {"message": "unauthorized"})
                    return
                if self.server.scenario == "tasks-status":
                    self._json(503, page(
                        0,
                        [task("task-001", "Inventory"), task("task-002", "Validation")],
                    ))
                    return
                if self.server.scenario == "tasks-malformed":
                    malformed = task("task-001", "Inventory")
                    malformed["id"] = 1
                    self._json(200, page(0, [malformed]))
                    return
                if self.server.scenario == "metadata-malformed":
                    malformed_page = page(0, [task("task-001", "Inventory")])
                    malformed_page["pageMetadata"]["totalPages"] = "two"
                    self._json(200, malformed_page)
                    return
                self._json(200, page(
                    0,
                    [task("task-001", "Inventory"), task("task-002", "Validation")],
                ))
                return
            if len(query) == 2 and dict(query) == {"pageNumber": "1", "pageSize": "2"}:
                authorization = self.headers.get("Authorization")
                if authorization == "Bearer access-token-1" and not self.server.refreshed:
                    self._json(401, {"message": "access token expired"})
                    return
                if authorization == "Bearer access-token-2" and self.server.refreshed:
                    self._json(200, page(
                        1,
                        [task("task-003", "Deployment"), task("task-004", "Cleanup")],
                    ))
                    return
                self._json(401, {"message": "unauthorized"})
                return
            if len(query) == 2 and dict(query) == {"pageNumber": "2", "pageSize": "2"}:
                if self.headers.get("Authorization") != "Bearer access-token-2":
                    self._json(401, {"message": "unauthorized"})
                    return
                self._json(200, page(2, [task("task-005", "Audit")]))
                return
            self._json(400, {"message": "unexpected query"})
            return

        if operation_id == "refreshAccessToken":
            try:
                refresh_token = json.loads(body)
            except json.JSONDecodeError:
                refresh_token = None
            if refresh_token != "refresh-token-1":
                self._json(400, {"message": "unexpected refresh token"})
                return
            if self.server.scenario == "refresh-status":
                self.server.refreshed = True
                self._json(201, "access-token-2")
                return
            if self.server.scenario == "refresh-malformed":
                self._json(200, {"accessToken": "access-token-2"})
                return
            self.server.refreshed = True
            self._json(200, "access-token-2")
            return

        self._json(404, {"message": "operation not served by pinned contract"})

    def _read_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding")
        if transfer_encoding is None:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length)
        if [item.strip().lower() for item in transfer_encoding.split(",")] != ["chunked"]:
            raise ValueError(f"unsupported Transfer-Encoding: {transfer_encoding}")

        chunks = bytearray()
        while True:
            size_line = self.rfile.readline()
            if not size_line.endswith(b"\r\n"):
                raise ValueError("malformed chunk size line")
            size = int(size_line[:-2].split(b";", 1)[0], 16)
            if size == 0:
                while self.rfile.readline() not in (b"\r\n", b""):
                    pass
                return bytes(chunks)
            chunks.extend(self.rfile.read(size))
            if self.rfile.read(2) != b"\r\n":
                raise ValueError("malformed chunk terminator")

    def _json(self, status: int, payload) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def task(task_id: str, name: str) -> dict:
    return {
        "id": task_id,
        "name": name,
        "status": "SUCCESSFUL",
        "creationTimestamp": "2026-01-01T00:00:00Z",
    }


def page(page_number: int, elements: list[dict]) -> dict:
    return {
        "elements": elements,
        "pageMetadata": {
            "pageNumber": page_number,
            "pageSize": len(elements),
            "totalElements": 5,
            "totalPages": 3,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=(
            "happy",
            "create-status",
            "create-malformed",
            "tasks-status",
            "tasks-malformed",
            "metadata-malformed",
            "refresh-status",
            "refresh-malformed",
        ),
        required=True,
    )
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = FixtureServer(("127.0.0.1", 0), Handler, routes, args.log, args.scenario)
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
