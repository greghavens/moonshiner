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


ROOT = Path(__file__).resolve().parent
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_IDS = {
    "createToken",
    "commissionHosts",
    "getTask",
}

USERNAME = "svc-vcf-commission"
PASSWORD = "dummy-vcf-login-pass-91"
ACCESS_TOKEN = "dummy-vcf-access-token-91"
REFRESH_TOKEN_ID = "dummy-vcf-refresh-token-91"

MINIMAL_FQDN = "esx-minimal.lab.example"
FULL_FQDN = "esx-vvol.lab.example"
TIMEOUT_FQDN = "esx-timeout.lab.example"

SCENARIOS = {
    MINIMAL_FQDN: {
        "task_id": "11111111-1111-4111-8111-111111111111",
        "statuses": ["PENDING", "In Progress", "Completed With Warning"],
    },
    FULL_FQDN: {
        "task_id": "22222222-2222-4222-8222-222222222222",
        "statuses": ["QUEUED", "Failed"],
    },
    TIMEOUT_FQDN: {
        "task_id": "33333333-3333-4333-8333-333333333333",
        "statuses": ["WAITING_FOR_LOCK", "IN_PROGRESS"],
    },
}

EXPECTED_BODIES: dict[str, list[dict[str, Any]]] = {
    MINIMAL_FQDN: [
        {
            "fqdn": MINIMAL_FQDN,
            "username": "root",
            "password": "dummy-esxi-minimal-pass-91",
            "storageType": "VSAN",
            "networkPoolId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        }
    ],
    FULL_FQDN: [
        {
            "fqdn": FULL_FQDN,
            "username": "root",
            "password": "dummy-esxi-vvol-pass-91",
            "storageType": "VVOL",
            "vvolStorageProtocolType": "FC",
            "networkPoolId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "networkPoolName": "vvol-host-pool",
            "sshThumbprint": "SHA256:fixture-ssh-thumbprint",
            "sslThumbprint": "AA:BB:CC:DD:EE:FF",
        }
    ],
    TIMEOUT_FQDN: [
        {
            "fqdn": TIMEOUT_FQDN,
            "username": "root",
            "password": "dummy-esxi-timeout-pass-91",
            "storageType": "NFS",
            "networkPoolId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
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


def load_routes() -> list[Route]:
    contract = json.loads(
        (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
    )
    source = contract.get("source", {})
    if source.get("commitSha") != PINNED_COMMIT:
        raise RuntimeError("contract is not pinned to the expected repository commit")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract has an unexpected specification path")
    operations = contract.get("operations", [])
    operation_ids = {item.get("operationId") for item in operations}
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the loopback service")
    return [Route.from_contract(item) for item in operations]


class MockState:
    def __init__(self, routes: list[Route], request_log: Path) -> None:
        self.routes = routes
        self.request_log = request_log
        self.sequence = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(
        self, method: str, path: str
    ) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method:
                continue
            match = route.pattern.fullmatch(path)
            if match:
                return route, {
                    key: unquote(value) for key, value in match.groupdict().items()
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

    def do_PATCH(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def _dispatch(self) -> None:
        split_target = urlsplit(self.path)
        route, parameters = self.server.state.match(self.command, split_target.path)
        connection_version_probe = (
            self.command == "GET" and split_target.path == "/v1/sddc-manager"
        )
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(body_length)

        if connection_version_probe and split_target.query:
            status, response = 400, self._error(
                "QUERY_NOT_ALLOWED",
                "The SDK connection probe does not allow a query string",
                "fixture-query",
            )
        elif connection_version_probe:
            status, response = self._connection_version(body)
        elif route is None:
            status, response = 404, self._error(
                "NOT_FOUND", "Operation not served", "fixture-route"
            )
        elif split_target.query:
            status, response = 400, self._error(
                "QUERY_NOT_ALLOWED",
                "The protected contract does not allow a query string here",
                "fixture-query",
            )
        else:
            status, response = self._handle_operation(
                route.operation_id, parameters, body
            )

        headers = {
            name.lower(): value.strip() for name, value in self.headers.items()
        }
        self.server.state.append_log(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": split_target.path,
                "rawQuery": split_target.query,
                "query": {
                    key: values[0]
                    for key, values in parse_qs(
                        split_target.query, keep_blank_values=True
                    ).items()
                },
                "headers": headers,
                "authorization": self.headers.get("Authorization"),
                "contentType": self.headers.get("Content-Type"),
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    def _handle_operation(
        self, operation_id: str, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if operation_id == "createToken":
            return self._create_token(body)
        if operation_id == "commissionHosts":
            return self._commission_hosts(body)
        if operation_id == "getTask":
            return self._get_task(parameters, body)
        return 500, self._error(
            "HANDLER_MISSING", "Contract handler is missing", "fixture-handler"
        )

    def _connection_version(self, body: bytes) -> tuple[int, Any]:
        """Serve the genuine SDK's post-authentication version probe."""
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED",
                "SDK connection version probe must not have a body",
                "fixture-version-body",
            )
        return 200, {"version": "9.1.0.0"}

    def _create_token(self, body: bytes) -> tuple[int, Any]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if payload != {"username": USERNAME, "password": PASSWORD}:
            return 400, self._error(
                "INVALID_CREDENTIALS", "Invalid dummy credentials", "fixture-auth"
            )
        if self.headers.get("Authorization") is not None:
            return 400, self._error(
                "AUTH_NOT_ALLOWED",
                "Token creation must not carry a bearer token",
                "fixture-auth-header",
            )
        return 201, {
            "accessToken": ACCESS_TOKEN,
            "refreshToken": {"id": REFRESH_TOKEN_ID},
        }

    def _commission_hosts(self, body: bytes) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error(
                "INVALID_JSON", "Malformed host commission request", "fixture-json"
            )
        if (
            not isinstance(payload, list)
            or len(payload) != 1
            or not isinstance(payload[0], dict)
        ):
            return 400, self._error(
                "INVALID_BODY",
                "Host commission request must be a one-element array",
                "fixture-body",
            )

        fqdn = payload[0].get("fqdn")
        expected = EXPECTED_BODIES.get(fqdn)
        if expected is None:
            return 400, self._error(
                "UNKNOWN_SCENARIO", "Unknown loopback host", "fixture-host"
            )
        if payload != expected:
            return 400, self._error(
                "WIRE_SHAPE_MISMATCH",
                "Host specification has unexpected, empty, or missing members",
                "fixture-shape",
            )

        scenario = SCENARIOS[fqdn]
        task_id = scenario["task_id"]
        with self.server.state.lock:
            self.server.state.tasks[task_id] = {
                "statuses": list(scenario["statuses"]),
                "polls": 0,
            }
        # Deliberately terminal-looking: a 202 client still has to poll the task.
        return 202, self._task(task_id, "Successful")

    def _get_task(
        self, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        if not self._authorized():
            return 401, self._error(
                "UNAUTHORIZED", "Bearer token required", "fixture-401"
            )
        if body:
            return 400, self._error(
                "BODY_NOT_ALLOWED", "Task GET must not have a body", "fixture-get-body"
            )
        task_id = parameters.get("id", "")
        with self.server.state.lock:
            state = self.server.state.tasks.get(task_id)
            if state is None:
                return 404, self._error(
                    "TASK_NOT_FOUND", "Task not found", "fixture-task"
                )
            index = min(state["polls"], len(state["statuses"]) - 1)
            status = state["statuses"][index]
            state["polls"] += 1
        return 200, self._task(task_id, status)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

    @staticmethod
    def _error(code: str, message: str, reference: str) -> dict[str, str]:
        return {
            "errorCode": code,
            "message": message,
            "referenceToken": reference,
        }

    @staticmethod
    def _task(task_id: str, status: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": task_id,
            "name": "Commission host",
            "type": "HOST_COMMISSION",
            "status": status,
            "creationTimestamp": "2026-07-28T12:00:00Z",
        }
        normalized = status.strip().upper().replace(" ", "_")
        if normalized in {
            "SUCCESSFUL",
            "COMPLETED_WITH_WARNING",
            "SKIPPED",
            "FAILED",
            "CANCELLED",
            "TIMED_OUT",
        }:
            payload["completionTimestamp"] = "2026-07-28T12:00:05Z"
        if normalized == "FAILED":
            payload["errors"] = [
                {
                    "errorCode": "HOST_COMMISSION_FAILED",
                    "message": "The host commission workflow failed in the loopback fixture.",
                    "referenceToken": "fixture-host-failure-ref",
                }
            ]
        return payload

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
        raise SystemExit("usage: mock_sddc.py PORT_FILE REQUEST_LOG")
    port_file = Path(sys.argv[1]).resolve()
    request_log = Path(sys.argv[2]).resolve()
    state = MockState(load_routes(), request_log)
    server = ContractServer(("127.0.0.1", 0), state)
    write_atomic(port_file, str(server.server_port))
    server.serve_forever()


if __name__ == "__main__":
    main()
