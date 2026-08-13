#!/usr/bin/env python3
"""Loopback mock of the VCF 9.0 SDDC Manager REST API.

The mock is pinned to docs/contract.json: it refuses to start unless its route
table is exactly the operation set the contract names, and it validates request
bodies against the contract's request schemas. Every request it receives --
including rejected ones -- is appended to a JSONL request log so a test can
inspect the exact wire shape that was sent.

Run standalone:

    python3 tests/sddc_mock.py --log /tmp/requests.jsonl

It binds 127.0.0.1 on an ephemeral port and prints ``PORT=<port>`` on stdout.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures.json"

CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
FIXTURES = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))

# (method, path template) -> operationId. Checked against the contract below.
ROUTES = {
    ("POST", "/v1/tokens"): "createToken",
    ("GET", "/v1/tasks"): "getTasks",
    ("GET", "/v1/tasks/{id}"): "getTask",
    ("PATCH", "/v1/tasks/{id}"): "retryTask",
    ("GET", "/v1/notifications"): "getNotifications",
    ("POST", "/v1/system/support-bundles"): "startSupportBundle",
    ("GET", "/v1/system/support-bundles/{id}"): "getSupportBundleStatus",
}

CONTRACT_OPERATIONS = {
    operation["operationId"]: operation for operation in CONTRACT["operations"]
}


def _assert_pinned_to_contract() -> None:
    contract_routes = {
        (operation["method"], operation["path"]): operation["operationId"]
        for operation in CONTRACT["operations"]
    }
    if contract_routes != ROUTES:
        raise SystemExit(
            "mock route table does not match docs/contract.json: "
            f"contract={sorted(contract_routes.items())} mock={sorted(ROUTES.items())}"
        )


_assert_pinned_to_contract()

_TEMPLATE_PATTERNS = [
    (
        method,
        template,
        re.compile("^" + re.sub(r"\{[^/}]+\}", "([^/]+)", template) + "$"),
    )
    for (method, template) in ROUTES
]


class ApiError(Exception):
    """A modelled SDDC Manager error response."""

    def __init__(self, status: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.body = {
            "errorCode": error_code,
            "errorType": "VALIDATION_FAILED" if status == 400 else "ERROR",
            "message": message,
        }


class MockState:
    """Mutable server state; reset for every server instance."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sequence = 0
        self.issued_tokens: set[str] = set()
        tasks = json.loads(json.dumps(FIXTURES["tasks"]))
        # The API does not promise newest-first ordering. Serve the oldest
        # completed tasks first so callers must compare completion timestamps.
        self.tasks = sorted(
            tasks, key=lambda task: task.get("completionTimestamp") or ""
        )
        self.notifications = json.loads(json.dumps(FIXTURES["notifications"]))
        self.bundle_polls = 0
        self.bundle_started = False
        self.retried_task_ids: list[str] = []


def _log_record(log_path: Path, record: dict) -> None:
    if log_path is None:
        return
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()


def _match_route(method: str, path: str):
    for route_method, template, pattern in _TEMPLATE_PATTERNS:
        if route_method != method:
            continue
        match = pattern.match(path)
        if match:
            names = re.findall(r"\{([^/}]+)\}", template)
            return ROUTES[(route_method, template)], dict(zip(names, match.groups()))
    return None, {}


