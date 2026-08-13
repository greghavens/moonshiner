#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by protected verification.

The server refuses to start unless docs/contract.json is still the projection of
the pinned 9.0.0.0 specification, and it serves only the three operations that
the contract names. Every request is appended to a flushed JSONL log, together
with the running count of hosts the service actually commissioned, so a verifier
can prove that a refused precheck changed nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    ("validateHostCommissionSpec", "POST", "/v1/hosts/validations"),
    ("getHostCommissionValidationByID", "GET", "/v1/hosts/validations/{id}"),
    ("commissionHosts", "POST", "/v1/hosts"),
]

TIMESTAMP_BASE = "2026-04-09T09:30:"


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path: str

    @property
    def is_template(self) -> bool:
        return "{" in self.path

    @property
    def prefix(self) -> str:
        return self.path.split("{", 1)[0]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def error_body(code: str, message: str) -> dict[str, Any]:
    return {
        "errorCode": code,
        "errorType": "VALIDATION_FAILED" if code != "UNAUTHORIZED" else "UNAUTHORIZED",
        "message": message,
        "referenceToken": "MOCK-REF",
    }


class ContractPin:
    """The route table plus the HostCommissionSpec rules, read from the contract."""

    def __init__(self, contract_path: Path) -> None:
        contract = read_json(contract_path)
        source = contract.get("source", {})
        if source.get("repositoryCommitSha") != PINNED_COMMIT:
            raise RuntimeError("contract repository commit is not pinned to 9.0.0.0")
        if source.get("specPath") != PINNED_SPEC_PATH:
            raise RuntimeError("contract specification path is not pinned")
        operations = contract.get("operations", [])
        projected = [
            (item.get("operationId"), item.get("method"), item.get("path"))
            for item in operations
        ]
        if projected != EXPECTED_OPERATIONS:
            raise RuntimeError("contract operation set does not match the mock")

        spec = contract.get("schemas", {}).get("HostCommissionSpec", {})
        self.property_order: list[str] = list(spec.get("propertyOrder", []))
        self.required: list[str] = list(spec.get("required", []))
        if not self.property_order or not self.required:
            raise RuntimeError("contract does not project HostCommissionSpec")
        if not set(self.required) <= set(self.property_order):
            raise RuntimeError("HostCommissionSpec projection is inconsistent")
        self.routes = [Route(*entry) for entry in EXPECTED_OPERATIONS]

    def check_element(self, index: int, pairs: Any) -> str | None:
        """Validate one HostCommissionSpec as it arrived on the wire."""
        where = f"element {index}"
        if not isinstance(pairs, list):
            return f"{where} is not a JSON object"
        names = [name for name, _ in pairs]
        if len(names) != len(set(names)):
            return f"{where} repeats a member name"
        unknown = [name for name in names if name not in self.property_order]
        if unknown:
            return f"{where} carries members outside HostCommissionSpec: {', '.join(unknown)}"
        missing = [name for name in self.required if name not in names]
        if missing:
            return f"{where} omits required members: {', '.join(sorted(missing))}"
        ordered = [name for name in self.property_order if name in names]
        if names != ordered:
            return f"{where} does not follow the specification member order"
        for name, value in pairs:
            if value is None:
                return f"{where} sends {name} as null instead of omitting it"
            if not isinstance(value, str):
                return f"{where} sends {name} as {type(value).__name__} instead of a string"
            if not value.strip():
                return f"{where} sends {name} as an empty string instead of omitting it"
        return None

    def check_specs(self, body: bytes) -> tuple[list[dict[str, str]] | None, str | None]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None, "request body is not UTF-8"
        try:
            payload = json.loads(text, object_pairs_hook=lambda pairs: pairs)
        except json.JSONDecodeError as error:
            return None, f"request body is not JSON: {error.msg}"
        if not isinstance(payload, list):
            return None, "request body must be a JSON array of HostCommissionSpec"
        if not payload:
            return None, "request body must contain at least one HostCommissionSpec"
        for index, element in enumerate(payload):
            problem = self.check_element(index, element)
            if problem is not None:
                return None, problem
        return [dict(element) for element in payload], None


