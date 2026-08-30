#!/usr/bin/env python3
"""Contract-bound loopback fixture for the two selected SDDC LCM operations."""

from __future__ import annotations

import argparse
import json
import re
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit


TASK_IDS = tuple(
    f"7f134a9c-1cd8-4f34-9a38-4681b1086{suffix:03x}"
    for suffix in range(0x2FB, 0x306)
)


def task(task_id: str, status: Optional[str] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": task_id}
    if status is not None:
        payload["status"] = status
    return payload


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "name": "happy",
        "accepted": task(TASK_IDS[0], "PENDING"),
        "polls": [
            task(TASK_IDS[0], "PENDING"),
            task(TASK_IDS[0], "RUNNING"),
            task(TASK_IDS[0], "SUCCEEDED"),
        ],
    },
    {
        "name": "optional",
        "accepted": task(TASK_IDS[1], "PENDING"),
        "polls": [task(TASK_IDS[1], "SCHEDULED"), task(TASK_IDS[1], "SUCCEEDED")],
    },
    {
        "name": "accepted-succeeded",
        "accepted": task(TASK_IDS[2], "SUCCEEDED"),
        "polls": [task(TASK_IDS[2], "SUCCEEDED")],
    },
    {
        "name": "failed",
        "accepted": task(TASK_IDS[3], "PENDING"),
        "polls": [task(TASK_IDS[3], "FAILED")],
    },
    {
        "name": "canceled",
        "accepted": task(TASK_IDS[4], "PENDING"),
        "polls": [task(TASK_IDS[4], "CANCELED")],
    },
    {
        "name": "lowercase-status",
        "accepted": task(TASK_IDS[5], "PENDING"),
        "polls": [task(TASK_IDS[5], "succeeded")],
    },
    {
        "name": "missing-status",
        "accepted": task(TASK_IDS[6], "PENDING"),
        "polls": [task(TASK_IDS[6])],
    },
    {
        "name": "mismatched-id",
        "accepted": task(TASK_IDS[7], "PENDING"),
        "polls": [task(TASK_IDS[0], "SUCCEEDED")],
    },
    {
        "name": "invalid-accepted-id",
        "accepted": task("not-a-uuid", "PENDING"),
        "polls": [],
    },
    {
        "name": "empty-optional",
        "accepted": task(TASK_IDS[9], "PENDING"),
        "polls": [task(TASK_IDS[9], "SUCCEEDED")],
    },
    {
        "name": "timeout",
        "accepted": task(TASK_IDS[10], "PENDING"),
        "polls": [task(TASK_IDS[10], "RUNNING"), task(TASK_IDS[10], "SUCCEEDED")],
    },
)


def load_routes(contract_path: Path) -> dict[str, dict[str, Any]]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract.get("operations", [])
    by_id = {item["operationId"]: item for item in operations}
    if set(by_id) != {"setConfig", "getTask"} or len(operations) != 2:
        raise ValueError("mock contract must name exactly setConfig and getTask")

    prefix = contract["source"]["serverBasePath"].rstrip("/")
    routes: dict[str, dict[str, Any]] = {}
    for operation_id, operation in by_id.items():
        route_path = prefix + operation["path"]
        if operation_id == "getTask":
            route_path = route_path.replace(
                "{taskId}", r"(?P<taskId>[0-9a-fA-F-]{36})"
            )
        routes[operation_id] = {
            **operation,
            "pattern": re.compile(rf"^{route_path}$"),
        }
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], routes: dict[str, dict[str, Any]], log_path: Path):
        super().__init__(address, ContractHandler)
        self.routes = routes
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.submission_count = 0
        self.scenario_by_task_id: dict[str, int] = {}
        self.poll_counts: dict[int, int] = {}

    def record(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, separators=(",", ":"), sort_keys=True)
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _record_request(self, body: bytes, scenario_index: Optional[int]) -> None:
        split = urlsplit(self.path)
        try:
            body_json = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            body_json = "__unparseable__"
        self.server.record(
            {
                "method": self.command,
                "path": split.path,
                "query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "bodyUtf8": body.decode("utf-8", errors="replace"),
                "bodyJson": body_json,
                "workflow": scenario_index,
            }
        )

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self) -> None:
        body = self._read_body()
        path = urlsplit(self.path).path

        set_config = self.server.routes["setConfig"]
        if self.command == set_config["method"] and set_config["pattern"].fullmatch(path):
            with self.server.state_lock:
                scenario_index = self.server.submission_count
                self.server.submission_count += 1
                if scenario_index >= len(SCENARIOS):
                    self._record_request(body, None)
                    self._json_response(409, {"message": "unexpected extra submission"})
                    return
                scenario = SCENARIOS[scenario_index]
                accepted_id = scenario["accepted"].get("id")
                if isinstance(accepted_id, str):
                    self.server.scenario_by_task_id[accepted_id.lower()] = scenario_index
                self.server.poll_counts[scenario_index] = 0
            self._record_request(body, scenario_index)
            self._json_response(
                set_config["successResponse"]["status"],
                scenario["accepted"],
            )
            return

        get_task = self.server.routes["getTask"]
        match = get_task["pattern"].fullmatch(path)
        if self.command == get_task["method"] and match:
            requested_id = match.group("taskId").lower()
            with self.server.state_lock:
                scenario_index = self.server.scenario_by_task_id.get(requested_id)
                if scenario_index is None:
                    scenario = None
                else:
                    scenario = SCENARIOS[scenario_index]
                    poll_index = self.server.poll_counts[scenario_index]
                    self.server.poll_counts[scenario_index] += 1
            self._record_request(body, scenario_index)
            if scenario is None:
                self._json_response(404, {"message": "task not found"})
                return
            polls = scenario["polls"]
            if poll_index >= len(polls) and not scenario.get("repeatLastPoll", False):
                self._json_response(409, {"message": "unexpected extra poll"})
                return
            response_index = min(poll_index, len(polls) - 1)
            self._json_response(
                get_task["successResponse"]["status"],
                polls[response_index],
            )
            return

        self._record_request(body, None)
        known_path = any(route["pattern"].fullmatch(path) for route in self.server.routes.values())
        self._json_response(
            405 if known_path else 404,
            {"message": "operation is not served by this contract"},
        )

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), routes, args.log)

    def stop_server(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
