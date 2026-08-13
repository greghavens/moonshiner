#!/usr/bin/env python3
"""Contract-pinned loopback SDDC LCM service for protected verification.

Routes are derived from the operationIds in docs/contract.json; nothing outside
that focused projection is served. Every received request is appended to a
flushed JSONL log so the protected verifier can assert the exact wire shape.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
EXPECTED_OPERATION_IDS = [
    "getTasks",
    "generateComponentSupportBundle",
    "getTask",
    "getComponentSupportBundles",
]
EXPECTED_ROUTES = [
    ("GET", "/v1/tasks"),
    ("POST", "/v1/components/{componentId}/support-bundles"),
    ("GET", "/v1/tasks/{taskId}"),
    ("GET", "/v1/components/{componentId}/support-bundles"),
]
CORRELATION_HEADER = "X-Correlation-Id"
TASK_STATUS_ENUM = [
    "PENDING",
    "SCHEDULED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELED",
]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    template: str
    pattern: re.Pattern[str]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compile_template(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    for chunk in re.split(r"(\{[A-Za-z0-9_]+\})", template):
        if chunk.startswith("{") and chunk.endswith("}"):
            parts.append(f"(?P<{chunk[1:-1]}>[^/]+)")
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


def load_contract(contract_path: Path) -> tuple[list[Route], dict[str, str]]:
    contract = read_json(contract_path)
    source = contract.get("source", {})
    if source.get("repository") != "vmware/vcf-api-specs":
        raise RuntimeError("contract repository is not pinned")
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
            compile_template(item["path"]),
        )
        for item in operations
    ]
    if [(route.method, route.template) for route in routes] != EXPECTED_ROUTES:
        raise RuntimeError("contract route projection changed")

    filters = operations[0].get("focusedWireProfile", {}).get("filterValues", {})
    if not isinstance(filters, dict) or set(filters) != {"type", "resourceType"}:
        raise RuntimeError("contract task filter projection changed")
    if not all(isinstance(value, str) and value for value in filters.values()):
        raise RuntimeError("contract task filter values are invalid")

    body_profile = operations[1].get("focusedWireProfile", {})
    if body_profile.get("unsetBehavior") != "omit":
        raise RuntimeError("contract unset behavior must be omit")
    if body_profile.get("idempotencyKeyHeader") != CORRELATION_HEADER:
        raise RuntimeError("contract correlation header changed")
    spec = contract.get("schemas", {}).get("ComponentSupportBundleSpec", {})
    if list(spec.get("properties", {})) != ["lookBackWindow"]:
        raise RuntimeError("ComponentSupportBundleSpec projection changed")
    if contract.get("schemas", {}).get("TaskStatus", {}).get("enum") != TASK_STATUS_ENUM:
        raise RuntimeError("TaskStatus projection changed")
    return routes, dict(filters)


def require_text(source: dict[str, Any], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"scenario {name} is invalid")
    return value


class MockState:
    def __init__(
        self,
        routes: list[Route],
        filters: dict[str, str],
        request_log: Path,
        summary_path: Path,
        scenario: dict[str, Any],
    ) -> None:
        self.routes = routes
        self.type_filter = filters["type"]
        self.resource_type_filter = filters["resourceType"]
        self.request_log = request_log
        self.summary_path = summary_path
        self.access_token = require_text(scenario, "accessToken")
        self.component_id = require_text(scenario, "componentId")
        self.other_component_id = require_text(scenario, "otherComponentId")
        self.bundle_marker = require_text(scenario, "bundleMarker")

        tasks = scenario.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 4:
            raise RuntimeError("scenario must seed exactly four decoy tasks")
        self.tasks: list[dict[str, Any]] = [dict(item) for item in tasks]
        self.bundles: list[dict[str, Any]] = []
        self.correlation_index: dict[str, str] = {
            str(item["correlationId"]): str(item["id"]) for item in self.tasks
        }
        if len(self.correlation_index) != len(self.tasks):
            raise RuntimeError("scenario decoy correlation IDs are not unique")

        self.created_bundle_count = 0
        self.duplicate_attempts = 0
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")
        self.write_summary()

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            found = route.pattern.match(path)
            if found and route.method == method:
                return route, found.groupdict()
        return None, {}

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
            self.write_summary()

    def write_summary(self) -> None:
        """Publish the observable service effect after every recorded request."""
        payload = {
            "createdBundleCount": self.created_bundle_count,
            "duplicateAttempts": self.duplicate_attempts,
            "bundleIds": [bundle["id"] for bundle in self.bundles],
            "taskCount": len(self.tasks),
        }
        with self.summary_path.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())

    def create_bundle_task(self, correlation_id: str, look_back_window: int | None) -> dict[str, Any]:
        index = self.created_bundle_count
        self.created_bundle_count = index + 1
        bundle_id = f"sb-{self.bundle_marker}-{index}"
        stamp = f"2026-07-1{index + 1}T09:0{index}:00.000Z"
        self.bundles.append(
            {
                "id": bundle_id,
                "createdTimestamp": stamp,
                "size": 4096 + index,
                "name": f"support-bundle-{self.bundle_marker}-{index}.tgz",
                "url": f"https://vmsp.example.com/bundles/{bundle_id}",
            }
        )
        task_id = f"task-{self.bundle_marker}-{index}"
        task = {
            "id": task_id,
            "name": f"generate-support-bundle-{index}",
            "status": "SUCCEEDED",
            "type": self.type_filter,
            "createdBy": "lcm-service",
            "resourceId": self.component_id,
            "resourceType": self.resource_type_filter,
            "createTime": stamp,
            "correlationId": correlation_id,
            "retriable": False,
            "cancellable": False,
            "additionalDetails": {"supportBundleId": bundle_id},
        }
        if look_back_window is not None:
            task["additionalDetails"]["lookBackWindow"] = look_back_window
        self.tasks.append(task)
        self.correlation_index[correlation_id] = task_id
        return task


SUMMARY_FIELDS = [
    "id",
    "name",
    "status",
    "type",
    "createdBy",
    "resourceId",
    "resourceType",
    "createTime",
    "correlationId",
    "retriable",
    "cancellable",
]


def as_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {key: task[key] for key in SUMMARY_FIELDS if key in task}


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
        route, path_values = self.server.state.match(self.command, target.path)
        try:
            body_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            body_length = 0
        body = self.rfile.read(max(body_length, 0))

        if route is None:
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "operation is outside the focused contract"
            )
        elif self.headers.get("Authorization") != f"Bearer {self.server.state.access_token}":
            status, response = 401, error_body("UNAUTHORIZED", "bearer token is invalid")
        elif route.operation_id == "getTasks":
            status, response = self._get_tasks(target.query, body)
        elif route.operation_id == "generateComponentSupportBundle":
            status, response = self._generate(path_values, target.query, body)
        elif route.operation_id == "getTask":
            status, response = self._get_task(path_values, target.query, body)
        elif route.operation_id == "getComponentSupportBundles":
            status, response = self._get_bundles(path_values, target.query, body)
        else:  # pragma: no cover - defensive
            status, response = 404, error_body(
                "NOT_IN_CONTRACT", "unknown contract operation"
            )

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

    def _reject_correlation_header(self) -> tuple[int, Any] | None:
        if self.headers.get(CORRELATION_HEADER) is not None:
            return 400, error_body(
                "WIRE_SHAPE", "operation declares no correlation header parameter"
            )
        return None

    def _get_tasks(self, raw_query: str, body: bytes) -> tuple[int, Any]:
        state = self.server.state
        rejected = self._reject_correlation_header()
        if rejected:
            return rejected
        if body:
            return 400, error_body("WIRE_SHAPE", "getTasks must be bodyless")
        try:
            query = parse_qs(raw_query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            return 400, error_body("WIRE_SHAPE", "query is malformed")
        if any(len(values) != 1 or values[0] == "" for values in query.values()):
            return 400, error_body("WIRE_SHAPE", "query values must be singular")

        base_keys = {"type", "resourceId", "resourceType", "pageSize"}
        if set(query) not in (base_keys, base_keys | {"pageNumber"}):
            return 400, error_body("WIRE_SHAPE", "unexpected or unset query member")
        if query["type"][0] != state.type_filter:
            return 400, error_body("WIRE_SHAPE", "task type filter changed")
        if query["resourceType"][0] != state.resource_type_filter:
            return 400, error_body("WIRE_SHAPE", "resource type filter changed")
        resource_id = query["resourceId"][0]
        try:
            page_size = int(query["pageSize"][0])
            page_number = int(query["pageNumber"][0]) if "pageNumber" in query else 0
        except ValueError:
            return 400, error_body("WIRE_SHAPE", "page members must be integers")
        if not 1 <= page_size <= 50:
            return 400, error_body("PAGE_RANGE", "pageSize must be 1..50")
        if page_number < 0:
            return 400, error_body("PAGE_RANGE", "pageNumber must be nonnegative")

        prefix = (
            f"type={state.type_filter}"
            f"&resourceId={resource_id}"
            f"&resourceType={state.resource_type_filter}"
        )
        expected = (
            f"{prefix}&pageSize={page_size}"
            if "pageNumber" not in query
            else f"{prefix}&pageNumber={page_number}&pageSize={page_size}"
        )
        if raw_query != expected:
            return 400, error_body("WIRE_SHAPE", "query order or encoding changed")

        matched = [
            task
            for task in state.tasks
            if task.get("resourceId") == resource_id
            and task.get("resourceType") == state.resource_type_filter
            and task.get("type") == state.type_filter
        ]
        total_pages = max(1, math.ceil(len(matched) / page_size))
        if page_number >= total_pages:
            return 400, error_body("PAGE_RANGE", "page is out of range")
        start = page_number * page_size
        return 200, {
            "elements": [as_summary(task) for task in matched[start : start + page_size]],
            "pageMetadata": {
                "pageNumber": page_number,
                "pageSize": page_size,
                "totalElements": len(matched),
                "totalPages": total_pages,
            },
        }

    def _generate(
        self, path_values: dict[str, str], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        state = self.server.state
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "generate must omit its query")
        component_id = path_values.get("componentId", "")
        if component_id not in (state.component_id, state.other_component_id):
            return 404, error_body("NOT_FOUND", "component is unknown")
        media_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if media_type.strip().casefold() != "application/json":
            return 415, error_body("MEDIA_TYPE", "generate must be JSON")
        correlation_values = self.headers.get_all(CORRELATION_HEADER) or []
        if len(correlation_values) != 1 or not correlation_values[0].strip():
            return 400, error_body(
                "WIRE_SHAPE", "exactly one nonblank correlation header is required"
            )
        correlation_id = correlation_values[0]

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400, error_body("WIRE_SHAPE", "body is not JSON")
        if not isinstance(payload, dict):
            return 400, error_body("WIRE_SHAPE", "body must be a JSON object")
        if set(payload) - {"lookBackWindow"}:
            return 400, error_body("WIRE_SHAPE", "body member is outside the contract")
        look_back_window = payload.get("lookBackWindow")
        if "lookBackWindow" in payload and (
            isinstance(look_back_window, bool)
            or not isinstance(look_back_window, int)
            or look_back_window < 1
        ):
            return 400, error_body(
                "WIRE_SHAPE", "an unset optional member must be omitted, never blank"
            )

        if correlation_id in state.correlation_index:
            state.duplicate_attempts += 1
            return 409, error_body(
                "DUPLICATE_REQUEST", "this correlation ID already has a task"
            )
        task = state.create_bundle_task(correlation_id, look_back_window)
        accepted = {key: value for key, value in task.items() if key != "additionalDetails"}
        accepted["status"] = "PENDING"
        return 202, accepted

    def _get_task(
        self, path_values: dict[str, str], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        state = self.server.state
        rejected = self._reject_correlation_header()
        if rejected:
            return rejected
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "getTask must omit its query")
        if body:
            return 400, error_body("WIRE_SHAPE", "getTask must be bodyless")
        task_id = path_values.get("taskId", "")
        found = next((task for task in state.tasks if task["id"] == task_id), None)
        if found is None:
            return 404, error_body("NOT_FOUND", "task is unknown")
        return 200, dict(found)

    def _get_bundles(
        self, path_values: dict[str, str], raw_query: str, body: bytes
    ) -> tuple[int, Any]:
        state = self.server.state
        rejected = self._reject_correlation_header()
        if rejected:
            return rejected
        if raw_query:
            return 400, error_body("WIRE_SHAPE", "listing must omit its query")
        if body:
            return 400, error_body("WIRE_SHAPE", "listing must be bodyless")
        component_id = path_values.get("componentId", "")
        if component_id != state.component_id:
            return 404, error_body("NOT_FOUND", "component is unknown")
        return 200, [dict(bundle) for bundle in state.bundles]

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
    return {"errorCode": code, "message": message}


def write_port(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(str(port))
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        raise SystemExit(
            "usage: mock_server.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE "
            "SUMMARY_FILE"
        )
    port_file, log_file, contract_file, scenario_file, summary_file = map(Path, argv[1:])
    routes, filters = load_contract(contract_file)
    state = MockState(routes, filters, log_file, summary_file, read_json(scenario_file))
    server = ContractServer(("127.0.0.1", 0), state)
    write_port(port_file, int(server.server_address[1]))
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
