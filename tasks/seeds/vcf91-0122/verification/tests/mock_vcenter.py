#!/usr/bin/env python3
"""Contract-pinned loopback fixture for vcf91-0122."""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


EXPECTED_OPERATION = "Content.LocalLibrary_create"


def load_route(contract_path: Path) -> tuple[str, dict[str, Any]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("the protected contract must name exactly one operation")
    operation = operations[0]
    if (
        operation.get("operationId") != EXPECTED_OPERATION
        or operation.get("method") != "POST"
        or operation.get("path") != "/content/local-library"
        or contract.get("base_path") != "/api"
    ):
        raise ValueError("unexpected protected contract operation")
    return contract["base_path"] + operation["path"], operation


class FixtureState:
    def __init__(
        self,
        route: str,
        operation: dict[str, Any],
        config: dict[str, Any],
        log_path: Path,
    ) -> None:
        self.route = route
        self.operation = operation
        self.config = config
        self.log_path = log_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.effects = 0
        self.by_token: dict[str, tuple[bytes, str]] = {}

    def append_log(self, event: dict[str, Any]) -> None:
        encoded = (
            json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        with self.log_path.open("ab", buffering=0) as stream:
            stream.write(encoded)
            os.fsync(stream.fileno())


def values(handler: BaseHTTPRequestHandler, name: str) -> list[str]:
    return handler.headers.get_all(name, failobj=[])


def only(values_: list[str]) -> str | None:
    return values_[0] if len(values_) == 1 else None


def make_handler(state: FixtureState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else 0
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""

            sessions = values(self, "vmware-api-session-id")
            tokens = values(self, "Client-Token")
            accepts = values(self, "Accept")
            content_types = values(self, "Content-Type")
            content_lengths = values(self, "Content-Length")
            transfer_encodings = values(self, "Transfer-Encoding")
            authorizations = values(self, "Authorization")
            route_match = self.path == state.route
            method_match = self.command == state.operation["method"]
            header_match = (
                sessions == [state.config["session_id"]]
                and tokens == [state.config["client_token"]]
            )

            with state.lock:
                state.sequence += 1
                sequence = state.sequence
                behavior = state.config["behavior"]
                new_effect = False
                contract_match = route_match and method_match and header_match

                if contract_match and not (
                    sequence == 1 and behavior == "http_503"
                ):
                    token = tokens[0]
                    previous = state.by_token.get(token)
                    if previous is None:
                        state.by_token[token] = (
                            body,
                            state.config["library_id"],
                        )
                        state.effects += 1
                        new_effect = True
                    elif previous[0] != body:
                        contract_match = False

                event = {
                    "seq": sequence,
                    "operation_id": (
                        state.operation["operationId"]
                        if route_match and method_match
                        else None
                    ),
                    "method": self.command,
                    "raw_target": self.path,
                    "session": only(sessions),
                    "session_count": len(sessions),
                    "client_token": only(tokens),
                    "client_token_count": len(tokens),
                    "accept": only(accepts),
                    "accept_count": len(accepts),
                    "content_type": only(content_types),
                    "content_type_count": len(content_types),
                    "content_length": only(content_lengths),
                    "content_length_count": len(content_lengths),
                    "transfer_encoding_count": len(transfer_encodings),
                    "authorization_count": len(authorizations),
                    "body_length": len(body),
                    "body_b64": base64.b64encode(body).decode("ascii"),
                    "new_effect": new_effect,
                    "effect_count": state.effects,
                }
                state.append_log(event)

            if not contract_match:
                self.json_response(404, {"message": "route not in contract"})
                return
            if sequence == 1 and behavior == "http_503":
                self.json_response(
                    503,
                    {"message": state.config["server_error"]},
                )
                return
            if behavior == "drop_every" or (
                sequence == 1 and behavior == "drop_after_commit"
            ):
                self.drop_connection()
                return
            if sequence == 1 and behavior == "malformed_201":
                self.raw_response(201, b"{}")
                return
            self.json_response(201, state.config["library_id"])

        def do_GET(self) -> None:
            self.json_response(404, {"message": "route not in contract"})

        def raw_response(self, status: int, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()

        def json_response(self, status: int, value: object) -> None:
            data = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.raw_response(status, data)

        def drop_connection(self) -> None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            self.close_connection = True

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    route, operation = load_route(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    required = {
        "behavior",
        "session_id",
        "client_token",
        "library_id",
        "server_error",
    }
    if set(config) != required:
        raise ValueError("fixture config has an unexpected shape")
    if config["behavior"] not in {
        "success",
        "drop_after_commit",
        "drop_every",
        "http_503",
        "malformed_201",
    }:
        raise ValueError("unsupported fixture behavior")

    args.log.write_bytes(b"")
    state = FixtureState(route, operation, config, args.log)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(state),
    )
    server.daemon_threads = True
    print(
        json.dumps(
            {"host": "127.0.0.1", "port": server.server_port},
            separators=(",", ":"),
        ),
        flush=True,
    )
    server.serve_forever(poll_interval=0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
