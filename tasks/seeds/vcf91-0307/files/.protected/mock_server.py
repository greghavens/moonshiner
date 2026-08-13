#!/usr/bin/env python3
"""Contract-pinned loopback VCF Automation used by protected verification.

The callable routes are derived from docs/contract.json. Every other target is
refused, so the request log proves which contract operations were used.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATION_KEYS = [
    "requestCatalogItem",
    "getDeploymentById",
    "getRequestById",
    "actionRequest",
]
EXPECTED_ROUTES = [
    ("POST", "/catalog/api/items/{id}/request"),
    ("GET", "/deployment/api/deployments/{deploymentId}"),
    ("GET", "/deployment/api/requests/{requestId}"),
    ("POST", "/deployment/api/requests/{requestId}"),
]
NON_TERMINAL = {
    "CREATED",
    "PENDING",
    "INITIALIZATION",
    "CHECKING_APPROVAL",
    "APPROVAL_PENDING",
    "USER_INTERACTION_PENDING",
    "INPROGRESS",
    "COMPLETION",
}
TERMINAL = {"SUCCESSFUL", "FAILED", "ABORTED", "APPROVAL_REJECTED"}


def canonical_status(value: str) -> str:
    return value.strip().upper()


@dataclass(frozen=True)
class Route:
    operation_key: str
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
    if source.get("isPublishedSpecification") is not False:
        raise RuntimeError("contract must declare a reference-documentation source")
    if source.get("product") != "VCF Automation":
        raise RuntimeError("contract product changed")
    operations = contract.get("operations", [])
    if [item.get("operationKey") for item in operations] != EXPECTED_OPERATION_KEYS:
        raise RuntimeError("contract operation set does not match the mock")
    routes = [
        Route(
            item["operationKey"],
            item["method"].upper(),
            item["path"],
            route_pattern(item["path"]),
        )
        for item in operations
    ]
    if [(route.method, route.path_template) for route in routes] != EXPECTED_ROUTES:
        raise RuntimeError("contract route projection changed")
    classification = contract.get("asynchronousProfile", {}).get(
        "requestStatusClassification", {}
    )
    if set(classification.get("nonTerminal", [])) != NON_TERMINAL:
        raise RuntimeError("contract non-terminal status set changed")
    if set(classification.get("terminalSuccess", [])) | set(
        classification.get("terminalFailure", [])
    ) != TERMINAL:
        raise RuntimeError("contract terminal status set changed")
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
        if not isinstance(raw_cases, list) or not raw_cases:
            raise RuntimeError("scenario must contain at least one case")
        self.by_catalog_item: dict[str, dict[str, Any]] = {}
        self.by_deployment: dict[str, dict[str, Any]] = {}
        self.by_request: dict[str, dict[str, Any]] = {}
        self.decoy_requests: dict[str, dict[str, Any]] = {}
        for case in raw_cases:
            if not isinstance(case, dict):
                raise RuntimeError("scenario case is invalid")
            catalog_item_id = require_text(case, "catalogItemId")
            deployment_id = require_text(case, "deploymentId")
            request_id = require_text(case, "requestId")
            decoy_request_id = require_text(case, "decoyRequestId")
            known = (
                set(self.by_catalog_item)
                | set(self.by_deployment)
                | set(self.by_request)
                | set(self.decoy_requests)
            )
            if known & {catalog_item_id, deployment_id, request_id, decoy_request_id}:
                raise RuntimeError("scenario identifiers must be unique")
            statuses = case.get("statusSequence")
            if not isinstance(statuses, list) or not statuses:
                raise RuntimeError("scenario case needs a status sequence")
            canonical_statuses = [canonical_status(status) for status in statuses]
            allow_unknown = case.get("allowUnknownStatus") is True
            if any(
                status not in NON_TERMINAL | TERMINAL
                for status in canonical_statuses
            ) and not allow_unknown:
                raise RuntimeError("scenario status is outside the contract")
            if any(status in TERMINAL for status in canonical_statuses[:-1]):
                raise RuntimeError("only the last scripted status may be terminal")
            cancelable_sequence = case.get("cancelableSequence")
            if (
                not isinstance(cancelable_sequence, list)
                or not cancelable_sequence
                or any(not isinstance(value, bool) for value in cancelable_sequence)
            ):
                raise RuntimeError("scenario case needs cancelable values")
            if not isinstance(case.get("expectCancel"), bool):
                raise RuntimeError("scenario case needs an expected cancellation flag")
            if not isinstance(case.get("cancelResponseStatus"), int):
                raise RuntimeError("scenario case needs a cancellation response status")
            if not isinstance(case.get("expectedRequestBody"), dict):
                raise RuntimeError("scenario case needs an expected request body")
            if case.get("fault") not in {
                None,
                "accepted-cardinality",
                "accepted-blank",
                "accepted-blank-name",
                "deployment-id-case",
                "last-request-blank",
                "poll-id-case",
            }:
                raise RuntimeError("scenario case has an unsupported fault")
            self.by_catalog_item[catalog_item_id] = case
            self.by_deployment[deployment_id] = case
            self.by_request[request_id] = case
            self.decoy_requests[decoy_request_id] = case
        self.polls: dict[str, int] = {key: 0 for key in self.by_request}
        self.canceled: set[str] = set()
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

    def status_for_poll(self, case: dict[str, Any], poll: int) -> str:
        statuses: list[str] = case["statusSequence"]
        index = min(poll - 1, len(statuses) - 1)
        return statuses[index]

    def cancelable_for_poll(self, case: dict[str, Any], poll: int) -> bool:
        values: list[bool] = case["cancelableSequence"]
        index = min(max(poll - 1, 0), len(values) - 1)
        return values[index]

    def terminates(self, case: dict[str, Any]) -> bool:
        return canonical_status(case["statusSequence"][-1]) not in NON_TERMINAL


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
        elif route.operation_key == "requestCatalogItem":
            status, response = self._request_catalog_item(
                captures, target.query, body
            )
        elif route.operation_key == "getDeploymentById":
            status, response = self._get_deployment(captures, target.query, body)
        elif route.operation_key == "getRequestById":
            status, response = self._get_request(captures, target.query, body)
        elif route.operation_key == "actionRequest":
            status, response = self._action_request(captures, target.query, body)
        else:  # Defensive: routes only come from the validated contract.
            status, response = 404, error_body("NOT_IN_CONTRACT", "unknown operation")

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
        self._send(status, response)

    def _has_expected_common_headers(self) -> bool:
        expected_auth = f"Bearer {self.server.state.access_token}"
        accept_values = self.headers.get_all("Accept") or []
        return (
            self.headers.get_all("Authorization") == [expected_auth]
            and len(accept_values) == 1
            and "application/json" in accept_values[0]
        )

    def _json_content_type(self) -> bool:
        values = self.headers.get_all("Content-Type") or []
        return (
            len(values) == 1
            and values[0].split(";", 1)[0].strip().casefold() == "application/json"
        )

    def _request_catalog_item(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "catalog request takes no query")
        if not self._json_content_type():
            return 415, error_body("MEDIA_TYPE", "catalog request must be JSON")
        case = self.server.state.by_catalog_item.get(captures[0])
        if case is None:
            return 404, error_body("NOT_FOUND", "catalog item does not exist")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400, error_body("WIRE_SHAPE", "catalog request body is not JSON")
        if payload != case["expectedRequestBody"]:
            return 400, error_body(
                "WIRE_SHAPE",
                "catalog request body omits a bound member or serializes an unset one",
            )
        accepted = {
            "deploymentId": case["deploymentId"],
            "deploymentName": case["deploymentName"],
        }
        if case["fault"] == "accepted-cardinality":
            return 200, [accepted, {**accepted, "deploymentId": str(uuid.uuid4())}]
        if case["fault"] == "accepted-blank":
            return 200, [{**accepted, "deploymentId": "   "}]
        if case["fault"] == "accepted-blank-name":
            return 200, [{**accepted, "deploymentName": "   "}]
        return 200, [accepted]

    def _get_deployment(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if body:
            return 400, error_body("WIRE_SHAPE", "deployment read must be bodyless")
        if raw_query != "expandLastRequest=true":
            return 400, error_body(
                "WIRE_SHAPE",
                "deployment query must bind only expandLastRequest=true",
            )
        case = self.server.state.by_deployment.get(captures[0])
        if case is None:
            return 404, error_body("NOT_FOUND", "deployment does not exist")
        with self.server.state.lock:
            poll = self.server.state.polls[case["requestId"]]
        returned_id = case["deploymentId"]
        if case["fault"] == "deployment-id-case":
            returned_id = returned_id.upper()
        last_request = self._request_body(case, max(poll, 1))
        if case["fault"] == "last-request-blank":
            last_request["id"] = "   "
        return 200, {
            "id": returned_id,
            "name": case["deploymentName"],
            "orgId": case["orgId"],
            "projectId": case["projectId"],
            "catalogItemId": case["catalogItemId"],
            "createdBy": case["requestedBy"],
            "ownedBy": case["requestedBy"],
            "ownerType": "USER",
            "createdAt": case["createdAt"],
            "lastUpdatedAt": case["createdAt"],
            "status": "CREATE_INPROGRESS",
            "deleted": False,
            "inprogressRequests": [
                {
                    "id": case["decoyRequestId"],
                    "name": "Reconfigure placement",
                    "deploymentId": case["deploymentId"],
                    "status": "INPROGRESS",
                    "cancelable": True,
                    "requestedBy": case["requestedBy"],
                    "createdAt": case["createdAt"],
                    "totalTasks": 3,
                    "completedTasks": 1,
                }
            ],
            "lastRequest": last_request,
        }

    def _get_request(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        if raw_query or body:
            return 400, error_body("WIRE_SHAPE", "request poll must be bodyless")
        request_id = captures[0]
        decoy_case = self.server.state.decoy_requests.get(request_id)
        if decoy_case is not None:
            return 200, {
                "id": request_id,
                "name": "Reconfigure placement",
                "deploymentId": decoy_case["deploymentId"],
                "actionId": "Deployment.Update",
                "status": "FAILED",
                "cancelable": False,
                "dismissed": False,
                "requestedBy": decoy_case["requestedBy"],
                "createdAt": decoy_case["createdAt"],
                "totalTasks": 3,
                "completedTasks": 1,
                "details": "Unrelated in-flight request that is not the create request",
            }
        case = self.server.state.by_request.get(request_id)
        if case is None:
            return 404, error_body("NOT_FOUND", "request does not exist")
        with self.server.state.lock:
            if request_id in self.server.state.canceled:
                return 409, error_body(
                    "POLL_AFTER_CANCEL", "request was polled after it was canceled"
                )
            previous = self.server.state.polls[request_id]
            if (
                self.server.state.terminates(case)
                and previous >= len(case["statusSequence"])
            ):
                return 409, error_body(
                    "POLL_OVERSHOOT", "request was polled after a terminal status"
                )
            self.server.state.polls[request_id] = previous + 1
            poll = previous + 1
        response = self._request_body(case, poll)
        if case["fault"] == "poll-id-case":
            response["id"] = response["id"].upper()
        return 200, response

    def _action_request(
        self, captures: tuple[str, ...], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        # The reference page documents no request body. Content-Type is not
        # constrained here because different genuine HTTP stacks either omit it
        # or attach a harmless default to a bodyless POST.
        if body:
            return 400, error_body(
                "WIRE_SHAPE", "the action operation documents no request body"
            )
        if raw_query != "action=cancel":
            return 400, error_body(
                "WIRE_SHAPE", "action query must bind only action=cancel"
            )
        request_id = captures[0]
        case = self.server.state.by_request.get(request_id)
        if case is None:
            return 404, error_body("NOT_FOUND", "request does not exist")
        with self.server.state.lock:
            poll = self.server.state.polls[request_id]
            if poll == 0:
                return 409, error_body(
                    "CANCEL_BEFORE_POLL", "request was canceled before it was polled"
                )
            if canonical_status(
                self.server.state.status_for_poll(case, poll)
            ) in TERMINAL:
                return 409, error_body(
                    "CANCEL_AFTER_TERMINAL", "a terminal request cannot be canceled"
                )
            if not self.server.state.cancelable_for_poll(case, poll):
                return 409, error_body(
                    "NOT_CANCELABLE", "most recent poll reported cancelable false"
                )
            response_status = case["cancelResponseStatus"]
            if response_status != 200:
                return response_status, error_body(
                    "CANCEL_FAILED", "scripted cancellation failure"
                )
            self.server.state.canceled.add(request_id)
        return 200, None

    def _request_body(self, case: dict[str, Any], poll: int) -> dict[str, Any]:
        status = self.server.state.status_for_poll(case, poll)
        normalized = canonical_status(status)
        total = case["totalTasks"]
        completed = (
            total if normalized == "SUCCESSFUL" else min(poll, max(total - 1, 0))
        )
        payload: dict[str, Any] = {
            "id": case["requestId"],
            "name": case["requestName"],
            "deploymentId": case["deploymentId"],
            "actionId": "Deployment.Create",
            "blueprintId": case["blueprintId"],
            "catalogItemId": case["catalogItemId"],
            "status": status,
            "cancelable": self.server.state.cancelable_for_poll(case, poll),
            "dismissed": False,
            "requestedBy": case["requestedBy"],
            "createdAt": case["createdAt"],
            "updatedAt": case["createdAt"],
            "totalTasks": total,
            "completedTasks": completed,
            "details": case["details"],
        }
        if normalized == "SUCCESSFUL":
            payload["completedAt"] = case["completedAt"]
            payload["outputs"] = case["outputs"]
        elif normalized in TERMINAL:
            payload["completedAt"] = case["completedAt"]
        return payload

    def _send(self, status: int, value: Any) -> None:
        body = b""
        if value is not None:
            body = json.dumps(
                value, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)
        self.wfile.flush()


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"errorCode": code, "message": message}


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
