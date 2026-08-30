#!/usr/bin/env python3
"""Contract-pinned loopback vCenter fixture with expiring sessions."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


OPERATION_IDS = [
    "Cis.Session_create",
    "Vcenter.Datacenter_list",
    "Vcenter.VM_list",
]
FILTERS = {
    "Vcenter.Datacenter_list": ["datacenters", "names", "folders"],
    "Vcenter.VM_list": [
        "vms",
        "names",
        "folders",
        "datacenters",
        "hosts",
        "clusters",
        "resource_pools",
        "power_states",
    ],
}
POWER_STATES = {"POWERED_OFF", "POWERED_ON", "SUSPENDED"}


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value


def string_list(value: object, name: str, length: int | None = None) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
        or (length is not None and len(value) != length)
    ):
        raise ValueError(f"{name} must be a unique nonempty string list")
    return value


def records(value: object, kind: str) -> list[dict]:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError(f"{kind} records must contain at least three items")
    required = (
        ("datacenter", "name")
        if kind == "datacenter"
        else ("vm", "name", "power_state")
    )
    for item in value:
        if (
            not isinstance(item, dict)
            or any(
                not isinstance(item.get(field), str) or not item[field].strip()
                for field in required
            )
        ):
            raise ValueError(f"malformed {kind} record")
        if kind == "vm" and item["power_state"] not in POWER_STATES:
            raise ValueError("malformed VM power state")
    return value


def write_fsynced(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def append_fsynced(path: Path, value: dict, lock: threading.Lock) -> None:
    line = json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n"
    with lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def contract_routes(
    contract: dict,
) -> tuple[dict[tuple[str, str], str], dict[str, list[str]]]:
    operations = contract.get("operations")
    if (
        not isinstance(operations, list)
        or [item.get("operationId") for item in operations] != OPERATION_IDS
    ):
        raise ValueError("focused contract operationIds changed")
    expected = [
        (
            "Cis.Session_create",
            "POST",
            "/session",
            "/api/session",
            ["basic_auth"],
        ),
        (
            "Vcenter.Datacenter_list",
            "GET",
            "/vcenter/datacenter",
            "/api/vcenter/datacenter",
            ["api_key_auth"],
        ),
        (
            "Vcenter.VM_list",
            "GET",
            "/vcenter/vm",
            "/api/vcenter/vm",
            ["api_key_auth"],
        ),
    ]
    routes: dict[tuple[str, str], str] = {}
    projected_filters: dict[str, list[str]] = {}
    for operation, projection in zip(operations, expected):
        operation_id, method, spec_path, path, security = projection
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
        if operation_id in FILTERS:
            parameters = operation.get("parameters")
            if (
                not isinstance(parameters, list)
                or [item.get("name") for item in parameters]
                != FILTERS[operation_id]
            ):
                raise ValueError(f"filter projection changed for {operation_id}")
            for parameter in parameters:
                if (
                    parameter.get("in") != "query"
                    or parameter.get("required") is not False
                    or parameter.get("style") != "form"
                    or parameter.get("explode") is not True
                    or parameter.get("type") != "array"
                    or parameter.get("uniqueItems") is not True
                ):
                    raise ValueError("collection filter wire shape changed")
            projected_filters[operation_id] = [
                item["name"] for item in parameters
            ]
    if contract.get("securitySchemes") != {
        "basic_auth": {"type": "http", "scheme": "basic"},
        "api_key_auth": {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
    }:
        raise ValueError("focused security projection changed")
    return routes, projected_filters


def basic_value(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


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
    routes, filters = contract_routes(contract)

    username = nonblank(scenario.get("username"), "username")
    password = nonblank(scenario.get("password"), "password")
    tokens = string_list(scenario.get("tokens"), "tokens", 3)
    datacenters = records(scenario.get("datacenters"), "datacenter")
    vms = records(scenario.get("vms"), "vm")
    error_secret = nonblank(scenario.get("error_secret"), "error_secret")
    protocol_name = nonblank(scenario.get("protocol_name"), "protocol_name")
    failure_name = nonblank(scenario.get("failure_name"), "failure_name")
    perpetual_401_name = nonblank(
        scenario.get("perpetual_401_name"),
        "perpetual_401_name",
    )
    expected_basic = basic_value(username, password)

    state = {
        "request_index": 0,
        "issued": 0,
        "initial_successes": 0,
        "collection_counts": {
            "Vcenter.Datacenter_list": 0,
            "Vcenter.VM_list": 0,
        },
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
            if split.query and operation_id == "Cis.Session_create":
                operation_id = None

            length_values = self.headers.get_all("Content-Length", [])
            try:
                length = (
                    int(length_values[0])
                    if len(length_values) == 1
                    else 0
                )
            except ValueError:
                length = -1
            body = (
                self.rfile.read(length)
                if 0 < length <= 1024 * 1024
                else b""
            )
            authorization = self.headers.get("Authorization")
            session_token = self.headers.get("vmware-api-session-id")
            accept = self.headers.get("Accept")
            content_type = self.headers.get("Content-Type")
            transfer_encoding = self.headers.get("Transfer-Encoding")

            with state_lock:
                request_index = state["request_index"]
                state["request_index"] += 1

            entry = {
                "requestIndex": request_index,
                "operationId": operation_id,
                "method": self.command,
                "rawTarget": self.path,
                "rawPath": split.path,
                "rawQuery": split.query,
                "authorization": authorization,
                "vmwareApiSessionId": session_token,
                "accept": accept,
                "contentType": content_type,
                "transferEncoding": transfer_encoding,
                "contentLengthValues": length_values,
                "bodyLength": len(body),
                "bodyHex": body.hex(),
                "headerNames": [
                    name.lower() for name, _value in self.headers.items()
                ],
            }

            common_valid = (
                accept == "application/json"
                and content_type is None
                and transfer_encoding is None
                and not body
                and len(self.headers.get_all("Accept", [])) == 1
            )
            status: int
            response: object

            if operation_id is None:
                status = 404
                response = {
                    "error_type": "NOT_FOUND",
                    "messages": [],
                }
            elif operation_id == "Cis.Session_create":
                valid = (
                    common_valid
                    and session_token is None
                    and authorization == expected_basic
                    and len(self.headers.get_all("Authorization", [])) == 1
                )
                with state_lock:
                    issuance = state["issued"]
                    if valid and issuance < len(tokens):
                        state["issued"] += 1
                entry["sessionIssuance"] = issuance
                if not valid:
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [
                            {
                                "id": "mock.bad_credentials",
                                "default_message": error_secret,
                                "args": [],
                            }
                        ],
                    }
                elif issuance >= len(tokens):
                    status = 503
                    response = {
                        "error_type": "SERVICE_UNAVAILABLE",
                        "messages": [
                            {
                                "id": "mock.session_limit",
                                "default_message": error_secret,
                                "args": [],
                            }
                        ],
                    }
                else:
                    status = 201
                    response = tokens[issuance]
            else:
                query_pairs = parse_qsl(
                    split.query,
                    keep_blank_values=True,
                    strict_parsing=False,
                )
                allowed = filters[operation_id]
                query_valid = all(
                    name in allowed and value != ""
                    for name, value in query_pairs
                )
                names = [
                    value for name, value in query_pairs if name == "names"
                ]
                valid = (
                    common_valid
                    and authorization is None
                    and session_token is not None
                    and len(
                        self.headers.get_all(
                            "vmware-api-session-id",
                            [],
                        )
                    )
                    == 1
                    and query_valid
                )
                with state_lock:
                    issued_tokens = set(tokens[: state["issued"]])
                if not valid or session_token not in issued_tokens:
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [
                            {
                                "id": "mock.bad_session",
                                "default_message": error_secret,
                                "args": [],
                            }
                        ],
                    }
                elif perpetual_401_name in names:
                    status = 401
                    response = {
                        "error_type": "UNAUTHENTICATED",
                        "messages": [
                            {
                                "id": "mock.expired_session",
                                "default_message": error_secret,
                                "args": [],
                            }
                        ],
                    }
                elif session_token == tokens[0]:
                    with state_lock:
                        successes = state["initial_successes"]
                        if successes == 0:
                            state["initial_successes"] = 1
                    if successes > 0:
                        status = 401
                        response = {
                            "error_type": "UNAUTHENTICATED",
                            "messages": [
                                {
                                    "id": "mock.expired_session",
                                    "default_message": error_secret,
                                    "args": [],
                                }
                            ],
                        }
                    else:
                        status, response = self.collection_response(
                            operation_id,
                            names,
                        )
                else:
                    status, response = self.collection_response(
                        operation_id,
                        names,
                    )

            if isinstance(response, list):
                key = (
                    "datacenter"
                    if operation_id == "Vcenter.Datacenter_list"
                    else "vm"
                )
                entry["responseIds"] = [item.get(key) for item in response]
            entry["responseStatus"] = status
            append_fsynced(log_file, entry, log_lock)
            self.send_json(status, response)

        def collection_response(
            self,
            operation_id: str,
            names: list[str],
        ) -> tuple[int, object]:
            if operation_id == "Vcenter.Datacenter_list" and protocol_name in names:
                return 200, {"datacenter": "not-an-array"}
            if operation_id == "Vcenter.VM_list" and failure_name in names:
                return 503, {
                    "error_type": "SERVICE_UNAVAILABLE",
                    "messages": [
                        {
                            "id": "mock.inventory_failure",
                            "default_message": error_secret,
                            "args": [],
                        }
                    ],
                }
            source = (
                datacenters
                if operation_id == "Vcenter.Datacenter_list"
                else vms
            )
            with state_lock:
                count = state["collection_counts"][operation_id]
                state["collection_counts"][operation_id] = count + 1
            ordered = source if count % 2 == 0 else list(reversed(source))
            return 200, [dict(item) for item in ordered]

        do_GET = handle_focused
        do_POST = handle_focused

    log_file.parent.mkdir(parents=True, exist_ok=True)
    write_fsynced(log_file, "")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    write_fsynced(port_file, str(server.server_address[1]) + "\n")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
