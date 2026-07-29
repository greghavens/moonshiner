#!/usr/bin/env python3
"""Threaded loopback mock pinned to the focused ListTier1 contract."""

from __future__ import annotations

import argparse
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
OPERATION_ID = "ListTier1"


def load_contract() -> tuple[str, dict[str, object]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = contract.get("operations")
    if not isinstance(operations, dict) or list(operations) != [OPERATION_ID]:
        raise RuntimeError("mock contract must name only ListTier1")
    operation = operations[OPERATION_ID]
    if (
        not isinstance(operation, dict)
        or operation.get("operationId") != OPERATION_ID
        or operation.get("method") != "GET"
        or operation.get("path") != "/infra/tier-1s"
    ):
        raise RuntimeError("unexpected ListTier1 contract")
    if contract.get("basePath") != "/policy/api/v1":
        raise RuntimeError("unexpected NSX Policy basePath")
    return str(contract["basePath"]), operation


BASE_PATH, OPERATION = load_contract()
ROUTE = BASE_PATH + str(OPERATION["path"])


def basic_authorization(username: str, password: str) -> str:
    payload = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(payload).decode("ascii")


class ContractState:
    def __init__(self, log_path: Path, scenario_path: Path):
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        credentials = scenario.get("credentials")
        responses = scenario.get("responses")
        if not isinstance(credentials, dict) or not isinstance(responses, list):
            raise ValueError("runtime scenario requires credentials and responses")

        self.authorizations: dict[str, str] = {}
        for label in ("old", "new"):
            value = credentials.get(label)
            if not isinstance(value, dict):
                raise ValueError(f"runtime scenario is missing {label} credential")
            username = value.get("username")
            password = value.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError(f"runtime {label} credential must be strings")
            self.authorizations[label] = basic_authorization(username, password)

        self.responses = responses
        gate = scenario.get("gate")
        self.old_cursor: str | None = None
        self.new_cursor: str | None = None
        if gate is not None:
            if not isinstance(gate, dict):
                raise ValueError("runtime gate must be an object")
            old_cursor = gate.get("old_cursor")
            new_cursor = gate.get("new_cursor")
            if not isinstance(old_cursor, str) or not isinstance(new_cursor, str):
                raise ValueError("runtime gate cursors must be strings")
            self.old_cursor = old_cursor
            self.new_cursor = new_cursor

        self.new_seen = threading.Event()
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.counter_lock = threading.Lock()
        self.next_request_id = 1

    def request_id(self) -> int:
        with self.counter_lock:
            value = self.next_request_id
            self.next_request_id += 1
            return value

    def credential_label(self, authorization: str | None) -> str:
        for label, expected in self.authorizations.items():
            if authorization == expected:
                return label
        return "unknown"

    def response_for(self, cursor: str | None) -> tuple[int, object | None]:
        for response in self.responses:
            if isinstance(response, dict) and response.get("cursor") == cursor:
                status = response.get("status")
                if isinstance(status, bool) or not isinstance(status, int):
                    break
                return status, response.get("body")
        return (
            404,
            {
                "error_code": 40465,
                "error_message": "no runtime response for cursor",
                "module_name": "contract-mock",
                "details": "The request reached the only contract route.",
            },
        )

    def append_log(self, record: dict[str, object]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server_version = "ContractPinnedNsxRotationMock/1"
    sys_version = ""

    @property
    def state(self) -> ContractState:
        return self.server.contract_state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError:
            length = 0
        return self.rfile.read(max(0, length))

    def _headers(self) -> dict[str, str]:
        return {name.lower(): value for name, value in self.headers.items()}

    def _send_with_completion(
        self,
        request_id: int,
        operation_id: str | None,
        status: int,
        value: object | None,
    ) -> None:
        if value is None:
            body = b""
        else:
            body = json.dumps(
                value, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")

        self.send_response(status)
        if value is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()

        # Emit completion before the final byte. The client cannot release its
        # lease until that byte arrives, making retirement ordering observable.
        if body:
            if len(body) > 1:
                self.wfile.write(body[:-1])
                self.wfile.flush()
            self.state.append_log(
                {
                    "event": "complete",
                    "request_id": request_id,
                    "operationId": operation_id,
                    "status": status,
                }
            )
            self.wfile.write(body[-1:])
            self.wfile.flush()
        else:
            self.state.append_log(
                {
                    "event": "complete",
                    "request_id": request_id,
                    "operationId": operation_id,
                    "status": status,
                }
            )
        self.close_connection = True

    def _arrival(
        self,
        request_id: int,
        operation_id: str | None,
        body: bytes,
        credential_label: str,
    ) -> None:
        self.state.append_log(
            {
                "event": "arrival",
                "request_id": request_id,
                "operationId": operation_id,
                "method": self.command,
                "raw_target": self.path,
                "headers": self._headers(),
                "body_utf8": body.decode("utf-8", errors="replace"),
                "credential_label": credential_label,
            }
        )

    def _not_found(self) -> None:
        request_id = self.state.request_id()
        body = self._read_body()
        label = self.state.credential_label(self.headers.get("Authorization"))
        self._arrival(request_id, None, body, label)
        self._send_with_completion(
            request_id,
            None,
            404,
            {
                "error_code": 40464,
                "error_message": "operation is not present in the pinned contract",
                "module_name": "contract-mock",
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        target = urlsplit(self.path)
        if target.path != ROUTE or target.fragment:
            self._not_found()
            return

        request_id = self.state.request_id()
        body = self._read_body()
        label = self.state.credential_label(self.headers.get("Authorization"))
        self._arrival(request_id, OPERATION_ID, body, label)

        query = parse_qsl(target.query, keep_blank_values=True)
        cursor_values = [value for name, value in query if name == "cursor"]
        cursor = cursor_values[0] if cursor_values else None

        if label == "unknown":
            self._send_with_completion(
                request_id,
                OPERATION_ID,
                401,
                {
                    "error_code": 40165,
                    "error_message": "credential not accepted",
                    "module_name": "authentication",
                    "details": "Basic authentication failed.",
                },
            )
            return

        if (
            self.state.new_cursor is not None
            and cursor == self.state.new_cursor
            and label == "new"
        ):
            self.state.new_seen.set()

        if (
            self.state.old_cursor is not None
            and cursor == self.state.old_cursor
            and label == "old"
            and not self.state.new_seen.wait(timeout=4.0)
        ):
            self._send_with_completion(
                request_id,
                OPERATION_ID,
                504,
                {
                    "error_code": 50465,
                    "error_message": "replacement request did not arrive",
                    "module_name": "contract-mock",
                },
            )
            return

        status, response = self.state.response_for(cursor)
        self._send_with_completion(
            request_id, OPERATION_ID, status, response
        )

    do_POST = _not_found
    do_PUT = _not_found
    do_PATCH = _not_found
    do_DELETE = _not_found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True, type=Path)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--scenario-file", required=True, type=Path)
    args = parser.parse_args()

    args.log_file.write_text("", encoding="utf-8")
    state = ContractState(args.log_file, args.scenario_file)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.contract_state = state  # type: ignore[attr-defined]
    pending_port_file = args.port_file.with_name(args.port_file.name + ".tmp")
    pending_port_file.write_text(str(server.server_port), encoding="ascii")
    os.replace(pending_port_file, args.port_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
