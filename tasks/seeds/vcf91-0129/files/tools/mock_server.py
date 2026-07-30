#!/usr/bin/env python3
"""Loopback-only contract fixture for the protected acceptance verifier."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "Vcenter.Namespaces.Instances_createV2",
    "Vcenter.Namespaces.Instances_getV2",
}
EXPECTED_KUBERNETES_IDS = {
    "Kubernetes.Cluster_create",
    "Kubernetes.Cluster_get",
}


def compile_path(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"([^/]+)")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


def load_routes(path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], str]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operation_ids = {item["operationId"] for item in contract["operations"]}
    kubernetes_ids = {
        item["contract_id"] for item in contract["kubernetes_routes"]
    }
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise ValueError("contract contains an unexpected vCenter operationId set")
    if kubernetes_ids != EXPECTED_KUBERNETES_IDS:
        raise ValueError("contract contains an unexpected Kubernetes route set")

    routes: dict[tuple[str, str], dict[str, Any]] = {}
    base = contract["server_base_path"]
    for item in contract["operations"]:
        item = dict(item)
        item["pattern"] = compile_path(base + item["path"])
        routes[(item["method"], item["name"])] = item
    for item in contract["kubernetes_routes"]:
        item = dict(item)
        item["pattern"] = compile_path(item["path"])
        routes[(item["method"], item["name"])] = item
    return routes, contract["product_version"]


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        routes: dict[tuple[str, str], dict[str, Any]],
        product_version: str,
        log_path: Path,
        scenario: str,
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.product_version = product_version
        self.log_path = log_path
        self.scenario = scenario
        self.counters: dict[str, int] = {}
        self.log_lock = threading.Lock()

    def match_route(
        self, method: str, path: str
    ) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
        for (route_method, _), route in self.routes.items():
            if route_method != method:
                continue
            match = route["pattern"].fullmatch(path)
            if match:
                return route, match.groups()
        return None, ()

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("POST")

    def _read_body(self) -> tuple[bytes, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            return raw, {"_malformed_json": raw.decode("utf-8", errors="replace")}

    def _handle(self, method: str) -> None:
        target = urlsplit(self.path)
        raw, body = self._read_body()
        route, captures = self.server.match_route(method, target.path)
        record = {
            "method": method,
            "path": target.path,
            "query": target.query,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": body,
            "body_bytes": len(raw),
            "operation": route["name"] if route else None,
        }
        self.server.append_log(record)
        if route is None:
            self._json_response(
                404,
                {
                    "error": "route is outside the pinned contract",
                    "method": method,
                    "path": target.path,
                },
            )
            return

        name = route["name"]
        count = self.server.counters.get(name, 0) + 1
        self.server.counters[name] = count

        if name == "namespace.createV2":
            self._empty_response(204)
            return
        if name == "namespace.getV2":
            status = "CONFIGURING" if count == 1 else "RUNNING"
            self._json_response(
                200,
                {
                    "supervisor": "supervisor-21",
                    "config_status": status,
                    "description": "",
                    "messages": [],
                    "stats": {},
                    "access_list": [],
                    "storage_specs": [{"policy": "gold-storage"}],
                },
            )
            return
        if name == "kubernetes.cluster.create":
            namespace = captures[0]
            cluster = body.get("metadata", {}).get("name", "") if isinstance(body, dict) else ""
            self._json_response(
                201,
                {
                    "apiVersion": route["api_version"],
                    "kind": route["kind"],
                    "metadata": {"name": cluster, "namespace": namespace},
                },
            )
            return
        if name == "kubernetes.cluster.get":
            namespace, cluster = captures
            if self.server.scenario == "cluster_failed":
                phase = "Failed"
                ready = "False"
                reason = "ReconcileFailed"
                message = "node bootstrap failed"
            elif count == 1:
                phase = "Provisioning"
                ready = "False"
                reason = "Provisioning"
                message = "control plane is starting"
            elif count == 2:
                phase = "Provisioned"
                ready = "False"
                reason = "WaitingForWorkers"
                message = "worker nodes are not ready"
            else:
                phase = "Provisioned"
                ready = "True"
                reason = "Ready"
                message = "cluster is ready"
            self._json_response(
                200,
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta1",
                    "kind": "Cluster",
                    "metadata": {"name": cluster, "namespace": namespace},
                    "status": {
                        "phase": phase,
                        "conditions": [
                            {
                                "type": "Ready",
                                "status": ready,
                                "reason": reason,
                                "message": message,
                            }
                        ],
                    },
                },
            )
            return
        self._json_response(500, {"error": "unhandled contract operation"})

    def _empty_response(self, status: int) -> None:
        self.send_response(status)
        self.send_header("X-VCF-API-Version", self.server.product_version)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-VCF-API-Version", self.server.product_version)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument(
        "--scenario", choices=("ready", "cluster_failed"), default="ready"
    )
    args = parser.parse_args()

    routes, product_version = load_routes(args.contract)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes,
        product_version,
        args.log,
        args.scenario,
    )
    args.ready.write_text(
        json.dumps({"host": "127.0.0.1", "port": server.server_port}),
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
