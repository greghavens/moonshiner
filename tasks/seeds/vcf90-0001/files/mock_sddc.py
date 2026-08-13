#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by protected verification.

Routes are built from docs/contract.json. Nothing outside the four operations
that contract names is served, so a candidate cannot reach an operation the
pinned VMware Cloud Foundation 9.0 specification does not cover.

Every request is appended to a JSON Lines request log so verify.ps1 can assert
the exact wire shape the real VMware SDK produced.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent

PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_TAG = "9.0.0.0"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
PINNED_API_VERSION = "9.0.0.0"
EXPECTED_OPERATION_IDS = {
    "createToken",
    "getBundle",
    "startBundleDownloadByID",
    "getTask",
}

# The PowerCLI SDK issues this unversioned appliance read while
# Connect-VcfSddcManagerServer establishes a session. It is client handshake
# behaviour, not one of the contract operations, so it is answered separately
# and is never treated as a routable operation.
CONNECTION_PROBE = ("GET", "/v1/sddc-manager")

USERNAME = "svc-vcf-depot@vsphere.local"
PASSWORD = "dummy-vcf-login-pass-90"
ACCESS_TOKEN = "dummy-vcf-access-token-90"
REFRESH_TOKEN_ID = "dummy-vcf-refresh-token-90"

IMMEDIATE_BUNDLE = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
SCHEDULED_BUNDLE = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
TRAP_BUNDLE = "cccccccc-3333-4333-8333-cccccccccccc"
TIMEOUT_BUNDLE = "dddddddd-4444-4444-8444-dddddddddddd"
MISSING_BUNDLE = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee"
SKIPPED_BUNDLE = "ffffffff-6666-4666-8666-ffffffffffff"
CANCELLED_BUNDLE = "abababab-7777-4777-8777-abababababab"
EMPTY_TASK_BUNDLE = "cdcdcdcd-8888-4888-8888-cdcdcdcdcdcd"

BUNDLES: dict[str, dict[str, Any]] = {
    IMMEDIATE_BUNDLE: {
        "id": IMMEDIATE_BUNDLE,
        "type": "VMWARE_SOFTWARE",
        "version": "9.0.0.0-24001234",
        "vendor": "VMware",
        "sizeMB": 8452.5,
        "downloadStatus": "PENDING",
        "isCumulative": True,
    },
    SCHEDULED_BUNDLE: {
        "id": SCHEDULED_BUNDLE,
        "type": "SDDC_MANAGER",
        "version": "9.0.0.0-24005678",
        "vendor": "VMware",
        "sizeMB": 1204.0,
        "downloadStatus": "PENDING",
        "isCumulative": False,
    },
    TRAP_BUNDLE: {
        "id": TRAP_BUNDLE,
        "type": "VMWARE_SOFTWARE",
        "version": "9.0.0.0-24009012",
        "vendor": "VMware",
        "sizeMB": 6110.25,
        "downloadStatus": "PENDING",
        "isCumulative": True,
    },
    TIMEOUT_BUNDLE: {
        "id": TIMEOUT_BUNDLE,
        "type": "VMWARE_SOFTWARE",
        "version": "9.0.0.0-24003456",
        "vendor": "VMware",
        "sizeMB": 970.75,
        "downloadStatus": "PENDING",
        "isCumulative": False,
    },
    SKIPPED_BUNDLE: {
        "id": SKIPPED_BUNDLE,
        "type": "VMWARE_SOFTWARE",
        "version": "9.0.0.0-24007890",
        "vendor": "VMware",
        "sizeMB": 730.5,
        "downloadStatus": "PENDING",
        "isCumulative": False,
    },
    CANCELLED_BUNDLE: {
        "id": CANCELLED_BUNDLE,
        "type": "VMWARE_SOFTWARE",
        "version": "9.0.0.0-24001122",
        "vendor": "VMware",
        "sizeMB": 640.25,
        "downloadStatus": "PENDING",
        "isCumulative": True,
    },
    EMPTY_TASK_BUNDLE: {
        "id": EMPTY_TASK_BUNDLE,
        "type": "SDDC_MANAGER",
        "version": "9.0.0.0-24003344",
        "vendor": "VMware",
        "sizeMB": 512.0,
        "downloadStatus": "PENDING",
        "isCumulative": False,
    },
}

