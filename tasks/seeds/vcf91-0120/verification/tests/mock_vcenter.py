#!/usr/bin/env python3
"""Loopback vCenter fixture for the focused operations."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


EXPECTED_OPERATION_IDS = {"Cis.Session_create", "Vcenter.VM_list"}


def load_routes(contract_path: Path) -> dict[tuple[str, str], dict]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    names = {operation["operationId"] for operation in operations}
    if names != EXPECTED_OPERATION_IDS or len(operations) != 2:
        raise ValueError(
            "mock is pinned to exactly "
            f"{sorted(EXPECTED_OPERATION_IDS)}, got {sorted(names)}"
        )
    return {
        (operation["method"], operation["wire_path"]): operation
        for operation in operations
    }


def write_json_fsynced(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, routes, log_path: Path):
        super().__init__(address, handler)
        self.routes = routes
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.sequence = 0
        self.session_creations = 0

        suffix = secrets.token_hex(8)
        self.username = f"inventory-{suffix}@vsphere.local"
        self.password = f'pw\\"snowman-☃-{suffix}'
        self.initial_token = "0123456789abcdef0123456789abcdef"
        self.replacement_token = "fedcba9876543210fedcba9876543210"
        self.datacenter_ids = [
            "datacenter-3",
            "datacenter-404",
            "datacenter encoding+coverage",
        ]

    def append_log(self, entry: dict) -> None:
        with self.log_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def create_session(self) -> str | None:
        with self.state_lock:
            self.session_creations += 1
            if self.session_creations == 1:
                return self.initial_token
            if self.session_creations == 2:
                return self.replacement_token
            return None

    def summaries(self, datacenter_id: str) -> list[dict]:
        if datacenter_id == "datacenter encoding+coverage":
            return [
                {"vm": "vm-9001", "name": "missing-cpu-contract-coverage", "power_state": "POWERED_ON", "memory_size_MiB": 4096},
                {"vm": "vm-9002", "name": "null-memory-contract-coverage", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": None},
            ]
        if datacenter_id != "datacenter-3":
            return []
        return [
            {"vm": "vm-19", "name": "sddcm01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 16384},
            {"vm": "vm-20", "name": "vc01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 21504},
            {"vm": "vm-28", "name": "nsx01a", "power_state": "POWERED_ON", "cpu_count": 6, "memory_size_MiB": 24576},
            {"vm": "vm-33", "name": "vcf-msr01-nxpxf", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 10240},
            {"vm": "vm-34", "name": "vcf-msr01-5ghdn", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 24576},
            {"vm": "vm-35", "name": "vcf-msr01-x6j88", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 24576},
            {"vm": "vm-36", "name": "vcf-msr01-6zpgq", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 24576},
            {"vm": "vm-37", "name": "vcf01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 16384},
            {"vm": "vm-38", "name": "vcf-proxy01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 16384},
            {"vm": "vm-39", "name": "vcf-lic01", "power_state": "POWERED_ON", "cpu_count": 2, "memory_size_MiB": 4096},
            {"vm": "vm-43", "name": "vcf-asr01-szwjz", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 98304},
        ]


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch()

    def do_GET(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self.dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self.dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self.dispatch()

    def dispatch(self) -> None:
        split = urlsplit(self.path)
        body_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            body = body_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body = "<non-utf8>"

        operation = self.server.routes.get((self.command, split.path))
        headers: dict[str, list[str]] = {}
        for key, value in self.headers.raw_items():
            headers.setdefault(key.lower(), []).append(value)
        self.server.append_log(
            {
                "operationId": None if operation is None else operation["operationId"],
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": headers,
                "body": body,
            }
        )

        if operation is None:
            self.send_json(404, {"error_type": "NOT_FOUND"})
            return

        operation_id = operation["operationId"]
        if operation_id == "Cis.Session_create":
            expected = "Basic " + base64.b64encode(
                f"{self.server.username}:{self.server.password}".encode("utf-8")
            ).decode("ascii")
            if self.headers.get("Authorization") != expected:
                self.send_json(401, {"error_type": "UNAUTHENTICATED"})
                return
            token = self.server.create_session()
            if token is None:
                self.send_json(503, {"error_type": "SERVICE_UNAVAILABLE"})
                return
            self.send_json(201, token)
            return

        if operation_id == "Vcenter.VM_list":
            try:
                pairs = parse_qsl(
                    split.query,
                    keep_blank_values=True,
                    strict_parsing=True,
                    encoding="utf-8",
                    errors="strict",
                )
            except (UnicodeDecodeError, ValueError):
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            if len(pairs) != 1 or pairs[0][0] != "datacenters":
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            datacenter_id = pairs[0][1]
            if datacenter_id not in self.server.datacenter_ids:
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            session_id = self.headers.get("vmware-api-session-id")
            if session_id == self.server.initial_token:
                if datacenter_id == self.server.datacenter_ids[0]:
                    self.send_json(200, self.server.summaries(datacenter_id))
                else:
                    self.send_json(
                        401,
                        {
                            "error_type": "UNAUTHENTICATED",
                            "messages": [],
                        },
                    )
                return
            if session_id == self.server.replacement_token:
                self.send_json(200, self.server.summaries(datacenter_id))
                return
            self.send_json(401, {"error_type": "UNAUTHENTICATED"})
            return

        self.send_json(500, {"error_type": "INTERNAL_SERVER_ERROR"})

    def send_json(self, status: int, payload) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
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
    server = ContractServer(("127.0.0.1", 0), Handler, routes, args.log)
    write_json_fsynced(
        args.port_file,
        {
            "port": server.server_address[1],
            "username": server.username,
            "password": server.password,
            "initial_token": server.initial_token,
            "replacement_token": server.replacement_token,
            "datacenter_ids": server.datacenter_ids,
        },
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
