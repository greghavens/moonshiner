#!/usr/bin/env python3
"""Contract-pinned loopback VCF Installer used by protected verification."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATION_IDS = ["updateDepotSettings"]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path: str


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_routes(contract_path: Path) -> list[Route]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    if source.get("repositoryCommitSha") != PINNED_COMMIT:
        raise RuntimeError("contract repository commit is not pinned")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract specification path is not pinned")
    operations = contract.get("operations", [])
    operation_ids = [item.get("operationId") for item in operations]
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")
    routes = [
        Route(item["operationId"], item["method"].upper(), item["path"])
        for item in operations
    ]
    if [(route.method, route.path) for route in routes] != [
        ("PUT", "/v1/system/settings/depot")
    ]:
        raise RuntimeError("contract route projection changed")
    return routes


def require_nonblank(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"scenario {name} is invalid")
    return value


class MockState:
    def __init__(
        self,
        routes: list[Route],
        request_log: Path,
        scenario: dict[str, Any],
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        self.access_token = require_nonblank(scenario, "accessToken")
        response_plan = scenario.get("responsePlan", [500, 202])
        if (
            not isinstance(response_plan, list)
            or not response_plan
            or any(
                not (
                    isinstance(item, int)
                    and not isinstance(item, bool)
                    and 100 <= item <= 599
                    or isinstance(item, str)
                    and item in {"disconnect", "partial-202"}
                )
                for item in response_plan
            )
        ):
            raise RuntimeError("scenario responsePlan is invalid")
        self.response_plan: list[int | str] = response_plan
        self.success_response_supplied = "successResponse" in scenario
        self.success_response = scenario.get("successResponse")
        success_raw_body = scenario.get("successRawBody")
        if success_raw_body is not None and not isinstance(success_raw_body, str):
            raise RuntimeError("scenario successRawBody is invalid")
        self.success_raw_body = success_raw_body
        success_content_type = scenario.get("successContentType", "application/json")
        if not isinstance(success_content_type, str) or not success_content_type:
            raise RuntimeError("scenario successContentType is invalid")
        self.success_content_type = success_content_type
        self.representation: dict[str, Any] | None = None
        self.valid_attempts = 0
        self.effect_count = 0
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> Route | None:
        return next(
            (
                route
                for route in self.routes
                if route.method == method and route.path == path
            ),
            None,
        )

    def apply(
        self, representation: dict[str, Any]
    ) -> tuple[int | str, bool, int, int]:
        """Apply a scripted response while preserving replacement semantics."""
        with self.lock:
            self.valid_attempts += 1
            plan_index = min(self.valid_attempts - 1, len(self.response_plan) - 1)
            action = self.response_plan[plan_index]
            commits = action in {202, 500, "disconnect", "partial-202"}
            effect_applied = commits and representation != self.representation
            if effect_applied:
                self.representation = representation
                self.effect_count += 1
            return action, effect_applied, self.effect_count, self.valid_attempts

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, ContractHandler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        route = self.server.state.match(self.command, target.path)
        raw_length = self.headers.get("Content-Length")
        try:
            body_length = int(raw_length or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))
        effect_applied: bool | None = None
        effect_count: int | None = None
        valid_attempt: int | None = None
        wire_mode = "json"

        if route is None:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif route.operation_id == "updateDepotSettings":
            (
                status,
                response,
                effect_applied,
                effect_count,
                valid_attempt,
                wire_mode,
            ) = self._update_depot(target.query, body)
        else:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "unknown contract operation"
            )

        header_values: dict[str, list[str]] = {}
        for name in self.headers.keys():
            header_values[name.lower()] = self.headers.get_all(name) or []
        self.server.state.record(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "headerValues": header_values,
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
                "effectApplied": effect_applied,
                "effectCount": effect_count,
                "validAttempt": valid_attempt,
            }
        )
        if wire_mode == "disconnect":
            self._disconnect()
        elif wire_mode == "partial-202":
            self._send_partial_success(response)
        else:
            self._send_response(status, response)

    def _update_depot(
        self, raw_query: str, body: bytes
    ) -> tuple[int | None, Any, bool | None, int | None, int | None, str]:
        state = self.server.state
        if (self.headers.get_all("Authorization") or []) != [
            f"Bearer {state.access_token}"
        ]:
            return 401, error_body("UNAUTHORIZED", "invalid access token"), None, None, None, "json"
        if (self.headers.get_all("Accept") or []) != ["application/json"]:
            return 400, error_body("WIRE_SHAPE", "invalid Accept header"), None, None, None, "json"
        if (self.headers.get_all("Content-Type") or []) != ["application/json"]:
            return 400, error_body("WIRE_SHAPE", "invalid Content-Type header"), None, None, None, "json"
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "query is not allowed"), None, None, None, "json"
        try:
            representation = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, error_body("WIRE_SHAPE", "body is not JSON"), None, None, None, "json"
        if not valid_depot_settings(representation):
            return 400, error_body("WIRE_SHAPE", "body violates DepotSettings"), None, None, None, "json"

        action, effect_applied, effect_count, attempt = state.apply(representation)
        if action == "disconnect":
            return None, None, effect_applied, effect_count, attempt, action
        if action == "partial-202":
            response = (
                state.success_response
                if state.success_response_supplied
                else representation
            )
            return 202, response, effect_applied, effect_count, attempt, action

        status = action
        if status == 500:
            response: Any = error_body(
                "COMMIT_RESPONSE_FAILED",
                "replacement committed before response failure",
            )
        elif status == 202:
            response = (
                state.success_response
                if state.success_response_supplied
                else representation
            )
        else:
            response = error_body("SCRIPTED_RESPONSE", f"scripted HTTP {status}")
        return status, response, effect_applied, effect_count, attempt, "json"

    def _send_response(self, status: int | None, value: Any) -> None:
        if status is None:
            self._disconnect()
            return
        state = self.server.state
        if status == 202 and state.success_raw_body is not None:
            payload = state.success_raw_body.encode("utf-8")
        else:
            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        content_type = (
            state.success_content_type if status == 202 else "application/json"
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
            self.wfile.flush()

    def _send_partial_success(self, value: Any) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        cutoff = max(1, len(payload) // 2)
        self.send_response(202)
        self.send_header("Content-Type", self.server.state.success_content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload[:cutoff])
        self.wfile.flush()
        self._disconnect()

    def _disconnect(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()


def valid_depot_settings(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"vmwareAccount"}:
        return False
    account = value.get("vmwareAccount")
    if not isinstance(account, dict):
        return False
    allowed = {"downloadToken", "downloadActivationCode"}
    if not set(account) <= allowed or "downloadToken" not in account:
        return False
    token = account.get("downloadToken")
    if not isinstance(token, str) or not token.strip() or len(token) > 32:
        return False
    if "downloadActivationCode" in account:
        code = account["downloadActivationCode"]
        if not isinstance(code, str) or not code.strip():
            return False
    return True


def error_body(code: str, message: str) -> dict[str, object]:
    return {"errorCode": code, "message": message, "arguments": []}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    routes = load_routes(args.contract)
    state = MockState(routes, args.request_log, read_json(args.scenario))
    server = ContractServer(("127.0.0.1", 0), state)
    ready_payload = {
        "host": "127.0.0.1",
        "port": server.server_address[1],
        "operationIds": [route.operation_id for route in routes],
    }
    args.ready.write_text(json.dumps(ready_payload), encoding="utf-8")
    with server:
        server.serve_forever(poll_interval=0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
