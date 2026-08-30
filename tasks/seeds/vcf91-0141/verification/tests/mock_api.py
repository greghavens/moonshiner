"""Contract-pinned loopback service for the VKS retry exercise."""

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
from urllib.parse import unquote


def _route_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[a-z_]+\})", template)
    rendered: list[str] = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            rendered.append(f"(?P<{part[1:-1]}>[^/]+)")
        else:
            rendered.append(re.escape(part))
    return re.compile("^" + "".join(rendered) + "$")


class ContractMockServer(ThreadingHTTPServer):
    """A stateful mock whose allow-list is derived from docs/contract.json."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        contract_path: Path,
        request_log: Path,
        namespace: str,
        namespace_info: dict[str, Any],
        clusters: dict[str, dict[str, Any]],
        drop_modes: dict[str, str],
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            item["contractName"]: item for item in contract["operations"]
        }
        if set(operations) != {
            "getSupervisorNamespace",
            "getVksCluster",
            "patchVksClusterMetadata",
        }:
            raise ValueError("contract operation allow-list is not exact")

        namespace_operation = operations["getSupervisorNamespace"]
        get_operation = operations["getVksCluster"]
        patch_operation = operations["patchVksClusterMetadata"]
        if (
            namespace_operation.get("operationId")
            != "Vcenter.Namespaces.Instances_getV2"
            or namespace_operation.get("method") != "GET"
            or get_operation.get("method") != "GET"
            or patch_operation.get("method") != "PATCH"
        ):
            raise ValueError("contract methods or operationId are inconsistent")

        self.namespace_route = _route_pattern(namespace_operation["pathTemplate"])
        self.cluster_get_route = _route_pattern(get_operation["pathTemplate"])
        self.cluster_patch_route = _route_pattern(
            patch_operation["pathTemplate"]
        )
        self.patch_content_type = patch_operation["requestContentType"]
        self.request_log = request_log
        self.namespace = namespace
        self.namespace_info = copy.deepcopy(namespace_info)
        self.clusters = copy.deepcopy(clusters)
        self.drop_modes = dict(drop_modes)
        if set(self.clusters) != set(self.drop_modes):
            raise ValueError("every Cluster needs one drop mode")
        if not set(self.drop_modes.values()) <= {"before", "after", "none"}:
            raise ValueError("unknown drop mode")

        self.patch_attempts = {name: 0 for name in self.clusters}
        self.mutation_counts = {name: 0 for name in self.clusters}
        self.sequence = 0
        self.state_lock = threading.Lock()
        super().__init__(address, ContractRequestHandler)

    @property
    def root_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def append_log(self, record: dict[str, Any]) -> None:
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

    def next_sequence(self) -> int:
        with self.state_lock:
            self.sequence += 1
            return self.sequence

    def snapshot_stats(self) -> dict[str, Any]:
        with self.state_lock:
            return {
                "patch_attempts": dict(self.patch_attempts),
                "mutation_counts": dict(self.mutation_counts),
                "clusters": copy.deepcopy(self.clusters),
            }


class ContractRequestHandler(BaseHTTPRequestHandler):
    """Serve exactly the three routes named by the focused contract."""

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

    def _record(self, body: bytes) -> None:
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError:
            body_text = "<non-utf8>"
        self.contract_server.append_log(
            {
                "sequence": self.contract_server.next_sequence(),
                "method": self.command,
                "target": self.path,
                "headers": [[key, value] for key, value in self.headers.raw_items()],
                "body_utf8": body_text,
                "body_length": len(body),
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

    def _drop_connection(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()

    def _decoded_match(
        self, pattern: re.Pattern[str]
    ) -> dict[str, str] | None:
        match = pattern.fullmatch(self.path)
        if match is None:
            return None
        return {
            key: unquote(value, encoding="utf-8", errors="strict")
            for key, value in match.groupdict().items()
        }

    def do_GET(self) -> None:
        body = self._read_body()
        self._record(body)

        namespace_match = self._decoded_match(
            self.contract_server.namespace_route
        )
        if namespace_match is not None:
            if namespace_match["namespace"] != self.contract_server.namespace:
                self._send_json(404, {"error": "namespace not found"})
                return
            self._send_json(
                200,
                copy.deepcopy(self.contract_server.namespace_info),
            )
            return

        cluster_match = self._decoded_match(
            self.contract_server.cluster_get_route
        )
        if cluster_match is not None:
            if cluster_match["namespace"] != self.contract_server.namespace:
                self._send_json(404, {"error": "namespace not found"})
                return
            name = cluster_match["cluster_name"]
            with self.contract_server.state_lock:
                cluster = copy.deepcopy(
                    self.contract_server.clusters.get(name)
                )
            if cluster is None:
                self._send_json(404, {"error": "cluster not found"})
                return
            self._send_json(200, cluster)
            return

        self._send_json(404, {"error": "operation not in contract"})

    def do_PATCH(self) -> None:
        body = self._read_body()
        self._record(body)
        match = self._decoded_match(self.contract_server.cluster_patch_route)
        if match is None:
            self._send_json(404, {"error": "operation not in contract"})
            return
        if match["namespace"] != self.contract_server.namespace:
            self._send_json(404, {"error": "namespace not found"})
            return
        if self.headers.get("Content-Type") != self.contract_server.patch_content_type:
            self._send_json(415, {"error": "unsupported media type"})
            return

        try:
            patch = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return
        if not isinstance(patch, dict) or set(patch) != {"metadata"}:
            self._send_json(422, {"error": "invalid merge patch"})
            return
        metadata_patch = patch["metadata"]
        if (
            not isinstance(metadata_patch, dict)
            or set(metadata_patch) != {"resourceVersion", "annotations"}
            or not isinstance(metadata_patch.get("resourceVersion"), str)
            or not isinstance(metadata_patch.get("annotations"), dict)
        ):
            self._send_json(422, {"error": "invalid metadata merge patch"})
            return

        name = match["cluster_name"]
        should_drop = False
        drop_before = False
        result: dict[str, Any] | None = None
        with self.contract_server.state_lock:
            cluster = self.contract_server.clusters.get(name)
            if cluster is None:
                result = None
            else:
                current_version = cluster["metadata"]["resourceVersion"]
                if metadata_patch["resourceVersion"] != current_version:
                    result = {"conflict": current_version}
                else:
                    self.contract_server.patch_attempts[name] += 1
                    attempt = self.contract_server.patch_attempts[name]
                    mode = self.contract_server.drop_modes[name]
                    should_drop = attempt == 1 and mode in {"before", "after"}
                    drop_before = should_drop and mode == "before"
                    if not drop_before:
                        annotations = cluster["metadata"].setdefault(
                            "annotations", {}
                        )
                        annotations.update(metadata_patch["annotations"])
                        try:
                            next_version = str(int(current_version) + 1)
                        except ValueError:
                            next_version = current_version + "-next"
                        cluster["metadata"]["resourceVersion"] = next_version
                        self.contract_server.mutation_counts[name] += 1
                        result = copy.deepcopy(cluster)

        if should_drop and drop_before:
            self._drop_connection()
            return
        if result is None:
            self._send_json(404, {"error": "cluster not found"})
            return
        if "conflict" in result:
            self._send_json(409, {"error": "resourceVersion conflict"})
            return
        if should_drop:
            self._drop_connection()
            return
        self._send_json(200, result)

    def do_POST(self) -> None:
        body = self._read_body()
        self._record(body)
        self._send_json(404, {"error": "operation not in contract"})

    def do_PUT(self) -> None:
        body = self._read_body()
        self._record(body)
        self._send_json(404, {"error": "operation not in contract"})

    def do_DELETE(self) -> None:
        body = self._read_body()
        self._record(body)
        self._send_json(404, {"error": "operation not in contract"})