def _require_object(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ApiError(400, "INVALID_BODY", f"{where} must be a JSON object.")
    return value


def _validate_against_contract(value, node: dict, where: str) -> None:
    """Validate a request body fragment against a contract property node."""
    if value is None:
        raise ApiError(
            400,
            "NULL_PROPERTY",
            f"{where} must not be null; omit the property instead of sending null.",
        )
    if "properties" in node:
        _require_object(value, where)
        allowed = node["properties"]
        for key in value:
            if key not in allowed:
                raise ApiError(
                    400,
                    "UNKNOWN_PROPERTY",
                    f"{where}.{key} is not a property of {node.get('schema', 'the request schema')} "
                    f"in VCF {CONTRACT['source']['api_version']}.",
                )
        for key, child in value.items():
            _validate_against_contract(child, allowed[key], f"{where}.{key}")
        return
    node_type = node.get("type")
    if node_type == "array":
        if not isinstance(value, list):
            raise ApiError(400, "INVALID_TYPE", f"{where} must be an array.")
        items = node.get("items", {})
        for index, item in enumerate(value):
            _validate_against_contract(item, items, f"{where}[{index}]")
        return
    if node_type == "boolean" and not isinstance(value, bool):
        raise ApiError(400, "INVALID_TYPE", f"{where} must be a boolean.")
    if node_type == "string" and not isinstance(value, str):
        raise ApiError(400, "INVALID_TYPE", f"{where} must be a string.")


def _domain_of_task(task: dict) -> dict | None:
    for resource in task.get("resources", []):
        if resource.get("type") == "DOMAIN":
            return resource
    return None


def _list_view(task: dict) -> dict:
    """The task list view omits the sub-task tree and per-task errors."""
    return {
        key: value
        for key, value in task.items()
        if key not in ("subTasks", "errors")
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VMware-SDDC-Manager-Mock/9.0.0.0"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------
    def log_message(self, *_args) -> None:  # silence stderr access logging
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _read_body(self) -> str:
        length = self.headers.get("Content-Length")
        if not length:
            return ""
        return self.rfile.read(int(length)).decode("utf-8")

    def _dispatch(self, method: str) -> None:
        state = self.server.state
        received_at = time.monotonic()
        split = urlsplit(self.path)
        raw_body = self._read_body()
        operation_id, path_params = _match_route(method, split.path)

        try:
            if operation_id is None:
                raise ApiError(
                    404,
                    "OPERATION_NOT_SERVED",
                    f"{method} {split.path} is not one of the operations named by the contract.",
                )
            handler = getattr(self, f"_op_{operation_id}")
            if operation_id != "createToken":
                self._require_bearer(state)
            status, payload = handler(state, split, raw_body, path_params)
        except ApiError as error:
            status, payload = error.status, error.body

        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        with state.lock:
            state.sequence += 1
            sequence = state.sequence
        try:
            parsed_body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            parsed_body = None
        _log_record(
            self.server.log_path,
            {
                "seq": sequence,
                "operationId": operation_id,
                "method": method,
                "path": split.path,
                "query": split.query,
                "query_params": parse_qs(split.query, keep_blank_values=True),
                "path_params": path_params,
                "received_at": received_at,
                "headers": {
                    key.lower(): value for key, value in self.headers.items()
                },
                "body": raw_body,
                "body_json": parsed_body,
                "status": status,
            },
        )

        # Record the completed request before releasing the response. This
        # preserves causal request order even though ThreadingHTTPServer uses a
        # different handler thread for each urllib connection.
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _require_bearer(self, state: MockState) -> None:
        header = self.headers.get("Authorization")
        if not header or not header.startswith("Bearer "):
            raise ApiError(
                401,
                "UNAUTHORIZED",
                "Authorization header is missing or not in correct format.",
            )
        if header[len("Bearer "):] not in state.issued_tokens:
            raise ApiError(401, "UNAUTHORIZED", "The access token is not valid.")

    def _json_body(self, raw_body: str) -> dict:
        if not raw_body.strip():
            raise ApiError(400, "MISSING_BODY", "A request body is required.")
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError as error:
            raise ApiError(400, "INVALID_JSON", f"Malformed JSON body: {error}") from error

    def _reject_unknown_query(self, operation_id: str, split) -> dict:
        allowed = {
            parameter["name"]
            for parameter in CONTRACT_OPERATIONS[operation_id].get("parameters", [])
            if parameter["in"] == "query"
        }
        params = parse_qs(split.query, keep_blank_values=True)
        for name in params:
            if name not in allowed:
                raise ApiError(
                    400,
                    "UNKNOWN_QUERY_PARAMETER",
                    f"'{name}' is not a query parameter of {operation_id}.",
                )
        return params

    # -- operations -------------------------------------------------------
    def _op_createToken(self, state, split, raw_body, path_params):
        body = self._json_body(raw_body)
        _validate_against_contract(
            body, CONTRACT_OPERATIONS["createToken"]["request"], "TokenCreationSpec"
        )
        credentials = FIXTURES["credentials"]
        if body.get("username") != credentials["username"] or body.get(
            "password"
        ) != credentials["password"]:
            raise ApiError(400, "INVALID_CREDENTIALS", "Invalid username or password.")
        with state.lock:
            state.issued_tokens.add(credentials["accessToken"])
        return 201, {
            "accessToken": credentials["accessToken"],
            "refreshToken": {"id": credentials["refreshTokenId"]},
        }

    def _op_getTasks(self, state, split, raw_body, path_params):
        params = self._reject_unknown_query("getTasks", split)
        tasks = [_list_view(task) for task in state.tasks]
        task_status = params.get("taskStatus", [None])[0]
        if task_status is not None:
            tasks = [task for task in tasks if task["status"] == task_status]
        task_type = params.get("taskType", [None])[0]
        if task_type is not None:
            tasks = [task for task in tasks if task.get("type") == task_type]
        resource_id = params.get("resourceId", [None])[0]
        if resource_id is not None:
            tasks = [
                task
                for task in tasks
                if any(
                    resource.get("resourceId") == resource_id
                    for resource in task.get("resources", [])
                )
            ]
        total = len(tasks)
        limit = params.get("limit", [None])[0]
        if limit is not None:
            tasks = tasks[: int(limit)]
        return 200, {
            "elements": tasks,
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": len(tasks),
                "totalElements": total,
                "totalPages": 1 if total else 0,
            },
        }

    def _op_getTask(self, state, split, raw_body, path_params):
        self._reject_unknown_query("getTask", split)
        for task in state.tasks:
            if task["id"] == path_params["id"]:
                return 200, task
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {path_params['id']} was not found.")

    def _op_retryTask(self, state, split, raw_body, path_params):
        self._reject_unknown_query("retryTask", split)
        if raw_body.strip():
            raise ApiError(
                400, "UNEXPECTED_BODY", "retryTask does not accept a request body."
            )
        for task in state.tasks:
            if task["id"] != path_params["id"]:
                continue
            if task["status"] != "Failed" or not task.get("isRetryable"):
                raise ApiError(
                    409,
                    "TASK_NOT_RETRYABLE",
                    "Task can not be retried. Only a failed Task can be retried.",
                )
            with state.lock:
                task["status"] = "In Progress"
                task.pop("completionTimestamp", None)
                state.retried_task_ids.append(task["id"])
            return 200, None
        raise ApiError(404, "TASK_NOT_FOUND", f"Task {path_params['id']} was not found.")

    def _op_getNotifications(self, state, split, raw_body, path_params):
        self._reject_unknown_query("getNotifications", split)
        return 200, state.notifications

    def _op_startSupportBundle(self, state, split, raw_body, path_params):
        body = self._json_body(raw_body)
        _validate_against_contract(
            body,
            CONTRACT_OPERATIONS["startSupportBundle"]["request"],
            "SupportBundleSpec",
        )
        logs = body.get("logs")
        if not isinstance(logs, dict) or not any(logs.values()):
            raise ApiError(
                400,
                "NO_LOGS_SELECTED",
                "At least one log type must be selected for a support bundle.",
            )
        known = {
            domain["name"]: set(domain["clusters"])
            for domain in FIXTURES["inventory"]["domains"]
        }
        for entry in body.get("scope", {}).get("domains", []):
            domain_name = entry.get("domainName")
            if domain_name not in known:
                raise ApiError(
                    400, "UNKNOWN_DOMAIN", f"Domain '{domain_name}' was not found."
                )
            for cluster_name in entry.get("clusterNames", []):
                if cluster_name not in known[domain_name]:
                    raise ApiError(
                        400,
                        "UNKNOWN_CLUSTER",
                        f"Cluster '{cluster_name}' was not found in domain '{domain_name}'.",
                    )
        bundle = FIXTURES["supportBundle"]
        with state.lock:
            if state.bundle_started:
                raise ApiError(
                    409,
                    "OPERATION_IN_PROGRESS",
                    f"Operation is in progress for Id {bundle['id']}. "
                    "Wait for the operation to complete.",
                )
            state.bundle_started = True
        return 202, {
            "id": bundle["id"],
            "status": "IN_PROGRESS",
            "description": bundle["description"],
            "creationTimestamp": bundle["creationTimestamp"],
            "bundleAvailable": "false",
        }

    def _op_getSupportBundleStatus(self, state, split, raw_body, path_params):
        self._reject_unknown_query("getSupportBundleStatus", split)
        bundle = FIXTURES["supportBundle"]
        if path_params["id"] != bundle["id"]:
            raise ApiError(
                400,
                "SUPPORT_BUNDLE_NOT_FOUND",
                f"No support bundle operation with id {path_params['id']}.",
            )
        if not state.bundle_started:
            raise ApiError(
                400,
                "SUPPORT_BUNDLE_NOT_FOUND",
                f"No support bundle operation with id {path_params['id']}.",
            )
        with state.lock:
            state.bundle_polls += 1
            polls = state.bundle_polls
        if polls <= bundle["inProgressPolls"]:
            return 200, {
                "id": bundle["id"],
                "status": "IN_PROGRESS",
                "description": bundle["description"],
                "creationTimestamp": bundle["creationTimestamp"],
                "bundleAvailable": "false",
            }
        return 200, {
            "id": bundle["id"],
            "status": "COMPLETED_WITH_SUCCESS",
            "description": bundle["description"],
            "creationTimestamp": bundle["creationTimestamp"],
            "completionTimestamp": bundle["completionTimestamp"],
            "bundleName": bundle["bundleName"],
            "bundleAvailable": "true",
        }


def build_server(log_path: Path | None = None, port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    httpd.state = MockState()
    httpd.log_path = Path(log_path) if log_path else None
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--log", default=None, help="path to the JSONL request log")
    arguments = parser.parse_args()
    httpd = build_server(arguments.log, arguments.port)
    print(f"PORT={httpd.server_address[1]}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
