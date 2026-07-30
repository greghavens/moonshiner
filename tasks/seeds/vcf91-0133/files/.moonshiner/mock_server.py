#!/usr/bin/env python3
"""Contract-derived loopback service for vcf91-0133."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_VCENTER_IDS = {
    "Vcenter.Namespaces.Instances_getV2",
    "Vcenter.Namespaces.Instances_update",
}
EXPECTED_KUBERNETES_KEYS = {
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch",
}


def compile_path(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[A-Za-z_][A-Za-z0-9_]*\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(r"([^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


def load_routes(path: Path) -> list[dict[str, Any]]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    vcenter = contract["operations"]
    kubernetes = contract["kubernetesApi"]["operations"]
    if {item["operationId"] for item in vcenter} != EXPECTED_VCENTER_IDS:
        raise ValueError("unexpected vCenter operationId set")
    if {item["operationKey"] for item in kubernetes} != EXPECTED_KUBERNETES_KEYS:
        raise ValueError("unexpected Kubernetes operation key set")

    routes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*vcenter, *kubernetes]:
        route = dict(item)
        key = (route["method"], route["path"])
        if key in seen:
            raise ValueError("duplicate method and path in focused contract")
        seen.add(key)
        route["pattern"] = compile_path(route["path"])
        routes.append(route)
    return routes


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, Any]],
        log_path: Path,
        config: dict[str, str],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()

    def match_route(
        self, method: str, path: str
    ) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
        for route in self.routes:
            if route["method"] != method:
                continue
            match = route["pattern"].fullmatch(path)
            if match:
                return route, match.groups()
        return None, ()

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def _read_body(self) -> tuple[bytes, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length else 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw)
        except json.JSONDecodeError:
            return raw, {"_malformed": raw.decode("utf-8", errors="replace")}

    def _headers(self) -> tuple[list[list[str]], dict[str, list[str]]]:
        pairs: list[list[str]] = []
        grouped: dict[str, list[str]] = {}
        for key, value in self.headers.raw_items():
            lowered = key.lower()
            pairs.append([lowered, value])
            grouped.setdefault(lowered, []).append(value)
        return pairs, grouped

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw, body = self._read_body()
        route, captures = self.server.match_route(method, split.path)
        pairs, grouped = self._headers()
        self.server.append_log(
            {
                "method": method,
                "raw_target": self.path,
                "path": split.path,
                "query": split.query,
                "header_pairs": pairs,
                "headers": grouped,
                "body": body,
                "body_raw": raw.decode("utf-8", errors="replace"),
                "body_bytes": len(raw),
                "operation": route["name"] if route else None,
            }
        )
        if route is None:
            self._json(404, {"error_type": "OUTSIDE_FOCUSED_CONTRACT"})
            return

        name = route["name"]
        config = self.server.config
        decoded = tuple(unquote(value) for value in captures)

        if name == "namespace.getV2":
            if decoded != (config["namespace"],):
                self._json(404, {"error_type": "NOT_FOUND"})
                return
            self._json(
                200,
                {
                    "supervisor": config["supervisor"],
                    "config_status": "RUNNING",
                    "messages": [],
                    "stats": {
                        "cpu_used": 0,
                        "memory_used": 0,
                        "storage_used": 0,
                    },
                    "description": config["old_description"],
                    "access_list": [],
                    "storage_specs": [],
                },
            )
            return

        if name == "kubernetes.cluster.get":
            if decoded != (config["namespace"], config["cluster_name"]):
                self._json(
                    404,
                    {
                        "apiVersion": "v1",
                        "kind": "Status",
                        "status": "Failure",
                        "reason": "NotFound",
                        "code": 404,
                    },
                )
                return
            self._json(200, self._cluster())
            return

        if name == "namespace.update":
            if decoded != (config["namespace"],):
                self._json(404, {"error_type": "NOT_FOUND"})
                return
            self._empty(204)
            return

        if name == "kubernetes.cluster.patch":
            if decoded != (config["namespace"], config["cluster_name"]):
                self._json(
                    404,
                    {
                        "apiVersion": "v1",
                        "kind": "Status",
                        "status": "Failure",
                        "reason": "NotFound",
                        "code": 404,
                    },
                )
                return
            self._json(
                422,
                {
                    "apiVersion": "v1",
                    "kind": "Status",
                    "status": "Failure",
                    "reason": "Invalid",
                    "message": config["failure_marker"],
                    "code": 422,
                },
            )
            return

        self._json(500, {"error_type": "UNHANDLED_FOCUSED_ROUTE"})

    def _cluster(self) -> dict[str, Any]:
        config = self.server.config
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": {
                "name": config["cluster_name"],
                "namespace": config["namespace"],
            },
            "spec": {
                "topology": {
                    "class": config["cluster_class"],
                    "version": config["old_version"],
                }
            },
        }

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()

    routes = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    durable_write(args.log, "", "w")
    server = ContractServer(("127.0.0.1", 0), routes, args.log, config)
    host, port = server.server_address
    durable_write(
        args.ready,
        json.dumps({"host": host, "port": port}, separators=(",", ":")),
        "w",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
