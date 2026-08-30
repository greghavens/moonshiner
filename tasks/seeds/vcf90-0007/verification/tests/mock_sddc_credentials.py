#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by protected verification.

The service answers only the operations named by docs/contract.json plus the
single SDK connection probe that contract records under "sdkConnectionProbe".
Every request is appended to a JSONL log that is flushed and fsynced before the
response is written, so the verifier can replay the exact wire history.
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
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
PINNED_API_VERSION = "9.0.0.0"
EXPECTED_OPERATION_IDS = {
    "createToken",
    "getCredentials",
    "updateOrRotatePasswords",
    "getCredentialsTask",
    "retryCredentialsTask",
    "cancelCredentialsTask",
    "getCredential",
}

USERNAME = "svc-vcf-rotation"
PASSWORD = "dummy-vcf-login-pass-90"
ACCESS_TOKEN = "dummy-vcf-access-token-90"
REFRESH_TOKEN_ID = "dummy-vcf-refresh-token-90"

LOOKUP_QUERY_FIELDS = ("resourceName", "resourceType", "accountType")

SUCCESS_RESOURCE = "vc-a.rainpole.io"
RETRY_RESOURCE = "nsx-b.rainpole.io"
CANCEL_RESOURCE = "esx-c.rainpole.io"
DRAIN_RESOURCE = "vc-d.rainpole.io"
DUPLICATE_RESOURCE = "vc-duplicate.rainpole.io"
MISSING_ID_RESOURCE = "vc-missing-id.rainpole.io"
BLANK_TASK_RESOURCE = "vc-blank-task.rainpole.io"
MISMATCH_READBACK_RESOURCE = "vc-mismatch-readback.rainpole.io"
EMPTY_SECRET_RESOURCE = "vc-empty-secret.rainpole.io"
CANCELLED_RESOURCE = "vc-cancelled.rainpole.io"
RETRY_TIMEOUT_RESOURCE = "vc-retry-timeout.rainpole.io"
INCONSISTENT_RESOURCE = "vc-inconsistent.rainpole.io"

