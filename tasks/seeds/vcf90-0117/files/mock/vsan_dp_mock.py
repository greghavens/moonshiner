#!/usr/bin/env python3
"""Contract-pinned loopback mock for the focused vSAN DP seed."""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import quote


EXPECTED_OPERATION_IDS = {
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
    "Snapservice.Tasks_get",
}
TASK_ID = "task 17/blue"
SCENARIOS = {
    "lifecycle": ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"],
    "immediate": ["SUCCEEDED"],
    "failed": ["FAILED"],
    # Status values in the pinned contract are case-sensitive.
    "invalid": ["running", "SUCCEEDED"],
    # More polls than common ad-hoc retry limits, followed by success.
    "long": ["RUNNING"] * 129 + ["SUCCEEDED"],
    "empty": [],
}


def compile_path(template: str, names: tuple[str, ...]) -> re.Pattern[str]:
    pattern = re.escape(template)
    for name in names:
        pattern = pattern.replace(re.escape("{" + name + "}"), rf"(?P<{name}>[^/?]+)")
    return re.compile("^" + pattern + "$")


class VsanDpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], contract: dict, log_path: Path, scenario: str
    ):
        operations = contract.get("operations", {})
        if set(operations) != EXPECTED_OPERATION_IDS:
            raise ValueError("contract must name exactly the two focused operationIds")

        base_path = contract["basePath"]
        create = operations["Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"]
        task_get = operations["Snapservice.Tasks_get"]
        self.create_method = create["method"]
        self.task_method = task_get["method"]
        self.create_path = compile_path(base_path + create["path"], ("cluster", "pg"))
        self.task_path = compile_path(base_path + task_get["path"], ("task",))
        self.create_status = int(create["successResponse"]["status"])
        self.task_status = int(task_get["successResponse"]["status"])
        self.log_path = log_path
        self.log_lock = Lock()
        self.scenario = scenario
        self.task_states = SCENARIOS[scenario]
        self.task_polls = 0
        super().__init__(address, VsanDpHandler)

    def append_request(self, record: dict) -> None:
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")


class VsanDpHandler(BaseHTTPRequestHandler):
    server: VsanDpServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def record(self, body: bytes) -> None:
        self.server.append_request(
            {
                "method": self.command,
                "target": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
        )

    def send_json(self, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        body = self.read_body()
        self.record(body)
        if self.server.create_method != "POST" or not self.server.create_path.fullmatch(self.path):
            self.send_json(404, {"error": "operation not served"})
            return
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "invalid JSON"})
            return
        if document != {"name": "on-demand-2026-08-13"}:
            self.send_json(400, {"error": "unexpected create specification"})
            return
        task_id = "   " if self.server.scenario == "empty" else TASK_ID
        self.send_json(self.server.create_status, task_id)

    def do_GET(self) -> None:  # noqa: N802
        body = self.read_body()
        self.record(body)
        match = self.server.task_path.fullmatch(self.path)
        if self.server.task_method != "GET" or match is None:
            self.send_json(404, {"error": "operation not served"})
            return
        if match.group("task") != quote(TASK_ID, safe=""):
            self.send_json(404, {"error": "unknown task"})
            return
        if not self.server.task_states:
            self.send_json(409, {"error": "an empty task identifier must not be polled"})
            return
        state_index = min(self.server.task_polls, len(self.server.task_states) - 1)
        status = self.server.task_states[state_index]
        self.server.task_polls += 1
        result = {"snapshot": "snapshot-9001"} if status == "SUCCEEDED" else None
        response = {
            "description": {
                "id": "com.vmware.snapservice.snapshot.create",
                "default_message": "Create protection-group snapshot",
                "args": [],
            },
            "service": "com.vmware.snapservice",
            "operation": "create-protection-group-snapshot",
            "status": status,
            "cancelable": False,
            "result": result,
        }
        self.send_json(self.server.task_status, response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = VsanDpServer(("127.0.0.1", 0), contract, args.log, args.scenario)
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever()


if __name__ == "__main__":
    main()
