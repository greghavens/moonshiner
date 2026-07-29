#!/usr/bin/env python3
"""Loopback-only SDDC Manager mock pinned to docs/contract.json."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
ROUTES = {
    (method.upper(), path): operation["operationId"]
    for path, path_item in CONTRACT["paths"].items()
    for method, operation in path_item.items()
}

USERNAME = "svc-access"
PASSWORD = "loopback-only-password"
ACCESS_TOKEN = "loopback-access-token"
REFRESH_TOKEN = "loopback-refresh-token"


def make_user(user_id: str, name: str, domain: str, user_type: str, role_id: str) -> dict:
    return {
        "id": user_id,
        "name": name,
        "domain": domain,
        "type": user_type,
        "role": {"id": role_id},
        "creationTimestamp": "2026-01-01T00:00:00Z",
    }


STATE = {
    "users": [
        make_user("user-zulu", "zulu", "a.example", "USER", "VIEWER"),
        make_user("user-alpha", "Alpha", "z.example", "GROUP", "OPERATOR"),
    ],
    "collection_responses": 0,
    "mutations": 0,
}

PORT_FILE: Path
LOG_FILE: Path


def page_of_users() -> dict:
    STATE["collection_responses"] += 1
    users = list(STATE["users"])
    if STATE["collection_responses"] % 2:
        users.reverse()
    return {
        "elements": users,
        "pageMetadata": {
            "pageNumber": 0,
            "pageSize": len(users),
            "totalElements": len(users),
            "totalPages": 1,
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _record(self, body: bytes) -> None:
        record = {
            "method": self.command,
            "path": urlsplit(self.path).path,
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "body": body.decode("utf-8", "replace"),
        }
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

    def _dispatch(self) -> None:
        body = self._body()
        self._record(body)
        path = urlsplit(self.path).path
        operation_id = ROUTES.get((self.command, path))

        # SDK connection handshake: Connect-VcfSddcManagerServer performs
        # GET /v1/sddc-manager after createToken to validate the session.
        # It is infrastructure, not one of the contract operations under test.
        if self.command == "GET" and path == "/v1/sddc-manager":
            if not self._authorized():
                self._send(401, {"errorCode": "UNAUTHORIZED", "message": "Authentication required"})
                return
            self._send(
                200,
                {"id": "mock-sddc-manager", "fqdn": "127.0.0.1", "version": "9.1.0.0"},
            )
            return

        if operation_id == "createToken":
            try:
                request = json.loads(body)
            except (TypeError, ValueError):
                request = {}
            if request.get("username") != USERNAME or request.get("password") != PASSWORD:
                self._send(400, {"errorCode": "INVALID_CREDENTIALS", "message": "Invalid credentials"})
                return
            self._send(
                201,
                {
                    "accessToken": ACCESS_TOKEN,
                    "refreshToken": {"id": REFRESH_TOKEN},
                },
            )
            return

        if operation_id in {"getUsers", "addUsers"} and not self._authorized():
            self._send(401, {"errorCode": "UNAUTHORIZED", "message": "Authentication required"})
            return

        if operation_id == "getUsers":
            self._send(200, page_of_users())
            return

        if operation_id == "addUsers":
            try:
                requested_users = json.loads(body)
            except (TypeError, ValueError):
                requested_users = None
            if not isinstance(requested_users, list) or len(requested_users) != 1:
                self._send(400, {"errorCode": "BAD_REQUEST", "message": "One user is required"})
                return
            requested = requested_users[0]
            required = {"name", "domain", "type", "role"}
            if not isinstance(requested, dict) or not required.issubset(requested):
                self._send(400, {"errorCode": "BAD_REQUEST", "message": "User fields are missing"})
                return
            identity = (
                str(requested["name"]).casefold(),
                str(requested["domain"]).casefold(),
                str(requested["type"]).casefold(),
            )
            for existing in STATE["users"]:
                existing_identity = (
                    existing["name"].casefold(),
                    existing["domain"].casefold(),
                    existing["type"].casefold(),
                )
                if existing_identity == identity:
                    self._send(400, {"errorCode": "USER_ALREADY_EXISTS", "message": "Duplicate user"})
                    return
            STATE["mutations"] += 1
            STATE["users"].append(
                make_user(
                    f"user-created-{STATE['mutations']}",
                    str(requested["name"]),
                    str(requested["domain"]),
                    str(requested["type"]),
                    str(requested["role"]["id"]),
                )
            )
            self._send(201, page_of_users())
            return

        self._send(404, {"errorCode": "NOT_FOUND", "message": "Operation is not in contract"})

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()


def main() -> int:
    global PORT_FILE, LOG_FILE
    if len(sys.argv) != 3:
        print("usage: mock_sddc_manager.py PORT_FILE LOG_FILE", file=sys.stderr)
        return 2
    PORT_FILE = Path(sys.argv[1])
    LOG_FILE = Path(sys.argv[2])
    LOG_FILE.write_text("", encoding="utf-8")
    server = HTTPServer(("127.0.0.1", 0), Handler)
    temporary = PORT_FILE.with_suffix(".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, PORT_FILE)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
