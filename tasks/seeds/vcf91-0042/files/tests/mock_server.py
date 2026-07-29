#!/usr/bin/env python3
"""Loopback-only mock pinned to the two operations in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_ROUTES = {
    ("GET", "/v1/identity-broker/prechecks"): "getIdentityPrecheckResult",
    ("POST", "/v1/identity-providers"): "addExternalIdentityProvider",
}


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    routes = {
        (operation["method"], operation["path"]): operation
        for operation in operations
    }
    named = {route: operation["operationId"] for route, operation in routes.items()}
    if len(operations) != 2 or named != EXPECTED_ROUTES:
        raise ValueError(
            "mock is pinned to exactly getIdentityPrecheckResult "
            "and addExternalIdentityProvider"
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, log_path: Path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.sequence = 0
        self.precheck_count = 0
        self.mutation_count = 0
        self.mutation_allowed = False
        suffix = secrets.token_hex(10)
        self.access_token = "idp_access_" + secrets.token_hex(18)
        self.blocked_name = f'Blocked "IdP" \\ {suffix}'
        self.allowed_name = f'Federated "IdP" \\ Ω {suffix}'
        self.provider_type = f'OIDC-"broker"-{suffix}'

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
        self.dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self.dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self.dispatch()

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            body = body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body = "<non-utf8>"
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
            entry["mutationApplied"] = False
            self.server.append_log(entry)
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        operation_id = operation["operationId"]
        if operation_id == "getIdentityPrecheckResult":
            self.server.precheck_count += 1
            if self.server.precheck_count == 1:
                status = "FAILURE"
                payload = {
                    "status": status,
                    "details": [
                        {
                            "type": "CONNECTIVITY",
                            "message": "Federation endpoint is unavailable",
                        }
                    ],
                }
            else:
                status = "SUCCESS"
                payload = {"status": status}
                self.server.mutation_allowed = True
            entry["precheckStatus"] = status
            entry["mutationApplied"] = False
            entry["mutationCountAfter"] = self.server.mutation_count
            self.server.append_log(entry)
            self.send_json(200, payload)
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            entry["mutationApplied"] = False
            entry["mutationCountAfter"] = self.server.mutation_count
            self.server.append_log(entry)
            self.send_json(400, {"message": "Malformed JSON"})
            return

        expected = {
            "name": self.server.allowed_name,
            "type": self.server.provider_type,
        }
        if not self.server.mutation_allowed or payload != expected:
            entry["mutationApplied"] = False
            entry["mutationCountAfter"] = self.server.mutation_count
            self.server.append_log(entry)
            self.send_json(409, {"message": "Precheck gate is closed"})
            return

        self.server.mutation_allowed = False
        self.server.mutation_count += 1
        entry["mutationApplied"] = True
        entry["mutationCountAfter"] = self.server.mutation_count
        self.server.append_log(entry)
        self.send_empty(201)

    def send_json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

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
                "blocked_name": server.blocked_name,
                "allowed_name": server.allowed_name,
                "provider_type": server.provider_type,
            }
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
