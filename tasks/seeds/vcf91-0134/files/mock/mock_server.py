#!/usr/bin/env python3
"""Contract-pinned loopback server for the VCF/VKS acceptance suite."""

from __future__ import annotations

import argparse
import json
import re
import signal
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path_template: str
    pattern: re.Pattern[str]


def compile_operation(raw: dict[str, Any], prefix: str = "") -> Operation:
    template = prefix + raw["pathTemplate"]
    cursor = 0
    parts: list[str] = ["^"]
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9_]*)\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    parts.extend((re.escape(template[cursor:]), "$"))
    return Operation(
        name=raw["name"],
        method=raw["method"],
        path_template=template,
        pattern=re.compile("".join(parts)),
    )


def load_operations(contract_path: Path) -> list[Operation]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = [
        compile_operation(raw) for raw in contract["vsphereOperations"]
    ]
    operations.extend(
        compile_operation(raw, prefix="/supervisor")
        for raw in contract["vksKubernetesOperations"]
    )
    return operations


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        operations: list[Operation],
        request_log: Path,
        scenario: str,
        supervisor_id: str,
    ) -> None:
        super().__init__(address, ContractHandler)
        self.operations = operations
        self.request_log = request_log
        self.scenario = scenario
        self.supervisor_id = supervisor_id
        self.log_lock = threading.Lock()

    @property
    def origin(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def append_log(self, entry: dict[str, Any]) -> None:
        encoded = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        with self.log_lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        matched_operation: Operation | None = None
        match: re.Match[str] | None = None
        for operation in self.server.operations:
            candidate = operation.pattern.fullmatch(split.path)
            if candidate and operation.method == self.command:
                matched_operation = operation
                match = candidate
                break

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        body_text = raw_body.decode("utf-8", errors="strict")
        try:
            body_json = json.loads(body_text) if body_text else None
        except json.JSONDecodeError:
            body_json = "__INVALID_JSON__"

        entry = {
            "operation": matched_operation.name if matched_operation else None,
            "method": self.command,
            "rawPath": self.path,
            "path": split.path,
            "query": split.query,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body_text,
            "json": body_json,
        }
        self.server.append_log(entry)

        if matched_operation is None or match is None:
            self._send_json(404, {"error": "route is not named by contract"})
            return

        parameters = {key: unquote(value) for key, value in match.groupdict().items()}
        if matched_operation.name == "getSupervisorSummary":
            self._supervisor_summary(parameters["supervisor"])
        elif matched_operation.name == "getNamespaceV2":
            self._namespace_info(parameters["namespace"])
        elif matched_operation.name == "createVksCluster":
            self._send_json(201, body_json)
        else:
            self._send_json(500, {"error": "unhandled contract operation"})

    def _supervisor_summary(self, supervisor: str) -> None:
        kubernetes_status = (
            "WARNING"
            if self.server.scenario == "supervisor-not-ready"
            else "READY"
        )
        self._send_json(
            200,
            {
                "name": f"mock-{supervisor}",
                "apiendpoint": self.server.origin + "/supervisor",
                "stats": {},
                "config_status": "RUNNING",
                "kubernetes_status": kubernetes_status,
                "messages": [],
            },
        )

    def _namespace_info(self, namespace: str) -> None:
        config_status = (
            "ERROR" if self.server.scenario == "namespace-error" else "RUNNING"
        )
        self._send_json(
            200,
            {
                "supervisor": self.server.supervisor_id,
                "zones": [],
                "config_status": config_status,
                "messages": [],
                "stats": {},
                "description": f"mock namespace {namespace}",
                "access_list": [],
                "storage_specs": [],
            },
        )

    def _send_json(self, status: int, payload: Any) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=("ready", "supervisor-not-ready", "namespace-error"),
        required=True,
    )
    parser.add_argument("--supervisor-id", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    operations = load_operations(args.contract)
    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        operations,
        args.request_log,
        args.scenario,
        args.supervisor_id,
    )

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    args.ready_file.write_text(
        json.dumps({"baseUri": server.origin}, separators=(",", ":")),
        encoding="utf-8",
    )
    server.serve_forever(poll_interval=0.05)
    server.server_close()


if __name__ == "__main__":
    main()
