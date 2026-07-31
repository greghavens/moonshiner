#!/usr/bin/env python3
"""Contract-pinned loopback service for vcf91-0167."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


EXPECTED_OPERATION_IDS = {"getAllAgentGroupConfig"}


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
        self.calls = 0

    def append_log(self, item: dict[str, Any]) -> int:
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":"))
        with self.lock:
            call_index = self.calls
            self.calls += 1
            durable_write(self.log_path, encoded + "\n", "a")
        return call_index


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

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        length = int(raw_length) if raw_length else 0
        return self.rfile.read(length) if length else b""

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
        raw = self._read_body()
        route = self.server.routes.get((method, split.path))
        pairs, grouped = self._headers()
        call_index = self.server.append_log(
            {
                "method": method,
                "raw_target": self.path,
                "path": split.path,
                "query": split.query,
                "header_pairs": pairs,
                "headers": grouped,
                "body_raw": raw.decode("utf-8", errors="replace"),
                "body_bytes": len(raw),
                "operationId": route["operationId"] if route else None,
            }
        )
        if route is None:
            self._json(404, {"errorCode": "OUTSIDE_FOCUSED_CONTRACT"})
            return

        if route["operationId"] != "getAllAgentGroupConfig":
            self._json(500, {"errorCode": "UNHANDLED_CONTRACT_OPERATION"})
            return

        query = parse_qs(split.query, keep_blank_values=True)
        try:
            page_number = int(query["page"][0])
            page_size = int(query["size"][0])
        except (KeyError, ValueError, IndexError):
            self._json(400, {"errorCode": "MALFORMED_PAGEABLE"})
            return
        if page_number < 0 or page_size < 1:
            self._json(400, {"errorCode": "INVALID_PAGEABLE"})
            return

        layouts = self.server.config["layouts"]
        page_count = len(layouts[0])
        invocation = min(call_index // page_count, len(layouts) - 1)
        layout = layouts[invocation]
        if page_number >= len(layout):
            self._json(400, {"errorCode": "PAGE_OUT_OF_RANGE"})
            return

        groups = self.server.config["groups"]
        content = [groups[index] for index in layout[page_number]]
        total_elements = len(groups)
        response_page = {
            "content": content,
            "empty": not content,
            "first": page_number == 0,
            "last": page_number == len(layout) - 1,
            "number": page_number,
            "numberOfElements": len(content),
            "pageable": {
                "offset": page_number * page_size,
                "pageNumber": page_number,
                "pageSize": page_size,
                "paged": True,
                "sort": {
                    "empty": True,
                    "sorted": False,
                    "unsorted": True,
                },
                "unpaged": False,
            },
            "size": page_size,
            "sort": {
                "empty": True,
                "sorted": False,
                "unsorted": True,
            },
            "totalElements": total_elements,
            "totalPages": len(layout),
        }
        self._json(200, [response_page])

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
    if (
        len(config["groups"]) != 5
        or len(config["layouts"]) != 2
        or any(len(layout) != 3 for layout in config["layouts"])
    ):
        raise ValueError("runtime collection fixture has unexpected shape")
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
