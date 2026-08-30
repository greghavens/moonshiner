#!/usr/bin/env python3
"""Loopback-only mock for the two operations in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


CREATED = "2026-05-13T12:00:00Z"
COMPLETED = "2026-05-13T12:01:00Z"


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    names = {operation["operationId"] for operation in operations}
    required = {"updateBackupConfiguration", "getTask"}
    if names != required or len(operations) != 2:
        raise ValueError(f"mock is pinned to exactly {sorted(required)}, got {sorted(names)}")
    return {
        (operation["method"], operation["path"]): operation
        for operation in operations
    }


def task(status: str, task_id: str) -> dict:
    value = {
        "id": task_id,
        "name": "Update Backup Configuration",
        "status": status,
        "creationTimestamp": CREATED,
    }
    if status == "SUCCESSFUL":
        value["completionTimestamp"] = COMPLETED
    return value


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, log_path: Path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.sequence = 0
        self.task_reads = 0
        self.access_token = "tok_" + secrets.token_hex(16)
        self.task_id = "task backup+" + secrets.token_hex(8)

    def append_log(self, entry: dict) -> None:
        with self.log_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_PATCH(self) -> None:  # noqa: N802
        self.dispatch()

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self.dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self.dispatch()

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = body_bytes.decode("utf-8")
        operation, path_parameters = self.match_operation(self.command, split.path)
        self.server.append_log(
            {
                "operationId": None if operation is None else operation["operationId"],
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
        )

        if operation is None:
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        operation_id = operation["operationId"]
        if operation_id == "updateBackupConfiguration":
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"message": "Malformed JSON"})
                return
            if not isinstance(payload, dict) or not isinstance(payload.get("backupLocations"), list):
                self.send_json(400, {"message": "backupLocations is required by this scenario"})
                return
            self.send_json(202, task("PENDING", self.server.task_id))
            return

        if operation_id == "getTask":
            if unquote(path_parameters["id"]) != self.server.task_id:
                self.send_json(404, {"message": "Task not found"})
                return
            self.server.task_reads += 1
            status = "IN_PROGRESS" if self.server.task_reads == 1 else "SUCCESSFUL"
            self.send_json(200, task(status, self.server.task_id))
            return

        self.send_json(500, {"message": "Unreachable contract operation"})

    def match_operation(self, method: str, path: str):
        for (candidate_method, template), operation in self.server.routes.items():
            if method != candidate_method:
                continue
            names = re.findall(r"\{([^}]+)\}", template)
            pattern = "^" + re.sub(r"\{[^}]+\}", r"([^/]+)", template) + "$"
            match = re.match(pattern, path)
            if match:
                return operation, dict(zip(names, match.groups(), strict=True))
        return None, {}

    def send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, args.log)
    args.port_file.write_text(
        json.dumps(
            {
                "port": server.server_port,
                "access_token": server.access_token,
                "task_id": server.task_id,
            }
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
