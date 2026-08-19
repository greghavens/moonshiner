#!/usr/bin/env python3
"""Contract-pinned loopback mock for the selected VCF Automation operations."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


CREATED_AT = "2026-08-16T12:00:00Z"
COMPLETED_AT = "2026-08-16T12:01:00Z"
ALL_NONTERMINAL = [
    "CREATED",
    "PENDING",
    "INITIALIZATION",
    "CHECKING_APPROVAL",
    "APPROVAL_PENDING",
    "USER_INTERACTION_PENDING",
    "INPROGRESS",
    "COMPLETION",
]
TERMINAL_FAILURES = ["FAILED", "ABORTED", "APPROVAL_REJECTED"]
INVALID_DELETE_VARIANTS = [
    "missing-id",
    "wrong-id-type",
    "wrong-name-type",
    "missing-status",
    "wrong-status-type",
    "missing-created-at",
    "wrong-created-at-type",
    "wrong-requested-by-type",
    "fractional-completed-tasks",
    "overflow-completed-tasks",
    "missing-total-tasks",
    "wrong-total-tasks-type",
    "missing-deployment",
    "mismatched-deployment",
    "wrong-deployment-type",
    "truncated-json",
    "array-root",
    "invalid-utf8",
]
INVALID_GET_VARIANTS = [
    "missing-id",
    "wrong-id-type",
    "mismatched-request",
    "missing-deployment",
    "mismatched-deployment",
    "wrong-deployment-type",
    "wrong-name-type",
    "missing-status",
    "wrong-status-type",
    "unknown-status",
    "wrong-requested-by-type",
    "fractional-completed-tasks",
    "overflow-completed-tasks",
    "missing-total-tasks",
    "wrong-total-tasks-type",
    "missing-created-at",
    "wrong-created-at-type",
    "truncated-json",
    "array-root",
    "invalid-utf8",
]


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    keys = {operation["operation_key"] for operation in operations}
    required = {"deleteDeployment", "getRequest"}
    if keys != required or len(operations) != 2:
        raise ValueError(f"mock requires exactly {sorted(required)}, got {sorted(keys)}")
    return {
        (operation["method"], operation["path"]): operation
        for operation in operations
    }


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, log_path: Path, mode: str):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.mode = mode
        self.lock = threading.Lock()
        self.sequence = 0
        self.delete_attempts = 0
        self.mutation_count = 0
        self.request_reads = 0
        self.actual_status = "CREATED"
        self.terminal_reached = False
        self.access_token = "vcfa_" + secrets.token_hex(18)
        suffix = " /?#%é😀~" if mode == "all-statuses" else ""
        self.deployment_id = str(uuid.uuid4()) + suffix
        self.request_id = str(uuid.uuid4()) + suffix

    def append_log(self, entry: dict) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            entry["mutation_count_after"] = self.mutation_count
            entry["actual_status_after"] = self.actual_status
            entry["terminal_reached_after"] = self.terminal_reached
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def request_payload(self) -> dict:
        payload = {
            "actionId": "Deployment.Delete",
            "completedTasks": 1 if self.terminal_reached else 0,
            "createdAt": CREATED_AT,
            "deploymentId": self.deployment_id,
            "id": self.request_id,
            "name": "Delete Deployment",
            "requestedBy": "retry-harness",
            "status": self.actual_status,
            "totalTasks": 1,
        }
        if self.terminal_reached and self.mode != "success-no-completed-at":
            payload["completedAt"] = COMPLETED_AT
        return payload

    def invalid_payload(self, variant: str) -> dict | str | list | bytes:
        payload = copy.deepcopy(self.request_payload())
        if variant == "missing-id":
            del payload["id"]
        elif variant == "wrong-id-type":
            payload["id"] = 7
        elif variant == "mismatched-request":
            payload["id"] = "different-request"
        elif variant == "missing-deployment":
            del payload["deploymentId"]
        elif variant == "mismatched-deployment":
            payload["deploymentId"] = "different-deployment"
        elif variant == "wrong-deployment-type":
            payload["deploymentId"] = 7
        elif variant == "wrong-name-type":
            payload["name"] = 7
        elif variant == "missing-status":
            del payload["status"]
        elif variant == "wrong-status-type":
            payload["status"] = 7
        elif variant == "unknown-status":
            payload["status"] = "MYSTERY"
        elif variant == "missing-created-at":
            del payload["createdAt"]
        elif variant == "wrong-created-at-type":
            payload["createdAt"] = False
        elif variant == "wrong-requested-by-type":
            payload["requestedBy"] = False
        elif variant == "fractional-completed-tasks":
            payload["completedTasks"] = 1.0
        elif variant == "overflow-completed-tasks":
            payload["completedTasks"] = 2147483648
        elif variant == "missing-total-tasks":
            del payload["totalTasks"]
        elif variant == "wrong-total-tasks-type":
            payload["totalTasks"] = "1"
        elif variant == "truncated-json":
            return json.dumps(payload, separators=(",", ":"))[:-1]
        elif variant == "array-root":
            return [payload]
        elif variant == "invalid-utf8":
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            return encoded.replace(b"retry-harness", b"retry-\xffharness")
        else:
            raise AssertionError(f"unknown invalid variant {variant}")
        return payload


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_DELETE(self) -> None:  # noqa: N802
        self.dispatch()

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch()

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        operation, path_parameters = self.match_operation(self.command, split.path)
        entry = {
            "operation_key": None if operation is None else operation["operation_key"],
            "method": self.command,
            "target": self.path,
            "path": split.path,
            "query": split.query,
            "headers": self.header_values(),
            "body": body_bytes.decode("utf-8"),
        }

        if operation is None:
            self.respond(entry, 404, {"message": "No operation in pinned contract"})
            return
        if self.headers.get("Authorization") != "Bearer " + self.server.access_token:
            self.respond(entry, 401, {"message": "Unauthorized"})
            return

        key = operation["operation_key"]
        if key == "deleteDeployment":
            if unquote(path_parameters["deploymentId"]) != self.server.deployment_id:
                self.respond(entry, 404, {"message": "Deployment not found"})
                return
            self.handle_delete(entry)
            return

        if key == "getRequest":
            if unquote(path_parameters["requestId"]) != self.server.request_id:
                self.respond(entry, 404, {"message": "Request not found"})
                return
            self.handle_get(entry)
            return

        self.respond(entry, 500, {"message": "Unreachable contract operation"})

    def handle_delete(self, entry: dict) -> None:
        self.server.delete_attempts += 1
        self.server.mutation_count = 1
        self.server.actual_status = "INITIALIZATION" if self.server.mode == "retry" else "CREATED"
        self.server.terminal_reached = False

        if self.server.mode == "retry" and self.server.delete_attempts == 1:
            self.respond(entry, 503, {"message": "Injected ambiguous response after acceptance"})
            return
        if self.server.mode == "non-200" and self.server.delete_attempts == 1:
            self.respond(entry, 201, self.server.request_payload())
            return
        if self.server.mode == "invalid-delete":
            index = self.server.delete_attempts - 1
            variant = INVALID_DELETE_VARIANTS[index]
            self.respond(entry, 200, self.server.invalid_payload(variant))
            return
        self.respond(entry, 200, self.server.request_payload())

    def handle_get(self, entry: dict) -> None:
        self.server.request_reads += 1
        mode = self.server.mode
        if mode == "retry":
            states = ["PENDING", "INPROGRESS", "COMPLETION", "SUCCESSFUL"]
            self.server.actual_status = states[min(self.server.request_reads - 1, 3)]
        elif mode == "all-statuses":
            states = ALL_NONTERMINAL + ["SUCCESSFUL"]
            self.server.actual_status = states[min(self.server.request_reads - 1, 8)]
        elif mode == "success-no-completed-at":
            self.server.actual_status = "SUCCESSFUL"
        elif mode == "sleep-check":
            self.server.actual_status = (
                "PENDING" if self.server.request_reads == 1 else "SUCCESSFUL"
            )
        elif mode == "terminal-failures":
            self.server.actual_status = TERMINAL_FAILURES[self.server.request_reads - 1]
        elif mode == "invalid-get":
            self.server.actual_status = "SUCCESSFUL"
            variant = INVALID_GET_VARIANTS[self.server.request_reads - 1]
            self.server.terminal_reached = True
            self.respond(entry, 200, self.server.invalid_payload(variant))
            return
        elif mode == "non-200":
            self.respond(entry, 202, self.server.request_payload())
            return
        else:
            raise AssertionError(f"unsupported mock mode {mode}")

        self.server.terminal_reached = self.server.actual_status == "SUCCESSFUL"
        self.respond(entry, 200, self.server.request_payload())

    def respond(self, entry: dict, status: int, payload: dict | str | list | bytes) -> None:
        entry["response_status"] = status
        self.server.append_log(entry)
        if isinstance(payload, bytes):
            encoded = payload
        elif isinstance(payload, str):
            encoded = payload.encode("utf-8")
        else:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

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

    def header_values(self) -> dict[str, list[str]]:
        return {
            name.lower(): self.headers.get_all(name)
            for name in self.headers.keys()
        }

    def log_message(self, _format: str, *_args) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=[
            "retry",
            "all-statuses",
            "success-no-completed-at",
            "sleep-check",
            "terminal-failures",
            "invalid-delete",
            "invalid-get",
            "non-200",
        ],
        required=True,
    )
    args = parser.parse_args()

    routes = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, routes, args.log, args.mode)
    args.port_file.write_text(
        json.dumps(
            {
                "port": server.server_port,
                "access_token": server.access_token,
                "deployment_id": server.deployment_id,
                "request_id": server.request_id,
            }
        ),
        encoding="utf-8",
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
