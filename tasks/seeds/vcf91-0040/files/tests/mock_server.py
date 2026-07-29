#!/usr/bin/env python3
"""Loopback-only mock pinned to updateDepotSettings in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    names = {operation["operationId"] for operation in operations}
    required = {"updateDepotSettings"}
    if names != required or len(operations) != 1:
        raise ValueError(f"mock is pinned to exactly {sorted(required)}, got {sorted(names)}")
    return {
        (operation["method"], operation["path"]): operation
        for operation in operations
    }


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, log_path: Path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.sequence = 0
        self.applied_settings: str | None = None
        self.mutation_effect_count = 0
        self.access_token = "tok_" + secrets.token_hex(16)
        self.username = 'depot-user-"' + secrets.token_hex(8)
        self.password = "pw-\\" + secrets.token_hex(8) + "\nnext-line"

    def apply(self, payload: object) -> int:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.log_lock:
            if canonical != self.applied_settings:
                self.applied_settings = canonical
                self.mutation_effect_count += 1
            return self.mutation_effect_count

    def append_log(self, entry: dict) -> None:
        with self.log_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self.reject_unpinned()

    def do_POST(self) -> None:  # noqa: N802
        self.reject_unpinned()

    def do_PATCH(self) -> None:  # noqa: N802
        self.reject_unpinned()

    def do_DELETE(self) -> None:  # noqa: N802
        self.reject_unpinned()

    def do_HEAD(self) -> None:  # noqa: N802
        self.reject_unpinned()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.reject_unpinned()

    def reject_unpinned(self) -> None:
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.append_log(
            {
                "operationId": None,
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": {
                    key.lower(): value for key, value in self.headers.items()
                },
                "body": body_bytes.decode("utf-8", errors="replace"),
                "effectCountAfter": self.server.mutation_effect_count,
                "responseStatus": 404,
            }
        )
        self.send_json(404, {"message": "No operation in pinned contract"})

    def do_PUT(self) -> None:  # noqa: N802
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = body_bytes.decode("utf-8", errors="replace")
        operation = self.server.routes.get((self.command, split.path))

        entry = {
            "operationId": None if operation is None else operation["operationId"],
            "method": self.command,
            "target": self.path,
            "path": split.path,
            "query": split.query,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
        }

        if operation is None:
            entry["effectCountAfter"] = self.server.mutation_effect_count
            entry["responseStatus"] = 404
            self.server.append_log(entry)
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            entry["effectCountAfter"] = self.server.mutation_effect_count
            entry["responseStatus"] = 400
            self.server.append_log(entry)
            self.send_json(400, {"message": "Malformed JSON"})
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("vmwareAccount"), dict):
            entry["effectCountAfter"] = self.server.mutation_effect_count
            entry["responseStatus"] = 400
            self.server.append_log(entry)
            self.send_json(400, {"message": "vmwareAccount is required by this scenario"})
            return

        effect_count = self.server.apply(payload)
        response_status = 500 if self.server.sequence == 0 else 202
        entry["effectCountAfter"] = effect_count
        entry["responseStatus"] = response_status
        self.server.append_log(entry)
        if response_status == 500:
            self.send_json(
                500,
                {
                    "errorCode": "VCF_DEPOT_TRANSIENT",
                    "message": "simulated transient response after commit",
                },
            )
        else:
            self.send_json(202, payload)

    def send_json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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

    routes = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, args.log)
    args.port_file.write_text(
        json.dumps(
            {
                "port": server.server_port,
                "access_token": server.access_token,
                "username": server.username,
                "password": server.password,
            }
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
