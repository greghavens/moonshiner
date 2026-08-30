#!/usr/bin/env python3
"""Loopback-only mock for the two operations in docs/contract.json."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


AUTH_PATH = "/api/ni/auth/token"
VCENTER_PATH = "/api/ni/data-sources/vcenters"


class MockState:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.lock = threading.Lock()
        self.auth_count = 0
        self.initial_token_successes = 0
        self.created_nicknames: set[str] = set()
        self.next_entity_id = 1
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True, separators=(",", ":")))
                handle.write("\n")


class VcfNetworksMock(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], log_path: Path) -> None:
        super().__init__(address, VcfNetworksHandler)
        self.state = MockState(log_path)


class VcfNetworksHandler(BaseHTTPRequestHandler):
    server: VcfNetworksMock
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _send(self, status: int, body: dict[str, Any] | None = None) -> None:
        encoded = b"" if body is None else json.dumps(
            body, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def _read_body(self) -> tuple[bytes, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            return raw, json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw, None

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        raw, body = self._read_body()
        target = urlsplit(self.path)
        self.server.state.record(
            {
                "method": "POST",
                "path": target.path,
                "query": target.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "raw_body": raw.decode("utf-8", errors="replace"),
                "body": body,
            }
        )

        if target.query:
            self._send(400, {"error": "query parameters are not in this contract"})
            return
        if target.path == AUTH_PATH:
            self._create_token(body)
            return
        if target.path == VCENTER_PATH:
            self._add_vcenter(body)
            return
        self._send(404, {"error": "operation is not in the pinned contract"})

    def _create_token(self, body: Any) -> None:
        if not isinstance(body, dict):
            self._send(400, {"error": "JSON object required"})
            return
        with self.server.state.lock:
            self.server.state.auth_count += 1
            token = (
                "token-expiring"
                if self.server.state.auth_count == 1
                else "token-refreshed"
            )
        self._send(200, {"token": token, "expiry": 4102444800000})

    def _add_vcenter(self, body: Any) -> None:
        if not isinstance(body, dict):
            self._send(400, {"error": "JSON object required"})
            return
        authorization = self.headers.get("Authorization")
        if authorization == "NetworkInsight token-expiring":
            with self.server.state.lock:
                if self.server.state.initial_token_successes:
                    self._send(401)
                    return
                self.server.state.initial_token_successes += 1
        elif authorization != "NetworkInsight token-refreshed":
            self._send(401)
            return

        nickname = body.get("nickname")
        with self.server.state.lock:
            if nickname in self.server.state.created_nicknames:
                self._send(409, {"error": "duplicate vCenter"})
                return
            self.server.state.created_nicknames.add(nickname)
            entity_id = f"vc-{self.server.state.next_entity_id}"
            self.server.state.next_entity_id += 1
        response = dict(body)
        response.update({"entity_id": entity_id, "entity_type": "VCenterDataSource"})
        self._send(201, response)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.server.state.record(
            {
                "method": "GET",
                "path": urlsplit(self.path).path,
                "query": urlsplit(self.path).query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "raw_body": "",
                "body": None,
            }
        )
        self._send(404, {"error": "operation is not in the pinned contract"})


def create_server(log_path: Path) -> VcfNetworksMock:
    return VcfNetworksMock(("127.0.0.1", 0), log_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    server = create_server(args.log)
    print(
        json.dumps({"base_uri": f"http://127.0.0.1:{server.server_port}"}),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