SCENARIOS: dict[str, dict[str, Any]] = {
    SUCCESS_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d1000001-0001-4001-8001-000000000001",
        "domainName": "sfo-m01",
        "credentialId": "c1000001-0001-4001-8001-000000000001",
        "credentialType": "SSO",
        "accountType": "USER",
        "username": "svc-rotate-a@vsphere.local",
        "oldPassword": "dummy-old-secret-a-90",
        "newPassword": "dummy-rotated-secret-a-90",
        "taskId": "a1000001-0001-4001-8001-000000000001",
        "statuses": ["PENDING", "In Progress", "SUCCESSFUL"],
        "retryStatuses": None,
        "allowCancel": False,
    },
    RETRY_RESOURCE: {
        "resourceType": "NSXT_MANAGER",
        "resourceId": "d2000002-0002-4002-8002-000000000002",
        "domainName": "sfo-m01",
        "credentialId": "c2000002-0002-4002-8002-000000000002",
        "credentialType": "API",
        "accountType": None,
        "username": "svc-rotate-b",
        "oldPassword": "dummy-old-secret-b-90",
        "newPassword": "dummy-rotated-secret-b-90",
        "taskId": "a2000002-0002-4002-8002-000000000002",
        "statuses": ["IN_PROGRESS", "FAILED"],
        "retryStatuses": ["IN_PROGRESS", "FAILED"],
        "allowCancel": False,
    },
    CANCEL_RESOURCE: {
        "resourceType": "ESXI",
        "resourceId": "d3000003-0003-4003-8003-000000000003",
        "domainName": "sfo-w01",
        "credentialId": "c3000003-0003-4003-8003-000000000003",
        "credentialType": "SSH",
        "accountType": None,
        "username": "root",
        "oldPassword": "dummy-old-secret-c-90",
        "newPassword": "dummy-rotated-secret-c-90",
        "taskId": "a3000003-0003-4003-8003-000000000003",
        "statuses": ["IN_PROGRESS"],
        "retryStatuses": None,
        "allowCancel": True,
    },
    DRAIN_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d4000004-0004-4004-8004-000000000004",
        "domainName": "sfo-m01",
        "credentialId": "c4000004-0004-4004-8004-000000000004",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-rotate-d@vsphere.local",
        "oldPassword": "dummy-old-secret-d-90",
        "newPassword": "dummy-rotated-secret-d-90",
        "taskId": "a4000004-0004-4004-8004-000000000004",
        "statuses": ["IN_PROGRESS", "SUCCESSFUL"],
        "retryStatuses": None,
        "allowCancel": False,
    },
    DUPLICATE_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d5000005-0005-4005-8005-000000000005",
        "domainName": "sfo-m01",
        "credentialId": "c5000005-0005-4005-8005-000000000005",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-duplicate@vsphere.local",
        "oldPassword": "dummy-old-secret-e-90",
        "newPassword": "dummy-rotated-secret-e-90",
        "taskId": "a5000005-0005-4005-8005-000000000005",
        "statuses": ["SUCCESSFUL"],
        "retryStatuses": None,
        "allowCancel": False,
        "lookupMode": "duplicate",
    },
    MISSING_ID_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d6000006-0006-4006-8006-000000000006",
        "domainName": "sfo-m01",
        "credentialId": "c6000006-0006-4006-8006-000000000006",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-missing-id@vsphere.local",
        "oldPassword": "dummy-old-secret-f-90",
        "newPassword": "dummy-rotated-secret-f-90",
        "taskId": "a6000006-0006-4006-8006-000000000006",
        "statuses": ["SUCCESSFUL"],
        "retryStatuses": None,
        "allowCancel": False,
        "lookupMode": "missing-id",
    },
    BLANK_TASK_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d7000007-0007-4007-8007-000000000007",
        "domainName": "sfo-m01",
        "credentialId": "c7000007-0007-4007-8007-000000000007",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-blank-task@vsphere.local",
        "oldPassword": "dummy-old-secret-g-90",
        "newPassword": "dummy-rotated-secret-g-90",
        "taskId": "a7000007-0007-4007-8007-000000000007",
        "statuses": ["SUCCESSFUL"],
        "retryStatuses": None,
        "allowCancel": False,
        "blankTaskId": True,
    },
    MISMATCH_READBACK_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d8000008-0008-4008-8008-000000000008",
        "domainName": "sfo-m01",
        "credentialId": "c8000008-0008-4008-8008-000000000008",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-readback@vsphere.local",
        "oldPassword": "dummy-old-secret-h-90",
        "newPassword": "dummy-rotated-secret-h-90",
        "taskId": "a8000008-0008-4008-8008-000000000008",
        "statuses": [" successful "],
        "retryStatuses": None,
        "allowCancel": False,
        "readbackUsername": "svc-another-account@vsphere.local",
    },
    EMPTY_SECRET_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "d9000009-0009-4009-8009-000000000009",
        "domainName": "sfo-m01",
        "credentialId": "c9000009-0009-4009-8009-000000000009",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-empty-secret@vsphere.local",
        "oldPassword": "dummy-old-secret-i-90",
        "newPassword": "",
        "taskId": "a9000009-0009-4009-8009-000000000009",
        "statuses": ["SUCCESSFUL"],
        "retryStatuses": None,
        "allowCancel": False,
    },
    CANCELLED_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "da000010-0010-4010-8010-000000000010",
        "domainName": "sfo-m01",
        "credentialId": "ca000010-0010-4010-8010-000000000010",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-cancelled@vsphere.local",
        "oldPassword": "dummy-old-secret-j-90",
        "newPassword": "dummy-rotated-secret-j-90",
        "taskId": "aa000010-0010-4010-8010-000000000010",
        "statuses": [" user cancelled "],
        "retryStatuses": None,
        "allowCancel": False,
    },
    RETRY_TIMEOUT_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "db000011-0011-4011-8011-000000000011",
        "domainName": "sfo-m01",
        "credentialId": "cb000011-0011-4011-8011-000000000011",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-retry-timeout@vsphere.local",
        "oldPassword": "dummy-old-secret-k-90",
        "newPassword": "dummy-rotated-secret-k-90",
        "taskId": "ab000011-0011-4011-8011-000000000011",
        "statuses": ["PENDING", "FAILED"],
        "retryStatuses": ["IN_PROGRESS"],
        "allowCancel": True,
    },
    INCONSISTENT_RESOURCE: {
        "resourceType": "VCENTER",
        "resourceId": "dc000012-0012-4012-8012-000000000012",
        "domainName": "sfo-m01",
        "credentialId": "cc000012-0012-4012-8012-000000000012",
        "credentialType": "SSO",
        "accountType": None,
        "username": "svc-inconsistent@vsphere.local",
        "oldPassword": "dummy-old-secret-l-90",
        "newPassword": "dummy-rotated-secret-l-90",
        "taskId": "ac000012-0012-4012-8012-000000000012",
        "statuses": [" inconsistent "],
        "retryStatuses": None,
        "allowCancel": False,
    },
}

