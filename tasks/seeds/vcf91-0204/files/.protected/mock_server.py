#!/usr/bin/env python3
"""Contract-pinned loopback VCF Installer for protected verification."""

from __future__ import annotations

import json
import math
import os
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATION_IDS = ["getTasks", "refreshAccessToken"]


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
    if [item.get("operationId") for item in operations] != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")
    routes = [
        Route(item["operationId"], item["method"].upper(), item["path"])
        for item in operations
    ]
    if [(route.method, route.path) for route in routes] != [
        ("GET", "/v1/tasks"),
        ("PATCH", "/v1/tokens/access-token/refresh"),
    ]:
        raise RuntimeError("contract route projection changed")
    return routes


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
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
        self.old_token = require_text(scenario, "oldToken")
        self.new_token = require_text(scenario, "newToken")
        self.refresh_token_id = require_text(scenario, "refreshTokenId")
        self.page_size = scenario.get("pageSize")
        self.tasks = scenario.get("tasks")
        if (
            isinstance(self.page_size, bool)
            or not isinstance(self.page_size, int)
            or not 1 <= self.page_size <= 2_147_483_647
        ):
            raise RuntimeError("scenario pageSize is invalid")
        if not isinstance(self.tasks, list) or len(self.tasks) != 5:
            raise RuntimeError("scenario tasks are invalid")
        self.total_pages = math.ceil(len(self.tasks) / self.page_size)
        if self.total_pages != 3:
            raise RuntimeError("protected scenario must span exactly three pages")
        self.expiry_sent = False
        self.refreshed = False
        self.successful_pages: list[int] = []
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

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        route = self.server.state.match(self.command, target.path)
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        if route is None:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif route.operation_id == "getTasks":
            status, response = self._get_tasks(target.query, body)
        elif route.operation_id == "refreshAccessToken":
            status, response = self._refresh(target.query, body)
        else:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "unknown contract operation"
            )

        header_values = {
            name.lower(): self.headers.get_all(name) or [] for name in self.headers.keys()
        }
        self.server.state.record(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "query": parse_qs(target.query, keep_blank_values=True),
                "headerValues": header_values,
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    def _get_tasks(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        try:
            query = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return 400, error_body("WIRE_SHAPE", "query is malformed")
        expected_keys = (
            {"pageSize"}
            if not state.successful_pages
            else {"pageNumber", "pageSize"}
        )
        if body or set(query) != expected_keys:
            return 400, error_body("WIRE_SHAPE", "unexpected body or query member")
        if any(len(values) != 1 or values[0] == "" for values in query.values()):
            return 400, error_body("WIRE_SHAPE", "query values must be singular")
        if query["pageSize"][0] != str(state.page_size):
            return 400, error_body("WIRE_SHAPE", "pageSize changed")

        page_number = 0
        if "pageNumber" in query:
            try:
                page_number = int(query["pageNumber"][0])
            except ValueError:
                return 400, error_body("WIRE_SHAPE", "pageNumber is not an integer")
        expected_page = len(state.successful_pages)
        if page_number != expected_page:
            return 409, error_body("WORK_REPLAY", "page sequence changed")
        expected_target = (
            f"pageSize={state.page_size}"
            if page_number == 0
            else f"pageNumber={page_number}&pageSize={state.page_size}"
        )
        if raw_query != expected_target:
            return 400, error_body("WIRE_SHAPE", "query order or encoding changed")

        authorization = self.headers.get("Authorization")
        if page_number == 1 and not state.expiry_sent:
            state.expiry_sent = True
            if authorization != f"Bearer {state.old_token}":
                return 403, error_body(
                    "AUTHORIZATION", "unexpected token before expiry"
                )
            return 401, error_body("ACCESS_TOKEN_EXPIRED", "access token expired")

        expected_token = state.new_token if state.refreshed else state.old_token
        if authorization != f"Bearer {expected_token}":
            return 403, error_body("AUTHORIZATION", "unexpected access token")
        if page_number >= state.total_pages:
            return 400, error_body("PAGE_RANGE", "page is out of range")

        start = page_number * state.page_size
        elements = list(state.tasks[start : start + state.page_size])
        if page_number % 2 == 0:
            elements.reverse()
        state.successful_pages.append(page_number)
        return 200, {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": page_number,
                "pageSize": state.page_size,
                "totalElements": len(state.tasks),
                "totalPages": state.total_pages,
            },
        }

    def _refresh(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "refresh query must be absent")
        if not state.expiry_sent or state.refreshed:
            return 409, error_body("REFRESH_SEQUENCE", "refresh is out of sequence")
        if self.headers.get("Authorization") != f"Bearer {state.old_token}":
            return 403, error_body("AUTHORIZATION", "refresh used the wrong token")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if media_type.strip().casefold() != "application/json":
            return 415, error_body("MEDIA_TYPE", "refresh must be JSON")
        expected = json.dumps(state.refresh_token_id, ensure_ascii=False).encode("utf-8")
        if body != expected:
            return 400, error_body("WIRE_SHAPE", "refresh body is not the JSON string")
        state.refreshed = True
        return 200, state.new_token

    def _send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"errorCode": code, "message": message, "arguments": []}


def write_port(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(str(port))
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        raise SystemExit(
            "usage: mock_server.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )
    port_file, log_file, contract_file, scenario_file = map(Path, argv[1:])
    routes = load_routes(contract_file)
    state = MockState(routes, log_file, read_json(scenario_file))
    server = ContractServer(("127.0.0.1", 0), state)
    write_port(port_file, int(server.server_address[1]))
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