# submission_status is what the 202 carries. It is deliberately terminal-looking
# for TRAP_BUNDLE: a client that trusts the submission response reports success
# for a download that actually fails.
SCENARIOS: dict[str, dict[str, Any]] = {
    IMMEDIATE_BUNDLE: {
        "task_id": "11111111-1111-4111-8111-111111111111",
        "submission_status": "PENDING",
        "statuses": ["Pending", "In Progress", "SUCCESSFUL"],
        "errors": [],
    },
    SCHEDULED_BUNDLE: {
        "task_id": "22222222-2222-4222-8222-222222222222",
        "submission_status": "PENDING",
        "statuses": ["IN_PROGRESS", "Completed With Warning"],
        "errors": [],
    },
    TRAP_BUNDLE: {
        "task_id": "33333333-3333-4333-8333-333333333333",
        "submission_status": "SUCCESSFUL",
        "statuses": ["IN_PROGRESS", "Failed"],
        "errors": [
            {
                "errorCode": "BUNDLE_DOWNLOAD_FAILED",
                "message": "The depot rejected the bundle transfer in the loopback fixture.",
                "referenceToken": "GH7YT2",
            },
            {
                "errorCode": "SECOND_ERROR_MUST_NOT_WIN",
                "message": "This second task error must not become the exception message.",
                "referenceToken": "SECOND",
            },
        ],
    },
    TIMEOUT_BUNDLE: {
        "task_id": "44444444-4444-4444-8444-444444444444",
        "submission_status": "PENDING",
        # "Validating" is not a status the contract classifies. It must be
        # treated as non-terminal and bounded by PollLimit.
        "statuses": ["Validating", "IN_PROGRESS"],
        "errors": [],
    },
    SKIPPED_BUNDLE: {
        "task_id": "55555555-5555-4555-8555-555555555555",
        "submission_status": "PENDING",
        "statuses": [" in progress ", " skipped "],
        "errors": [],
    },
    CANCELLED_BUNDLE: {
        "task_id": "66666666-6666-4666-8666-666666666666",
        "submission_status": "PENDING",
        "statuses": [" cancelled "],
        "errors": [],
    },
    EMPTY_TASK_BUNDLE: {
        # Whitespace is deliberately not a usable task id. A correct client
        # rejects it before attempting the first getTask request.
        "task_id": "   ",
        "submission_status": "PENDING",
        "statuses": ["SUCCESSFUL"],
        "errors": [],
    },
}

TASK_NAME = "Bundle Download"
TASK_TYPE = "BUNDLE_DOWNLOAD"
CREATION_TIMESTAMP = "2026-02-11T08:15:00.000Z"


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


def load_contract() -> dict[str, Any]:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("commitSha") != PINNED_COMMIT:
        raise RuntimeError("contract is not pinned to the expected repository commit")
    if source.get("tag") != PINNED_TAG:
        raise RuntimeError("contract is not pinned to the expected repository tag")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract has an unexpected specification path")
    if source.get("apiVersion") != PINNED_API_VERSION:
        raise RuntimeError("contract is not the 9.0.0.0 revision of the specification")
    if source.get("sourceKind") != "openapi-specification":
        raise RuntimeError("contract does not declare an OpenAPI specification source")
    operation_ids = {item.get("operationId") for item in contract.get("operations", [])}
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the loopback service")
    return contract


def download_spec_members(contract: dict[str, Any]) -> set[str]:
    schema = contract["schemas"]["BundleDownloadSpec"]
    return set(schema.get("properties", {}))


