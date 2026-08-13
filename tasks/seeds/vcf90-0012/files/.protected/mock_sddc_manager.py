#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by protected verification.

The server refuses to start unless docs/contract.json is still the projection of
the pinned 9.0.0.0 specification, and it serves only the operations that the
contract names. Every request is appended to a flushed JSONL log.
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
from urllib.parse import parse_qsl, unquote, urlsplit


PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    ("createToken", "POST", "/v1/tokens"),
    ("getCredentialsTasks", "GET", "/v1/credentials/tasks"),
    ("updateOrRotatePasswords", "PATCH", "/v1/credentials"),
    ("retryCredentialsTask", "PATCH", "/v1/credentials/tasks/{id}"),
]

LIVE_STATUSES = frozenset({"PENDING", "IN_PROGRESS", "SUCCESSFUL"})
TIMESTAMP_BASE = "2026-03-04T12:00:"


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


def load_routes(contract_path: Path) -> list[Route]:
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
    return [Route(*entry) for entry in EXPECTED_OPERATIONS]


def require_nonblank(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"scenario {name} is invalid")
    return value


def intent_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("entityType")),
        str(entry.get("resourceName")),
        str(entry.get("username")),
        str(entry.get("credentialType")),
    )


def task_intents(task: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    sub_tasks = task.get("subTasks", [])
    if not isinstance(sub_tasks, list) or not all(
        isinstance(sub_task, dict) for sub_task in sub_tasks
    ):
        return []
    return [intent_key(sub_task) for sub_task in sub_tasks]


class MockState:
    """Stateful credentials service: rotation creates tasks, retry resumes them."""

    def __init__(
        self,
        routes: list[Route],
        request_log: Path,
        scenario: dict[str, Any],
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        self.username = require_nonblank(scenario, "username")
        self.password = require_nonblank(scenario, "password")
        self.access_token = require_nonblank(scenario, "accessToken")
        self.refresh_token_id = require_nonblank(scenario, "refreshTokenId")
        self.tasks: list[dict[str, Any]] = list(scenario.get("credentialsTasks", []))
        self.rotation_effects: dict[tuple[str, str, str, str], int] = {}
        self.sequence = 0
        self.clock = 0
        self.task_counter = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method or route.is_template:
                continue
            if route.path == path:
                return route, {}
        for route in self.routes:
            if route.method != method or not route.is_template:
                continue
            if path.startswith(route.prefix):
                remainder = path[len(route.prefix) :]
                if remainder and "/" not in remainder:
                    return route, {"id": unquote(remainder)}
        return None, {}

    def next_timestamp(self) -> str:
        self.clock += 1
        return f"{TIMESTAMP_BASE}{self.clock:02d}.000Z"

    def has_live_task(self, intents: list[tuple[str, str, str, str]]) -> bool:
        return any(
            task.get("type") == "ROTATE"
            and task.get("status") in LIVE_STATUSES
            and task_intents(task) == intents
            for task in self.tasks
        )

    def start_rotation(self, intents: list[tuple[str, str, str, str]]) -> dict[str, Any]:
        """Create a new rotation task; a live prior task makes this a duplicate."""
        duplicates = []
        if self.has_live_task(intents):
            duplicates = ["|".join(item) for item in intents]
        for key in intents:
            self.rotation_effects[key] = self.rotation_effects.get(key, 0) + 1
        created = self.new_task(intents, "IN_PROGRESS")
        self.tasks.insert(0, created)
        return {
            "kind": "rotationStarted",
            "taskId": created["id"],
            "duplicateIntents": duplicates,
            "rotationEffectTotal": sum(self.rotation_effects.values()),
        }

    def resume_rotation(self, task: dict[str, Any]) -> dict[str, Any]:
        """Continue an existing failed task in place without a new effect."""
        task["status"] = "IN_PROGRESS"
        task.pop("completionTimestamp", None)
        task.pop("errors", None)
        for sub in task.get("subTasks", []):
            sub["status"] = "IN_PROGRESS"
            sub.pop("completionTimestamp", None)
        return {
            "kind": "rotationResumed",
            "taskId": task["id"],
            "duplicateIntents": [],
            "rotationEffectTotal": sum(self.rotation_effects.values()),
        }

    def new_task(
        self, intents: list[tuple[str, str, str, str]], status: str
    ) -> dict[str, Any]:
        stamp = self.next_timestamp()
        self.task_counter += 1
        index = self.task_counter
        return {
            "id": f"rotate-task-{index:04d}",
            "name": "Rotate Passwords",
            "type": "ROTATE",
            "status": status,
            "creationTimestamp": stamp,
            "isAutoRotate": False,
            "subTasks": [
                {
                    "id": f"rotate-subtask-{index:04d}-{position:02d}",
                    "name": "Rotate Password",
                    "description": f"Rotate {key[3]} password for {key[1]}",
                    "creationTimestamp": stamp,
                    "status": status,
                    "entityType": key[0],
                    "resourceName": key[1],
                    "username": key[2],
                    "credentialType": key[3],
                }
                for position, key in enumerate(intents, start=1)
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
            elif route.operation_id == "createToken":
                status, response = self._create_token(target.query, body)
            elif route.operation_id == "getCredentialsTasks":
                status, response = self._get_tasks(target.query, body)
            elif route.operation_id == "updateOrRotatePasswords":
                status, response, effect = self._rotate(target.query, body)
            else:
                status, response, effect = self._retry(
                    target.query, body, path_params.get("id", "")
                )

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
                    "rotationEffectTotal": sum(state.rotation_effects.values()),
                }
            )
        self._send_json(status, response)

    def _create_token(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if self.headers.get_all("Authorization"):
            return 400, error_body(
                "WIRE_SHAPE", "createToken must not carry an Authorization header"
            )
        problem = self._check_json_request(raw_query)
        if problem is not None:
            return problem
        spec = decode_json_object(body)
        if spec is None or not set(spec) <= {"username", "password", "apiKey", "idToken"}:
            return 400, error_body("WIRE_SHAPE", "body violates TokenCreationSpec")
        if not all(isinstance(value, str) for value in spec.values()):
            return 400, error_body("WIRE_SHAPE", "TokenCreationSpec members must be strings")
        if spec.get("username") != state.username or spec.get("password") != state.password:
            # Deliberately reflect the rejected password.  A robust client must not
            # forward arbitrary server diagnostics that disclose its credentials.
            return 400, error_body(
                "INVALID_CREDENTIALS",
                f"rejected password {spec.get('password')!r}",
            )
        return 201, {
            "accessToken": state.access_token,
            "refreshToken": {"id": state.refresh_token_id},
        }

    def _get_tasks(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        if not self._authorized():
            return 401, error_body("UNAUTHORIZED", "invalid access token")
        if (self.headers.get_all("Accept") or []) != ["application/json"]:
            return 400, error_body("WIRE_SHAPE", "invalid Accept header")
        if body:
            return 400, error_body("WIRE_SHAPE", "getCredentialsTasks takes no body")
        limit: int | None = None
        if raw_query:
            pairs = parse_qsl(raw_query, keep_blank_values=True)
            if [name for name, _ in pairs] != ["limit"]:
                return 400, error_body("WIRE_SHAPE", "only the limit parameter is defined")
            try:
                limit = int(pairs[0][1])
            except ValueError:
                return 400, error_body("WIRE_SHAPE", "limit must be an int32")
            if limit < 1:
                return 400, error_body("WIRE_SHAPE", "limit must be positive")
        elements = state.tasks if limit is None else state.tasks[:limit]
        return 200, {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": len(elements),
                "totalElements": len(state.tasks),
                "totalPages": 1,
            },
        }

    def _rotate(
        self, raw_query: str, body: bytes
    ) -> tuple[int, Any, dict[str, Any] | None]:
        state = self.server.state
        if not self._authorized():
            return 401, error_body("UNAUTHORIZED", "invalid access token"), None
        problem = self._check_json_request(raw_query)
        if problem is not None:
            return problem[0], problem[1], None
        spec = decode_json_object(body)
        intents = credentials_update_intents(spec)
        if intents is None:
            return 400, error_body("WIRE_SHAPE", "body violates CredentialsUpdateSpec"), None
        effect = state.start_rotation(intents)
        return 202, accepted_task(effect["taskId"], state.next_timestamp()), effect

    def _retry(
        self, raw_query: str, body: bytes, task_id: str
    ) -> tuple[int, Any, dict[str, Any] | None]:
        state = self.server.state
        if not self._authorized():
            return 401, error_body("UNAUTHORIZED", "invalid access token"), None
        problem = self._check_json_request(raw_query)
        if problem is not None:
            return problem[0], problem[1], None
        spec = decode_json_object(body)
        intents = credentials_update_intents(spec)
        if intents is None:
            return 400, error_body("WIRE_SHAPE", "body violates CredentialsUpdateSpec"), None
        task = next((item for item in state.tasks if item.get("id") == task_id), None)
        if task is None:
            return 400, error_body("TASK_NOT_FOUND", "no credentials task with that ID"), None
        if task.get("status") != "FAILED":
            return 400, error_body("TASK_NOT_RETRYABLE", "only a failed task can be retried"), None
        if task_intents(task) != intents:
            return (
                400,
                error_body("TASK_MISMATCH", "retry body does not describe the task"),
                None,
            )
        effect = state.resume_rotation(task)
        return 202, accepted_task(task_id, state.next_timestamp()), effect

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.state.access_token}"
        return (self.headers.get_all("Authorization") or []) == [expected]

    def _check_json_request(self, raw_query: str) -> tuple[int, Any] | None:
        if (self.headers.get_all("Accept") or []) != ["application/json"]:
            return 400, error_body("WIRE_SHAPE", "invalid Accept header")
        if (self.headers.get_all("Content-Type") or []) != ["application/json"]:
            return 400, error_body("WIRE_SHAPE", "invalid Content-Type header")
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "operation defines no query parameters")
        return None

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
            self.wfile.flush()


def decode_json_object(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def credentials_update_intents(
    spec: dict[str, Any] | None,
) -> list[tuple[str, str, str, str]] | None:
    """Validate a CredentialsUpdateSpec structurally and return its rotation intents."""
    if spec is None or not set(spec) <= {"operationType", "elements", "autoRotatePolicy"}:
        return None
    if not isinstance(spec.get("operationType"), str) or not spec["operationType"]:
        return None
    elements = spec.get("elements")
    if not isinstance(elements, list) or not elements:
        return None
    intents: list[tuple[str, str, str, str]] = []
    for element in elements:
        if not isinstance(element, dict):
            return None
        if not set(element) <= {"resourceName", "resourceId", "resourceType", "credentials"}:
            return None
        resource_type = element.get("resourceType")
        resource_name = element.get("resourceName")
        credentials = element.get("credentials")
        if not isinstance(resource_type, str) or not resource_type:
            return None
        if not isinstance(resource_name, str) or not resource_name:
            return None
        if not isinstance(credentials, list) or not credentials:
            return None
        for credential in credentials:
            if not isinstance(credential, dict):
                return None
            if not set(credential) <= {
                "credentialType",
                "accountType",
                "username",
                "password",
            }:
                return None
            username = credential.get("username")
            credential_type = credential.get("credentialType")
            if not isinstance(username, str) or not username:
                return None
            if not isinstance(credential_type, str) or not credential_type:
                return None
            intents.append(
                (resource_type, resource_name, username, credential_type)
            )
    return intents


def accepted_task(task_id: str, stamp: str) -> dict[str, object]:
    return {
        "id": task_id,
        "name": "Rotate Passwords",
        "type": "CREDENTIALS",
        "status": "IN_PROGRESS",
        "creationTimestamp": stamp,
        "isCancellable": True,
        "isRetryable": True,
    }


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
    args.ready.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": server.server_address[1],
                "operationIds": [route.operation_id for route in routes],
            }
        ),
        encoding="utf-8",
    )
    with server:
        server.serve_forever(poll_interval=0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
