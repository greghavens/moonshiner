#!/usr/bin/env python3
"""Contract-pinned loopback Log Management service for vcf91-0171."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATION_IDS = [
    "createAgentSecret",
    "createAgentSession",
    "revokeAgentSecret",
]


def durable_write(path: Path, text: str, mode: str) -> None:
    with path.open(mode, encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    template: str
    pattern: re.Pattern[str]


def route_pattern(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    position = 0
    for match in re.finditer(r"\{([^{}]+)\}", template):
        parts.append(re.escape(template[position : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        position = match.end()
    parts.append(re.escape(template[position:]))
    return re.compile("^" + "".join(parts) + "$")


def load_routes(path: Path) -> list[Route]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract["operations"]
    operation_ids = [item["operationId"] for item in operations]
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise ValueError("unexpected focused operationId set or order")

    routes: list[Route] = []
    seen: set[tuple[str, str]] = set()
    for item in operations:
        key = (item["method"], item["path"])
        if key in seen:
            raise ValueError("duplicate focused method and path")
        seen.add(key)
        routes.append(
            Route(
                operation_id=item["operationId"],
                method=item["method"],
                template=item["path"],
                pattern=route_pattern(item["path"]),
            )
        )
    return routes


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[Route],
        log_path: Path,
        config: dict[str, Any],
    ) -> None:
        super().__init__(address, Handler)
        self.routes = routes
        self.log_path = log_path
        self.config = config
        self.lock = threading.Lock()

    def match_route(
        self, method: str, path: str
    ) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if method != route.method:
                continue
            match = route.pattern.fullmatch(path)
            if match is not None:
                return route, {
                    key: unquote(value)
                    for key, value in match.groupdict().items()
                }
        return None, {}

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
            lowered = key.casefold()
            pairs.append([lowered, value])
            grouped.setdefault(lowered, []).append(value)
        return pairs, grouped

    def _handle(self, method: str) -> None:
        split = urlsplit(self.path)
        raw, body = self._read_body()
        route, path_parameters = self.server.match_route(method, split.path)
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
                "operationId": route.operation_id if route else None,
                "path_parameters": path_parameters,
            }
        )
        if route is None:
            self._json(404, {"errorCode": "OUTSIDE_FOCUSED_CONTRACT"})
            return

        config = self.server.config
        if route.operation_id == "createAgentSecret":
            create_status = int(config.get("create_http_status", 201))
            if create_status != 201:
                headers = {}
                redirect_target = config.get("create_redirect_target")
                if redirect_target is not None:
                    headers["Location"] = str(redirect_target)
                self._json(
                    create_status,
                    {"errorCode": "CREATE_FAILED"},
                    headers=headers,
                )
                return
            create_response = {
                "id": config["created_id"],
                "name": config["new_name"],
                "secret": config["create_secret"],
                "status": config["create_status"],
            }
            if config.get("create_invalid_fields"):
                create_response["id"] = ""
                create_response["name"] = config["new_name"] + "-wrong"
                create_response["status"] = ""
            self._json(
                201,
                create_response,
            )
            return

        if route.operation_id == "createAgentSession":
            exchange_status = int(config.get("exchange_http_status", 200))
            if exchange_status != 200:
                self._json(
                    exchange_status,
                    {"errorCode": "EXCHANGE_FAILED"},
                )
                return
            exchange_response = {
                "access_token": config["new_access_token"],
                "name": config["new_name"],
                "new_secret": config["new_secret"],
                "ttl": config["response_ttl"],
            }
            if config.get("exchange_invalid_fields"):
                exchange_response["name"] = config["new_name"] + "-wrong"
                exchange_response["ttl"] = str(config["response_ttl"])
            self._json(
                200,
                exchange_response,
            )
            return

        if route.operation_id == "revokeAgentSecret":
            revoke_response = {
                "id": config["revoked_id"],
                "name": path_parameters["secretName"],
                "status": config["revoke_status"],
            }
            if config.get("revoke_invalid_fields"):
                revoke_response["id"] = ""
                revoke_response["name"] = config["old_name"] + "-wrong"
                revoke_response["status"] = ""
            self._json(
                200,
                revoke_response,
            )
            return

        self._json(500, {"errorCode": "UNHANDLED_CONTRACT_OPERATION"})

    def _json(
        self,
        status: int,
        value: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self._raw(status, raw, headers=headers)

    def _raw(
        self,
        status: int,
        raw: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