class MockState:
    """Stateful host service: prechecks resolve on a schedule, commissions stick."""

    def __init__(self, pin: ContractPin, request_log: Path, scenario: dict[str, Any]) -> None:
        self.pin = pin
        self.request_log = request_log
        self.access_token = self._require(scenario, "accessToken")
        self.validation_id = self._require(scenario, "validationId")
        self.task_id = self._require(scenario, "taskId")
        self.polls_before_terminal = int(scenario.get("pollsBeforeTerminal", 1))
        if self.polls_before_terminal < 1:
            raise RuntimeError("scenario pollsBeforeTerminal must be at least 1")
        self.execution_status = self._require(scenario, "executionStatus")
        self.result_status = scenario.get("resultStatus")
        self.validation_checks = list(scenario.get("validationChecks", []))
        self.accepted_validation_id = scenario.get(
            "acceptedValidationId", self.validation_id
        )
        self.commission_error_message = scenario.get("commissionErrorMessage")
        self.submitted_specs: list[dict[str, str]] | None = None
        self.poll_count = 0
        self.commission_effect_total = 0
        self.commissioned_fqdns: list[str] = []
        self.sequence = 0
        self.clock = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    @staticmethod
    def _require(scenario: dict[str, Any], name: str) -> str:
        value = scenario.get(name)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"scenario {name} is invalid")
        return value

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.pin.routes:
            if route.method == method and not route.is_template and route.path == path:
                return route, {}
        for route in self.pin.routes:
            if route.method != method or not route.is_template:
                continue
            if path.startswith(route.prefix):
                remainder = path[len(route.prefix) :]
                if remainder and "/" not in remainder:
                    return route, {"id": remainder}
        return None, {}

    def next_timestamp(self) -> str:
        self.clock += 1
        return f"{TIMESTAMP_BASE}{self.clock:02d}.000Z"

    def accepted_body(self) -> dict[str, Any]:
        """The 202 names the validation to watch; its status is not yet meaningful.

        SDDC Manager answers the submission before the checks have run, and the
        placeholder it echoes can already read as a clean pass. A client that
        believes it would commission hosts the precheck is about to reject.
        """
        return {
            "id": self.accepted_validation_id,
            "description": "Validation for host commission specification",
            "executionStatus": "COMPLETED",
            "resultStatus": "SUCCEEDED",
        }

    def validation_body(self, terminal: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.validation_id,
            "description": "Validation for host commission specification",
            "executionStatus": self.execution_status if terminal else "IN_PROGRESS",
        }
        if terminal:
            if self.result_status is not None:
                body["resultStatus"] = self.result_status
            if self.validation_checks:
                body["validationChecks"] = self.validation_checks
        else:
            body["resultStatus"] = "UNKNOWN"
        return body

    def task_body(self) -> dict[str, Any]:
        stamp = self.next_timestamp()
        return {
            "id": self.task_id,
            "name": "Commissioning Hosts",
            "type": "HOST_COMMISSION",
            "status": "IN_PROGRESS",
            "creationTimestamp": stamp,
            "resources": [
                {"resourceId": fqdn, "type": "ESXI", "fqdn": fqdn}
                for fqdn in self.commissioned_fqdns
            ],
        }

    def record(self, entry: dict[str, Any]) -> None:
        with self.request_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")
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

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        state = self.server.state
        target = urlsplit(self.path)
        raw_length = self.headers.get("Content-Length")
        try:
            body_length = max(int(raw_length or "0"), 0)
        except ValueError:
            body_length = 0
        body = self.rfile.read(body_length)

        with state.lock:
            route, path_params = state.match(self.command, target.path)
            effect: dict[str, Any] | None = None
            if route is None:
                status, response = 404, error_body(
                    "NOT_IN_CONTRACT", "operation is outside the focused contract"
                )
            else:
                problem = self._check_common(target.query)
                if problem is not None:
                    status, response = problem
                elif route.operation_id == "validateHostCommissionSpec":
                    status, response = self._validate(body)
                elif route.operation_id == "getHostCommissionValidationByID":
                    status, response = self._poll(path_params.get("id", ""), body)
                else:
                    status, response, effect = self._commission(body)

            header_values: dict[str, list[str]] = {}
            for name in self.headers.keys():
                header_values[name.lower()] = self.headers.get_all(name) or []
            state.sequence += 1
            state.record(
                {
                    "sequence": state.sequence,
                    "operationId": route.operation_id if route else None,
                    "method": self.command,
                    "rawTarget": self.path,
                    "path": target.path,
                    "rawQuery": target.query,
                    "pathParams": path_params,
                    "headerValues": header_values,
                    "bodyLength": len(body),
                    "body": body.decode("utf-8", errors="replace"),
                    "responseStatus": status,
                    "effect": effect,
                    "commissionEffectTotal": state.commission_effect_total,
                    "commissionedFqdns": list(state.commissioned_fqdns),
                }
            )
        self._send_json(status, response)

    def _check_common(self, raw_query: str) -> tuple[int, Any] | None:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "no query parameter is in this contract")
        authorization = self.headers.get_all("Authorization") or []
        if len(authorization) != 1:
            return 401, error_body(
                "UNAUTHORIZED", "exactly one Authorization header is required"
            )
        if authorization[0] != f"Bearer {state.access_token}":
            return 401, error_body("UNAUTHORIZED", "bearer token is not recognised")
        accept = self.headers.get_all("Accept") or []
        if [value.strip() for value in accept] != ["application/json"]:
            return 400, error_body("WIRE_SHAPE", "Accept must be application/json")
        content_types = self.headers.get_all("Content-Type") or []
        if self.command == "POST":
            if len(content_types) != 1:
                return 400, error_body(
                    "WIRE_SHAPE", "exactly one Content-Type header is required"
                )
            base = content_types[0].split(";", 1)[0].strip().casefold()
            if base != "application/json":
                return 415, error_body("WIRE_SHAPE", "Content-Type must be application/json")
        elif content_types:
            return 400, error_body("WIRE_SHAPE", "a GET must not declare a Content-Type")
        return None

    def _validate(self, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        specs, problem = state.pin.check_specs(body)
        if problem is not None:
            return 400, error_body("WIRE_SHAPE", problem)
        state.submitted_specs = specs
        return 202, state.accepted_body()

    def _poll(self, validation_id: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if body:
            return 400, error_body("WIRE_SHAPE", "a validation poll must not carry a body")
        if state.submitted_specs is None:
            return 400, error_body(
                "NO_SUCH_VALIDATION", "no host commission validation has been submitted"
            )
        if validation_id != state.validation_id:
            return 400, error_body("NO_SUCH_VALIDATION", "unknown validation id")
        state.poll_count += 1
        terminal = state.poll_count >= state.polls_before_terminal
        return 202, state.validation_body(terminal=terminal)

    def _commission(self, body: bytes) -> tuple[int, Any, dict[str, Any] | None]:
        state = self.server.state
        specs, problem = state.pin.check_specs(body)
        if problem is not None:
            return 400, error_body("WIRE_SHAPE", problem), None
        if isinstance(state.commission_error_message, str):
            return (
                400,
                error_body("COMMISSION_REJECTED", state.commission_error_message),
                None,
            )
        state.commission_effect_total += 1
        for spec in specs:
            state.commissioned_fqdns.append(spec["fqdn"])
        effect = {
            "kind": "hostsCommissioned",
            "taskId": state.task_id,
            "hostCount": len(specs),
            "commissionEffectTotal": state.commission_effect_total,
        }
        return 202, state.task_body(), effect

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(encoded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--request-log", required=True)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()

    pin = ContractPin(Path(args.contract))
    scenario = read_json(Path(args.scenario))
    state = MockState(pin, Path(args.request_log), scenario)
    server = ContractServer(("127.0.0.1", 0), state)
    host, port = server.server_address[0], server.server_address[1]

    ready_path = Path(args.ready_file)
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "port": port,
        "operationIds": [route.operation_id for route in pin.routes],
        "repositoryCommitSha": PINNED_COMMIT,
    }
    temporary = ready_path.with_suffix(ready_path.suffix + ".partial")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(ready_path)

    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