CREATED = "2026-04-02T08:00:00.000Z"
MODIFIED = "2026-04-02T08:00:00.000Z"
ROTATED = "2026-04-02T09:15:30.000Z"


def expected_rotate_body(scenario: dict[str, Any]) -> dict[str, Any]:
    """The only CredentialsUpdateSpec this fixture accepts for a resource."""
    credential: dict[str, Any] = {
        "credentialType": scenario["credentialType"],
        "username": scenario["username"],
    }
    if scenario["accountType"] is not None:
        credential["accountType"] = scenario["accountType"]
    return {
        "operationType": "ROTATE",
        "elements": [
            {
                "resourceName": scenario["resourceName"],
                "resourceType": scenario["resourceType"],
                "credentials": [credential],
            }
        ],
    }


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path_template: str
    pattern: re.Pattern[str]

    @staticmethod
    def from_contract(operation: dict[str, Any]) -> "Route":
        path_template = operation["path"]
        pieces: list[str] = []
        cursor = 0
        for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9]*)\}", path_template):
            pieces.append(re.escape(path_template[cursor : match.start()]))
            pieces.append(f"(?P<{match.group(1)}>[^/]+)")
            cursor = match.end()
        pieces.append(re.escape(path_template[cursor:]))
        return Route(
            operation_id=operation["operationId"],
            method=operation["method"].upper(),
            path_template=path_template,
            pattern=re.compile("^" + "".join(pieces) + "$"),
        )


