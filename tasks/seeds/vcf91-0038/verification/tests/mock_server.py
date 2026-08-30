#!/usr/bin/env python3
"""Loopback-only mock for the three operations in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    names = {operation["operationId"] for operation in operations}
    required = {"createToken", "getDomain", "refreshAccessToken"}
    if names != required or len(operations) != 3:
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
        suffix = secrets.token_hex(8)
        self.username = f'svc-"inventory"-{suffix}@vsphere.local'
        self.password = f'pw\\{suffix}\n"rotate"'
        self.initial_token = "access_old_" + secrets.token_hex(16)
        self.refreshed_token = "access_new_" + secrets.token_hex(16)
        self.refresh_token = "refresh id+" + secrets.token_hex(12)
        self.domain_ids = [
            "domain mgmt+" + secrets.token_hex(5),
            "domain/finance " + secrets.token_hex(5),
            "domain-研究-" + secrets.token_hex(5),
        ]
        self.domain_names = [
            'Management "Core"',
            "Finance\\Edge",
            "Research Ω",
        ]

    def append_log(self, entry: dict) -> None:
        with self.log_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")

    def domain(self, domain_id: str) -> dict:
        index = self.domain_ids.index(domain_id)
        return {
            "id": domain_id,
            "name": self.domain_names[index],
            "status": "ACTIVE",
            "type": "MANAGEMENT" if index == 0 else "VI",
        }


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch()

    def do_GET(self) -> None:  # noqa: N802
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
        body = body_bytes.decode("utf-8")
        operation, path_parameters = self.match_operation(self.command, split.path)
        self.server.append_log(
            {
                "operationId": None if operation is None else operation["operationId"],
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )

        if operation is None:
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        operation_id = operation["operationId"]
        if operation_id == "createToken":
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"message": "Malformed JSON"})
                return
            if (
                not isinstance(payload, dict)
                or payload.get("username") != self.server.username
                or payload.get("password") != self.server.password
            ):
                self.send_json(400, {"message": "Bad credentials document"})
                return
            self.send_json(
                201,
                {
                    "accessToken": self.server.initial_token,
                    "refreshToken": {"id": self.server.refresh_token},
                },
            )
            return

        if operation_id == "refreshAccessToken":
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"message": "Malformed JSON"})
                return
            if payload != self.server.refresh_token:
                self.send_json(404, {"message": "Refresh token not found"})
                return
            self.send_json(200, self.server.refreshed_token)
            return

        if operation_id == "getDomain":
            domain_id = unquote(path_parameters["id"])
            if domain_id not in self.server.domain_ids:
                self.send_json(404, {"message": "Domain not found"})
                return
            authorization = self.headers.get("Authorization")
            if authorization == "Bearer " + self.server.initial_token:
                if domain_id == self.server.domain_ids[0]:
                    self.send_json(200, self.server.domain(domain_id))
                else:
                    self.send_json(
                        401,
                        {
                            "errorCode": "TOKEN_EXPIRED",
                            "message": "Access token expired",
                        },
                    )
                return
            if authorization == "Bearer " + self.server.refreshed_token:
                self.send_json(200, self.server.domain(domain_id))
                return
            self.send_json(401, {"message": "Missing or invalid bearer token"})
            return

        self.send_json(500, {"message": "Unreachable contract operation"})

    def match_operation(self, method: str, path: str):
        for (candidate_method, template), operation in self.server.routes.items():
            if method != candidate_method:
                continue
            names = re.findall(r"\{([^}]+)\}", template)
            pattern = "^" + re.sub(r"\{[^}]+\}", r"([^/]+)", template) + "$"
            match = re.match(pattern, path)
            if match:
                return operation, dict(zip(names, match.groups(), strict=True))
        return None, {}

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
                "username": server.username,
                "password": server.password,
                "initial_token": server.initial_token,
                "refreshed_token": server.refreshed_token,
                "refresh_token": server.refresh_token,
                "domain_ids": server.domain_ids,
                "domain_names": server.domain_names,
            }
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
