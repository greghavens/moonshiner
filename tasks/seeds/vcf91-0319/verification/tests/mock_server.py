#!/usr/bin/env python3
"""Contract-pinned loopback VCF Automation service used by protected verification.

Every callable route is derived from ``docs/contract.json``. An operation the
contract does not name is not served. Every request is appended to a JSONL log
that the verifier reads.
"""

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
from urllib.parse import parse_qs, unquote, urlencode, urlsplit


EXPECTED_OPERATION_KEYS = [
    "getCatalogItems",
    "requestCatalogItemInstances",
    "getDeploymentById",
    "submitDeploymentActionRequest",
]
EXPECTED_ROUTES = [
    ("GET", "/catalog/api/items"),
    ("POST", "/catalog/api/items/{id}/request"),
    ("GET", "/deployment/api/deployments/{deploymentId}"),
    ("POST", "/deployment/api/deployments/{deploymentId}/requests"),
]
REFERENCE_ROOT = "https://developer.broadcom.com/xapis/vm-apps-org-catalog/latest/"


@dataclass(frozen=True)
class Route:
    operation_key: str
    method: str
    path_template: str
    pattern: re.Pattern[str]
    body_member_order: tuple[str, ...]


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


def load_contract(contract_path: Path) -> tuple[list[Route], dict[str, Any]]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    if source.get("kind") != "reference-documentation":
        raise RuntimeError("contract source kind changed")
    if source.get("isPublishedSpecification") is not False:
        raise RuntimeError("contract must declare a reference-documentation source")
    if source.get("referenceRoot") != REFERENCE_ROOT:
        raise RuntimeError("contract reference root changed")
    operations = contract.get("operations", [])
    if [item.get("operationKey") for item in operations] != EXPECTED_OPERATION_KEYS:
        raise RuntimeError("contract operation set does not match the mock")
    if [
        (item.get("method"), item.get("path")) for item in operations
    ] != EXPECTED_ROUTES:
        raise RuntimeError("contract route projection changed")

    routes: list[Route] = []
    for item in operations:
        body = item.get("requestBody")
        order = tuple(body.get("memberOrder", ())) if isinstance(body, dict) else ()
        routes.append(
            Route(
                item["operationKey"],
                item["method"].upper(),
                item["path"],
                route_pattern(item["path"]),
                order,
            )
        )
    envelope = contract.get("errorEnvelope", {})
    if list(envelope.get("members", ())) != ["statusCode", "errorCode", "message"]:
        raise RuntimeError("contract error envelope changed")
    return routes, contract


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"scenario {name} is invalid")
    return value


def encode_body(member_order: tuple[str, ...], values: dict[str, Any]) -> bytes:
    """Serialize the members the caller supplied, in the documented order."""
    ordered = {
        name: values[name]
        for name in member_order
        if name in values and values[name] is not None
    }
    return json.dumps(ordered, separators=(",", ":")).encode("utf-8")


