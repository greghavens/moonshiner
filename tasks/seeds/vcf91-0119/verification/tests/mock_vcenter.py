#!/usr/bin/env python3
"""Contract-pinned loopback fixture for the focused vCenter operations."""

from __future__ import annotations

import argparse
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote


def _load_contract(path: Path) -> tuple[str, dict[str, dict]]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = {
        operation["operationId"]: operation
        for operation in contract["operations"]
    }
    expected = {"Vcenter.VM_clone$Task", "Cis.Tasks_get"}
    if set(operations) != expected:
        raise RuntimeError("contract operation set is not the pinned focused set")
    return contract["server"]["api_root_suffix"], operations


class FixtureState:
    def __init__(
        self,
        api_root: str,
        operations: dict[str, dict],
        config: dict,
        log_path: Path,
    ) -> None:
        self.api_root = api_root
        self.operations = operations
        self.config = config
        self.log_path = log_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.polls = 0

        clone = operations["Vcenter.VM_clone$Task"]
        task = operations["Cis.Tasks_get"]
        self.clone_target = api_root + clone["path"]
        task_template = api_root + task["path"]
        before, after = task_template.split("{task}", 1)
        encoded_task = quote(config["task_id"], safe="-._~", encoding="utf-8")
        self.task_target = before + encoded_task + after

    def append_log(
        self,
        handler: BaseHTTPRequestHandler,
        body: bytes,
        operation_id: str | None,
        poll_ordinal: int | None,
    ) -> None:
        with self.lock:
            self.sequence += 1
            event = {
                "seq": self.sequence,
                "method": handler.command,
                "raw_target": handler.path,
                "operation_id": operation_id,
                "poll_ordinal": poll_ordinal,
                "session": handler.headers.get("vmware-api-session-id"),
                "accept": handler.headers.get("Accept"),
                "content_type": handler.headers.get("Content-Type"),
                "body_length": len(body),
                "body_b64": base64.b64encode(body).decode("ascii"),
            }
            encoded = (
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            with self.log_path.open("ab", buffering=0) as stream:
                stream.write(encoded)
                os.fsync(stream.fileno())

    def next_poll(self) -> tuple[int, str]:
        with self.lock:
            self.polls += 1
            ordinal = self.polls
        states = ("PENDING", "RUNNING", "BLOCKED", "SUCCEEDED")
        return ordinal, states[min(ordinal - 1, len(states) - 1)]


def _handler_type(state: FixtureState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            body = self._read_body()
            operation_id = None
            if self.path == state.clone_target:
                operation_id = "Vcenter.VM_clone$Task"
            state.append_log(self, body, operation_id, None)
            if operation_id is None:
                self._json_response(404, {"error": "route not in contract"})
                return
            self._json_response(202, state.config["task_id"])

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            body = self._read_body()
            operation_id = None
            poll_ordinal = None
            status = None
            if self.path == state.task_target:
                operation_id = "Cis.Tasks_get"
                poll_ordinal, status = state.next_poll()
            state.append_log(
                self,
                body,
                operation_id,
                poll_ordinal,
            )
            if operation_id is None:
                self._json_response(404, {"error": "route not in contract"})
                return

            response = {
                "description": {
                    "id": "vcf.clone.progress",
                    "default_message": f"clone poll {poll_ordinal}",
                    "args": [],
                },
                "service": "com.vmware.vcenter.VM",
                "operation": "clone",
                "status": status,
                "cancelable": status != "SUCCEEDED",
            }
            if status == "SUCCEEDED":
                response["result"] = state.config["virtual_machine_id"]
            self._json_response(200, response)

        def _read_body(self) -> bytes:
            raw_length = self.headers.get("Content-Length")
            length = int(raw_length) if raw_length else 0
            return self.rfile.read(length) if length else b""

        def _json_response(self, status: int, value: object) -> None:
            data = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()

    api_root, operations = _load_contract(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_bytes(b"")
    state = FixtureState(api_root, operations, config, args.log)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_type(state),
    )
    server.daemon_threads = True
    print(
        json.dumps({"host": "127.0.0.1", "port": server.server_port}),
        flush=True,
    )
    server.serve_forever(poll_interval=0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
