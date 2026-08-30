#!/usr/bin/env python3
"""Contract-pinned loopback mock for the focused Log Management operations."""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


def _compile_template(template: str) -> re.Pattern[str]:
    parts = ["^"]
    cursor = 0
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"([^/]+)")
        cursor = match.end()
    parts.extend((re.escape(template[cursor:]), "$"))
    return re.compile("".join(parts))


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        contract_path: Path,
        log_path: Path,
        state_path: Path,
        old_exchange_delay_ms: int,
    ) -> None:
        super().__init__(address, ContractHandler)
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations missing")
        self.routes: list[tuple[str, str, re.Pattern[str]]] = []
        for operation in operations:
            self.routes.append(
                (
                    operation["contractName"],
                    operation["method"],
                    _compile_template(operation["pathTemplate"]),
                )
            )
        names = [item[0] for item in self.routes]
        if names != [
            "createAgentSecret",
            "createAgentSession",
            "revokeAgentSecret",
        ]:
            raise ValueError("unexpected contract operation allow-list")
        self.log_path = log_path
        self.state_path = state_path
        self.old_exchange_delay = old_exchange_delay_ms / 1000.0
        self.state_lock = threading.Lock()
        self.secrets_by_name: dict[str, str] = {}
        self.names_by_secret: dict[str, str] = {}
        self.creation_order: list[str] = []
        self.revoked: set[str] = set()
        self.active_exchanges: dict[str, int] = {}
        self.sessions: dict[str, list[dict[str, Any]]] = {}
        self.early_revocations: list[str] = []
        self._write_state_locked()

    def match(
        self, method: str, raw_target: str
    ) -> tuple[str | None, list[str]]:
        path = urlsplit(raw_target).path
        for name, expected_method, pattern in self.routes:
            match = pattern.fullmatch(path)
            if method == expected_method and match is not None:
                return name, [unquote(value) for value in match.groups()]
        return None, []

    def append_request(
        self, handler: BaseHTTPRequestHandler, operation: str | None, body: bytes
    ) -> None:
        entry = {
            "operation": operation,
            "method": handler.command,
            "rawTarget": handler.path,
            "headers": [
                [name, value] for name, value in handler.headers.raw_items()
            ],
            "bodyBase64": base64.b64encode(body).decode("ascii"),
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def write_state(self) -> None:
        with self.state_lock:
            self._write_state_locked()

    def _write_state_locked(self) -> None:
        value = {
            "secrets": self.secrets_by_name,
            "creationOrder": self.creation_order,
            "revoked": sorted(self.revoked),
            "activeExchanges": self.active_exchanges,
            "sessions": self.sessions,
            "earlyRevocations": self.early_revocations,
        }
        self.state_path.write_text(
            json.dumps(value, separators=(",", ":")), encoding="utf-8"
        )


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length_text = self.headers.get("Content-Length")
        try:
            length = 0 if length_text is None else int(length_text)
        except ValueError:
            self._respond(400, {"error": "invalid content length"})
            return
        body = self.rfile.read(length) if length else b""
        operation, captures = self.server.match(self.command, self.path)
        self.server.append_request(self, operation, body)
        if operation is None:
            self._respond(404, {"error": "route not in contract"})
        elif operation == "createAgentSecret":
            self._create_secret(body)
        elif operation == "createAgentSession":
            self._create_session(body)
        elif operation == "revokeAgentSecret":
            self._revoke(captures, body)
        else:
            self._respond(500, {"error": "unknown contract operation"})

    def _create_secret(self, body: bytes) -> None:
        value = self._body_object(body)
        if value is None:
            return
        name = value.get("name")
        if not isinstance(name, str) or not name:
            self._respond(400, {"error": "name required"})
            return
        with self.server.state_lock:
            if name in self.server.secrets_by_name:
                self._respond(400, {"error": "duplicate name"})
                return
            secret = "secret_" + secrets.token_urlsafe(24)
            self.server.secrets_by_name[name] = secret
            self.server.names_by_secret[secret] = name
            self.server.creation_order.append(name)
            self.server.active_exchanges[name] = 0
            self.server.sessions[name] = []
            self.server._write_state_locked()
        self._respond(
            201,
            {
                "id": "id_" + secrets.token_hex(8),
                "name": name,
                "secret": secret,
                "status": "ACTIVE",
            },
        )

    def _create_session(self, body: bytes) -> None:
        value = self._body_object(body)
        if value is None:
            return
        secret = value.get("secret")
        ttl = value.get("ttl", 1_800_000)
        if not isinstance(secret, str):
            self._respond(400, {"error": "secret required"})
            return
        with self.server.state_lock:
            name = self.server.names_by_secret.get(secret)
            if name is None or name in self.server.revoked:
                self._respond(400, {"error": "invalid secret"})
                return
            self.server.active_exchanges[name] += 1
            is_old = name == self.server.creation_order[0]
            self.server._write_state_locked()
        status = 500
        response: object = {"error": "exchange did not complete"}
        try:
            if is_old:
                time.sleep(self.server.old_exchange_delay)
            with self.server.state_lock:
                if name in self.server.revoked:
                    status = 400
                    response = {"error": "secret revoked in flight"}
                else:
                    token = "access_" + secrets.token_urlsafe(24)
                    rotated = "next_" + secrets.token_urlsafe(24)
                    session = {
                        "access_token": token,
                        "name": name,
                        "new_secret": rotated,
                        "ttl": ttl,
                    }
                    self.server.sessions[name].append(session)
                    self.server._write_state_locked()
                    status = 200
                    response = session
        finally:
            with self.server.state_lock:
                self.server.active_exchanges[name] -= 1
                self.server._write_state_locked()
        self._respond(status, response)

    def _revoke(self, captures: list[str], body: bytes) -> None:
        if body:
            self._respond(400, {"error": "revoke has no request body"})
            return
        if len(captures) != 1:
            self._respond(404, {"error": "missing secret name"})
            return
        name = captures[0]
        with self.server.state_lock:
            if name not in self.server.secrets_by_name:
                self._respond(400, {"error": "unknown secret"})
                return
            if self.server.active_exchanges.get(name, 0):
                self.server.early_revocations.append(name)
            self.server.revoked.add(name)
            self.server._write_state_locked()
        self._respond(
            200,
            {
                "id": "id_" + secrets.token_hex(8),
                "name": name,
                "status": "REVOKED",
            },
        )

    def _body_object(self, body: bytes) -> dict[str, object] | None:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid JSON"})
            return None
        if not isinstance(value, dict):
            self._respond(400, {"error": "object required"})
            return None
        return value

    def _respond(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--old-exchange-delay-ms", type=int, default=1200)
    arguments = parser.parse_args()
    server = ContractServer(
        ("127.0.0.1", 0),
        arguments.contract,
        arguments.log,
        arguments.state,
        arguments.old_exchange_delay_ms,
    )
    arguments.port_file.write_text(
        json.dumps({"port": server.server_address[1]}), encoding="utf-8"
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
