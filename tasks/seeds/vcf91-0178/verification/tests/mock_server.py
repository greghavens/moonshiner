#!/usr/bin/env python3
"""Contract-pinned loopback Log Management service for vcf91-0178."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


EXPECTED_OPERATION_IDS = {
    "testLogForwarderConnection",
    "createLogForwarder",
}


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def compile_path_template(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{[^{}]+\}", template):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(r"[^/]+")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


def load_routes(path: Path) -> list[dict[str, Any]]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if {item["operationId"] for item in operations} != EXPECTED_OPERATION_IDS:
        raise ValueError("unexpected focused operationId set")

    routes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()
    for item in operations:
        operation_id = item["operationId"]
        route_key = (item["method"], item["path"])
        if operation_id in seen_ids or route_key in seen_routes:
            raise ValueError("duplicate focused operation or route")
        seen_ids.add(operation_id)
        seen_routes.add(route_key)
        routes.append(
            {
                **item,
                "pathPattern": compile_path_template(item["path"]),
            }
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[dict[str, Any]],
        log_path: Path,
        state_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.state_path = state_path
        self.config = config
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {
            "createCount": 0,
            "forwarders": [],
        }
        self._write_state()

    def find_route(self, method: str, path: str) -> dict[str, Any] | None:
        for route in self.routes:
            if (
                route["method"] == method
                and route["pathPattern"].fullmatch(path)
            ):
                return route
        return None

    def append_log(self, item: dict[str, Any]) -> None:
        encoded = json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.lock:
            durable_write(self.log_path, encoded + "\n", "a")

    def commit_create(self, body: Any) -> None:
        with self.lock:
            self.state["createCount"] += 1
            self.state["forwarders"].append(body)
            self._write_state()

    def _write_state(self) -> None:
        encoded = json.dumps(
            self.state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        durable_write(self.state_path, encoded + "\n", "w")


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
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            return b"", {"_invalidContentLength": raw_length}
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return raw, None
        try:
            return raw, json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return raw, {"_malformed": raw.decode("utf-8", errors="replace")}

    def _headers(self) -> tuple[list[list[str]], dict[str, list[str]]]:
        pairs: list[list[str]] = []
        grouped: dict[str, list[str]] = {}
        for key, value in self.headers.raw_items():
            lowered = key.lower()
            pairs.append([lowered, value])
            grouped.setdefault(lowered, []).append(value)
        return pairs, grouped

    def _response_for(
        self,
        operation_id: str | None,
        body: Any,
    ) -> tuple[int, Any, bool]:
        config = self.server.config
        if operation_id == "testLogForwarderConnection":
            if config["scenario"] == "precheck_failure":
                return (
                    502,
                    {
                        "errorCode": "CERTIFICATE_NOT_TRUSTED",
                        "errorMessage": config["sensitive_error"],
                    },
                    False,
                )
            return 200, None, False

        if operation_id == "createLogForwarder":
            self.server.commit_create(body)
            response = {
                "id": config["created_id"],
                "enabled": config["enabled"],
                "host": config["host"],
                "name": config["name"],
                "port": config["port"],
                "protocol": config["protocol"],
                "sslEnabled": config["ssl_enabled"],
                "transportProtocol": config["transport_protocol"],
            }
            if config["scenario"] == "bad_create_response":
                response["host"] = config["host"] + ".mismatch"
            return 201, response, True

        return (
            404,
            {
                "errorCode": "OUTSIDE_FOCUSED_CONTRACT",
                "errorMessage": "route is not in the focused contract",
            },
            False,
        )

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw, body = self._read_body()
        route = self.server.find_route(method, split.path)
        operation_id = route["operationId"] if route else None
        status, response, effect_committed = self._response_for(
            operation_id,
            body,
        )
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
                "operationId": operation_id,
                "response_status": status,
                "effect_committed": effect_committed,
            }
        )
        if response is None:
            self._empty(status)
        else:
            self._json(status, response)

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def _json(self, status: int, value: Any) -> None:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
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
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    routes = load_routes(args.contract)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        routes,
        args.log,
        args.state,
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
