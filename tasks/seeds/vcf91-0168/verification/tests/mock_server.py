#!/usr/bin/env python3
"""Contract-pinned loopback service for vcf91-0168."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "getAllLogForwarders",
    "createLogForwarder",
}


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def load_contract(
    path: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if {item["operationId"] for item in operations} != EXPECTED_OPERATION_IDS:
        raise ValueError("unexpected focused operationId set")

    routes: dict[tuple[str, str], dict[str, Any]] = {}
    for item in operations:
        key = (item["method"], item["path"])
        if key in routes:
            raise ValueError("duplicate focused method and path")
        routes[key] = item
    return routes, contract


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: dict[tuple[str, str], dict[str, Any]],
        contract: dict[str, Any],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.contract = contract
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()
        self.forwarders = list(config["initial_forwarders"])

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(item) for item in self.forwarders]

    def create(self, body: Any) -> dict[str, Any] | None:
        if not isinstance(body, dict):
            return None
        create_operation = next(
            item
            for item in self.contract["operations"]
            if item["operationId"] == "createLogForwarder"
        )
        expected_keys = create_operation["requestBody"]["focusedPropertyOrder"]
        if list(body.keys()) != expected_keys:
            return None

        created = {"id": self.config["created_id"], **body}
        with self.lock:
            self.forwarders.append(created)
        return created


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

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
        route = self.server.routes.get((method, split.path))
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
                "operationId": route["operationId"] if route else None,
            }
        )
        if route is None:
            self._json(404, {"errorCode": "OUTSIDE_FOCUSED_CONTRACT"})
            return

        if route["operationId"] == "getAllLogForwarders":
            self._json(200, self.server.snapshot())
            return

        if route["operationId"] == "createLogForwarder":
            created = self.server.create(body)
            if created is None:
                self._json(400, {"errorCode": "CONTRACT_BODY_MISMATCH"})
            else:
                self._json(201, created)
            return

        self._json(500, {"errorCode": "UNHANDLED_CONTRACT_OPERATION"})

    def _json(self, status: int, value: Any) -> None:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
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
    routes, contract = load_contract(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        routes,
        contract,
        args.log,
        config,
    )
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
