#!/usr/bin/env python3
"""Loopback-only HTTP fixture whose routes are loaded from docs/contract.json."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit


def _compile_path(template: str) -> re.Pattern[str]:
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([^{}]+)\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(fr"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(pieces) + "$")


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        contract_path: Path,
        log_path: Path,
        responses: dict[str, tuple[int, dict[str, Any]]] | None = None,
    ):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.base_path = contract["basePath"].rstrip("/")
        self.routes = [
            {
                "operationId": operation["operationId"],
                "method": operation["method"],
                "pattern": _compile_path(self.base_path + operation["path"]),
            }
            for operation in contract["operations"]
        ]
        self.log_path = log_path
        self.responses = dict(responses or {})
        self._log_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), ContractHandler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address
        return f"http://{host}:{port}"

    def append_log(self, entry: dict[str, Any]) -> None:
        with self._log_lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer

    def log_message(self, _format: str, *args: object) -> None:
        return

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        raw_path = urlsplit(self.path).path
        decoded_path = unquote(raw_path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""

        route = None
        path_values: dict[str, str] = {}
        for candidate in self.server.routes:
            match = candidate["pattern"].fullmatch(raw_path)
            if candidate["method"] == self.command and match:
                route = candidate
                path_values = {name: unquote(value) for name, value in match.groupdict().items()}
                break

        self.server.append_log(
            {
                "method": self.command,
                "requestTarget": self.path,
                "rawPath": raw_path,
                "path": decoded_path,
                "operationId": route["operationId"] if route else None,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
        )

        if route is None:
            self._json_response(404, {"code": "UNKNOWN_OPERATION", "message": "Route is not in the pinned contract"})
            return

        if route["operationId"] == "updateVcenter":
            try:
                request = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json_response(400, {"code": "INVALID_JSON", "message": "A JSON request body is required"})
                return
            status, response = self.server.responses.get(
                "updateVcenter",
                (200, {"entity_id": path_values["id"], **request}),
            )
            self._json_response(status, response)
            return

        if route["operationId"] == "enableVcenter":
            status, response = self.server.responses.get(
                "enableVcenter",
                (500, {"code": "COLLECTOR_UNAVAILABLE", "message": "The collector is offline"}),
            )
            self._json_response(status, response)
            return

        self._json_response(501, {"code": "UNCONFIGURED_OPERATION", "message": "No fixture behavior is configured"})

    def _json_response(self, status: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_contract_server(
    contract_path: Path,
    log_path: Path,
    responses: dict[str, tuple[int, dict[str, Any]]] | None = None,
) -> tuple[ContractServer, threading.Thread]:
    server = ContractServer(contract_path, log_path, responses)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
