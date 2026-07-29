#!/usr/bin/env python3
"""Contract-pinned loopback vCenter fixture for the protected verifier."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit


EXPECTED_OPERATIONS = {
    "Vcenter.Cluster.EvcMode_checkSet$Task": (
        "POST",
        "/vcenter/cluster/{cluster}/evc-mode?action=check-set&vmw-task=true",
    ),
    "Cis.Tasks_get": ("GET", "/cis/tasks/{task}"),
    "Vcenter.Cluster.EvcMode_set$Task": (
        "PUT",
        "/vcenter/cluster/{cluster}/evc-mode?vmw-task=true",
    ),
}


def load_and_pin_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    actual = {
        operation["operationId"]: (
            operation["method"],
            operation["path_template"],
        )
        for operation in contract["operations"]
    }
    if actual != EXPECTED_OPERATIONS:
        raise ValueError("contract operations do not match the pinned VCF 9.1 fixture")
    if contract.get("server_base_path") != "/api":
        raise ValueError("unexpected vCenter API base path")
    return contract


def valid_set_spec(value: object) -> bool:
    if not isinstance(value, dict) or set(value) - {"evc_mode"}:
        return False
    if "evc_mode" not in value:
        return value == {}
    mode = value["evc_mode"]
    if not isinstance(mode, dict) or set(mode) != {"key", "masks"}:
        return False
    if not isinstance(mode["key"], str) or not mode["key"]:
        return False
    if not isinstance(mode["masks"], list):
        return False
    return all(
        isinstance(mask, dict)
        and set(mask) == {"key", "name", "value"}
        and all(isinstance(mask[field], str) for field in ("key", "name", "value"))
        for mask in mode["masks"]
    )


class State:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.prechecks: dict[str, dict] = {}
        self.mutations: list[dict] = []

    def log(self, entry: dict) -> None:
        encoded = json.dumps(entry, separators=(",", ":"), sort_keys=True)
        with self.lock:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded + "\n")


def localizable(message_id: str, default_message: str) -> dict:
    return {
        "args": [],
        "default_message": default_message,
        "id": message_id,
    }


def task_info(task_id: str, precheck: dict) -> dict:
    cluster = precheck["cluster"]
    if cluster == "domain-c8":
        result = [
            {
                "error": {
                    "data": {},
                    "error_type": "EVC_COMPATIBILITY",
                    "messages": [
                        localizable(
                            "com.example.evc.incompatible",
                            "A host is not compatible with the requested EVC change.",
                        )
                    ],
                },
                "host_system": "host-88",
            }
        ]
    else:
        result = []
    return {
        "cancelable": False,
        "description": localizable(
            "com.vmware.vcenter.cluster.evc_mode.check_set",
            f"Check EVC mode for {cluster}",
        ),
        "operation": "check_set",
        "result": result,
        "service": "com.vmware.vcenter.cluster.evc_mode",
        "status": "SUCCEEDED",
    }


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = "VcfContractFixture/1"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def send_json(self, status: int, value: object) -> None:
            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def read_body(self) -> str:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length).decode("utf-8") if length else ""

        def record(self, body: str) -> None:
            state.log(
                {
                    "api_session_id": self.headers.get("vmware-api-session-id"),
                    "body": body,
                    "content_type": self.headers.get("Content-Type"),
                    "method": self.command,
                    "target": self.path,
                }
            )

        def authenticate(self) -> bool:
            if self.headers.get("vmware-api-session-id") != "session-test-token":
                self.send_json(401, {"error_type": "UNAUTHENTICATED"})
                return False
            return True

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = self.read_body()
            self.record(body)
            if not self.authenticate():
                return
            split = urlsplit(self.path)
            match = re.fullmatch(
                r"/api/vcenter/cluster/([^/]+)/evc-mode", split.path
            )
            query = parse_qsl(split.query, keep_blank_values=True)
            if (
                match is None
                or query != [("action", "check-set"), ("vmw-task", "true")]
            ):
                self.send_json(404, {"error_type": "NOT_FOUND"})
                return
            try:
                spec = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            if not valid_set_spec(spec):
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            cluster = unquote(match.group(1))
            task_id = f"precheck-{cluster}"
            with state.lock:
                state.prechecks[task_id] = {"cluster": cluster, "spec": spec}
            self.send_json(202, task_id)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = self.read_body()
            self.record(body)
            if not self.authenticate():
                return
            split = urlsplit(self.path)
            match = re.fullmatch(r"/api/cis/tasks/([^/]+)", split.path)
            if match is None or split.query:
                self.send_json(404, {"error_type": "NOT_FOUND"})
                return
            task_id = unquote(match.group(1))
            with state.lock:
                precheck = state.prechecks.get(task_id)
            if precheck is None:
                self.send_json(404, {"error_type": "NOT_FOUND"})
                return
            self.send_json(200, task_info(task_id, precheck))

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = self.read_body()
            self.record(body)
            if not self.authenticate():
                return
            split = urlsplit(self.path)
            match = re.fullmatch(
                r"/api/vcenter/cluster/([^/]+)/evc-mode", split.path
            )
            query = parse_qsl(split.query, keep_blank_values=True)
            if match is None or query != [("vmw-task", "true")]:
                self.send_json(404, {"error_type": "NOT_FOUND"})
                return
            try:
                spec = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            if not valid_set_spec(spec):
                self.send_json(400, {"error_type": "INVALID_ARGUMENT"})
                return
            cluster = unquote(match.group(1))
            task_id = f"precheck-{cluster}"
            with state.lock:
                precheck = state.prechecks.get(task_id)
                allowed = (
                    precheck is not None
                    and cluster != "domain-c8"
                    and precheck["spec"] == spec
                )
                if allowed:
                    state.mutations.append({"cluster": cluster, "spec": spec})
            if not allowed:
                self.send_json(409, {"error_type": "PRECHECK_REQUIRED"})
                return
            self.send_json(202, f"mutation-{cluster}")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    arguments = parser.parse_args()

    load_and_pin_contract(arguments.contract)
    arguments.log_file.write_text("", encoding="utf-8")
    state = State(arguments.log_file)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    arguments.port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
