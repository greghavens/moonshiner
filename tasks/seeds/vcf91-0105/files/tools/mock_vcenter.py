#!/usr/bin/env python3
"""Contract-pinned loopback fixture for drain-safe vCenter session rotation."""

from __future__ import annotations

import base64
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


OPERATION_IDS = [
    "Cis.Session_create",
    "Vcenter.VM_list",
    "Cis.Session_delete",
]
POWER_STATES = {"POWERED_OFF", "POWERED_ON", "SUSPENDED"}


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_fsynced(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def append_fsynced(path: Path, value: dict, lock: threading.Lock) -> None:
    text = json.dumps(
        value,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"
    with lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


def nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def contract_routes(contract: dict) -> tuple[dict[tuple[str, str], str], list[str]]:
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 3:
        raise ValueError("focused contract must contain exactly three operations")
    if [item.get("operationId") for item in operations] != OPERATION_IDS:
        raise ValueError("focused contract operation order changed")

    expected = [
        ("Cis.Session_create", "POST", "/session", "/api/session", ["basic_auth"]),
        (
            "Vcenter.VM_list",
            "GET",
            "/vcenter/vm",
            "/api/vcenter/vm",
            ["api_key_auth"],
        ),
        ("Cis.Session_delete", "DELETE", "/session", "/api/session", ["api_key_auth"]),
    ]
    routes: dict[tuple[str, str], str] = {}
    for operation, projected in zip(operations, expected):
        operation_id, method, spec_path, path, security = projected
        if (
            operation.get("operationId") != operation_id
            or operation.get("method") != method
            or operation.get("specPathItem") != spec_path
            or operation.get("path") != path
            or operation.get("requestBody") is not None
            or operation.get("security") != security
        ):
            raise ValueError(f"contract projection changed for {operation_id}")
        routes[(method, path)] = operation_id

    filters = operations[1].get("parameters")
    if not isinstance(filters, list):
        raise ValueError("VM list filter projection is missing")
    names = [item.get("name") for item in filters]
    if names != [
        "vms",
        "names",
        "folders",
        "datacenters",
        "hosts",
        "clusters",
        "resource_pools",
        "power_states",
    ]:
        raise ValueError("VM list filter order changed")
    for parameter in filters:
        if (
            parameter.get("in") != "query"
            or parameter.get("required") is not False
            or parameter.get("style") != "form"
            or parameter.get("explode") is not True
            or parameter.get("type") != "array"
            or parameter.get("uniqueItems") is not True
        ):
            raise ValueError("VM list filter wire projection changed")

    if contract.get("securitySchemes") != {
        "basic_auth": {"type": "http", "scheme": "basic"},
        "api_key_auth": {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
    }:
        raise ValueError("focused security projection changed")
    return routes, names


def encode_basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def exploded_target(
    base_path: str,
    filter_names: list[str],
    values: dict,
) -> str:
    pairs: list[str] = []
    for name in filter_names:
        selected = values.get(name)
        if selected is None:
            continue
        if (
            not isinstance(selected, list)
            or not selected
            or any(not isinstance(item, str) or not item for item in selected)
        ):
            raise ValueError(f"slow_filters.{name} must be a nonempty string list")
        for item in selected:
            pairs.append(
                f"{quote(name, safe='-._~')}={quote(item, safe='-._~')}"
            )
    return base_path + ("?" + "&".join(pairs) if pairs else "")


def validate_vm_records(value: object, name: str) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a nonempty list")
    for item in value:
        if (
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(field), str)
                or not item[field].strip()
                for field in ("vm", "name", "power_state")
            )
            or item["power_state"] not in POWER_STATES
        ):
            raise ValueError(f"{name} contains a malformed VM summary")
    return value


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE "
            "SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_object(Path(sys.argv[3]))
    scenario = load_object(Path(sys.argv[4]))
    routes, filter_names = contract_routes(contract)

    username = nonblank(scenario.get("username"), "username")
    old_password = nonblank(scenario.get("old_password"), "old_password")
    new_password = nonblank(scenario.get("new_password"), "new_password")
    old_token = nonblank(scenario.get("old_token"), "old_token")
    new_token = nonblank(scenario.get("new_token"), "new_token")
    error_secret = nonblank(scenario.get("error_secret"), "error_secret")
    release_file = Path(nonblank(scenario.get("release_file"), "release_file"))
    slow_filters = scenario.get("slow_filters")
    if not isinstance(slow_filters, dict):
        raise ValueError("slow_filters must be an object")
    unexpected = set(slow_filters).difference(filter_names)
    if unexpected:
        raise ValueError("slow_filters contains an unknown contract filter")
    slow_target = exploded_target("/api/vcenter/vm", filter_names, slow_filters)
    slow_vms = validate_vm_records(scenario.get("slow_vms"), "slow_vms")
    fast_vms = validate_vm_records(scenario.get("fast_vms"), "fast_vms")

    credentials = {
        encode_basic(username, old_password): ("old", old_token),
        encode_basic(username, new_password): ("new", new_token),
    }
    state = {
        "request_index": 0,
        "old_active": True,
        "new_active": False,
        "slow_arrived": False,
    }
    state_lock = threading.Lock()
    log_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def send_empty(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def send_json(self, status: int, value: object) -> None:
            body = json.dumps(
                value,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def handle_focused(self) -> None:
            split = urlsplit(self.path)
            operation_id = routes.get((self.command, split.path))
            if split.query and operation_id != "Vcenter.VM_list":
                operation_id = None

            lengths = self.headers.get_all("Content-Length", [])
            try:
                length = int(lengths[0]) if len(lengths) == 1 else 0
            except ValueError:
                length = -1
            body = self.rfile.read(length) if 0 < length <= 1024 * 1024 else b""
            authorization = self.headers.get("Authorization")
            session_token = self.headers.get("vmware-api-session-id")

            with state_lock:
                request_index = state["request_index"]
                state["request_index"] += 1
                old_active_at_arrival = state["old_active"]
                slow_arrived_at_arrival = state["slow_arrived"]

            entry = {
                "requestIndex": request_index,
                "operationId": operation_id,
                "method": self.command,
                "rawTarget": self.path,
                "rawPath": split.path,
                "rawQuery": split.query,
                "authorization": authorization,
                "vmwareApiSessionId": session_token,
                "accept": self.headers.get("Accept"),
                "contentType": self.headers.get("Content-Type"),
                "transferEncoding": self.headers.get("Transfer-Encoding"),
                "contentLengthValues": lengths,
                "bodyLength": len(body),
                "bodyHex": body.hex(),
                "oldSessionActiveAtArrival": old_active_at_arrival,
                "slowRequestArrivedAtArrival": slow_arrived_at_arrival,
                "releasePresentAtArrival": release_file.exists(),
                "headerNames": [
                    name.lower() for name, _value in self.headers.items()
                ],
            }

            relevant_common = (
                self.headers.get("Accept") == "application/json"
                and self.headers.get("Content-Type") is None
                and self.headers.get("Transfer-Encoding") is None
                and not body
                and len(self.headers.get_all("Accept", [])) == 1
            )

            if operation_id == "Cis.Session_create":
                generation = credentials.get(authorization)
                entry["credentialGeneration"] = (
                    generation[0] if generation is not None else None
                )
                valid = (
                    relevant_common
                    and session_token is None
                    and len(self.headers.get_all("Authorization", [])) == 1
                    and generation is not None
                )
                if not valid:
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [
                            {
                                "args": [],
                                "default_message": error_secret,
                                "id": "com.vmware.vapi.std.errors.unauthenticated",
                            }
                        ],
                    }
                else:
                    name, token = generation
                    with state_lock:
                        if name == "new":
                            state["new_active"] = True
                    status = 201
                    response = token
                entry["status"] = status
                append_fsynced(log_file, entry, log_lock)
                self.send_json(status, response)
                return

            if operation_id == "Vcenter.VM_list":
                with state_lock:
                    token_active = (
                        session_token == old_token and state["old_active"]
                    ) or (
                        session_token == new_token and state["new_active"]
                    )
                valid = (
                    relevant_common
                    and authorization is None
                    and token_active
                    and len(
                        self.headers.get_all("vmware-api-session-id", [])
                    )
                    == 1
                )
                is_slow = (
                    session_token == old_token and self.path == slow_target
                )
                is_fast = self.path == "/api/vcenter/vm"
                if not valid or not (is_slow or is_fast):
                    status = 400 if token_active else 401
                    response = {
                        "error_type": (
                            "INVALID_ARGUMENT"
                            if token_active
                            else "UNAUTHENTICATED"
                        ),
                        "messages": [],
                    }
                    entry["status"] = status
                    append_fsynced(log_file, entry, log_lock)
                    self.send_json(status, response)
                    return

                entry["status"] = 200
                entry["held"] = is_slow
                if is_slow:
                    with state_lock:
                        state["slow_arrived"] = True
                append_fsynced(log_file, entry, log_lock)
                if is_slow:
                    deadline = time.monotonic() + 10.0
                    while not release_file.exists():
                        if time.monotonic() >= deadline:
                            self.send_json(
                                503,
                                {
                                    "error_type": "SERVICE_UNAVAILABLE",
                                    "messages": [],
                                },
                            )
                            return
                        time.sleep(0.005)
                    self.send_json(200, slow_vms)
                else:
                    self.send_json(200, fast_vms)
                return

            if operation_id == "Cis.Session_delete":
                with state_lock:
                    deleting_old = (
                        session_token == old_token and state["old_active"]
                    )
                    deleting_new = (
                        session_token == new_token and state["new_active"]
                    )
                    old_too_early = deleting_old and not release_file.exists()
                    valid = deleting_old or deleting_new
                    if valid and not old_too_early:
                        if deleting_old:
                            state["old_active"] = False
                        else:
                            state["new_active"] = False
                valid = (
                    valid
                    and relevant_common
                    and authorization is None
                    and len(
                        self.headers.get_all("vmware-api-session-id", [])
                    )
                    == 1
                )
                status = 204 if valid and not old_too_early else 409
                entry["status"] = status
                entry["deletingGeneration"] = (
                    "old" if deleting_old else "new" if deleting_new else None
                )
                entry["oldRetirementTooEarly"] = old_too_early
                append_fsynced(log_file, entry, log_lock)
                if status == 204:
                    self.send_empty(status)
                else:
                    self.send_json(
                        status,
                        {
                            "error_type": "RESOURCE_BUSY",
                            "messages": [],
                        },
                    )
                return

            entry["status"] = 404
            append_fsynced(log_file, entry, log_lock)
            self.send_json(
                404,
                {"error_type": "NOT_FOUND", "messages": []},
            )

        do_GET = handle_focused
        do_POST = handle_focused
        do_DELETE = handle_focused
        do_PUT = handle_focused
        do_PATCH = handle_focused

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    write_fsynced(port_file, f"{server.server_port}\n")

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
