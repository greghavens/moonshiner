#!/usr/bin/env python3
"""Loopback-only mock whose route allow-list is loaded from docs/contract.json."""

from __future__ import annotations

import argparse
import base64
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


EXPECTED_CONTRACT_NAMES = {
    "getSupervisorNamespace",
    "getVksDeployment",
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
        if names != EXPECTED_CONTRACT_NAMES:
            raise ValueError("unexpected contract operation set")

        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.log_path = log_path
        self.lock = threading.Lock()
        self.task_reads = 0

    def match(self, method: str, path: str) -> tuple[str | None, list[str]]:
        for name, allowed_method, pattern in self.routes:
            match = pattern.fullmatch(path)
            if method == allowed_method and match:
                return name, [urllib.parse.unquote(value) for value in match.groups()]
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

    def next_task_status(self) -> str:
        scenario = self.config["scenario"]
        if scenario == "happy":
            sequence = ["PENDING", "RUNNING", "SUCCEEDED"]
        elif scenario == "explicit_false":
            sequence = ["SUCCEEDED"]
        elif scenario == "task_failed":
            sequence = ["RUNNING", "FAILED"]
        elif scenario == "poll_limit":
            sequence = ["RUNNING"]
        else:
            sequence = ["SUCCEEDED"]
        with self.lock:
            index = self.task_reads
            self.task_reads += 1
        return sequence[min(index, len(sequence) - 1)]


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
        headers = [(name, value) for name, value in self.headers.raw_items()]
        self.server.state.append_log(
            self.command,
            self.path,
            split.path,
            operation,
            headers,
            body,
        )

        if operation is None:
            self._respond(404, {"error": "route not in contract"})
            return

        config = self.server.state.config
        if operation == "getSupervisorNamespace":
            if captures != [config["supervisorNamespace"]]:
                self._respond(404, {"error": "namespace not found"})
                return
            status = (
                "ERROR"
                if config["scenario"] == "namespace_not_ready"
                else "RUNNING"
            )
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

        if operation == "getVksDeployment":
            if captures != [config["workloadNamespace"], config["deployment"]]:
                self._respond(404, {"error": "deployment not found"})
                return
            stable = config["scenario"] != "unstable"
            replicas = config["replicas"]
            self._respond(
                200,
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": config["deployment"],
                        "namespace": config["workloadNamespace"],
                        "generation": config["generation"],
                    },
                    "spec": {"replicas": replicas},
                    "status": {
                        "observedGeneration": config["generation"],
                        "availableReplicas": replicas if stable else replicas - 1,
                        "updatedReplicas": replicas,
                        "unavailableReplicas": 0 if stable else 1,
                    },
                },
            )
            return

        if operation == "createSupervisorBackup":
            if captures != [config["supervisor"]]:
                self._respond(404, {"error": "supervisor not found"})
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
            if status == "FAILED":
                response["error"] = {"fixture": "redacted"}
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
