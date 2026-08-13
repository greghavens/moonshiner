#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by protected verification."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_IDS = [
    "getTask",
    "getResourceWarnings",
    "startSupportBundle",
    "getSupportBundleStatus",
]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path_template: str
    pattern: re.Pattern[str]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def route_pattern(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(r"([^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


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
        Route(
            item["operationId"],
            item["method"].upper(),
            item["path"],
            route_pattern(item["path"]),
        )
        for item in operations
    ]
    if [(route.method, route.path_template) for route in routes] != [
        ("GET", "/v1/tasks/{id}"),
        ("GET", "/v1/resource-warnings"),
        ("POST", "/v1/system/support-bundles"),
        ("GET", "/v1/system/support-bundles/{id}"),
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
        self.access_token = require_text(scenario, "accessToken")
        raw_cases = scenario.get("cases")
        if not isinstance(raw_cases, list) or len(raw_cases) != 4:
            raise RuntimeError("scenario must contain four cases")
        self.cases_by_task: dict[str, dict[str, Any]] = {}
        self.cases_by_resource: dict[str, dict[str, Any]] = {}
        self.cases_by_bundle: dict[str, dict[str, Any]] = {}
        self.cases_by_log_property: dict[str, dict[str, Any]] = {}
        for case in raw_cases:
            if not isinstance(case, dict):
                raise RuntimeError("scenario case is invalid")
            task_id = require_text(case, "taskId")
            resource_id = require_text(case, "resourceId")
            bundle_id = require_text(case, "bundleId")
            log_property = require_text(case, "logProperty")
            if any(
                key in target
                for key, target in (
                    (task_id, self.cases_by_task),
                    (resource_id, self.cases_by_resource),
                    (bundle_id, self.cases_by_bundle),
                    (log_property, self.cases_by_log_property),
                )
            ):
                raise RuntimeError("scenario identifiers must be unique")
            self.cases_by_task[task_id] = case
            self.cases_by_resource[resource_id] = case
            self.cases_by_bundle[bundle_id] = case
            self.cases_by_log_property[log_property] = case
        if set(self.cases_by_log_property) != {
            "vcLogs",
            "nsxLogs",
            "esxLogs",
            "sddcManagerLogs",
        }:
            raise RuntimeError("protected cases must cover every supported log mapping")
        self.status_polls = {bundle_id: 0 for bundle_id in self.cases_by_bundle}
        self.ready_to_start: str | None = None
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> tuple[Route, tuple[str, ...]] | None:
        for route in self.routes:
            if route.method != method:
                continue
            match = route.pattern.fullmatch(path)
            if match:
                return route, tuple(unquote(value) for value in match.groups())
        return None

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

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        matched = self.server.state.match(self.command, target.path)
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        route: Route | None = None
        captures: tuple[str, ...] = ()
        if matched is not None:
            route, captures = matched

        if route is None:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif not self._has_expected_common_headers():
            status, response = 400, error_body(
                "WIRE_HEADERS", "authorization or accept header is invalid"
            )
        elif route.operation_id == "getTask":
            status, response = self._get_task(captures, target.query, body)
        elif route.operation_id == "getResourceWarnings":
            status, response = self._get_warnings(target.query, body)
        elif route.operation_id == "startSupportBundle":
            status, response = self._start_bundle(target.query, body)
        elif route.operation_id == "getSupportBundleStatus":
            status, response = self._get_bundle_status(
                captures, target.query, body
            )
        else:  # Defensive: routes only come from the validated contract.
            status, response = 404, error_body("NOT_IN_CONTRACT", "unknown operation")

        header_values = {
            name.lower(): self.headers.get_all(name) or []
            for name in self.headers.keys()
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

    def _has_expected_common_headers(self) -> bool:
        expected_auth = f"Bearer {self.server.state.access_token}"
        accept_values = self.headers.get_all("Accept") or []
        return (
            self.headers.get_all("Authorization") == [expected_auth]
            and len(accept_values) == 1
            and "application/json" in accept_values[0]
        )

    def _get_task(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query or body or len(captures) != 1:
            return 400, error_body("WIRE_SHAPE", "getTask must be bodyless")
        case = self.server.state.cases_by_task.get(captures[0])
        if case is None:
            return 404, error_body("TASK_NOT_FOUND", "task does not exist")
        return 200, case["task"]

    def _get_warnings(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        if body:
            return 400, error_body("WIRE_SHAPE", "warning query must be bodyless")
        try:
            query = parse_qs(
                raw_query, keep_blank_values=True, strict_parsing=True
            )
        except ValueError:
            return 400, error_body("WIRE_SHAPE", "warning query is malformed")
        if set(query) != {"resourceType", "resourceIds"}:
            return 400, error_body(
                "WIRE_SHAPE", "warning query has an unset or missing member"
            )
        if any(len(values) != 1 or not values[0] for values in query.values()):
            return 400, error_body("WIRE_SHAPE", "warning query values must be singular")
        resource_id = query["resourceIds"][0]
        case = self.server.state.cases_by_resource.get(resource_id)
        if case is None:
            return 404, error_body("RESOURCE_NOT_FOUND", "resource does not exist")
        resource_type = require_text(case, "resourceType")
        expected_query = f"resourceType={resource_type}&resourceIds={resource_id}"
        if raw_query != expected_query:
            return 400, error_body(
                "WIRE_SHAPE", "warning query order or values changed"
            )
        with self.server.state.lock:
            self.server.state.ready_to_start = require_text(case, "logProperty")
        return 200, case["warningPage"]

    def _start_bundle(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "support bundle query must be absent")
        content_types = self.headers.get_all("Content-Type") or []
        if (
            len(content_types) != 1
            or content_types[0].split(";", 1)[0].strip().casefold()
            != "application/json"
        ):
            return 415, error_body(
                "MEDIA_TYPE", "support bundle content type must be JSON"
            )
        with self.server.state.lock:
            log_property = self.server.state.ready_to_start
            self.server.state.ready_to_start = None
        if log_property is None:
            return 409, error_body(
                "EVIDENCE_SEQUENCE", "warnings must be read before collecting logs"
            )
        case = self.server.state.cases_by_log_property[log_property]
        expected_body = json.dumps(
            {"logs": {log_property: True}}, separators=(",", ":")
        ).encode("utf-8")
        if body != expected_body:
            return 400, error_body(
                "WIRE_SHAPE", "support bundle contains unset or wrong log members"
            )
        return 202, case["bundleStarted"]

    def _get_bundle_status(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query or body or len(captures) != 1:
            return 400, error_body(
                "WIRE_SHAPE", "support bundle status must be bodyless"
            )
        bundle_id = captures[0]
        case = self.server.state.cases_by_bundle.get(bundle_id)
        if case is None:
            return 404, error_body("BUNDLE_NOT_FOUND", "bundle does not exist")
        with self.server.state.lock:
            self.server.state.status_polls[bundle_id] += 1
            poll = self.server.state.status_polls[bundle_id]
        if poll == 1:
            return 200, case["bundlePending"]
        if poll == 2:
            return 200, case["bundleComplete"]
        return 409, error_body("POLL_OVERSHOOT", "bundle was polled after completion")

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
