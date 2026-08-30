"""Contract-derived loopback service for the Java session-rotation task."""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_IDENTITIES = {
    "createVcenterSession": (
        "POST",
        "Cis.Session_create",
        None,
    ),
    "listSupervisorNamespaces": (
        "GET",
        "Vcenter.Namespaces.User.Instances_list",
        None,
    ),
    "getVksCluster": (
        "GET",
        None,
        "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
    ),
    "deleteVcenterSession": (
        "DELETE",
        "Cis.Session_delete",
        None,
    ),
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
    """Serve only operations named by the focused protected contract."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        *,
        contract_path: Path,
        request_log: Path,
        old_session: str,
        new_session: str,
        expected_basic: str,
        kubernetes_token: str,
        namespace: str,
        cluster_name: str,
        topology_version: str,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list):
            raise ValueError("contract operations are missing")
        by_name = {item.get("contractName"): item for item in operations}
        if (
            set(by_name) != set(EXPECTED_IDENTITIES)
            or len(by_name) != len(operations)
        ):
            raise ValueError("contract route allow-list is not exact")

        self.routes: list[dict[str, Any]] = []
        for name, expected in EXPECTED_IDENTITIES.items():
            operation = by_name[name]
            method, operation_id, operation_key = expected
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
                }
            )

        self.request_log = request_log
        self.old_session = old_session
        self.new_session = new_session
        self.expected_basic = expected_basic
        self.kubernetes_token = kubernetes_token
        self.namespace = namespace
        self.cluster_name = cluster_name
        self.topology_version = topology_version

        self.old_namespace_started = threading.Event()
        self.new_cluster_response_sent = threading.Event()
        self.old_namespace_completed = threading.Event()
        self.state_lock = threading.Lock()
        self.sequence = 0
        self.create_count = 0
        self.old_namespace_count = 0
        self.new_namespace_count = 0
        self.cluster_get_count = 0
        self.delete_count = 0
        self.delete_before_drain = False
        self.deleted_old_session = False
        super().__init__(address, ContractRequestHandler)

    @property
    def root_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    @property
    def master_host(self) -> str:
        host, port = self.server_address
        return f"{host}:{port}"

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

    def snapshot(self) -> dict[str, object]:
        with self.state_lock:
            return {
                "create_count": self.create_count,
                "old_namespace_count": self.old_namespace_count,
                "new_namespace_count": self.new_namespace_count,
                "cluster_get_count": self.cluster_get_count,
                "delete_count": self.delete_count,
                "delete_before_drain": self.delete_before_drain,
                "deleted_old_session": self.deleted_old_session,
            }


class ContractRequestHandler(BaseHTTPRequestHandler):
    """Handle the focused contract without any out-of-contract control route."""

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

    def _record(self, body: bytes, operation: str | None) -> None:
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

    def _send_json(self, status: int, value: object) -> None:
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
        self.close_connection = True

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _dispatch(self) -> None:
        body = self._read_body()
        split = urlsplit(self.path)
        route, captures = self.contract_server.match_route(
            self.command,
            split.path,
        )
        name = route["name"] if route else None
        self._record(body, name)
        if route is None:
            self._send_json(404, {"error": "operation not in contract"})
            return
        if split.query or body:
            self._send_json(400, {"error": "wire shape rejected"})
            return

        server = self.contract_server
        if name == "createVcenterSession":
            if self.headers.get("Authorization") != server.expected_basic:
                self._send_json(401, {"error": "authentication rejected"})
                return
            with server.state_lock:
                server.create_count += 1
            self._send_json(201, server.new_session)
            return

        if name == "listSupervisorNamespaces":
            session = self.headers.get("vmware-api-session-id")
            if session == server.old_session:
                with server.state_lock:
                    server.old_namespace_count += 1
                server.old_namespace_started.set()
                if not server.new_cluster_response_sent.wait(timeout=8):
                    self._send_json(503, {"error": "handoff did not progress"})
                    return
                self._send_json(
                    200,
                    [
                        {
                            "namespace": server.namespace,
                            "master_host": server.master_host,
                        }
                    ],
                )
                server.old_namespace_completed.set()
                return
            if session == server.new_session:
                with server.state_lock:
                    server.new_namespace_count += 1
                self._send_json(
                    200,
                    [
                        {
                            "namespace": server.namespace,
                            "master_host": server.master_host,
                        }
                    ],
                )
                return
            self._send_json(401, {"error": "authentication rejected"})
            return

        if name == "getVksCluster":
            if (
                self.headers.get("Authorization")
                != f"Bearer {server.kubernetes_token}"
                or captures.get("namespace") != server.namespace
                or captures.get("cluster_name") != server.cluster_name
            ):
                self._send_json(401, {"error": "authentication rejected"})
                return
            with server.state_lock:
                server.cluster_get_count += 1
                cluster_get_count = server.cluster_get_count
            self._send_json(
                200,
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "Cluster",
                    "metadata": {
                        "namespace": server.namespace,
                        "name": server.cluster_name,
                    },
                    "spec": {
                        "topology": {
                            "version": server.topology_version,
                        }
                    },
                },
            )
            if cluster_get_count == 1:
                server.new_cluster_response_sent.set()
            return

        if name == "deleteVcenterSession":
            session = self.headers.get("vmware-api-session-id")
            with server.state_lock:
                server.delete_count += 1
                drained = (
                    server.old_namespace_completed.is_set()
                    and server.cluster_get_count == 2
                )
                if session == server.old_session and drained:
                    server.deleted_old_session = True
                else:
                    server.delete_before_drain = True
            if session != server.old_session:
                self._send_json(401, {"error": "authentication rejected"})
                return
            if not drained:
                self._send_json(409, {"error": "old session is still leased"})
                return
            self._send_empty(204)
            return

        self._send_json(500, {"error": "unreachable contract branch"})

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()
