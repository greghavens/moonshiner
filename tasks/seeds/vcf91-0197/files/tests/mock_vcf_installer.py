#!/usr/bin/env python3
"""Contract-pinned loopback implementation for the protected verifier."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


TASK_ID = "7d5de5c8-e8d0-4a38-a61d-0eef8917db51"
SCENARIOS = [
    {
        "id": TASK_ID,
        "statuses": ["in progress", "SUCCESSFUL"],
    },
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "statuses": ["COMPLETED_WITH_WARNING"],
    },
    {
        "id": "22222222-2222-4222-8222-222222222222",
        "statuses": ["pending", "Queued", "FAILED"],
    },
    {
        "id": "33333333-3333-4333-8333-333333333333",
        "statuses": ["CANCELLED"],
    },
    {
        "id": "44444444-4444-4444-8444-444444444444",
        "statuses": ["SKIPPED"],
    },
    {
        "id": "55555555-5555-4555-8555-555555555555",
        "statuses": ["TIMED_OUT"],
    },
    {
        "id": "66666666-6666-4666-8666-666666666666",
        "statuses": ["paused"],
    },
    {
        "id": "77777777-7777-4777-8777-777777777777",
        "statuses": ["IN_PROGRESS"],
        "first_poll_delay": 1.15,
    },
]
EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getApplianceInfo": ("GET", "/v1/system/appliance-info"),
    "updateProxyConfiguration": ("PATCH", "/v1/system/proxy-configuration"),
    "getTask": ("GET", "/v1/tasks/{id}"),
}


class State:
    def __init__(self, contract_path: Path, log_path: Path) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            operation_id: (definition["method"], definition["path"])
            for operation_id, definition in self.contract["operations"].items()
        }
        if actual != EXPECTED_OPERATIONS:
            raise ValueError("contract operations do not match the pinned mock")
        if self.contract["operationIds"] != list(EXPECTED_OPERATIONS):
            raise ValueError("contract operationIds do not match the pinned mock")
        self.log_path = log_path
        self.lock = threading.Lock()
        self.patch_count = 0
        self.poll_counts: dict[str, int] = {}

    def append_request(self, record: dict[str, object]) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server_version = "VcfInstallerContractMock/9.1"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _record(self, body: bytes) -> tuple[str, str]:
        split = urlsplit(self.path)
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.state.append_request(
            {
                "method": self.command,
                "path": split.path,
                "query": split.query,
                "headers": headers,
                "body": body.decode("utf-8"),
            }
        )
        return split.path, split.query

    def _json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def _not_found(self) -> None:
        self._json(
            404,
            {
                "errorCode": "VCF_CONTRACT_ROUTE_NOT_FOUND",
                "message": "No operation in the pinned contract matches this request",
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        path, query = self._record(body)
        if path != "/v1/tokens" or query:
            self._not_found()
            return
        self._json(
            201,
            {
                "accessToken": "loopback-access-token",
                "refreshToken": {"id": "loopback-refresh-token"},
            },
        )

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        path, query = self._record(body)
        if path != "/v1/system/proxy-configuration" or query:
            self._not_found()
            return
        with self.state.lock:
            scenario_index = self.state.patch_count
            self.state.patch_count += 1
            if scenario_index >= len(SCENARIOS):
                scenario = None
            else:
                scenario = SCENARIOS[scenario_index]
                self.state.poll_counts[str(scenario["id"])] = 0
        if scenario is None:
            self._json(
                409,
                {
                    "errorCode": "VCF_TOO_MANY_UPDATES",
                    "message": "Verifier scenario exhausted",
                },
            )
            return
        self._json(
            202,
            {
                "id": scenario["id"],
                "name": "Update proxy configuration",
                "status": "PENDING",
                "creationTimestamp": "2026-08-02T12:00:00Z",
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        path, query = self._record(body)
        if query:
            self._not_found()
            return
        if path == "/v1/system/appliance-info":
            self._json(
                200,
                {
                    "role": "VcfInstaller",
                    "version": "9.1.0.0.25380678",
                    "dnsDomain": "example.com",
                },
            )
            return
        match = re.fullmatch(r"/v1/tasks/([^/]+)", path)
        if not match:
            self._not_found()
            return
        task_id = match.group(1)
        scenario = next(
            (item for item in SCENARIOS if item["id"] == task_id),
            None,
        )
        with self.state.lock:
            if scenario is None or task_id not in self.state.poll_counts:
                poll_count = 0
            else:
                self.state.poll_counts[task_id] += 1
                poll_count = self.state.poll_counts[task_id]
        if scenario is None or poll_count == 0:
            self._not_found()
            return
        delay = float(scenario.get("first_poll_delay", 0)) if poll_count == 1 else 0
        if delay:
            time.sleep(delay)
        statuses = scenario["statuses"]
        assert isinstance(statuses, list)
        status = statuses[min(poll_count - 1, len(statuses) - 1)]
        payload: dict[str, object] = {
            "id": task_id,
            "name": "Update proxy configuration",
            "status": status,
            "creationTimestamp": "2026-08-02T12:00:00Z",
        }
        if str(status).upper() not in {"PENDING", "IN_PROGRESS", "QUEUED"}:
            payload["completionTimestamp"] = "2026-08-02T12:00:02Z"
        self._json(200, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    args.log.write_text("", encoding="utf-8")
    state = State(args.contract, args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