class MockState:
    def __init__(self, contract: dict[str, Any], request_log: Path) -> None:
        self.routes = [Route.from_contract(op) for op in contract["operations"]]
        self.allowed_download_members = download_spec_members(contract)
        self.request_log = request_log
        self.sequence = 0
        self.polls: dict[str, int] = {}
        self.submitted: dict[str, str] = {}
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method:
                continue
            found = route.pattern.fullmatch(path)
            if found:
                return route, dict(found.groupdict())
        return None, {}

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()

    def next_poll(self, bundle_id: str) -> tuple[str, list[dict[str, str]]]:
        scenario = SCENARIOS[bundle_id]
        with self.lock:
            index = self.polls.get(bundle_id, 0)
            self.polls[bundle_id] = index + 1
        statuses = scenario["statuses"]
        if index < len(statuses):
            status = statuses[index]
        else:
            # Exhausted scripted statuses: hold on the last one so a client that
            # never stops polling is bounded by its own PollLimit.
            status = statuses[-1]
        errors = scenario["errors"] if status.strip().upper().replace(" ", "_") == "FAILED" else []
        return status, errors


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type, state: MockState) -> None:
        super().__init__(address, handler)
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

    def do_PATCH(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _error(code: str, message: str, token: str) -> dict[str, Any]:
        return {"errorCode": code, "message": message, "referenceToken": token}

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

    def _task(self, task_id: str, status: str, errors: list[dict[str, str]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": task_id,
            "name": TASK_NAME,
            "type": TASK_TYPE,
            "status": status,
            "creationTimestamp": CREATION_TIMESTAMP,
        }
        if status.strip().upper().replace(" ", "_") in {
            "SUCCESSFUL",
            "COMPLETED_WITH_WARNING",
            "SKIPPED",
            "FAILED",
            "CANCELLED",
        }:
            body["completionTimestamp"] = "2026-02-11T08:22:00.000Z"
        if errors:
            body["errors"] = errors
        return body

    # --------------------------------------------------------------- dispatch

    def _dispatch(self) -> None:
        split_target = urlsplit(self.path)
        route, parameters = self.server.state.match(self.command, split_target.path)
        is_probe = (self.command, split_target.path) == CONNECTION_PROBE

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        raw_body = self.rfile.read(length) if length > 0 else b""

        if is_probe:
            status, response = self._connection_probe()
        elif route is None:
            status, response = 404, self._error(
                "NOT_FOUND",
                "The protected contract does not serve this operation.",
                "fixture-route",
            )
        elif split_target.query:
            status, response = 400, self._error(
                "QUERY_NOT_ALLOWED",
                "The protected contract does not allow a query string here.",
                "fixture-query",
            )
        else:
            status, response = self._handle_operation(
                route.operation_id, parameters, raw_body
            )

        try:
            parsed = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None

        headers = {name.lower(): value.strip() for name, value in self.headers.items()}
        self.server.state.append_log(
            {
                "operationId": route.operation_id if route else None,
                "connectionProbe": is_probe,
                "method": self.command,
                "rawTarget": self.path,
                "path": split_target.path,
                "query": split_target.query,
                "pathParameters": parameters,
                "authorization": headers.get("authorization"),
                "contentType": headers.get("content-type"),
                "accept": headers.get("accept"),
                "userAgent": headers.get("user-agent"),
                "body": raw_body.decode("utf-8", errors="replace"),
                "json": parsed,
                "responseStatus": status,
            }
        )

        payload = json.dumps(response).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _connection_probe(self) -> tuple[int, dict[str, Any]]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "A valid bearer token is required.", "fixture-auth"
            )
        return 200, {
            "id": "99999999-9999-4999-8999-999999999999",
            "fqdn": "127.0.0.1",
            "version": PINNED_API_VERSION,
        }

    def _handle_operation(
        self, operation_id: str, parameters: dict[str, str], raw_body: bytes
    ) -> tuple[int, dict[str, Any]]:
        if operation_id == "createToken":
            return self._create_token(raw_body)
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "A valid bearer token is required.", "fixture-auth"
            )
        if operation_id == "getBundle":
            return self._get_bundle(parameters.get("id", ""))
        if operation_id == "startBundleDownloadByID":
            return self._start_download(parameters.get("id", ""), raw_body)
        if operation_id == "getTask":
            return self._get_task(parameters.get("id", ""))
        return 404, self._error("NOT_FOUND", "Unhandled operation.", "fixture-route")

    def _create_token(self, raw_body: bytes) -> tuple[int, dict[str, Any]]:
        try:
            spec = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error(
                "BAD_REQUEST", "TokenCreationSpec must be JSON.", "fixture-token"
            )
        if not isinstance(spec, dict):
            return 400, self._error(
                "BAD_REQUEST", "TokenCreationSpec must be an object.", "fixture-token"
            )
        if spec.get("username") != USERNAME or spec.get("password") != PASSWORD:
            return 400, self._error(
                "BAD_REQUEST", "Unknown fixture credentials.", "fixture-token"
            )
        return 201, {
            "accessToken": ACCESS_TOKEN,
            "refreshToken": {"id": REFRESH_TOKEN_ID},
        }

    def _get_bundle(self, bundle_id: str) -> tuple[int, dict[str, Any]]:
        bundle = BUNDLES.get(bundle_id)
        if bundle is None:
            return 404, self._error(
                "BUNDLE_NOT_FOUND",
                f"No bundle with id {bundle_id} exists in the loopback fixture.",
                "fixture-bundle",
            )
        return 200, dict(bundle)

    def _start_download(
        self, bundle_id: str, raw_body: bytes
    ) -> tuple[int, dict[str, Any]]:
        if bundle_id not in SCENARIOS:
            return 404, self._error(
                "BUNDLE_NOT_FOUND",
                f"No bundle with id {bundle_id} exists in the loopback fixture.",
                "fixture-bundle",
            )
        try:
            spec = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error(
                "BAD_REQUEST", "BundleUpdateSpec must be JSON.", "fixture-body"
            )
        if not isinstance(spec, dict) or set(spec) - {"bundleDownloadSpec"}:
            return 400, self._error(
                "BAD_REQUEST",
                "BundleUpdateSpec accepts only bundleDownloadSpec.",
                "fixture-body",
            )
        download = spec.get("bundleDownloadSpec")
        if not isinstance(download, dict):
            return 400, self._error(
                "BAD_REQUEST",
                "bundleDownloadSpec must be a BundleDownloadSpec object.",
                "fixture-body",
            )
        unknown = set(download) - self.server.state.allowed_download_members
        if unknown:
            return 400, self._error(
                "BAD_REQUEST",
                "BundleDownloadSpec members outside the contract: "
                + ", ".join(sorted(unknown)),
                "fixture-body",
            )
        with self.server.state.lock:
            self.server.state.submitted[bundle_id] = SCENARIOS[bundle_id]["task_id"]
        scenario = SCENARIOS[bundle_id]
        return 202, self._task(scenario["task_id"], scenario["submission_status"], [])

    def _get_task(self, task_id: str) -> tuple[int, dict[str, Any]]:
        for bundle_id, scenario in SCENARIOS.items():
            if scenario["task_id"] != task_id:
                continue
            with self.server.state.lock:
                submitted = bundle_id in self.server.state.submitted
            if not submitted:
                return 404, self._error(
                    "TASK_NOT_FOUND",
                    "That task has not been submitted in this fixture run.",
                    "fixture-task",
                )
            status, errors = self.server.state.next_poll(bundle_id)
            return 200, self._task(task_id, status, errors)
        return 404, self._error(
            "TASK_NOT_FOUND", f"No task with id {task_id}.", "fixture-task"
        )


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: mock_sddc.py <port-file> <request-log>", file=sys.stderr)
        return 2
    port_file = Path(sys.argv[1])
    request_log = Path(sys.argv[2])

    contract = load_contract()
    state = MockState(contract, request_log)
    server = ContractServer(("127.0.0.1", 0), ContractHandler, state)

    port = server.server_address[1]
    port_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = port_file.with_suffix(port_file.suffix + ".tmp")
    tmp.write_text(str(port), encoding="utf-8")
    tmp.replace(port_file)

    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
