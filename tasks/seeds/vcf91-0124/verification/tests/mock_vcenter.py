#!/usr/bin/env python3
"""Contract-pinned loopback vCenter used only by the protected verifier."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit


EXPECTED_OPERATIONS = (
    "Vcenter.Cluster.EvcMode_checkSet$Task",
    "Cis.Tasks_get",
    "Vcenter.Cluster.EvcMode_set$Task",
)


def compact(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def compile_template(base_path: str, template: str) -> tuple[re.Pattern[str], str]:
    path, separator, query = template.partition("?")
    pieces = re.split(r"(\{[A-Za-z0-9_]+\})", base_path + path)
    expression = ""
    for piece in pieces:
        if piece.startswith("{") and piece.endswith("}"):
            expression += rf"(?P<{piece[1:-1]}>[^/]+)"
        else:
            expression += re.escape(piece)
    return re.compile(rf"^{expression}$"), query if separator else ""


class ContractState:
    def __init__(
        self,
        contract_path: Path,
        log_path: Path,
        session_id: str,
        task_prefix: str,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract["operations"]
        operation_ids = tuple(item["operationId"] for item in operations)
        if operation_ids != EXPECTED_OPERATIONS:
            raise ValueError(
                "contract must name exactly the three EVC gate operations"
            )

        base_path = contract["server_base_path"]
        self.routes: list[dict[str, Any]] = []
        for item in operations:
            path_re, query = compile_template(base_path, item["path_template"])
            self.routes.append(
                {
                    "operationId": item["operationId"],
                    "method": item["method"],
                    "path_re": path_re,
                    "query": query,
                    "success": item["success"]["status"],
                }
            )

        self.log_path = log_path
        self.session_id = session_id
        self.task_prefix = task_prefix
        self.lock = threading.Lock()
        self.precheck_count = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.passed_clusters: set[str] = set()

    def match(self, method: str, raw_target: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        split = urlsplit(raw_target)
        for route in self.routes:
            if route["method"] != method or route["query"] != split.query:
                continue
            matched = route["path_re"].fullmatch(split.path)
            if matched is not None:
                return route, matched.groupdict()
        return None, {}

    def log(
        self,
        handler: BaseHTTPRequestHandler,
        operation_id: str,
        body: bytes,
        status: int,
    ) -> None:
        def values(name: str) -> list[str]:
            return handler.headers.get_all(name) or []

        session_values = values("vmware-api-session-id")
        accept_values = values("Accept")
        content_type_values = values("Content-Type")
        entry = {
            "operationId": operation_id,
            "method": handler.command,
            "rawTarget": handler.path,
            "bodyBase64": b64(body),
            "sessionCount": len(session_values),
            "sessionBase64": b64(
                session_values[0].encode("utf-8") if session_values else b""
            ),
            "acceptCount": len(accept_values),
            "acceptBase64": b64(
                accept_values[0].encode("utf-8") if accept_values else b""
            ),
            "contentTypeCount": len(content_type_values),
            "contentTypeBase64": b64(
                content_type_values[0].encode("utf-8")
                if content_type_values
                else b""
            ),
            "authorizationCount": len(values("Authorization")),
            "status": status,
        }
        encoded = (
            json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        with self.lock:
            with self.log_path.open("ab", buffering=0) as stream:
                stream.write(encoded)
                os.fsync(stream.fileno())

    def begin_precheck(self, cluster: str) -> tuple[int, bytes]:
        with self.lock:
            self.precheck_count += 1
            ordinal = self.precheck_count
            if ordinal == 1:
                kind = "set"
            elif ordinal == 2:
                kind = "clear"
            elif ordinal == 3:
                kind = "reject"
            else:
                return 409, compact({"error_type": "fixture.too_many_checks"})
            task_id = f"{self.task_prefix}/{kind} task?#\u03a9"
            raw_task = quote(task_id, safe="-._~")
            self.tasks[raw_task] = {
                "id": task_id,
                "kind": kind,
                "cluster": cluster,
                "polls": 0,
            }
        return 202, compact(task_id)

    def poll_task(self, raw_task: str) -> tuple[int, bytes]:
        with self.lock:
            task = self.tasks.get(raw_task)
            if task is None:
                return 404, compact({"error_type": "fixture.unknown_task"})
            task["polls"] += 1
            poll = task["polls"]
            kind = task["kind"]
            if kind == "set":
                status = ("PENDING", "RUNNING", "SUCCEEDED")[
                    min(poll, 3) - 1
                ]
            else:
                status = "SUCCEEDED"

            info: dict[str, Any] = {
                "cancelable": False,
                "description": {
                    "id": "fixture.evc.check",
                    "default_message": "fixture EVC precheck",
                    "args": [],
                },
                "operation": "Vcenter.Cluster.EvcMode_checkSet$Task",
                "service": "com.vmware.vcenter.cluster.evc_mode",
                "status": status,
            }
            if status == "SUCCEEDED":
                if kind == "reject":
                    info["result"] = [
                        {
                            "error": {
                                "error_type": "com.vmware.vapi.std.errors.invalid_argument",
                                "messages": [
                                    {
                                        "id": "fixture.evc.unsupported",
                                        "default_message": "host rejects requested EVC mode",
                                        "args": [],
                                    }
                                ],
                            },
                            "host_system": "host-runtime",
                        }
                    ]
                else:
                    info["result"] = []
                    self.passed_clusters.add(task["cluster"])
        return 200, compact(info)

    def mutate(self, cluster: str) -> tuple[int, bytes]:
        with self.lock:
            if cluster not in self.passed_clusters:
                return 409, compact({"error_type": "fixture.precheck_required"})
            task = self.tasks_by_cluster(cluster)
            mutation_id = (
                f"{self.task_prefix}/mutation-{task['kind']} accepted"
            )
        return 202, compact(mutation_id)

    def tasks_by_cluster(self, cluster: str) -> dict[str, Any]:
        for task in self.tasks.values():
            if task["cluster"] == cluster:
                return task
        raise AssertionError("passed cluster has no precheck task")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self.handle_request()

    def do_POST(self) -> None:  # noqa: N802
        self.handle_request()

    def do_PUT(self) -> None:  # noqa: N802
        self.handle_request()

    def handle_request(self) -> None:
        state: ContractState = self.server.state  # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(max(length, 0)) if length else b""
        route, variables = state.match(self.command, self.path)
        if route is None:
            operation_id = ""
            status = 404
            response = compact({"error_type": "fixture.route_not_in_contract"})
        else:
            operation_id = route["operationId"]
            if operation_id == "Vcenter.Cluster.EvcMode_checkSet$Task":
                status, response = state.begin_precheck(variables["cluster"])
            elif operation_id == "Cis.Tasks_get":
                status, response = state.poll_task(variables["task"])
            elif operation_id == "Vcenter.Cluster.EvcMode_set$Task":
                status, response = state.mutate(variables["cluster"])
            else:
                raise AssertionError("route was not pinned by the contract")

        state.log(self, operation_id, body, status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--task-prefix", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_bytes(b"")
    state = ContractState(
        args.contract, args.log, args.session, args.task_prefix
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    with args.port_file.open("r+") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
