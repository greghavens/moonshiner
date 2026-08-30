#!/usr/bin/env python3
"""Loopback-only mock for the contract's single NSX Policy operation."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
OPERATION_ID = "UpdateGroupForDomain"


def load_contract() -> tuple[str, dict[str, object]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, dict) or list(operations) != [OPERATION_ID]:
        raise RuntimeError("mock contract must name only UpdateGroupForDomain")
    operation = operations[OPERATION_ID]
    if (
        operation.get("operationId") != OPERATION_ID
        or operation.get("method") != "PUT"
        or operation.get("path")
        != "/infra/domains/{domain-id}/groups/{group-id}"
    ):
        raise RuntimeError("unexpected operation contract")
    if contract.get("basePath") != "/policy/api/v1":
        raise RuntimeError("unexpected basePath contract")
    return contract["basePath"], operation


class ContractState:
    def __init__(self, log_path: Path, mode: str):
        self.log_path = log_path
        self.mode = mode
        self.resources: dict[tuple[str, str], dict[str, object]] = {}
        self.drop_pending = True
        self.attempt = 0

    def append_log(self, record: dict[str, object]) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())


BASE_PATH, OPERATION = load_contract()
_escaped_route = re.escape(BASE_PATH + str(OPERATION["path"]))
_escaped_route = _escaped_route.replace(
    re.escape("{domain-id}"), r"(?P<domain_id>[^/]+)"
)
_escaped_route = _escaped_route.replace(
    re.escape("{group-id}"), r"(?P<group_id>[^/]+)"
)
ROUTE = re.compile(rf"^{_escaped_route}$")


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractPinnedNsxPolicyMock/1"
    sys_version = ""

    @property
    def state(self) -> ContractState:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            length = 0
        return self.rfile.read(length)

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _not_found(self) -> None:
        body = self._body()
        self.state.append_log(
            {
                "operationId": None,
                "method": self.command,
                "raw_target": self.path,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body_utf8": body.decode("utf-8", errors="replace"),
                "status": 404,
            }
        )
        self._send_json(
            404,
            {
                "error_code": 40401,
                "error_message": "operation is not present in the pinned contract",
                "module_name": "contract-mock",
            },
        )

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        match = ROUTE.fullmatch(target.path)
        if match is None or target.query or target.fragment:
            self._not_found()
            return

        body = self._body()
        self.state.attempt += 1
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.state.append_log(
                {
                    "operationId": OPERATION_ID,
                    "attempt": self.state.attempt,
                    "method": self.command,
                    "raw_target": self.path,
                    "headers": {
                        name.lower(): value for name, value in self.headers.items()
                    },
                    "body_utf8": body.decode("utf-8", errors="replace"),
                    "effect": "none",
                    "resource_count": len(self.state.resources),
                    "response_dropped": False,
                    "status": 400,
                }
            )
            self._send_json(
                400,
                {
                    "error_code": 40001,
                    "error_message": "request body is not UTF-8 JSON",
                    "module_name": "contract-mock",
                },
            )
            return
        if not isinstance(decoded, dict) or decoded.get("resource_type") != "Group":
            self.state.append_log(
                {
                    "operationId": OPERATION_ID,
                    "attempt": self.state.attempt,
                    "method": self.command,
                    "raw_target": self.path,
                    "headers": {
                        name.lower(): value for name, value in self.headers.items()
                    },
                    "body_utf8": body.decode("utf-8"),
                    "effect": "none",
                    "resource_count": len(self.state.resources),
                    "response_dropped": False,
                    "status": 400,
                }
            )
            self._send_json(
                400,
                {
                    "error_code": 40002,
                    "error_message": "request body is not a Group",
                    "module_name": "contract-mock",
                },
            )
            return

        domain_id = unquote(match.group("domain_id"))
        group_id = unquote(match.group("group_id"))
        if self.state.mode == "http-error":
            self.state.append_log(
                {
                    "operationId": OPERATION_ID,
                    "attempt": self.state.attempt,
                    "method": self.command,
                    "raw_target": self.path,
                    "headers": {
                        name.lower(): value for name, value in self.headers.items()
                    },
                    "body_utf8": body.decode("utf-8"),
                    "effect": "none",
                    "resource_count": len(self.state.resources),
                    "response_dropped": False,
                    "status": 503,
                }
            )
            self._send_json(
                503,
                {
                    "error_code": 50362,
                    "error_message": "retry policy test",
                    "module_name": "contract-mock",
                    "details": "a received HTTP response must not be replayed",
                },
            )
            return

        key = (domain_id, group_id)
        previous = self.state.resources.get(key)
        if previous is None:
            effect = "created"
        elif previous == decoded:
            effect = "unchanged"
        else:
            effect = "replaced"
        self.state.resources[key] = copy.deepcopy(decoded)
        drop_response = self.state.drop_pending
        self.state.drop_pending = False

        self.state.append_log(
            {
                "operationId": OPERATION_ID,
                "attempt": self.state.attempt,
                "method": self.command,
                "raw_target": self.path,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body_utf8": body.decode("utf-8"),
                "effect": effect,
                "resource_count": len(self.state.resources),
                "response_dropped": drop_response,
            }
        )

        if drop_response:
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return

        response = copy.deepcopy(decoded)
        response["id"] = group_id
        response["path"] = f"/infra/domains/{domain_id}/groups/{group_id}"
        response["_revision"] = 0
        self._send_json(200, response)

    do_GET = _not_found
    do_POST = _not_found
    do_PATCH = _not_found
    do_DELETE = _not_found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument(
        "--mode",
        choices=("drop-after-commit", "http-error"),
        default="drop-after-commit",
    )
    args = parser.parse_args()

    args.log_file.write_text("", encoding="utf-8")
    state = ContractState(args.log_file, args.mode)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.contract_state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
