#!/usr/bin/env python3
"""Contract-pinned loopback service for vcf91-0165."""

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
    "createAgentSecret",
    "listAgentSecrets",
}


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def load_routes(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
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
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: dict[tuple[str, str], dict[str, Any]],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()
        self.poll_calls = 0

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")

    def next_status(self) -> str:
        with self.lock:
            self.poll_calls += 1
            index = min(
                self.poll_calls,
                len(self.config["status_sequence"]),
            ) - 1
        return self.config["status_sequence"][index]


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

        config = self.server.config
        if route["operationId"] == "createAgentSecret":
            self._json(
                201,
                {
                    "id": config["secret_id"],
                    "name": config["secret_name"],
                    "secret": config["one_time_secret"],
                    "status": config["create_status"],
                },
            )
            return

        if route["operationId"] == "listAgentSecrets":
            self._json(
                200,
                {
                    "id": config["secret_id"],
                    "name": config["secret_name"],
                    "status": self.server.next_status(),
                },
            )
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
    routes = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not config["status_sequence"]:
        raise ValueError("status sequence must not be empty")
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), routes, args.log, config)
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
