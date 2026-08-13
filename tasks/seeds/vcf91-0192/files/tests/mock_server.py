#!/usr/bin/env python3
"""Loopback-only HTTP mock generated from the operation set in contract.json."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}


def _template_regex(template: str) -> re.Pattern[str]:
    chunks: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9_]*)\}", template):
        chunks.append(re.escape(template[cursor:match.start()]))
        chunks.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    chunks.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(chunks) + "$")


class ContractMockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, contract_path: Path, request_log_path: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        routes: list[dict[str, Any]] = []
        for path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if method.lower() in HTTP_METHODS:
                    routes.append(
                        {
                            "method": method.upper(),
                            "path": path,
                            "pattern": _template_regex(path),
                            "operationId": operation["operationId"],
                        }
                    )

        self.operation_routes = tuple(routes)
        self.request_log_path = request_log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.resources: dict[str, dict[str, Any]] = {}
        self.effect_count = 0
        self.valid_request_count = 0
        self.log_forwarder_schema = contract["components"]["schemas"]["LogForwarder"]
        request_log_path.write_text("", encoding="utf-8")
        super().__init__(("127.0.0.1", 0), ContractRequestHandler)

    def append_request(self, record: dict[str, Any]) -> None:
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.log_lock:
            with self.request_log_path.open("a", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
                stream.flush()


class ContractRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        server: ContractMockServer = self.server  # type: ignore[assignment]
        split = urllib.parse.urlsplit(self.path)
        path_matches = [
            (route, route["pattern"].fullmatch(split.path))
            for route in server.operation_routes
            if route["pattern"].fullmatch(split.path)
        ]
        if not path_matches:
            self._send_json(404, {"errorCode": "API_ERROR", "errorMessage": "unknown path"})
            return

        route, match = path_matches[0]
        if route["method"] != self.command:
            self._send_json(405, {"errorCode": "API_ERROR", "errorMessage": "method not allowed"})
            return
        if split.query or route["operationId"] != "updateLogForwarder":
            self._send_json(400, {"errorCode": "API_ERROR", "errorMessage": "invalid target"})
            return

        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else -1
        except ValueError:
            length = -1
        raw = self.rfile.read(length) if length >= 0 else b""
        try:
            raw_text = raw.decode("utf-8")
            body = json.loads(raw_text)
            self._validate_log_forwarder(body)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"errorCode": "JSON_FORMAT_ERROR", "errorMessage": str(exc)})
            return

        assert match is not None
        resource_id = urllib.parse.unquote(match.group("id"), encoding="utf-8", errors="strict")
        with server.state_lock:
            effect_applied = server.resources.get(resource_id) != body
            if effect_applied:
                server.resources[resource_id] = body
                server.effect_count += 1
            server.valid_request_count += 1
            request_number = server.valid_request_count
            response_status = 500 if request_number == 1 else 200

        header_names = sorted({name.lower() for name, _ in self.headers.raw_items()})
        headers = {name: self.headers.get_all(name) for name in header_names}
        server.append_request(
            {
                "operationId": route["operationId"],
                "method": self.command,
                "target": self.path,
                "requestVersion": self.request_version,
                "headers": headers,
                "rawBody": raw_text,
                "body": body,
                "effectApplied": effect_applied,
                "responseStatus": response_status,
            }
        )

        if response_status == 500:
            self._send_json(
                500,
                {
                    "errorCode": "INTERNAL_SERVER_ERROR",
                    "errorMessage": "ambiguous failure after applying replacement",
                },
            )
            return

        response = {"id": resource_id}
        response.update(body)
        self._send_json(200, response)

    def _validate_log_forwarder(self, body: Any) -> None:
        server: ContractMockServer = self.server  # type: ignore[assignment]
        if not isinstance(body, dict):
            raise ValueError("LogForwarder must be an object")
        properties = server.log_forwarder_schema["properties"]
        unknown = set(body) - set(properties)
        if unknown:
            raise ValueError(f"unknown LogForwarder properties: {sorted(unknown)}")
        if "id" in body:
            raise ValueError("read-only id is not accepted in a request")
        if "protocol" in body and body["protocol"] not in properties["protocol"]["enum"]:
            raise ValueError("invalid protocol")
        if (
            "transportProtocol" in body
            and body["transportProtocol"] not in properties["transportProtocol"]["enum"]
        ):
            raise ValueError("invalid transportProtocol")

    def _send_json(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
            self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


def start_contract_mock(contract_path: Path, request_log_path: Path) -> ContractMockServer:
    """Create, but do not start, the contract-pinned loopback server."""
    return ContractMockServer(contract_path, request_log_path)
