#!/usr/bin/env python3
"""Contract-pinned loopback fixture for vCenter namespaces and VKS Cluster API."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def append_log(path: Path, lock: threading.Lock, entry: dict) -> None:
    payload = json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n"
    with lock:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())


def main() -> int:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py PORT_FILE LOG_FILE CONTRACT_FILE SCENARIO_FILE"
        )

    port_file = Path(sys.argv[1])
    log_file = Path(sys.argv[2])
    contract = load_json(sys.argv[3])
    scenario = load_json(sys.argv[4])

    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise ValueError("contract must name exactly one vCenter operation")
    vcenter_operation = operations[0]
    if (
        vcenter_operation.get("operationId")
        != "Vcenter.Namespaces.User.Instances_list"
        or vcenter_operation.get("method") != "GET"
    ):
        raise ValueError("unexpected vCenter operation contract")
    vcenter_path = vcenter_operation.get("path")
    if not isinstance(vcenter_path, str) or not vcenter_path.startswith("/api/"):
        raise ValueError("invalid vCenter operation path")

    kubernetes = contract.get("kubernetesApi")
    kube_operations = (
        kubernetes.get("operations") if isinstance(kubernetes, dict) else None
    )
    if not isinstance(kube_operations, list) or len(kube_operations) != 1:
        raise ValueError("contract must name exactly one Kubernetes operation")
    kube_operation = kube_operations[0]
    if (
        kube_operation.get("operationKey")
        != "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
        or kube_operation.get("method") != "GET"
        or kube_operation.get("apiGroup") != "cluster.x-k8s.io"
        or kube_operation.get("version") != "v1beta2"
        or kube_operation.get("resource") != "clusters"
        or kube_operation.get("verb") != "list"
    ):
        raise ValueError("unexpected Kubernetes operation contract")
    kube_template = kube_operation.get("pathTemplate")
    if kube_template != (
        "/apis/cluster.x-k8s.io/v1beta2/"
        "namespaces/{namespace}/clusters"
    ):
        raise ValueError("invalid Kubernetes operation path template")

    old_token = scenario["old_token"]
    fresh_token = scenario["fresh_token"]
    expiry_namespace = scenario["expiry_namespace"]
    namespaces = scenario["namespaces"]
    clusters_by_namespace = scenario["clusters_by_namespace"]
    if not isinstance(namespaces, list) or not namespaces:
        raise ValueError("scenario namespaces must be a non-empty list")
    if set(namespaces) != set(clusters_by_namespace):
        raise ValueError("scenario cluster map must exactly match namespaces")
    if expiry_namespace not in namespaces:
        raise ValueError("expiry namespace is not in the scenario")

    kube_paths = {
        kube_template.replace("{namespace}", quote(namespace, safe="")): namespace
        for namespace in namespaces
    }
    state_lock = threading.Lock()
    log_lock = threading.Lock()
    state = {
        "vcenter_successes": 0,
        "kube_successes": 0,
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "ContractFixture"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, value: object) -> None:
            body = json.dumps(
                value, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            split = urlsplit(self.path)
            declared_length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(declared_length) if declared_length else b""
            vmware_token = self.headers.get("vmware-api-session-id")
            authorization = self.headers.get("Authorization")
            operation_id = None
            operation_key = None
            collection_reversed = None
            status = 404
            response: object = {
                "error_type": "NOT_FOUND",
                "messages": [],
            }

            with state_lock:
                is_vcenter = self.command == "GET" and split.path == vcenter_path
                kube_namespace = (
                    kube_paths.get(split.path) if self.command == "GET" else None
                )

                if is_vcenter:
                    operation_id = vcenter_operation["operationId"]
                    if split.query:
                        status = 400
                        response = {
                            "error_type": "INVALID_ARGUMENT",
                            "messages": [],
                        }
                    elif vmware_token not in (old_token, fresh_token):
                        status = 401
                        response = {
                            "error_type": "UNAUTHENTICATED",
                            "messages": [],
                        }
                    else:
                        status = 200
                        state["vcenter_successes"] += 1
                        collection_reversed = state["vcenter_successes"] % 2 == 1
                        ordered_namespaces = list(namespaces)
                        if collection_reversed:
                            ordered_namespaces.reverse()
                        endpoint = (
                            f"http://127.0.0.1:{self.server.server_port}"
                        )
                        response = [
                            {
                                "namespace": namespace,
                                "master_host": endpoint,
                            }
                            for namespace in ordered_namespaces
                        ]
                elif kube_namespace is not None:
                    operation_key = kube_operation["operationKey"]
                    bearer = (
                        authorization[7:]
                        if isinstance(authorization, str)
                        and authorization.startswith("Bearer ")
                        else None
                    )
                    if split.query:
                        status = 400
                        response = {
                            "kind": "Status",
                            "apiVersion": "v1",
                            "status": "Failure",
                            "reason": "BadRequest",
                            "code": 400,
                        }
                    elif bearer == old_token and kube_namespace == expiry_namespace:
                        status = 401
                        response = {
                            "kind": "Status",
                            "apiVersion": "v1",
                            "status": "Failure",
                            "reason": "Unauthorized",
                            "code": 401,
                        }
                    elif bearer in (old_token, fresh_token):
                        status = 200
                        state["kube_successes"] += 1
                        collection_reversed = state["kube_successes"] % 2 == 1
                        items = list(clusters_by_namespace[kube_namespace])
                        if collection_reversed:
                            items.reverse()
                        response = {
                            "apiVersion": "cluster.x-k8s.io/v1beta2",
                            "kind": "ClusterList",
                            "metadata": {
                                "resourceVersion": str(
                                    1000 + state["kube_successes"]
                                )
                            },
                            "items": items,
                        }
                    else:
                        status = 401
                        response = {
                            "kind": "Status",
                            "apiVersion": "v1",
                            "status": "Failure",
                            "reason": "Unauthorized",
                            "code": 401,
                        }

                entry = {
                    "operationId": operation_id,
                    "operationKey": operation_key,
                    "method": self.command,
                    "rawTarget": self.path,
                    "path": split.path,
                    "rawQuery": split.query,
                    "vmwareApiSessionId": vmware_token,
                    "authorization": authorization,
                    "accept": self.headers.get("Accept"),
                    "contentType": self.headers.get("Content-Type"),
                    "contentLengthHeader": self.headers.get("Content-Length"),
                    "transferEncoding": self.headers.get("Transfer-Encoding"),
                    "bodyLength": len(body),
                    "bodyHex": body.hex(),
                    "status": status,
                    "collectionReversed": collection_reversed,
                }
                append_log(log_file, log_lock, entry)

            self._send_json(status, response)

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
