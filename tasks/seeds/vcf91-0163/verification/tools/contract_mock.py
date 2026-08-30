#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


def load_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator:
            raise ValueError("invalid fixture property")
        values[key] = value
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
        expected = {
            "listAuthorizedSupervisorNamespaces",
            "listPodWarningEvents",
            "readPreviousPodLog",
        }
        if {route[0] for route in self.routes} != expected:
            raise ValueError("unexpected contract operation set")
        self.log_path = log_path
        self.fixture = load_properties(fixture_path)
        self.log_lock = threading.Lock()

    def match(
        self, method: str, raw_target: str
    ) -> tuple[str | None, list[str]]:
        path = urlsplit(raw_target).path
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
        length_text = self.headers.get("Content-Length")
        length = int(length_text) if length_text is not None else 0
        body = self.rfile.read(length) if length else b""
        operation, captures = self.server.match(self.command, self.path)
        self._append_log(operation, body)

        if operation is None:
            self._json(404, {"error": "route not in contract"})
            return
        if operation == "listAuthorizedSupervisorNamespaces":
            self._list_namespaces(captures)
            return
        if operation == "listPodWarningEvents":
            self._list_events(captures)
            return
        if operation == "readPreviousPodLog":
            self._read_log(captures)
            return
        self._json(500, {"error": "unknown contract route"})

    def _list_namespaces(self, captures: list[str]) -> None:
        if captures:
            self._json(500, {"error": "unexpected route capture"})
            return
        fixture = self.server.fixture
        if fixture["scenario"] == "vcenter_failure":
            self._json(
                503,
                {
                    "message": fixture["serverBodyMarker"],
                    "session": fixture["vcenterSession"],
                },
            )
            return
        values = [
            {
                "namespace": fixture["otherSupervisorNamespace"],
                "master_host": "https://other-supervisor.example.test:6443",
            }
        ]
        if fixture["scenario"] != "unauthorized_namespace":
            values.append(
                {
                    "namespace": fixture["supervisorNamespace"],
                    "master_host": fixture["supervisorMasterHost"],
                }
            )
        self._json(200, values)

    def _list_events(self, captures: list[str]) -> None:
        fixture = self.server.fixture
        if captures != [fixture["workloadNamespace"]]:
            self._json(404, {"error": "workload namespace mismatch"})
            return
        if fixture["scenario"] == "bad_events":
            self._json(
                200,
                {
                    "apiVersion": "v1",
                    "kind": "EventList",
                    "metadata": {"resourceVersion": "7"},
                    "items": [
                        self._event(
                            "BackOff",
                            "wrong Pod identity",
                            "not-an-integer",
                            fixture["otherPodName"],
                        )
                    ],
                },
            )
            return

        items = [
            self._event(
                "Unhealthy",
                "Readiness probe failed for runtime fixture",
                2,
                fixture["podName"],
            )
        ]
        if fixture["scenario"] in {"correlated", "event_only"}:
            items.append(
                self._event(
                    "BackOff",
                    "Back-off restarting failed container "
                    + fixture["containerName"],
                    7,
                    fixture["podName"],
                )
            )
        self._json(
            200,
            {
                "apiVersion": "v1",
                "kind": "EventList",
                "metadata": {"resourceVersion": "19"},
                "items": items,
            },
        )

    def _event(
        self, reason: str, message: str, count: object, pod_name: str
    ) -> dict[str, object]:
        fixture = self.server.fixture
        return {
            "type": "Warning",
            "reason": reason,
            "message": message,
            "count": count,
            "involvedObject": {
                "kind": "Pod",
                "namespace": fixture["workloadNamespace"],
                "name": pod_name,
            },
        }

    def _read_log(self, captures: list[str]) -> None:
        fixture = self.server.fixture
        if captures != [fixture["workloadNamespace"], fixture["podName"]]:
            self._json(404, {"error": "Pod identity mismatch"})
            return
        if fixture["scenario"] == "event_only":
            text = (
                "2026-07-30T12:04:09Z java.net.ConnectException: "
                "Connection refused\n"
            )
        else:
            text = (
                "2026-07-30T12:04:09Z java.net.UnknownHostException: "
                + fixture["upstreamHost"]
                + "\n"
                + "2026-07-30T12:04:09Z"
                + " at java.base/java.net.InetAddress.lookupAllHostAddr\n"
            )
        self._bytes(200, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _append_log(self, operation: str | None, body: bytes) -> None:
        entry = {
            "method": self.command,
            "rawTarget": self.path,
            "operation": operation,
            "headers": [[name, value] for name, value in self.headers.raw_items()],
            "bodyBase64": base64.b64encode(body).decode("ascii"),
        }
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self.server.log_lock:
            with self.server.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._bytes(status, body, "application/json")

    def _bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
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
