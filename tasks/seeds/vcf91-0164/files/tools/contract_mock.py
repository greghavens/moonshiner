#!/usr/bin/env python3
"""Loopback-only mock whose route allow-list is loaded from contract.json."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


EXPECTED_CONTRACT_NAMES = {
    "getSupervisorNamespace",
    "listVksClusters",
    "createSupervisorBackup",
    "getTask",
}


def compact_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compile_template(template: str) -> re.Pattern[str]:
    cursor = 0
    pieces = ["^"]
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append("([^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    pieces.append("$")
    return re.compile("".join(pieces))


class State:
    def __init__(self, contract_path: Path, config_path: Path, log_path: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations missing")

        self.routes: list[tuple[str, str, re.Pattern[str]]] = []
        names: set[str] = set()
        for operation in operations:
            name = operation.get("contractName")
            method = operation.get("method")
            template = operation.get("pathTemplate")
            if not all(isinstance(item, str) for item in (name, method, template)):
                raise ValueError("invalid contract operation")
            names.add(name)
            self.routes.append((name, method, compile_template(template)))
        if names != EXPECTED_CONTRACT_NAMES or len(self.routes) != 4:
            raise ValueError("unexpected contract operation set")

        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.log_path = log_path
        self.lock = threading.Lock()
        self.task_reads = 0
        self.list_reads = 0

    def match(self, method: str, path: str) -> tuple[str | None, list[str]]:
        for name, allowed_method, pattern in self.routes:
            match = pattern.fullmatch(path)
            if method == allowed_method and match:
                return name, [
                    urllib.parse.unquote(value, encoding="utf-8", errors="strict")
                    for value in match.groups()
                ]
        return None, []

    def append_log(
        self,
        method: str,
        raw_target: str,
        path: str,
        operation: str | None,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> None:
        entry = {
            "method": method,
            "rawTarget": raw_target,
            "path": path,
            "operation": operation,
            "headers": headers,
            "bodyBase64": base64.b64encode(body).decode("ascii"),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def next_task_status(self) -> str:
        scenario = self.config["scenario"]
        sequences = {
            "happy": ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
            "empty_comment": ["SUCCEEDED"],
            "task_failed": ["RUNNING", "FAILED"],
            "poll_timeout": ["RUNNING"],
            "inventory_changed": ["SUCCEEDED"],
            "api_error": ["SUCCEEDED"],
            "malformed_task": ["MYSTERY"],
            "result_value": ["SUCCEEDED"],
        }
        sequence = sequences.get(scenario, ["SUCCEEDED"])
        with self.lock:
            index = self.task_reads
            self.task_reads += 1
        return sequence[min(index, len(sequence) - 1)]

    def next_cluster_items(self) -> tuple[int, list[dict[str, object]]]:
        with self.lock:
            read_number = self.list_reads
            self.list_reads += 1

        items = []
        for cluster in self.config["clusters"]:
            version = cluster["version"]
            if (
                self.config["scenario"] == "inventory_changed"
                and read_number >= 1
                and cluster["name"] == self.config["clusters"][1]["name"]
            ):
                version = version + "-changed"
            items.append(
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "Cluster",
                    "metadata": {
                        "name": cluster["name"],
                        "namespace": self.config["namespace"],
                    },
                    "spec": {"topology": {"version": version}},
                }
            )
        orientation = read_number + (1 if self.config["initialReverse"] else 0)
        if orientation % 2 == 1:
            items.reverse()
        return read_number, items


class Handler(BaseHTTPRequestHandler):
    server: "ContractServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else 0
        except ValueError:
            length = 0
        if length < 0 or length > 1_048_576:
            self._respond(413, {"error": "request too large"})
            return
        body = self.rfile.read(length) if length else b""
        split = urllib.parse.urlsplit(self.path)
        operation, captures = self.server.state.match(self.command, split.path)
        self.server.state.append_log(
            self.command,
            self.path,
            split.path,
            operation,
            list(self.headers.raw_items()),
            body,
        )

        if operation is None:
            self._respond(404, {"error": "route not in contract"})
            return

        config = self.server.state.config
        scenario = config["scenario"]

        if operation == "getSupervisorNamespace":
            if captures != [config["namespace"]]:
                self._respond(404, {"error": "namespace not found"})
                return
            status = "ERROR" if scenario == "namespace_not_ready" else "RUNNING"
            self._respond(
                200,
                {
                    "supervisor": config["supervisor"],
                    "config_status": status,
                    "description": "runtime fixture",
                    "messages": [],
                    "stats": {
                        "cpu_used": config["cpuUsed"],
                        "memory_used": config["memoryUsed"],
                        "storage_used": config["storageUsed"],
                    },
                    "access_list": [],
                    "storage_specs": [],
                },
            )
            return

        if operation == "listVksClusters":
            if captures != [config["namespace"]]:
                self._respond(404, {"error": "namespace not found"})
                return
            read_number, items = self.server.state.next_cluster_items()
            if scenario == "malformed_cluster" and read_number == 0:
                items[1]["metadata"]["name"] = items[0]["metadata"]["name"]
            self._respond(
                200,
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "ClusterList",
                    "metadata": {"resourceVersion": str(700 + read_number)},
                    "items": items,
                },
            )
            return

        if operation == "createSupervisorBackup":
            if captures != [config["supervisor"]]:
                self._respond(404, {"error": "supervisor not found"})
                return
            if scenario == "api_error":
                self._respond(
                    503,
                    {
                        "error": config["session"]
                        + " "
                        + config["token"]
                        + " runtime-secret-body"
                    },
                )
                return
            self._respond(200, config["taskId"])
            return

        if operation == "getTask":
            if captures != [config["taskId"]]:
                self._respond(404, {"error": "task not found"})
                return
            status = self.server.state.next_task_status()
            response = {
                "description": {
                    "id": "runtime.backup",
                    "default_message": "Supervisor backup",
                    "args": [],
                },
                "service": "com.vmware.vcenter.namespace_management",
                "operation": "backup",
                "status": status,
                "cancelable": False,
            }
            if scenario == "result_value":
                response["result"] = {
                    "ticket": config["resultMarker"],
                    "parts": ["metadata", 2],
                }
            if status == "FAILED":
                response["error"] = {
                    "secret": config["session"] + config["token"]
                }
            self._respond(200, response)
            return

        self._respond(500, {"error": "unreachable contract state"})

    def _respond(self, status: int, value: object) -> None:
        payload = compact_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


class ContractServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def __init__(self, address: tuple[str, int], state: State):
        super().__init__(address, Handler)
        self.state = state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()

    state = State(args.contract, args.config, args.log)
    server = ContractServer(("127.0.0.1", 0), state)
    host, port = server.server_address
    args.ready.write_text(
        json.dumps({"endpoint": f"http://{host}:{port}"}, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
