#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError("invalid fixture property")
        values[key.strip()] = value.strip()
    return values


def compile_template(template: str) -> re.Pattern[str]:
    parts: list[str] = ["^"]
    cursor = 0
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"([^/]+)")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    parts.append("$")
    return re.compile("".join(parts))


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        contract_path: Path,
        log_path: Path,
        fixture_path: Path,
    ) -> None:
        super().__init__(address, ContractHandler)
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations missing")
        self.routes: list[tuple[str, str, re.Pattern[str]]] = []
        for operation in operations:
            self.routes.append(
                (
                    operation["contractName"],
                    operation["method"],
                    compile_template(operation["pathTemplate"]),
                )
            )
        if {route[0] for route in self.routes} != {
            "getSupervisorNamespace",
            "createVksCluster",
        }:
            raise ValueError("unexpected contract operation set")
        self.log_path = log_path
        self.fixture = load_properties(fixture_path)

    def match(
        self, method: str, raw_path: str
    ) -> tuple[str | None, list[str]]:
        path = urlsplit(raw_path).path
        for name, expected_method, pattern in self.routes:
            match = pattern.fullmatch(path)
            if expected_method == method and match is not None:
                return name, [unquote(item) for item in match.groups()]
        return None, []


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        length_text = self.headers.get("Content-Length")
        length = int(length_text) if length_text is not None else 0
        body = self.rfile.read(length) if length else b""
        operation, captures = self.server.match(self.command, self.path)
        self._append_log(operation, body)

        if operation is None:
            self._respond(404, {"error": "route not in contract"})
            return
        if operation == "getSupervisorNamespace":
            self._get_namespace(captures)
            return
        if operation == "createVksCluster":
            self._create_cluster(captures)
            return
        self._respond(500, {"error": "unknown contract route"})

    def _get_namespace(self, captures: list[str]) -> None:
        fixture = self.server.fixture
        if captures != [fixture["supervisorNamespace"]]:
            self._respond(404, {"error": "fixture namespace mismatch"})
            return
        scenario = fixture["scenario"]
        if scenario == "namespace_not_ready":
            status = "ERROR"
        else:
            status = "RUNNING"
        supervisor = fixture["supervisor"]
        if scenario == "supervisor_mismatch":
            supervisor = fixture["differentSupervisor"]
        if scenario == "malformed_precheck":
            self._respond(
                200,
                {
                    "supervisor": supervisor,
                    "config_status": status,
                    "description": "runtime fixture",
                    "messages": [],
                    "stats": {"cpu_used": 7, "memory_used": 31},
                    "access_list": [],
                    "storage_specs": [],
                },
            )
            return
        self._respond(
            200,
            {
                "supervisor": supervisor,
                "config_status": status,
                "description": "runtime fixture",
                "messages": [],
                "stats": {
                    "cpu_used": 7,
                    "memory_used": 31,
                    "storage_used": 127,
                },
                "access_list": [],
                "storage_specs": [],
            },
        )

    def _create_cluster(self, captures: list[str]) -> None:
        fixture = self.server.fixture
        if captures != [fixture["supervisorNamespace"]]:
            self._respond(404, {"error": "fixture namespace mismatch"})
            return
        scenario = fixture["scenario"]
        if scenario == "create_rejected":
            self._respond(409, {"message": "fixture conflict contains a secret"})
            return
        name = fixture["clusterName"]
        if scenario == "create_bad_identity":
            name = fixture["differentClusterName"]
        self._respond(
            201,
            {
                "apiVersion": "cluster.x-k8s.io/v1beta2",
                "kind": "Cluster",
                "metadata": {
                    "name": name,
                    "namespace": fixture["supervisorNamespace"],
                    "uid": fixture["uid"],
                    "resourceVersion": fixture["resourceVersion"],
                },
                "status": {"phase": fixture["phase"]},
            },
        )

    def _append_log(self, operation: str | None, body: bytes) -> None:
        entry = {
            "method": self.command,
            "rawTarget": self.path,
            "operation": operation,
            "headers": [[name, value] for name, value in self.headers.raw_items()],
            "bodyBase64": base64.b64encode(body).decode("ascii"),
        }
        with self.server.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _respond(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    arguments = parser.parse_args()

    server = ContractServer(
        ("127.0.0.1", 0),
        arguments.contract,
        arguments.log,
        arguments.fixture,
    )
    arguments.port_file.write_text(
        json.dumps({"port": server.server_address[1]}),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
