#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager fixture for user access."""

from __future__ import annotations

import argparse
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_ROUTES = [
    ("getUsers", "GET", "/v1/users"),
    ("addUsers", "POST", "/v1/users"),
]


class FixtureState:
    """Mutable state shared by the fixture's request handlers."""

    def __init__(
        self,
        contract_path: Path,
        scenario_path: Path,
        log_path: Path,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        routes = [
            (item.get("operationId"), item.get("method"), item.get("path"))
            for item in contract.get("operations", [])
        ]
        if routes != EXPECTED_ROUTES:
            raise RuntimeError("loopback fixture contract routes do not match")
        self.routes = {
            (method, path): operation_id
            for operation_id, method, path in routes
        }

        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        expected_keys = {
            "access_token",
            "assigned_id",
            "creation_timestamp",
            "drop_first_add",
            "fault",
            "target",
            "users",
        }
        if set(scenario) != expected_keys:
            raise RuntimeError("loopback scenario has an unexpected shape")
        if (
            not isinstance(scenario["access_token"], str)
            or not isinstance(scenario["assigned_id"], str)
            or not isinstance(scenario["creation_timestamp"], str)
            or not isinstance(scenario["drop_first_add"], bool)
            or scenario["fault"]
            not in {"none", "bad_page", "get_201", "add_400"}
            or not isinstance(scenario["target"], dict)
            or not isinstance(scenario["users"], list)
            or any(not isinstance(item, dict) for item in scenario["users"])
        ):
            raise RuntimeError("loopback scenario values are invalid")

        self.scenario = scenario
        self.users = [dict(item) for item in scenario["users"]]
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.reverse_next = True
        self.add_count = 0

    def append_log(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open(
                "a",
                encoding="utf-8",
                newline="\n",
            ) as stream:
                stream.write(line + "\n")

    def collection_payload(self) -> dict[str, Any]:
        with self.state_lock:
            elements = [dict(item) for item in self.users]
            if self.reverse_next:
                elements.reverse()
            self.reverse_next = not self.reverse_next
        count = len(elements)
        return {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": count,
                "totalElements": count,
                "totalPages": 0 if count == 0 else 1,
            },
        }


class FixtureServer(ThreadingHTTPServer):
    state: FixtureState


class Handler(BaseHTTPRequestHandler):
    """Serve only the methods and path present in the protected contract."""

    server: FixtureServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        operation_id = self.server.state.routes.get(
            (self.command, parsed.path)
        )
        body = self._read_body()
        entry: dict[str, Any] = {
            "operationId": operation_id,
            "method": self.command,
            "rawTarget": self.path,
            "accept": self.headers.get("Accept"),
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "body": body.decode("utf-8", errors="replace"),
        }

        if operation_id == "getUsers":
            self._get_users(entry, parsed, body)
        elif operation_id == "addUsers":
            self._add_users(entry, parsed, body)
        else:
            self._send_json(
                entry,
                404,
                {
                    "errorCode": "ROUTE_NOT_IN_CONTRACT",
                    "message": "Route is not served by this fixture",
                },
            )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > 1_048_576:
            return b""
        return self.rfile.read(length)

    def _common_headers_are_valid(self, entry: dict[str, Any]) -> bool:
        token = self.server.state.scenario["access_token"]
        return (
            entry["accept"] == "application/json"
            and entry["authorization"] == "Bearer " + token
        )

    def _get_users(
        self,
        entry: dict[str, Any],
        parsed: Any,
        body: bytes,
    ) -> None:
        valid = (
            not parsed.query
            and not parsed.fragment
            and not body
            and entry["contentType"] is None
            and self._common_headers_are_valid(entry)
        )
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "BAD_GET_USERS",
                    "message": "getUsers request did not match the contract",
                },
            )
            return

        state = self.server.state
        payload = state.collection_payload()
        entry["servedElementIds"] = [
            item.get("id") for item in payload["elements"]
        ]
        if state.scenario["fault"] == "bad_page":
            payload["pageMetadata"]["pageSize"] += 1
        status = 201 if state.scenario["fault"] == "get_201" else 200
        self._send_json(entry, status, payload)

    def _add_users(
        self,
        entry: dict[str, Any],
        parsed: Any,
        body: bytes,
    ) -> None:
        state = self.server.state
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        expected_target = state.scenario["target"]
        valid = (
            not parsed.query
            and not parsed.fragment
            and entry["contentType"] == "application/json"
            and self._common_headers_are_valid(entry)
            and decoded == [expected_target]
        )
        if not valid:
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "BAD_ADD_USERS",
                    "message": "addUsers request did not match the contract",
                },
            )
            return
        if state.scenario["fault"] == "add_400":
            self._send_json(
                entry,
                400,
                {
                    "errorCode": "ADD_REJECTED",
                    "message": "The requested grant was rejected",
                },
            )
            return

        target_identity = self._identity(expected_target)
        with state.state_lock:
            state.add_count += 1
            duplicate = any(
                self._identity(item) == target_identity
                for item in state.users
            )
            if not duplicate:
                created = {
                    "id": state.scenario["assigned_id"],
                    **expected_target,
                    "creationTimestamp": state.scenario[
                        "creation_timestamp"
                    ],
                }
                state.users.append(created)
            should_drop = (
                not duplicate
                and state.scenario["drop_first_add"]
                and state.add_count == 1
            )

        if duplicate:
            self._send_json(
                entry,
                409,
                {
                    "errorCode": "USER_ACCESS_ALREADY_EXISTS",
                    "message": "Duplicate user access was rejected",
                },
            )
            return
        if should_drop:
            entry["effectApplied"] = True
            entry["status"] = None
            entry["connectionDropped"] = True
            state.append_log(entry)
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return

        payload = state.collection_payload()
        entry["servedElementIds"] = [
            item.get("id") for item in payload["elements"]
        ]
        self._send_json(entry, 201, payload)

    @staticmethod
    def _identity(item: dict[str, Any]) -> tuple[str, str, str]:
        values: list[str] = []
        for key in ("name", "domain", "type"):
            value = item.get(key)
            values.append(value.casefold() if isinstance(value, str) else "")
        return (values[0], values[1], values[2])

    def _send_json(
        self,
        entry: dict[str, Any],
        status: int,
        payload: Any,
    ) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        entry["status"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--port-file", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = FixtureState(args.contract, args.scenario, args.log)
    server = FixtureServer(("127.0.0.1", 0), Handler)
    server.state = state
    args.port_file.write_text(
        str(server.server_address[1]),
        encoding="ascii",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
