#!/usr/bin/env python3
"""Loopback mock pinned to exactly the operations in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


EXPECTED_ROUTES = {
    ("PATCH", "/v1/tokens/access-token/refresh"): "refreshAccessToken",
    ("GET", "/v1/credentials"): "getCredentials",
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
            "mock is pinned to exactly refreshAccessToken and getCredentials"
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    def __init__(
        self,
        address,
        handler,
        routes,
        log_path: Path,
        first_request_marker: Path,
    ):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.first_request_marker = first_request_marker
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.sequence = 0
        self.credentials_count = 0
        self.refresh_committed = threading.Event()
        suffix = secrets.token_hex(10)
        self.old_access_token = "old_access_" + secrets.token_hex(18)
        self.new_access_token = "new_access_" + secrets.token_hex(18)
        self.refresh_token_id = f'refresh_"id"\\{suffix}'
        self.resource_name = f'vc /rack?name="Ω"&plus+{suffix}'

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

    def do_PATCH(self) -> None:  # noqa: N802
        self.dispatch()

    def do_POST(self) -> None:  # noqa: N802
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
            entry["responseStatus"] = 404
            self.server.append_log(entry)
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        if operation["operationId"] == "refreshAccessToken":
            expected_body = json.dumps(
                self.server.refresh_token_id,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            valid = (
                body == expected_body
                and "authorization"
                not in {key.lower() for key in self.headers.keys()}
            )
            entry["responseStatus"] = 200 if valid else 400
            entry["refreshCommitted"] = valid
            self.server.append_log(entry)
            if valid:
                self.send_json(200, self.server.new_access_token)
                self.server.refresh_committed.set()
            else:
                self.send_json(400, {"message": "invalid refresh request"})
                self.server.refresh_committed.set()
            return

        with self.server.state_lock:
            self.server.credentials_count += 1
            request_number = self.server.credentials_count

        authorization = self.headers.get("Authorization")
        if request_number == 1:
            entry["responseStatus"] = 401
            entry["credentialRequestNumber"] = request_number
            self.server.append_log(entry)
            self.server.first_request_marker.write_text("started", encoding="utf-8")
            if not self.server.refresh_committed.wait(timeout=5):
                self.send_json(503, {"message": "refresh did not arrive"})
                return
            self.send_json(401, {"message": "old bearer rejected at cutover"})
            return

        valid = authorization == "Bearer " + self.server.new_access_token
        entry["responseStatus"] = 200 if valid else 401
        entry["credentialRequestNumber"] = request_number
        self.server.append_log(entry)
        if valid:
            self.send_json(
                200,
                {
                    "elements": [
                        {
                            "id": "credential-after-cutover",
                            "resourceName": self.server.resource_name,
                        }
                    ]
                },
            )
        else:
            self.send_json(401, {"message": "new bearer required"})

    def send_json(self, status: int, payload: object) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
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
    parser.add_argument("--first-request-marker", type=Path, required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes,
        args.log,
        args.first_request_marker,
    )
    args.port_file.write_text(
        json.dumps(
            {
                "port": server.server_port,
                "old_access_token": server.old_access_token,
                "new_access_token": server.new_access_token,
                "refresh_token_id": server.refresh_token_id,
                "resource_name": server.resource_name,
            }
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
