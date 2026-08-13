#!/usr/bin/env python3
"""Contract-derived IPv4-loopback vCenter fixture for protected verification."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATION_IDS = {
    "Vcenter.VM_clone$Task",
    "Cis.Tasks_get",
    "Vcenter.VM_list",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def compile_template(template: str) -> re.Pattern[str]:
    cursor = 0
    pieces: list[str] = ["^"]
    for match in re.finditer(r"\{[^{}]+\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(r"[^/?#]+")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    pieces.append("$")
    return re.compile("".join(pieces))


def derive_routes(contract: dict[str, Any]) -> list[dict[str, Any]]:
    source = contract.get("source")
    if not isinstance(source, dict):
        raise ValueError("contract source is missing")
    if source.get("specPath") != (
        "specifications/vsphere/openapi/automation/vcenter.yaml"
    ):
        raise ValueError("contract is not the vCenter Automation specification")
    commit = source.get("commitSha")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("contract repository commit is not immutable")
    if source.get("basePath") != "/api":
        raise ValueError("contract base path must be /api")

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 3:
        raise ValueError("focused contract must contain exactly three operations")

    routes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("contract operation must be an object")
        operation_id = operation.get("operationId")
        method = operation.get("method")
        path = operation.get("path")
        raw_query = operation.get("rawQuery")
        if not isinstance(operation_id, str) or operation_id in seen:
            raise ValueError("contract operationId is missing or duplicated")
        if method not in {"GET", "POST"}:
            raise ValueError("focused mock permits only contract GET and POST")
        if not isinstance(path, str) or not path.startswith("/api/"):
            raise ValueError("contract operation path must be under /api")
        if not isinstance(raw_query, str):
            raise ValueError("contract operation rawQuery must be a string")
        responses = operation.get("responses")
        if not isinstance(responses, dict):
            raise ValueError("contract operation responses are missing")
        success_status = 202 if operation_id == "Vcenter.VM_clone$Task" else 200
        if str(success_status) not in responses:
            raise ValueError("contract operation lacks its expected success status")
        seen.add(operation_id)
        routes.append(
            {
                "operation_id": operation_id,
                "method": method,
                "path": path,
                "pattern": compile_template(path),
                "raw_query": raw_query,
                "success_status": success_status,
            }
        )
    if seen != EXPECTED_OPERATION_IDS:
        raise ValueError("contract operation allow-list is not the focused workflow")
    return routes


def validate_scenario(scenario: dict[str, Any]) -> None:
    token = scenario.get("session_token")
    clones = scenario.get("clones")
    inventory = scenario.get("inventory")
    if not isinstance(token, str) or not token:
        raise ValueError("scenario session token is missing")
    if not isinstance(clones, list) or not clones:
        raise ValueError("scenario clones must be a nonempty list")
    if not isinstance(inventory, list):
        raise ValueError("scenario inventory must be a list")
    task_ids: set[str] = set()
    for clone in clones:
        if not isinstance(clone, dict):
            raise ValueError("scenario clone must be an object")
        if set(clone) != {"source", "name", "task_id", "polls"}:
            raise ValueError("scenario clone keys are invalid")
        if any(
            not isinstance(clone[key], str) or not clone[key]
            for key in ("source", "name", "task_id")
        ):
            raise ValueError("scenario clone strings must be nonempty")
        if clone["task_id"] in task_ids:
            raise ValueError("scenario task identifiers must be unique")
        task_ids.add(clone["task_id"])
        polls = clone["polls"]
        if not isinstance(polls, list) or not polls:
            raise ValueError("scenario clone polls must be nonempty")
        for poll in polls:
            if not isinstance(poll, dict) or not isinstance(poll.get("status"), str):
                raise ValueError("scenario task poll is invalid")
    for item in inventory:
        if not isinstance(item, dict):
            raise ValueError("scenario inventory item must be an object")


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: list[dict[str, Any]],
        scenario: dict[str, Any],
        log_path: Path,
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.scenario = scenario
        self.log_path = log_path
        self.sequence = 0
        self.clone_index = 0
        self.list_index = 0
        self.task_state: dict[str, dict[str, Any]] = {}
        self.active_task: str | None = None
        self.state_lock = threading.Lock()

    def route_for(self, method: str, path: str) -> dict[str, Any] | None:
        for route in self.routes:
            pattern = route["pattern"]
            if route["method"] == method and pattern.fullmatch(path):
                return route
        return None

    def log_record(self, record: dict[str, Any]) -> None:
        with self.state_lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        route = self.server.route_for(self.command, split.path)
        content_length_text = self.headers.get("Content-Length")
        try:
            content_length = int(content_length_text) if content_length_text else 0
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length) if content_length > 0 else b""

        status = 404
        payload: Any = {"error_type": "NOT_FOUND", "messages": []}
        response_variant: str | None = None
        response_vm_ids: list[str] | None = None

        if route is not None:
            status, payload, response_variant, response_vm_ids = self.handle_route(
                route, split.query, split.path, body
            )

        self.server.log_record(
            {
                "operation_id": route["operation_id"] if route is not None else None,
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": [
                    {"name": name, "value": value}
                    for name, value in self.headers.raw_items()
                ],
                "body_length": len(body),
                "body_base64": base64.b64encode(body).decode("ascii"),
                "response_status": status,
                "response_variant": response_variant,
                "response_vm_ids": response_vm_ids,
            }
        )
        self.send_json(status, payload)

    def handle_route(
        self,
        route: dict[str, Any],
        query: str,
        path: str,
        body: bytes,
    ) -> tuple[int, Any, str | None, list[str] | None]:
        if self.headers.get("vmware-api-session-id") != self.server.scenario[
            "session_token"
        ]:
            return 401, {"error_type": "UNAUTHENTICATED", "messages": []}, None, None
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept.lower():
            return 406, {"error_type": "NOT_ACCEPTABLE", "messages": []}, None, None
        if query != route["raw_query"]:
            return 400, {"error_type": "INVALID_ARGUMENT", "messages": []}, None, None

        operation_id = route["operation_id"]
        if operation_id == "Vcenter.VM_clone$Task":
            return self.handle_clone(body)
        if operation_id == "Cis.Tasks_get":
            return self.handle_task(path)
        if operation_id == "Vcenter.VM_list":
            return self.handle_inventory(body)
        return 404, {"error_type": "NOT_FOUND", "messages": []}, None, None

    def handle_clone(
        self, body: bytes
    ) -> tuple[int, Any, str | None, list[str] | None]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            return 415, {"error_type": "UNSUPPORTED_MEDIA_TYPE", "messages": []}, None, None
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, {"error_type": "INVALID_ARGUMENT", "messages": []}, None, None

        with self.server.state_lock:
            clones = self.server.scenario["clones"]
            if self.server.clone_index >= len(clones):
                return 409, {"error_type": "ALREADY_EXISTS", "messages": []}, None, None
            clone = clones[self.server.clone_index]
            if (
                not isinstance(decoded, dict)
                or list(decoded) != ["source", "name"]
                or decoded.get("source") != clone["source"]
                or decoded.get("name") != clone["name"]
            ):
                return 400, {"error_type": "INVALID_ARGUMENT", "messages": []}, None, None
            self.server.clone_index += 1
            task_id = clone["task_id"]
            self.server.task_state[task_id] = {
                "polls": clone["polls"],
                "index": 0,
                "succeeded": False,
            }
            self.server.active_task = task_id
        return 202, task_id, None, None

    def handle_task(
        self, path: str
    ) -> tuple[int, Any, str | None, list[str] | None]:
        prefix = "/api/cis/tasks/"
        encoded_task = path[len(prefix) :]
        task_id = unquote(encoded_task)
        with self.server.state_lock:
            state = self.server.task_state.get(task_id)
            if state is None:
                return 404, {"error_type": "NOT_FOUND", "messages": []}, None, None
            index = state["index"]
            polls = state["polls"]
            if index >= len(polls):
                return 500, {"error_type": "NO_RESPONSE", "messages": []}, None, None
            payload = polls[index]
            state["index"] = index + 1
            if payload.get("status") == "SUCCEEDED":
                state["succeeded"] = True
        return 200, payload, None, None

    def handle_inventory(
        self, body: bytes
    ) -> tuple[int, Any, str | None, list[str] | None]:
        if body:
            return 400, {"error_type": "INVALID_ARGUMENT", "messages": []}, None, None
        with self.server.state_lock:
            active = self.server.active_task
            state = self.server.task_state.get(active) if active is not None else None
            if state is None or not state["succeeded"]:
                return 409, {"error_type": "NOT_ALLOWED_IN_CURRENT_STATE", "messages": []}, None, None
            configured = list(self.server.scenario["inventory"])
            if self.server.list_index % 2 == 0:
                payload = configured
                variant = "forward"
            else:
                payload = list(reversed(configured))
                variant = "reverse"
            self.server.list_index += 1
        vm_ids = [str(item.get("vm")) for item in payload]
        return 200, payload, variant, vm_ids

    def send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_PATCH = dispatch
    do_DELETE = dispatch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()

    contract = load_json(args.contract)
    scenario = load_json(args.scenario)
    if not isinstance(contract, dict) or not isinstance(scenario, dict):
        raise ValueError("contract and scenario must be JSON objects")
    routes = derive_routes(contract)
    validate_scenario(scenario)

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, scenario, args.log)
    ready = {
        "host": "127.0.0.1",
        "port": server.server_address[1],
        "operation_ids": sorted(route["operation_id"] for route in routes),
    }
    ready_temp = args.ready_file.with_name(
        f".{args.ready_file.name}.{os.getpid()}.tmp"
    )
    with ready_temp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(ready, separators=(",", ":")))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(ready_temp, args.ready_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"mock startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