def load_contract() -> tuple[list[Route], str]:
    contract = json.loads(
        (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
    )
    source = contract.get("source", {})
    if source.get("commitSha") != PINNED_COMMIT:
        raise RuntimeError("contract is not pinned to the expected repository commit")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract has an unexpected specification path")
    if source.get("apiVersion") != PINNED_API_VERSION:
        raise RuntimeError("contract is not pinned to the VCF 9.0 specification")
    operations = contract.get("operations", [])
    if {item.get("operationId") for item in operations} != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the loopback service")
    probe = contract.get("sdkConnectionProbe", {})
    if probe.get("method") != "GET" or not probe.get("path"):
        raise RuntimeError("contract does not declare the SDK connection probe")
    return [Route.from_contract(item) for item in operations], probe["path"]


class MockState:
    def __init__(self, routes: list[Route], probe_path: str, request_log: Path) -> None:
        self.routes = routes
        self.probe_path = probe_path
        self.request_log = request_log
        self.sequence = 0
        self.lock = threading.Lock()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.rotated: set[str] = set()
        self.submitted: set[str] = set()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method:
                continue
            found = route.pattern.fullmatch(path)
            if found:
                return route, {
                    key: unquote(value) for key, value in found.groupdict().items()
                }
        return None, {}

    def append_log(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - outside the contract, must 404
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        route, parameters = self.server.state.match(self.command, target.path)
        query = {
            key: values[0]
            for key, values in parse_qs(target.query, keep_blank_values=True).items()
        }
        probe = (
            self.command == "GET" and target.path == self.server.state.probe_path
        )
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(body_length)

        if probe:
            status, response = self._connection_probe(target.query, body)
        elif route is None:
            status, response = 404, self._error(
                "NOT_FOUND",
                "The loopback service serves only the contract operations",
                "fixture-route",
            )
        elif route.operation_id != "getCredentials" and target.query:
            status, response = 400, self._error(
                "QUERY_NOT_ALLOWED",
                "The projected contract defines no query field here",
                "fixture-query",
            )
        else:
            status, response = self._handle(route.operation_id, parameters, query, body)

        self.server.state.append_log(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "query": query,
                "headers": {
                    name.lower(): value.strip() for name, value in self.headers.items()
                },
                "authorization": self.headers.get("Authorization"),
                "contentType": self.headers.get("Content-Type"),
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    def _handle(
        self,
        operation_id: str,
        parameters: dict[str, str],
        query: dict[str, str],
        body: bytes,
    ) -> tuple[int, Any]:
        if operation_id == "createToken":
            return self._create_token(body)
        if operation_id == "getCredentials":
            return self._get_credentials(query, body)
        if operation_id == "updateOrRotatePasswords":
            return self._rotate(body)
        if operation_id == "getCredentialsTask":
            return self._get_task(parameters, body)
        if operation_id == "retryCredentialsTask":
            return self._retry_task(parameters, body)
        if operation_id == "cancelCredentialsTask":
            return self._cancel_task(parameters, body)
        if operation_id == "getCredential":
            return self._get_credential(parameters, body)
        return 500, self._error(
            "HANDLER_MISSING", "Contract handler is missing", "fixture-handler"
        )

    # ---- operations ----------------------------------------------------

    def _connection_probe(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if raw_query or body:
            return 400, self._error(
                "PROBE_SHAPE",
                "The SDK connection probe carries no query and no body",
                "fixture-probe",
            )
        return 200, {"version": PINNED_API_VERSION}

    def _create_token(self, body: bytes) -> tuple[int, Any]:
        if self.headers.get("Authorization") is not None:
            return 400, self._error(
                "AUTH_NOT_ALLOWED",
                "Token creation must not carry a bearer token",
                "fixture-auth-header",
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if payload != {"username": USERNAME, "password": PASSWORD}:
            return 400, self._error(
                "INVALID_CREDENTIALS", "Invalid dummy credentials", "fixture-auth"
            )
        return 201, {
            "accessToken": ACCESS_TOKEN,
            "refreshToken": {"id": REFRESH_TOKEN_ID},
        }

    def _get_credentials(
        self, query: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED",
                "The credential lookup is bodyless",
                "fixture-get-body",
            )
        unknown = sorted(set(query) - set(LOOKUP_QUERY_FIELDS))
        if unknown:
            return 400, self._error(
                "QUERY_NOT_ALLOWED",
                f"Unset optional query fields must be omitted, not sent: {unknown}",
                "fixture-query-extra",
            )
        blank = sorted(name for name, value in query.items() if value == "")
        if blank:
            return 400, self._error(
                "QUERY_NOT_ALLOWED",
                f"Query fields must never be sent empty: {blank}",
                "fixture-query-blank",
            )
        resource_name = query.get("resourceName")
        if resource_name is None or "resourceType" not in query:
            return 400, self._error(
                "QUERY_INCOMPLETE",
                "The lookup filters on resourceName and resourceType",
                "fixture-query-missing",
            )
        scenario = SCENARIOS.get(resource_name)
        if scenario is None:
            return 200, {
                "elements": [],
                "pageMetadata": self._page_metadata(0),
            }
        if query["resourceType"] != scenario["resourceType"]:
            return 200, {
                "elements": [],
                "pageMetadata": self._page_metadata(0),
            }
        account_type = query.get("accountType")
        if account_type is not None and account_type != scenario["accountType"]:
            return 200, {
                "elements": [],
                "pageMetadata": self._page_metadata(0),
            }
        target = self._credential(scenario)
        lookup_mode = scenario.get("lookupMode")
        if lookup_mode == "missing-id":
            target["id"] = ""
        elements = [
            # Same credential type, different account: must not be selected.
            self._credential(
                scenario,
                credential_id="cdec0000-0000-4000-8000-00000000dec0",
                username="svc-decoy-" + scenario["username"],
                credential_type=scenario["credentialType"],
                password="dummy-decoy-secret-90",
            ),
            target,
            # Same account name, different credential type: must not be selected.
            self._credential(
                scenario,
                credential_id="cdec0001-0001-4001-8001-00000000dec1",
                credential_type="SSH"
                if scenario["credentialType"] != "SSH"
                else "API",
                password="dummy-decoy-secret-90",
            ),
        ]
        if lookup_mode == "duplicate":
            elements.append(
                self._credential(
                    scenario,
                    credential_id="cdec0002-0002-4002-8002-00000000dec2",
                )
            )
        return 200, {
            "elements": elements,
            "pageMetadata": self._page_metadata(len(elements)),
        }

    def _rotate(self, body: bytes) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error(
                "INVALID_JSON", "Malformed rotation request", "fixture-json"
            )
        scenario = self._scenario_for_spec(payload)
        if scenario is None:
            return 400, self._error(
                "UNKNOWN_SCENARIO",
                "Unknown loopback resource in the rotation request",
                "fixture-resource",
            )
        if payload != expected_rotate_body(scenario):
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH",
                "CredentialsUpdateSpec has unexpected, empty or missing members",
                "fixture-shape",
            )
        name = scenario["resourceName"]
        with self.server.state.lock:
            if name in self.server.state.submitted:
                return 409, self._error(
                    "ROTATION_IN_PROGRESS",
                    "A rotation was already submitted for this resource",
                    "fixture-duplicate",
                )
            self.server.state.submitted.add(name)
            self.server.state.tasks[scenario["taskId"]] = {
                "resourceName": name,
                "statuses": list(scenario["statuses"]),
                "polls": 0,
                "retried": False,
                "cancelled": False,
            }
        # Deliberately terminal-looking: acceptance is not completion.
        accepted = self._task(scenario["taskId"], "Successful")
        if scenario.get("blankTaskId"):
            accepted["id"] = " "
        return 202, accepted

    def _get_task(
        self, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED",
                "The credentials task read is bodyless",
                "fixture-get-body",
            )
        task_id = parameters.get("id", "")
        with self.server.state.lock:
            state = self.server.state.tasks.get(task_id)
            if state is None:
                return 404, self._error(
                    "TASK_NOT_FOUND", "Credentials task not found", "fixture-task"
                )
            if state["cancelled"]:
                status = "USER_CANCELLED"
            else:
                index = min(state["polls"], len(state["statuses"]) - 1)
                status = state["statuses"][index]
                state["polls"] += 1
            resource_name = state["resourceName"]
        if self._normalize(status) == "SUCCESSFUL":
            with self.server.state.lock:
                self.server.state.rotated.add(resource_name)
        return 200, self._credentials_task(task_id, status, resource_name)

    def _retry_task(
        self, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        task_id = parameters.get("id", "")
        with self.server.state.lock:
            state = self.server.state.tasks.get(task_id)
            if state is None:
                return 404, self._error(
                    "TASK_NOT_FOUND", "Credentials task not found", "fixture-task"
                )
            resource_name = state["resourceName"]
            already_retried = state["retried"]
        scenario = SCENARIOS[resource_name]
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error(
                "INVALID_JSON", "Malformed retry request", "fixture-json"
            )
        if payload != expected_rotate_body(scenario):
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH",
                "A retry resends the original CredentialsUpdateSpec unchanged",
                "fixture-retry-shape",
            )
        if already_retried or scenario["retryStatuses"] is None:
            return 409, self._error(
                "RETRY_NOT_ALLOWED",
                "This credentials task cannot be retried again",
                "fixture-retry",
            )
        with self.server.state.lock:
            state = self.server.state.tasks[task_id]
            state["retried"] = True
            state["statuses"] = list(scenario["retryStatuses"])
            state["polls"] = 0
        return 202, self._task(task_id, "In Progress")

    def _cancel_task(
        self, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED",
                "The credentials task cancel is bodyless",
                "fixture-delete-body",
            )
        task_id = parameters.get("id", "")
        with self.server.state.lock:
            state = self.server.state.tasks.get(task_id)
            if state is None:
                return 404, self._error(
                    "TASK_NOT_FOUND", "Credentials task not found", "fixture-task"
                )
            if not SCENARIOS[state["resourceName"]]["allowCancel"]:
                return 409, self._error(
                    "CANCEL_NOT_ALLOWED",
                    "This credentials task cannot be cancelled",
                    "fixture-cancel",
                )
            state["cancelled"] = True
        return 202, self._task(task_id, "USER_CANCELLED")

    def _get_credential(
        self, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED",
                "The credential read is bodyless",
                "fixture-get-body",
            )
        credential_id = parameters.get("id", "")
        for scenario in SCENARIOS.values():
            if scenario["credentialId"] == credential_id:
                with self.server.state.lock:
                    rotated = scenario["resourceName"] in self.server.state.rotated
                return 200, self._credential(scenario, rotated=rotated)
        return 404, self._error(
            "CREDENTIAL_NOT_FOUND", "Credential not found", "fixture-credential"
        )

    # ---- payload builders ----------------------------------------------

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

    @staticmethod
    def _scenario_for_spec(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        elements = payload.get("elements")
        if not isinstance(elements, list) or len(elements) != 1:
            return None
        if not isinstance(elements[0], dict):
            return None
        return SCENARIOS.get(elements[0].get("resourceName"))

    @staticmethod
    def _normalize(status: str) -> str:
        return status.strip().upper().replace(" ", "_")

    @staticmethod
    def _page_metadata(count: int) -> dict[str, Any]:
        return {
            "pageNumber": 0,
            "pageSize": count,
            "totalElements": count,
            "totalPages": 1 if count else 0,
        }

    @staticmethod
    def _credential(
        scenario: dict[str, Any],
        *,
        credential_id: str | None = None,
        username: str | None = None,
        credential_type: str | None = None,
        password: str | None = None,
        rotated: bool = False,
    ) -> dict[str, Any]:
        effective_username = username
        if effective_username is None:
            effective_username = scenario["username"]
            if rotated and scenario.get("readbackUsername") is not None:
                effective_username = scenario["readbackUsername"]
        effective_password = password
        if effective_password is None:
            effective_password = (
                scenario["newPassword"] if rotated else scenario["oldPassword"]
            )
        return {
            "id": scenario["credentialId"] if credential_id is None else credential_id,
            "credentialType": (
                scenario["credentialType"]
                if credential_type is None
                else credential_type
            ),
            "accountType": scenario["accountType"] or "USER",
            "username": effective_username,
            "password": effective_password,
            "creationTimestamp": CREATED,
            "modificationTimestamp": ROTATED if rotated else MODIFIED,
            "resource": {
                "resourceId": scenario["resourceId"],
                "resourceName": scenario["resourceName"],
                "resourceType": scenario["resourceType"],
                "domainNames": [scenario["domainName"]],
            },
        }

    @staticmethod
    def _task(task_id: str, status: str) -> dict[str, Any]:
        return {
            "id": task_id,
            "name": "Rotate credentials",
            "type": "CREDENTIALS_ROTATE",
            "status": status,
            "creationTimestamp": CREATED,
        }

    def _credentials_task(
        self, task_id: str, status: str, resource_name: str
    ) -> dict[str, Any]:
        scenario = SCENARIOS[resource_name]
        normalized = self._normalize(status)
        payload: dict[str, Any] = {
            "id": task_id,
            "name": "Rotate credentials",
            "type": "ROTATE",
            "status": status,
            "creationTimestamp": CREATED,
            "isAutoRotate": False,
            "subTasks": [
                {
                    "name": "Rotate " + scenario["credentialType"] + " credential",
                    "status": status,
                    "resourceId": scenario["resourceId"],
                    "resourceName": scenario["resourceName"],
                    "resourceType": scenario["resourceType"],
                    "creationTimestamp": CREATED,
                    "username": scenario["username"],
                }
            ],
        }
        if normalized in {"SUCCESSFUL", "FAILED", "USER_CANCELLED", "INCONSISTENT"}:
            payload["completionTimestamp"] = ROTATED
        if normalized == "FAILED":
            payload["errors"] = [
                {
                    "errorCode": "CREDENTIAL_ROTATE_FAILED",
                    "errorType": "EXTERNAL_SERVICE_ERROR",
                    "message": (
                        "The credential rotation workflow failed in the "
                        "loopback fixture."
                    ),
                    "referenceToken": "fixture-rotate-failure-ref",
                }
            ]
        return payload

    @staticmethod
    def _error(code: str, message: str, reference: str) -> dict[str, str]:
        return {
            "errorCode": code,
            "message": message,
            "referenceToken": reference,
        }

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def write_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: mock_sddc_credentials.py PORT_FILE REQUEST_LOG")
    for name, scenario in SCENARIOS.items():
        scenario["resourceName"] = name
    routes, probe_path = load_contract()
    state = MockState(routes, probe_path, Path(sys.argv[2]).resolve())
    server = ContractServer(("127.0.0.1", 0), state)
    write_atomic(Path(sys.argv[1]).resolve(), str(server.server_port))
    server.serve_forever()


if __name__ == "__main__":
    main()
