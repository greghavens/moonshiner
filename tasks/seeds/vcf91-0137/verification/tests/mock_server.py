#!/usr/bin/env python3
"""Contract-pinned loopback service for vcf91-0137."""

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
    "Vcenter.Namespaces.User.Instances_list",
}
EXPECTED_KUBERNETES_KEYS = {
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:create",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
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
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()
        self.collection_calls = 0
        self.poll_calls = 0
        self.created = False

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

    def next_collection(self) -> tuple[list[str], bool]:
        with self.lock:
            self.collection_calls += 1
            created = self.created
            descending = self.collection_calls % 2 == 1
        names = list(self.config["existing_cluster_names"])
        if created:
            names.append(self.config["cluster_name"])
        names.sort()
        if descending:
            names.reverse()
        return names, descending

    def mark_created(self) -> None:
        with self.lock:
            self.created = True

    def next_phase(self) -> str:
        phases = ["Pending", "Provisioning", "Provisioned"]
        with self.lock:
            self.poll_calls += 1
            position = min(self.poll_calls, len(phases)) - 1
        return phases[position]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

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

        if name == "namespace.listAuthorized":
            self._json(
                200,
                [
                    {
                        "namespace": "unrelated-" + config["suffix"],
                        "master_host": "127.0.0.1:9",
                    },
                    {
                        "namespace": config["namespace"],
                        "master_host": config["master_host"],
                    },
                ],
            )
            return

        if name == "kubernetes.clusters.list":
            if decoded != (config["namespace"],):
                self._status(404, "NotFound")
                return
            names, descending = self.server.next_collection()
            self._json(
                200,
                {
                    "apiVersion": "cluster.x-k8s.io/v1beta2",
                    "kind": "ClusterList",
                    "metadata": {
                        "resourceVersion": config["resource_version"],
                        "serverOrder": (
                            "descending" if descending else "ascending"
                        ),
                    },
                    "items": [
                        self._cluster_item(cluster_name, "Provisioned")
                        for cluster_name in names
                    ],
                },
            )
            return

        if name == "kubernetes.clusters.create":
            if decoded != (config["namespace"],):
                self._status(404, "NotFound")
                return
            self.server.mark_created()
            self._json(
                201,
                self._cluster_item(config["cluster_name"], "Pending"),
            )
            return

        if name == "kubernetes.cluster.get":
            if decoded != (config["namespace"], config["cluster_name"]):
                self._status(404, "NotFound")
                return
            self._json(
                200,
                self._cluster_item(
                    config["cluster_name"],
                    self.server.next_phase(),
                ),
            )
            return

        self._json(500, {"error_type": "UNHANDLED_CONTRACT_OPERATION"})

    def _cluster_item(self, name: str, phase: str) -> dict[str, Any]:
        return {
            "apiVersion": "cluster.x-k8s.io/v1beta2",
            "kind": "Cluster",
            "metadata": {
                "name": name,
                "namespace": self.server.config["namespace"],
                "uid": self.server.config["cluster_uids"][name],
            },
            "status": {
                "phase": phase,
                "observedGeneration": 1,
            },
        }

    def _json(self, status: int, value: Any) -> None:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def _status(self, status: int, text: str) -> None:
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    routes = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), routes, args.log, config)
    config["master_host"] = f"127.0.0.1:{server.server_port}"
    durable_write(
        args.ready,
        json.dumps(
            {"host": "127.0.0.1", "port": server.server_port},
            separators=(",", ":"),
        ),
        "w",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
