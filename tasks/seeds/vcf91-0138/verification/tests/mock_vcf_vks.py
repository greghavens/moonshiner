#!/usr/bin/env python3
"""Contract-pinned loopback fixture for Supervisor namespace and VKS creation."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


def load_object(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def compact(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def append_log(path: Path, entry: dict) -> None:
    encoded = json.dumps(
        entry,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcf_vks.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_object(sys.argv[3])
    scenario = load_object(sys.argv[4])

    vcenter_operations = contract.get("operations")
    kubernetes_api = contract.get("supervisorKubernetesApi")
    kubernetes_operations = (
        kubernetes_api.get("operations")
        if isinstance(kubernetes_api, dict)
        else None
    )
    if (
        not isinstance(vcenter_operations, list)
        or len(vcenter_operations) != 2
        or not isinstance(kubernetes_operations, list)
        or len(kubernetes_operations) != 2
    ):
        raise ValueError(
            "the contract must name two vCenter and two Kubernetes operations"
        )
    operations = vcenter_operations + kubernetes_operations
    expected_contract = [
        (
            "createSupervisorNamespace",
            "Vcenter.Namespaces.Instances_createV2",
            "POST",
            "/api/vcenter/namespaces/instances/v2",
        ),
        (
            "getSupervisorNamespace",
            "Vcenter.Namespaces.Instances_getV2",
            "GET",
            "/api/vcenter/namespaces/instances/v2/{namespace}",
        ),
        (
            "createVksCluster",
            None,
            "POST",
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters",
        ),
        (
            "getVksCluster",
            None,
            "GET",
            (
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                "{namespace}/clusters/{cluster_name}"
            ),
        ),
    ]
    for operation, expected in zip(operations, expected_contract):
        name, operation_id, method, path_template = expected
        if operation.get("contractName") != name:
            raise ValueError(f"unexpected contract operation name: {name}")
        if operation.get("operationId") != operation_id:
            raise ValueError(f"unexpected operationId for {name}")
        if operation.get("method") != method:
            raise ValueError(f"unexpected method for {name}")
        if operation.get("pathTemplate") != path_template:
            raise ValueError(f"unexpected path template for {name}")

    namespace = scenario["namespace"]
    supervisor = scenario["supervisor"]
    cluster_name = scenario["cluster_name"]
    session_id = scenario["session_id"]
    bearer_token = scenario["bearer_token"]
    expected_namespace = scenario["expected_namespace_body"]
    expected_cluster = scenario["expected_cluster_body"]
    for label, value in (
        ("namespace", namespace),
        ("supervisor", supervisor),
        ("cluster_name", cluster_name),
        ("session_id", session_id),
        ("bearer_token", bearer_token),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"scenario {label} must be a non-empty string")

    escaped_namespace = quote(namespace, safe="")
    escaped_cluster = quote(cluster_name, safe="")
    routes = {
        "createSupervisorNamespace": (
            "POST",
            operations[0]["pathTemplate"],
        ),
        "getSupervisorNamespace": (
            "GET",
            operations[1]["pathTemplate"].replace(
                "{namespace}", escaped_namespace
            ),
        ),
        "createVksCluster": (
            "POST",
            operations[2]["pathTemplate"].replace(
                "{namespace}", escaped_namespace
            ),
        ),
        "getVksCluster": (
            "GET",
            operations[3]["pathTemplate"]
            .replace("{namespace}", escaped_namespace)
            .replace("{cluster_name}", escaped_cluster),
        ),
    }

    expected_namespace_bytes = compact(expected_namespace)
    expected_cluster_bytes = compact(expected_cluster)
    state = {
        "namespace_created": False,
        "namespace_ready": False,
        "namespace_polls": 0,
        "cluster_created": False,
        "cluster_polls": 0,
    }
    state_lock = threading.Lock()

    def namespace_info(config_status: str) -> dict:
        return {
            "supervisor": supervisor,
            "config_status": config_status,
            "messages": [],
            "stats": {"cpu_used": 0, "memory_used": 0, "storage_used": 0},
            "description": "",
            "access_list": [],
            "storage_specs": [],
        }

    def cluster_resource(ready_status: str) -> dict:
        condition = {
            "type": "Ready",
            "status": ready_status,
            "reason": (
                "Provisioned" if ready_status == "True" else "Creating"
            ),
            "observedGeneration": 1,
        }
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": {
                "name": cluster_name,
                "namespace": namespace,
                "generation": 1,
            },
            "spec": expected_cluster["spec"],
            "status": {
                "phase": (
                    "Provisioned" if ready_status == "True" else "Provisioning"
                ),
                "conditions": [condition],
            },
        }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send(self, status: int, value: dict | None) -> None:
            body = b"" if value is None else compact(value)
            self.send_response(status)
            if value is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _handle(self) -> None:
            split = urlsplit(self.path)
            content_length = int(
                self.headers.get("Content-Length", "0") or "0"
            )
            body = self.rfile.read(content_length) if content_length else b""
            operation_name = None
            for name, (method, path) in routes.items():
                if self.command == method and split.path == path:
                    operation_name = name
                    break

            status = 404
            response: dict | None = {
                "kind": "Status",
                "status": "Failure",
                "reason": "NotFound",
            }
            request_valid = False

            if operation_name is not None:
                is_vcenter = operation_name.endswith("SupervisorNamespace")
                expected_body = {
                    "createSupervisorNamespace": expected_namespace_bytes,
                    "createVksCluster": expected_cluster_bytes,
                }.get(operation_name, b"")
                expected_content_type = (
                    "application/json" if expected_body else None
                )
                auth_valid = (
                    self.headers.get("vmware-api-session-id") == session_id
                    and self.headers.get("Authorization") is None
                    if is_vcenter
                    else self.headers.get("Authorization")
                    == f"Bearer {bearer_token}"
                    and self.headers.get("vmware-api-session-id") is None
                )
                request_valid = (
                    split.query == ""
                    and self.headers.get("Accept") == "application/json"
                    and self.headers.get("Content-Type")
                    == expected_content_type
                    and body == expected_body
                    and auth_valid
                )
                if not request_valid:
                    status = 400
                    response = {
                        "kind": "Status",
                        "status": "Failure",
                        "reason": "BadRequest",
                    }
                else:
                    with state_lock:
                        if operation_name == "createSupervisorNamespace":
                            if state["namespace_created"]:
                                status = 409
                                response = {
                                    "error_type": "ALREADY_EXISTS",
                                    "messages": [],
                                }
                            else:
                                state["namespace_created"] = True
                                status = 204
                                response = None
                        elif operation_name == "getSupervisorNamespace":
                            if not state["namespace_created"]:
                                status = 404
                                response = {
                                    "error_type": "NOT_FOUND",
                                    "messages": [],
                                }
                            else:
                                state["namespace_polls"] += 1
                                config_status = (
                                    "CONFIGURING"
                                    if state["namespace_polls"] == 1
                                    else "RUNNING"
                                )
                                state["namespace_ready"] = (
                                    config_status == "RUNNING"
                                )
                                status = 200
                                response = namespace_info(config_status)
                        elif operation_name == "createVksCluster":
                            if not state["namespace_ready"]:
                                status = 409
                                response = {
                                    "kind": "Status",
                                    "status": "Failure",
                                    "reason": "NamespaceNotReady",
                                }
                            elif state["cluster_created"]:
                                status = 409
                                response = {
                                    "kind": "Status",
                                    "status": "Failure",
                                    "reason": "AlreadyExists",
                                }
                            else:
                                state["cluster_created"] = True
                                status = 201
                                response = cluster_resource("False")
                        elif operation_name == "getVksCluster":
                            if not state["cluster_created"]:
                                status = 404
                                response = {
                                    "kind": "Status",
                                    "status": "Failure",
                                    "reason": "NotFound",
                                }
                            else:
                                state["cluster_polls"] += 1
                                ready_status = (
                                    "Unknown"
                                    if state["cluster_polls"] == 1
                                    else "True"
                                )
                                status = 200
                                response = cluster_resource(ready_status)

            entry = {
                "contractName": operation_name,
                "operationId": (
                    operations[0]["operationId"]
                    if operation_name == "createSupervisorNamespace"
                    else operations[1]["operationId"]
                    if operation_name == "getSupervisorNamespace"
                    else None
                ),
                "method": self.command,
                "rawTarget": self.path,
                "path": split.path,
                "rawQuery": split.query,
                "vmwareApiSessionId": self.headers.get(
                    "vmware-api-session-id"
                ),
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "contentType": self.headers.get("Content-Type"),
                "contentLength": len(body),
                "bodyHex": body.hex(),
                "requestValid": request_valid,
                "status": status,
            }
            append_log(log_file, entry)
            self._send(status, response)

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle

    log_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port_file.write_text(str(server.server_port), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
