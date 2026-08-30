#!/usr/bin/env python3
"""Contract-pinned loopback mock for selected vCenter Automation operations."""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


CLONE = "Vcenter.VM_clone$Task"
TASK_GET = "Cis.Tasks_get"
VM_LIST = "Vcenter.VM_list"
EXPECTED_OPERATIONS = {CLONE, TASK_GET, VM_LIST}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--session-token", required=True)
    return parser.parse_args()


class MockState:
    def __init__(
        self,
        contract: dict[str, Any],
        log_path: Path,
        session_token: str,
    ) -> None:
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations must be an array")
        operation_ids = {item.get("operationId") for item in operations}
        if operation_ids != EXPECTED_OPERATIONS:
            raise ValueError(f"unexpected contract operation set: {operation_ids!r}")

        api = contract.get("api")
        if not isinstance(api, dict) or api.get("server_path") != "/api":
            raise ValueError("unexpected vCenter API server path")
        self.api_prefix = api["server_path"]
        self.session_header = api["security_scheme"]["header"]
        self.session_token = session_token
        self.routes: list[dict[str, Any]] = []
        for operation in operations:
            self.routes.append(
                {
                    "method": operation["method"],
                    "path": operation["request_path"],
                    "operationId": operation["operationId"],
                    "fixed_query": operation.get("fixed_query"),
                }
            )

        self.log_path = log_path
        self.log_path.write_text("", encoding="utf-8")
        self.lock = threading.Lock()
        self.sequence = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.vms: list[dict[str, Any]] = [
            {
                "vm": "vm-300",
                "name": "zulu",
                "power_state": "POWERED_OFF",
                "cpu_count": 4,
                "memory_size_mib": 8192,
            },
            {
                "vm": "vm-100",
                "name": "alpha",
                "power_state": "POWERED_ON",
                "cpu_count": 2,
                "memory_size_mib": 4096,
            },
            {
                "vm": "vm-200",
                "name": "mike",
                "power_state": "SUSPENDED",
                "cpu_count": 1,
                "memory_size_mib": 2048,
            },
        ]
        self.list_reversed = False

    def match(
        self,
        method: str,
        request_path: str,
        query: dict[str, list[str]],
    ) -> tuple[str | None, dict[str, str]]:
        if not request_path.startswith(self.api_prefix + "/"):
            return None, {}
        relative = request_path[len(self.api_prefix) :]
        request_segments = relative.strip("/").split("/")

        for route in self.routes:
            if route["method"] != method:
                continue
            template_segments = route["path"].strip("/").split("/")
            if len(template_segments) != len(request_segments):
                continue
            values: dict[str, str] = {}
            matched = True
            for expected, actual in zip(template_segments, request_segments):
                if expected.startswith("{") and expected.endswith("}"):
                    values[expected[1:-1]] = unquote(actual)
                elif expected != actual:
                    matched = False
                    break
            if not matched:
                continue
            fixed_query = route["fixed_query"]
            if fixed_query is None:
                if query:
                    continue
            elif query != {key: [value] for key, value in fixed_query.items()}:
                continue
            return route["operationId"], values
        return None, {}

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            encoded = json.dumps(
                entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server: "MockServer"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._handle()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle(self) -> None:
        parsed = urlsplit(self.path)
        query = {
            key: values
            for key, values in parse_qs(
                parsed.query, keep_blank_values=True
            ).items()
        }
        operation_id, path_values = self.server.state.match(
            self.command, parsed.path, query
        )
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        entry: dict[str, Any] = {
            "method": self.command,
            "operationId": operation_id,
            "path": parsed.path,
            "query": query,
        }

        if operation_id is None:
            self._respond(404, {"error": "operation not in pinned contract"}, entry)
            return
        if self.headers.get(self.server.state.session_header) != (
            self.server.state.session_token
        ):
            self._respond(401, {"error": "missing or invalid session token"}, entry)
            return
        if self.headers.get("Authorization") is not None:
            self._respond(400, {"error": "Authorization is not in this contract"}, entry)
            return
        if self.headers.get("Accept") != "application/json":
            self._respond(406, {"error": "Accept must be application/json"}, entry)
            return

        if operation_id == CLONE:
            self._clone(raw_body, entry)
        elif operation_id == TASK_GET:
            self._get_task(path_values, raw_body, entry)
        elif operation_id == VM_LIST:
            self._list_vms(raw_body, entry)
        else:
            self._respond(500, {"error": "mock has no behavior for operation"}, entry)

    def _clone(self, raw_body: bytes, entry: dict[str, Any]) -> None:
        if self.headers.get("Content-Type") != "application/json":
            self._respond(415, {"error": "Content-Type must be application/json"}, entry)
            return
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid JSON"}, entry)
            return
        entry["request_json"] = body
        entry["request_body_utf8"] = raw_body.decode("utf-8", errors="replace")
        if (
            not isinstance(body, dict)
            or list(body) != ["source", "name"]
            or not isinstance(body["source"], str)
            or not body["source"].strip()
            or not isinstance(body["name"], str)
            or not body["name"].strip()
        ):
            self._respond(
                400,
                {"error": "CloneSpec must contain only nonblank source and name"},
                entry,
            )
            return

        task_number = len(self.server.state.tasks) + 1
        task_id = f"task {task_number}/blue"
        vm_id = f"clone-{task_number}"
        self.server.state.tasks[task_id] = {
            "polls": 0,
            "vm": {
                "vm": vm_id,
                "name": body["name"],
                "power_state": "POWERED_OFF",
                "cpu_count": 2,
                "memory_size_mib": 4096,
            },
            "materialized": False,
        }
        self._respond(202, task_id, entry)

    def _get_task(
        self,
        path_values: dict[str, str],
        raw_body: bytes,
        entry: dict[str, Any],
    ) -> None:
        if raw_body or self.headers.get("Content-Type") is not None:
            self._respond(400, {"error": "task GET must be bodyless"}, entry)
            return
        task_id = path_values["task"]
        task = self.server.state.tasks.get(task_id)
        if task is None:
            self._respond(404, {"error": "unknown task"}, entry)
            return

        task["polls"] += 1
        if task["polls"] == 1:
            status = "PENDING"
        elif task["polls"] == 2:
            status = "RUNNING"
        else:
            status = "SUCCEEDED"
            if not task["materialized"]:
                self.server.state.vms.append(task["vm"])
                task["materialized"] = True
        entry["task"] = task_id
        entry["poll_number"] = task["polls"]
        entry["returned_status"] = status
        payload: dict[str, Any] = {
            "description": {
                "id": "com.vmware.vcenter.vm.clone",
                "default_message": "Clone virtual machine",
            },
            "service": "com.vmware.vcenter.vm",
            "operation": "clone",
            "status": status,
            "cancelable": True,
        }
        if status == "SUCCEEDED":
            payload["result"] = task["vm"]["vm"]
        self._respond(200, payload, entry)

    def _list_vms(self, raw_body: bytes, entry: dict[str, Any]) -> None:
        if raw_body or self.headers.get("Content-Type") is not None:
            self._respond(400, {"error": "VM list GET must be bodyless"}, entry)
            return
        if any(
            task["polls"] < 3 for task in self.server.state.tasks.values()
        ):
            self._respond(
                409,
                {"error": "clone task has not reached terminal success"},
                entry,
            )
            return

        results = list(self.server.state.vms)
        self.server.state.list_reversed = not self.server.state.list_reversed
        orientation = (
            "REVERSED" if self.server.state.list_reversed else "FORWARD"
        )
        if self.server.state.list_reversed:
            results.reverse()
        entry["list_orientation"] = orientation
        entry["response_order"] = [item["name"] for item in results]
        self._respond(200, results, entry)

    def _respond(
        self,
        status: int,
        payload: Any,
        entry: dict[str, Any],
    ) -> None:
        encoded = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        entry["response_status"] = status
        self.server.state.append_log(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class MockServer(ThreadingHTTPServer):
    def __init__(self, state: MockState) -> None:
        super().__init__(("127.0.0.1", 0), Handler)
        self.state = state


def main() -> int:
    args = parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    state = MockState(contract, args.log, args.session_token)
    server = MockServer(state)

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    args.ready.write_text(base_url, encoding="utf-8")
    server.serve_forever(poll_interval=0.05)
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
