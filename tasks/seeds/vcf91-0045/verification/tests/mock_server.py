#!/usr/bin/env python3
"""Loopback-only mock pinned to the operations in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


CREATED = "2026-07-29T12:00:00Z"
COMPLETED = "2026-07-29T12:01:00Z"


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    expected = {"updateHosts", "getTask", "getHosts"}
    names = {operation["operationId"] for operation in operations}
    if names != expected or len(operations) != len(expected):
        raise ValueError(
            f"mock requires exactly {sorted(expected)}, got {sorted(names)}"
        )
    return {
        (operation["method"], operation["path"]): operation
        for operation in operations
    }


def task_payload(task_id: str, status: str) -> dict:
    payload = {
        "id": task_id,
        "name": "Refresh host records",
        "status": status,
        "creationTimestamp": CREATED,
    }
    if status == "SUCCESSFUL":
        payload["completionTimestamp"] = COMPLETED
    return payload


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, log_path: Path):
        super().__init__(address, handler)
        nonce = secrets.token_hex(8)
        self.routes = routes
        self.log_path = log_path
        self.state_lock = threading.Lock()
        self.sequence = 0
        self.update_count = 0
        self.host_reads = 0
        self.access_token = "bearer_" + secrets.token_hex(18)
        self.selected_host_ids = [
            'host-"alpha"\\' + nonce,
            "host-雪-" + nonce,
        ]
        self.task_ids = [
            "refresh task/+" + nonce + "-one",
            "refresh task/+" + nonce + "-two",
        ]
        self.task_reads = {task_id: 0 for task_id in self.task_ids}
        shared_fqdn = "node-" + nonce + ".a.lab.example"
        hosts = [
            {
                "id": "id-z-" + nonce,
                "fqdn": "zulu-" + nonce + ".lab.example",
                "status": "ASSIGNED",
            },
            {
                "id": "id-b-" + nonce,
                "fqdn": shared_fqdn,
                "status": "UNASSIGNED_USEABLE",
            },
            {
                "id": "id-a-" + nonce,
                "fqdn": shared_fqdn,
                "status": "ASSIGNED",
            },
            {
                "id": "id-m-" + nonce,
                "fqdn": "Beta-" + nonce + ".lab.example",
                "status": "UNASSIGNED_UNUSEABLE",
            },
        ]
        self.hosts = sorted(hosts, key=lambda host: (host["fqdn"], host["id"]))

    def append_log(self, entry: dict) -> None:
        with self.state_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
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
        entry = {
            "operationId": None if operation is None else operation["operationId"],
            "method": self.command,
            "target": self.path,
            "path": split.path,
            "query": split.query,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
        }

        if operation is None:
            entry["responseStatus"] = 404
            self.server.append_log(entry)
            self.send_json(404, {"message": "No operation in pinned contract"})
            return

        operation_id = operation["operationId"]
        if operation_id == "updateHosts":
            self.handle_update(entry, body)
            return
        if operation_id == "getTask":
            self.handle_task(entry, path_parameters)
            return
        if operation_id == "getHosts":
            self.handle_hosts(entry)
            return
        raise AssertionError("unreachable operation")

    def handle_update(self, entry: dict, body: str) -> None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            entry["responseStatus"] = 400
            self.server.append_log(entry)
            self.send_json(400, {"message": "Malformed JSON"})
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("hostIds"), list):
            entry["responseStatus"] = 400
            self.server.append_log(entry)
            self.send_json(400, {"message": "hostIds is required"})
            return
        with self.server.state_lock:
            if self.server.update_count >= len(self.server.task_ids):
                task_id = None
            else:
                task_id = self.server.task_ids[self.server.update_count]
                self.server.update_count += 1
        if task_id is None:
            entry["responseStatus"] = 400
            self.server.append_log(entry)
            self.send_json(400, {"message": "Unexpected extra update"})
            return
        entry["responseStatus"] = 202
        entry["taskStatus"] = "PENDING"
        entry["taskId"] = task_id
        self.server.append_log(entry)
        self.send_json(202, task_payload(task_id, "PENDING"))

    def handle_task(self, entry: dict, path_parameters: dict[str, str]) -> None:
        task_id = unquote(path_parameters["id"])
        with self.server.state_lock:
            if task_id not in self.server.task_reads:
                read_number = None
            else:
                self.server.task_reads[task_id] += 1
                read_number = self.server.task_reads[task_id]
        if read_number is None or read_number > 3:
            entry["responseStatus"] = 404
            self.server.append_log(entry)
            self.send_json(404, {"message": "Task not found"})
            return
        statuses = ["PENDING", "IN_PROGRESS", "SUCCESSFUL"]
        status = statuses[read_number - 1]
        entry["responseStatus"] = 200
        entry["taskStatus"] = status
        entry["taskId"] = task_id
        entry["taskReadNumber"] = read_number
        self.server.append_log(entry)
        self.send_json(200, task_payload(task_id, status))

    def handle_hosts(self, entry: dict) -> None:
        with self.server.state_lock:
            finished = sum(reads >= 3 for reads in self.server.task_reads.values())
            self.server.host_reads += 1
            read_number = self.server.host_reads
        if finished < read_number or read_number > 2:
            entry["responseStatus"] = 409
            self.server.append_log(entry)
            self.send_json(409, {"message": "Task is not terminal"})
            return
        elements = (
            list(reversed(self.server.hosts))
            if read_number % 2 == 1
            else list(self.server.hosts)
        )
        entry["responseStatus"] = 200
        entry["hostReadNumber"] = read_number
        entry["hostResponseOrder"] = [
            {"fqdn": host["fqdn"], "id": host["id"]} for host in elements
        ]
        self.server.append_log(entry)
        self.send_json(
            200,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": 0,
                    "pageSize": len(elements),
                    "totalElements": len(elements),
                    "totalPages": 1,
                },
            },
        )

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
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
                "selected_host_ids": server.selected_host_ids,
                "task_ids": server.task_ids,
                "hosts": server.hosts,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
