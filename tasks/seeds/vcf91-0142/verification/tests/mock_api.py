"""Contract-derived loopback service for the coordinated-change exercise."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_CONTRACT_NAMES = {
    "getSupervisorNamespace",
    "getVksCluster",
    "updateSupervisorNamespace",
    "patchVksClusterVersion",
}


def _route_pattern(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


class ContractMockServer(ThreadingHTTPServer):
    """A stateful fixture whose route allow-list comes from the contract."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        contract_path: Path,
        request_log: Path,
        namespace: str,
        supervisor: str,
        cluster_name: str,
        cluster_class: str,
        old_description: str,
        old_version: str,
        failure_marker: str,
        response_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations are missing")
        by_name = {item.get("contractName"): item for item in operations}
        if set(by_name) != EXPECTED_CONTRACT_NAMES:
            raise ValueError("contract operation allow-list is not exact")
        if len(by_name) != len(operations):
            raise ValueError("contract operation names are not unique")

        expected_identity = {
            "getSupervisorNamespace": (
                "GET",
                "Vcenter.Namespaces.Instances_getV2",
                None,
            ),
            "getVksCluster": (
                "GET",
                None,
                "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
            ),
            "updateSupervisorNamespace": (
                "PATCH",
                "Vcenter.Namespaces.Instances_update",
                None,
            ),
            "patchVksClusterVersion": (
                "PATCH",
                None,
                "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch",
            ),
        }
        self.routes: list[dict[str, Any]] = []
        for name, operation in by_name.items():
            method, operation_id, operation_key = expected_identity[name]
            if (
                operation.get("method") != method
                or operation.get("operationId") != operation_id
                or operation.get("operationKey") != operation_key
            ):
                raise ValueError(f"contract identity changed for {name}")
            self.routes.append(
                {
                    "name": name,
                    "method": method,
                    "pattern": _route_pattern(operation["pathTemplate"]),
                    "operation": operation,
                }
            )

        self.request_log = request_log
        self.namespace = namespace
        self.supervisor = supervisor
        self.cluster_name = cluster_name
        self.cluster_class = cluster_class
        self.namespace_description = old_description
        self.cluster_version = old_version
        self.failure_marker = failure_marker
        self.response_overrides = copy.deepcopy(response_overrides or {})
        if not set(self.response_overrides) <= EXPECTED_CONTRACT_NAMES:
            raise ValueError("response override names are not in the contract")
        self.namespace_update_count = 0
        self.cluster_patch_attempts = 0
        self.sequence = 0
        self.state_lock = threading.Lock()
        super().__init__(address, ContractRequestHandler)

    @property
    def root_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def match_route(
        self,
        method: str,
        path: str,
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        for route in self.routes:
            if route["method"] != method:
                continue
            match = route["pattern"].fullmatch(path)
            if match is not None:
                captures = {
                    key: unquote(value, encoding="utf-8", errors="strict")
                    for key, value in match.groupdict().items()
                }
                return route, captures
        return None, {}

    def append_log(self, record: dict[str, Any]) -> None:
        with self.state_lock:
            self.sequence += 1
            record["sequence"] = self.sequence
        encoded = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            self.request_log,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def snapshot(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "namespace_description": self.namespace_description,
                "cluster_version": self.cluster_version,
                "namespace_update_count": self.namespace_update_count,
                "cluster_patch_attempts": self.cluster_patch_attempts,
            }


class ContractRequestHandler(BaseHTTPRequestHandler):
    """Serve no operation outside the four names in the focused contract."""

    protocol_version = "HTTP/1.1"

    @property
    def contract_server(self) -> ContractMockServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            return b""
        return self.rfile.read(max(0, length))

    def _record(
        self,
        body: bytes,
        operation: str | None,
    ) -> None:
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError:
            body_text = "<non-utf8>"
        split = urlsplit(self.path)
        self.contract_server.append_log(
            {
                "method": self.command,
                "raw_target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": [
                    [key.lower(), value]
                    for key, value in self.headers.raw_items()
                ],
                "body_utf8": body_text,
                "body_length": len(body),
                "operation": operation,
            }
        )

    def _send_bytes(
        self,
        status: int,
        data: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
        self.close_connection = True

    def _send_json(
        self,
        status: int,
        value: object,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self._send_bytes(status, data, headers=headers)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _drop_connection(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    @staticmethod
    def _json_object(body: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _dispatch(self) -> None:
        body = self._read_body()
        split = urlsplit(self.path)
        route, captures = self.contract_server.match_route(
            self.command,
            split.path,
        )
        self._record(body, route["name"] if route else None)
        if route is None:
            self._send_json(404, {"error": "operation not in contract"})
            return

        server = self.contract_server
        name = route["name"]
        if captures.get("namespace") != server.namespace:
            self._send_json(404, {"error": "namespace not found"})
            return
        if "cluster_name" in captures and (
            captures["cluster_name"] != server.cluster_name
        ):
            self._send_json(404, {"error": "Cluster not found"})
            return

        override = server.response_overrides.get(name)
        if override is not None:
            if override.get("disconnect") is True:
                if name == "patchVksClusterVersion":
                    with server.state_lock:
                        server.cluster_patch_attempts += 1
                self._drop_connection()
                return
            status = override.get("status", 200)
            if not isinstance(status, int):
                self._send_json(500, {"error": "invalid response override"})
                return
            headers = override.get("headers")
            if not isinstance(headers, dict):
                headers = None
            raw = override.get("raw")
            if isinstance(raw, bytes):
                self._send_bytes(status, raw, headers=headers)
            else:
                self._send_json(
                    status,
                    copy.deepcopy(override.get("json")),
                    headers=headers,
                )
            return

        if name == "getSupervisorNamespace":
            self._send_json(
                200,
                {
                    "supervisor": server.supervisor,
                    "config_status": "RUNNING",
                    "messages": [],
                    "stats": {},
                    "description": server.namespace_description,
                    "access_list": [],
                    "storage_specs": [],
                },
            )
            return

        if name == "getVksCluster":
            self._send_json(
                200,
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "Cluster",
                    "metadata": {
                        "name": server.cluster_name,
                        "namespace": server.namespace,
                    },
                    "spec": {
                        "topology": {
                            "class": server.cluster_class,
                            "version": server.cluster_version,
                        }
                    },
                },
            )
            return

        value = self._json_object(body)
        request = route["operation"].get("requestBody", {})
        if self.headers.get("Content-Type") != request.get("contentType"):
            self._send_json(415, {"error": "unsupported media type"})
            return

        if name == "updateSupervisorNamespace":
            if (
                value is None
                or set(value) != {"description"}
                or not isinstance(value.get("description"), str)
                or not value["description"].strip()
            ):
                self._send_json(400, {"error": "invalid UpdateSpec"})
                return
            with server.state_lock:
                server.namespace_description = value["description"]
                server.namespace_update_count += 1
            self._send_empty(204)
            return

        if name == "patchVksClusterVersion":
            topology = (
                value.get("spec", {}).get("topology")
                if isinstance(value, dict)
                and isinstance(value.get("spec"), dict)
                else None
            )
            if (
                set(value or {}) != {"spec"}
                or set(value["spec"]) != {"topology"}
                or not isinstance(topology, dict)
                or set(topology) != {"version"}
                or not isinstance(topology.get("version"), str)
                or not topology["version"].strip()
            ):
                self._send_json(400, {"error": "invalid merge patch"})
                return
            with server.state_lock:
                server.cluster_patch_attempts += 1
            self._send_json(
                422,
                {
                    "apiVersion": "v1",
                    "kind": "Status",
                    "status": "Failure",
                    "reason": "Invalid",
                    "message": server.failure_marker,
                    "code": 422,
                },
            )
            return

        self._send_json(500, {"error": "unhandled contract operation"})

    def do_GET(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()