class MockState:
    def __init__(
        self,
        routes: list[Route],
        request_log: Path,
        scenario: dict[str, Any],
    ) -> None:
        self.routes_by_key = {route.operation_key: route for route in routes}
        self.routes = routes
        self.request_log = request_log
        self.access_token = require_text(scenario, "accessToken")
        self.project_id = require_text(scenario, "projectId")

        raw_cases = scenario.get("cases")
        lookup_cases = scenario.get("lookupCases")
        response_cases = scenario.get("responseCases")
        if not isinstance(raw_cases, list) or len(raw_cases) != 2:
            raise RuntimeError("scenario must contain two cases")
        if not isinstance(lookup_cases, list) or len(lookup_cases) != 2:
            raise RuntimeError("scenario must contain two catalog lookup checks")
        if not isinstance(response_cases, list) or len(response_cases) != 4:
            raise RuntimeError("scenario must contain four unusable response checks")
        all_cases = raw_cases + lookup_cases + response_cases
        self.cases_by_search: dict[str, dict[str, Any]] = {}
        self.cases_by_item: dict[str, dict[str, Any]] = {}
        self.cases_by_deployment: dict[str, dict[str, Any]] = {}
        for case in all_cases:
            if not isinstance(case, dict):
                raise RuntimeError("scenario case is invalid")
            search = require_text(case, "catalogItemName")
            item_id = require_text(case, "catalogItemId")
            deployment_id = require_text(case, "deploymentId")
            if (
                search in self.cases_by_search
                or item_id in self.cases_by_item
                or deployment_id in self.cases_by_deployment
            ):
                raise RuntimeError("scenario identifiers must be unique")
            self.cases_by_search[search] = case
            self.cases_by_item[item_id] = case
            self.cases_by_deployment[deployment_id] = case
        outcomes = {case["actionOutcome"]["status"] for case in raw_cases}
        if outcomes != {200, 409}:
            raise RuntimeError("scenario must cover one applied and one failed action")

        self.progress = {require_text(case, "key"): 0 for case in all_cases}
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> tuple[Route, tuple[str, ...]] | None:
        for route in self.routes:
            if route.method != method:
                continue
            found = route.pattern.fullmatch(path)
            if found:
                return route, tuple(unquote(value) for value in found.groups())
        return None

    def advance(self, case: dict[str, Any], expected: int) -> bool:
        with self.lock:
            if self.progress[case["key"]] != expected:
                return False
            self.progress[case["key"]] = expected + 1
            return True

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
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        matched = self.server.state.match(self.command, target.path)
        route: Route | None = None
        captures: tuple[str, ...] = ()
        if matched is not None:
            route, captures = matched

        if route is None:
            status, response = 404, error_body(
                404, "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif not self._has_expected_common_headers():
            status, response = 401, error_body(
                401, "WIRE_HEADERS", "authorization or accept header is invalid"
            )
        elif route.operation_key == "getCatalogItems":
            status, response = self._get_catalog_items(target.query, body)
        elif route.operation_key == "requestCatalogItemInstances":
            status, response = self._request_catalog_item(
                route, captures, target.query, body
            )
        elif route.operation_key == "getDeploymentById":
            status, response = self._get_deployment(captures, target.query, body)
        elif route.operation_key == "submitDeploymentActionRequest":
            status, response = self._submit_action(route, captures, target.query, body)
        else:  # Defensive: routes only come from the validated contract.
            status, response = 404, error_body(
                404, "NOT_IN_CONTRACT", "unknown operation"
            )

        header_values = {
            name.lower(): self.headers.get_all(name) or []
            for name in self.headers.keys()
        }
        self.server.state.record(
            {
                "operationKey": route.operation_key if route else None,
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
        expected = f"Bearer {self.server.state.access_token}"
        accept = self.headers.get_all("Accept") or []
        return (
            self.headers.get_all("Authorization") == [expected]
            and accept == ["application/json"]
        )

    def _json_content_type(self) -> bool:
        values = self.headers.get_all("Content-Type") or []
        return values == ["application/json"]

    def _bodyless_ok(self, body: bytes) -> bool:
        return not body and not (self.headers.get_all("Content-Type") or [])

    def _get_catalog_items(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        if not self._bodyless_ok(body):
            return 400, error_body(
                400, "WIRE_SHAPE", "catalog item search must carry no body"
            )
        try:
            query = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return 400, error_body(400, "WIRE_SHAPE", "catalog query is malformed")
        if set(query) != {"search", "projects", "size"}:
            return 400, error_body(
                400, "WIRE_SHAPE", "catalog query has a missing or unset member"
            )
        if any(len(values) != 1 or not values[0] for values in query.values()):
            return 400, error_body(
                400, "WIRE_SHAPE", "catalog query values must be singular and nonblank"
            )
        case = self.server.state.cases_by_search.get(query["search"][0])
        if case is None:
            return 404, error_body(404, "NOT_FOUND", "no such catalog item name")
        expected = urlencode(
            [
                ("search", case["catalogItemName"]),
                ("projects", self.server.state.project_id),
                ("size", str(case["pageSize"])),
            ]
        )
        if raw_query != expected:
            return 400, error_body(
                400, "WIRE_SHAPE", "catalog query order or values changed"
            )
        if not self.server.state.advance(case, 0):
            return 409, error_body(409, "OUT_OF_SEQUENCE", "catalog was searched twice")
        if case.get("responseMode") == "catalog-http-error":
            return 401, error_body(
                401, "TOKEN_REJECTED", "catalog search rejected the access token"
            )
        return 200, case["catalogPage"]

    def _request_catalog_item(
        self, route: Route, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query or len(captures) != 1:
            return 400, error_body(
                400, "WIRE_SHAPE", "catalog item request takes no query"
            )
        if not self._json_content_type():
            return 415, error_body(
                415, "MEDIA_TYPE", "request body must be application/json"
            )
        case = self.server.state.cases_by_item.get(captures[0])
        if case is None:
            return 404, error_body(404, "NOT_FOUND", "no such catalog item id")
        if not self.server.state.advance(case, 1):
            return 409, error_body(
                409,
                "OUT_OF_SEQUENCE",
                "the catalog item must be resolved once before it is requested",
            )
        expected = encode_body(
            route.body_member_order,
            {
                "deploymentName": case["requestedDeploymentName"],
                "inputs": case.get("inputs"),
                "projectId": self.server.state.project_id,
                "reason": case.get("reason"),
            },
        )
        if body != expected:
            return 400, error_body(
                400,
                "WIRE_SHAPE",
                "catalog item request body sent an unset, reordered or wrong member",
            )
        response = [
            {
                "deploymentId": case["deploymentId"],
                "deploymentName": case["deploymentName"],
            }
        ]
        if case.get("responseMode") == "multiple-deployments":
            response.append(
                {
                    "deploymentId": case["extraDeploymentId"],
                    "deploymentName": case["deploymentName"] + "-extra",
                }
            )
        return 200, response

    def _get_deployment(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if not self._bodyless_ok(body) or len(captures) != 1:
            return 400, error_body(
                400, "WIRE_SHAPE", "deployment read must carry no body"
            )
        case = self.server.state.cases_by_deployment.get(captures[0])
        if case is None:
            return 404, error_body(404, "NOT_FOUND", "no such deployment")
        if raw_query != "expand=resources":
            return 400, error_body(
                400, "WIRE_SHAPE", "deployment read query changed or sent an unset member"
            )
        if not self.server.state.advance(case, 2):
            return 409, error_body(
                409, "OUT_OF_SEQUENCE", "the deployment must be read once after creation"
            )
        return 200, case["deployment"]

    def _submit_action(
        self, route: Route, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query or len(captures) != 1:
            return 400, error_body(400, "WIRE_SHAPE", "action request takes no query")
        if not self._json_content_type():
            return 415, error_body(
                415, "MEDIA_TYPE", "request body must be application/json"
            )
        case = self.server.state.cases_by_deployment.get(captures[0])
        if case is None:
            return 404, error_body(404, "NOT_FOUND", "no such deployment")
        if not self.server.state.advance(case, 3):
            return 409, error_body(
                409,
                "OUT_OF_SEQUENCE",
                "the deployment must be read before an action is submitted, and the "
                "action is submitted once",
            )
        expected = encode_body(
            route.body_member_order,
            {
                "actionId": case["actionId"],
                "reason": case.get("actionReason"),
            },
        )
        if body != expected:
            return 400, error_body(
                400,
                "WIRE_SHAPE",
                "action request body sent an unset, reordered or wrong member",
            )
        outcome = case["actionOutcome"]
        if outcome["status"] == 200:
            return 200, case["actionRequest"]
        return outcome["status"], error_body(
            outcome["status"], outcome["errorCode"], outcome["message"]
        )

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


def error_body(status: int, code: str, message: str) -> dict[str, Any]:
    return {"statusCode": status, "errorCode": code, "message": message}


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
    routes, _contract = load_contract(contract_file)
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
