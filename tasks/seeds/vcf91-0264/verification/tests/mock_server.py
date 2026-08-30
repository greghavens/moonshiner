#!/usr/bin/env python3
"""Contract-pinned loopback VCF Operations service used by protected verification.

Only the four operations named in ``docs/contract.json`` are routable.  Every
request, including rejected ones, is appended to a flushed and fsynced JSONL
request log so the verifier can assert the exact wire shape afterwards.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATION_IDS = [
    "acquireToken",
    "enumerateAdapterInstances",
    "testConnection",
    "createAdapterInstance",
]
EXPECTED_ROUTES = [
    ("POST", "/api/auth/token/acquire"),
    ("GET", "/api/adapters"),
    ("POST", "/api/adapters/testConnection"),
    ("POST", "/api/adapters"),
]
TOKEN_PREFIX = "vRealizeOpsToken"
SERVER_BASE_PATH = "/suite-api"

STAGE_AUTHENTICATED = 0
STAGE_ENUMERATED = 1
STAGE_TESTED = 2


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path: str
    authenticated: bool


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def load_routes(contract_path: Path) -> list[Route]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    if source.get("repositoryCommitSha") != PINNED_COMMIT:
        raise RuntimeError("contract repository commit is not pinned")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract specification path is not pinned")
    if source.get("serverBasePath") != SERVER_BASE_PATH:
        raise RuntimeError("contract server base path is not pinned")
    security = contract.get("security", {})
    if security.get("headerName") != "Authorization":
        raise RuntimeError("contract authorization header changed")
    if security.get("tokenPrefix") != TOKEN_PREFIX:
        raise RuntimeError("contract token prefix changed")
    operations = contract.get("operations", [])
    if [item.get("operationId") for item in operations] != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the mock")
    routes = [
        Route(
            item["operationId"],
            item["method"].upper(),
            item["path"],
            bool(item["authenticated"]),
        )
        for item in operations
    ]
    if [(route.method, route.path) for route in routes] != EXPECTED_ROUTES:
        raise RuntimeError("contract route projection changed")
    if [route.authenticated for route in routes] != [False, True, True, True]:
        raise RuntimeError("contract authentication projection changed")
    return routes


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"scenario {name} is invalid")
    return value


def pairs(
    source: dict[str, Any], name: str, *, allow_empty: bool = False
) -> list[tuple[str, str]]:
    value = source.get(name)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise RuntimeError(f"scenario {name} is invalid")
    result = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise RuntimeError(f"scenario {name} is invalid")
        result.append((str(item[0]), str(item[1])))
    return result


def expected_auth_body(case: dict[str, Any]) -> bytes:
    """username-password projected in contract declaration order."""
    payload: dict[str, Any] = {}
    auth_source = case.get("authSource")
    if auth_source:
        payload["authSource"] = auth_source
    payload["password"] = require_text(case, "password")
    payload["username"] = require_text(case, "username")
    return compact(payload)


def expected_create_body(case: dict[str, Any]) -> bytes:
    """create-adapter-instance projected in contract declaration order."""
    credential = {
        "adapterKindKey": require_text(case, "adapterKindKey"),
        "credentialKindKey": require_text(case, "credentialKindKey"),
        "fields": [
            {"name": name, "value": value}
            for name, value in pairs(case, "credentialFields")
        ],
        "name": require_text(case, "credentialName"),
    }
    payload: dict[str, Any] = {
        "adapterKindKey": require_text(case, "adapterKindKey"),
        "credential": credential,
    }
    description = case.get("description")
    if description:
        payload["description"] = description
    payload["name"] = require_text(case, "instanceName")
    payload["resourceIdentifiers"] = [
        {"name": name, "value": value}
        for name, value in pairs(case, "resourceIdentifiers", allow_empty=True)
    ]
    return compact(payload)


class MockState:
    def __init__(
        self, routes: list[Route], request_log: Path, scenario: dict[str, Any]
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        raw_cases = scenario.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise RuntimeError("scenario must contain cases")
        self.cases_by_auth_body: dict[bytes, dict[str, Any]] = {}
        self.cases_by_token: dict[str, dict[str, Any]] = {}
        self.expected_create: dict[str, bytes] = {}
        outcomes = []
        for case in raw_cases:
            if not isinstance(case, dict):
                raise RuntimeError("scenario case is invalid")
            key = require_text(case, "key")
            token = require_text(case, "token")
            auth_body = expected_auth_body(case)
            if auth_body in self.cases_by_auth_body or token in self.cases_by_token:
                raise RuntimeError("scenario cases must be distinguishable")
            self.cases_by_auth_body[auth_body] = case
            self.cases_by_token[token] = case
            self.expected_create[key] = expected_create_body(case)
            outcomes.append(require_text(case, "outcome"))
        mode = scenario.get("mode")
        if mode == "gate-flow":
            if len(raw_cases) != 3 or sorted(outcomes) != [
                "created",
                "duplicate-name",
                "test-connection",
            ]:
                raise RuntimeError(
                    "gate-flow must cover one success and both gate refusals"
                )
        elif mode == "optional-success":
            if len(raw_cases) != 1 or outcomes != ["created"]:
                raise RuntimeError("optional-success must contain one successful case")
        elif mode == "api-errors":
            fail_operations = [case.get("failOperationId") for case in raw_cases]
            if len(raw_cases) != 4 or fail_operations != EXPECTED_OPERATION_IDS:
                raise RuntimeError("api-errors must cover each contracted operation")
        else:
            raise RuntimeError("scenario mode is invalid")
        self.stage: dict[str, int] = {}
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> Route | None:
        for route in self.routes:
            if route.method == method and SERVER_BASE_PATH + route.path == path:
                return route
        return None

    @staticmethod
    def forced_error(
        case: dict[str, Any], operation_id: str
    ) -> tuple[int, Any] | None:
        if case.get("failOperationId") != operation_id:
            return None
        status = case.get("failStatus")
        message = case.get("failMessage")
        if not isinstance(status, int) or not isinstance(message, str):
            raise RuntimeError("forced API error is invalid")
        return status, error_body(status, message)

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
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        if route is None:
            status, response = 404, error_body(
                404, "operation is outside the focused contract"
            )
        elif not self._accepts_json():
            status, response = 406, error_body(406, "Accept header must request JSON")
        elif route.operation_id == "acquireToken":
            status, response = self._acquire_token(target.query, body)
        else:
            case = self._authenticated_case()
            if case is None:
                status, response = 401, error_body(
                    401, "Authorization header is missing or malformed"
                )
            elif route.operation_id == "enumerateAdapterInstances":
                status, response = self._enumerate(case, target.query, body)
            elif route.operation_id == "testConnection":
                status, response = self._test_connection(case, target.query, body)
            else:
                status, response = self._create_instance(case, target.query, body)

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

    def _accepts_json(self) -> bool:
        values = self.headers.get_all("Accept") or []
        return len(values) == 1 and "application/json" in values[0]

    def _authenticated_case(self) -> dict[str, Any] | None:
        values = self.headers.get_all("Authorization") or []
        if len(values) != 1:
            return None
        prefix, separator, token = values[0].partition(" ")
        if prefix != TOKEN_PREFIX or not separator:
            return None
        return self.server.state.cases_by_token.get(token)

    def _json_content_type(self) -> bool:
        values = self.headers.get_all("Content-Type") or []
        return len(values) == 1 and values[0] == "application/json"

    def _acquire_token(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        if raw_query:
            return 400, error_body(400, "token acquisition takes no query members")
        if self.headers.get_all("Authorization"):
            return 400, error_body(
                400, "token acquisition is an unauthenticated operation"
            )
        if not self._json_content_type():
            return 415, error_body(415, "request media type must be application/json")
        case = self.server.state.cases_by_auth_body.get(body)
        if case is None:
            return 401, error_body(
                401, "credential body did not match the pinned wire shape"
            )
        forced = self.server.state.forced_error(case, "acquireToken")
        if forced is not None:
            return forced
        with self.server.state.lock:
            self.server.state.stage[case["key"]] = STAGE_AUTHENTICATED
        return 200, {
            "token": case["token"],
            "validity": case["tokenValidity"],
            "expiresAt": case["tokenExpiresAt"],
            "roles": list(case.get("tokenRoles", [])),
        }

    def _enumerate(
        self, case: dict[str, Any], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if body or self.headers.get_all("Content-Type"):
            return 400, error_body(400, "adapter enumeration must be bodyless")
        expected = "adapterKindKey=" + case["adapterKindKey"]
        if raw_query != expected:
            return 400, error_body(
                400, "adapter enumeration query is not the pinned wire shape"
            )
        forced = self.server.state.forced_error(case, "enumerateAdapterInstances")
        if forced is not None:
            return forced
        with self.server.state.lock:
            if self.server.state.stage.get(case["key"]) != STAGE_AUTHENTICATED:
                return 409, error_body(409, "adapter enumeration ran out of order")
            self.server.state.stage[case["key"]] = STAGE_ENUMERATED
        return 200, case["inventory"]

    def _test_connection(
        self, case: dict[str, Any], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query:
            return 400, error_body(400, "connection test takes no query members")
        if not self._json_content_type():
            return 415, error_body(415, "request media type must be application/json")
        with self.server.state.lock:
            if self.server.state.stage.get(case["key"]) != STAGE_ENUMERATED:
                return 409, error_body(
                    409, "the adapter name must be checked before testing a connection"
                )
        if body != self.server.state.expected_create[case["key"]]:
            return 400, error_body(
                400, "connection test body carries unset or misordered members"
            )
        forced = self.server.state.forced_error(case, "testConnection")
        if forced is not None:
            return forced
        if case["outcome"] == "test-connection":
            return 400, error_body(400, case["testConnectionMessage"])
        with self.server.state.lock:
            self.server.state.stage[case["key"]] = STAGE_TESTED
        return 201, case["testedInstance"]

    def _create_instance(
        self, case: dict[str, Any], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query != "force=false":
            return 400, error_body(
                400, "adapter creation query is not the pinned wire shape"
            )
        if not self._json_content_type():
            return 415, error_body(415, "request media type must be application/json")
        with self.server.state.lock:
            if self.server.state.stage.get(case["key"]) != STAGE_TESTED:
                return 409, error_body(
                    409, "a passing connection precheck must gate adapter creation"
                )
        if body != self.server.state.expected_create[case["key"]]:
            return 400, error_body(
                400, "adapter creation body differs from the tested body"
            )
        forced = self.server.state.forced_error(case, "createAdapterInstance")
        if forced is not None:
            return forced
        return 201, case["createdInstance"]

    def _send_json(self, status: int, value: Any) -> None:
        body = compact(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
            self.wfile.flush()


def error_body(status: int, message: str) -> dict[str, Any]:
    """The ``error`` model projected in docs/contract.json."""
    return {
        "apiErrorCode": status * 10,
        "httpStatusCode": status,
        "message": message,
        "moreInformation": [],
        "type": "Error",
        "validationFailures": [],
    }


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
