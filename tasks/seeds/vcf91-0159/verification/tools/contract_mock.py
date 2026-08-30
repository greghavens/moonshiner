#!/usr/bin/env python3
"""Loopback-only mock whose complete route allow-list comes from the contract."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


EXPECTED_CONTRACT_NAMES = {
    "getSupervisorNamespace",
    "applyVksCluster",
}


def compact_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compile_template(template: str) -> re.Pattern[str]:
    cursor = 0
    pieces = ["^"]
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append("([^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    pieces.append("$")
    return re.compile("".join(pieces))


class State:
    def __init__(
        self,
        contract_path: Path,
        config_path: Path,
        log_path: Path,
        state_path: Path,
    ):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations missing")

        self.routes: list[tuple[str, str, re.Pattern[str]]] = []
        names: set[str] = set()
        for operation in operations:
            name = operation.get("contractName")
            method = operation.get("method")
            template = operation.get("pathTemplate")
            if not all(isinstance(item, str) for item in (name, method, template)):
                raise ValueError("invalid contract operation")
            names.add(name)
            self.routes.append((name, method, compile_template(template)))
        if names != EXPECTED_CONTRACT_NAMES:
            raise ValueError("unexpected contract operation set")

        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.log_path = log_path
        self.state_path = state_path
        self.lock = threading.Lock()
        self.patch_attempts = 0
        self.effects = 0
        self.applied: set[str] = set()
        self._write_state()

    def match(self, method: str, path: str) -> tuple[str | None, list[str]]:
        for name, allowed_method, pattern in self.routes:
            match = pattern.fullmatch(path)
            if method == allowed_method and match:
                return name, [
                    urllib.parse.unquote(value, encoding="utf-8", errors="strict")
                    for value in match.groups()
                ]
        return None, []

    def append_log(
        self,
        method: str,
        raw_target: str,
        path: str,
        operation: str | None,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> None:
        entry = {
            "method": method,
            "rawTarget": raw_target,
            "path": path,
            "operation": operation,
            "headers": headers,
            "bodyBase64": base64.b64encode(body).decode("ascii"),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def commit_apply(self, captures: list[str], raw_query: str, body: bytes) -> int:
        fingerprint = hashlib.sha256(
            compact_json(captures)
            + b"\0"
            + raw_query.encode("ascii")
            + b"\0"
            + body
        ).hexdigest()
        with self.lock:
            self.patch_attempts += 1
            if fingerprint not in self.applied:
                self.applied.add(fingerprint)
                self.effects += 1
            self._write_state_locked()
            return self.patch_attempts

    def _write_state(self) -> None:
        with self.lock:
            self._write_state_locked()

    def _write_state_locked(self) -> None:
        payload = json.dumps(
            {
                "effects": self.effects,
                "patchAttempts": self.patch_attempts,
            },
            separators=(",", ":"),
        )
        with self.state_path.open("w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server: "ContractServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle()

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle()

    def _handle(self) -> None:
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else 0
        except ValueError:
            length = 0
        if length < 0 or length > 1_048_576:
            self._respond(413, {"error": "request too large"})
            return

        body = self.rfile.read(length) if length else b""
        split = urllib.parse.urlsplit(self.path)
        operation, captures = self.server.state.match(self.command, split.path)
        headers = [(name, value) for name, value in self.headers.raw_items()]
        self.server.state.append_log(
            self.command,
            self.path,
            split.path,
            operation,
            headers,
            body,
        )

        if operation is None:
            self._respond(404, {"error": "route not in contract"})
            return

        config = self.server.state.config
        scenario = config["scenario"]
        if operation == "getSupervisorNamespace":
            if captures != [config["namespace"]]:
                self._respond(404, {"error": "namespace not found"})
                return
            if scenario == "redirect":
                self.send_response(307)
                self.send_header(
                    "Location",
                    "/api/vcenter/namespaces/instances/v2/redirected",
                )
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            status = "ERROR" if scenario == "namespace_not_ready" else "RUNNING"
            self._respond(
                200,
                {
                    "supervisor": config["supervisor"],
                    "config_status": status,
                    "description": "runtime fixture",
                    "messages": [],
                    "stats": {
                        "cpu_used": config["cpuUsed"],
                        "memory_used": config["memoryUsed"],
                        "storage_used": config["storageUsed"],
                    },
                    "access_list": [],
                    "storage_specs": [],
                },
            )
            return

        if operation == "applyVksCluster":
            if captures != [config["namespace"], config["cluster"]]:
                self._respond(404, {"error": "Cluster not found"})
                return
            attempt = self.server.state.commit_apply(
                captures, split.query, body
            )
            if scenario == "ambiguous" and attempt == 1:
                self._truncate_success_response()
                return
            self._respond(
                200,
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "Cluster",
                    "metadata": {
                        "name": config["cluster"],
                        "namespace": config["namespace"],
                        "uid": config["uid"],
                        "resourceVersion": config["resourceVersion"],
                        "generation": config["generation"],
                    },
                },
            )
            return

        self._respond(500, {"error": "unreachable contract state"})

    def _truncate_success_response(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "1024")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b"{")
        self.wfile.flush()
        self.close_connection = True

    def _respond(self, status: int, value: object) -> None:
        payload = compact_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)


class ContractServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: State):
        super().__init__(address, Handler)
        self.state = state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()

    state = State(args.contract, args.config, args.log, args.state)
    server = ContractServer(("127.0.0.1", 0), state)
    host, port = server.server_address
    with args.ready.open("w", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {"endpoint": f"http://{host}:{port}"},
                separators=(",", ":"),
            )
        )
        stream.flush()
        os.fsync(stream.fileno())
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
