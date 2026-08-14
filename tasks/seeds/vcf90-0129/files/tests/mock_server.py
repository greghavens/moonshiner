#!/usr/bin/env python3
"""Contract-pinned loopback service for the acceptance verifier."""

from __future__ import annotations

import argparse
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        contract_path: Path,
        log_path: Path,
        drop_first_create_response: bool = True,
        reject_create: bool = False,
        first_page_contains_applications: bool = False,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        base_path = contract["servers"][0]["url"].rstrip("/")
        routes: dict[tuple[str, str], dict[str, Any]] = {}
        for relative_path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    routes[(method.upper(), f"{base_path}{relative_path}")] = operation

        super().__init__(address, ContractHandler)
        self.routes = routes
        self.contract = contract
        self.cursor = contract["components"]["schemas"]["PagedListResponse"]["properties"]["cursor"]["example"]
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.applications: list[dict[str, str]] = []
        self.drop_first_create_response = drop_first_create_response
        self.reject_create = reject_create
        self.first_page_contains_applications = first_page_contains_applications

    def append_log(self, record: dict[str, Any]) -> None:
        with self.log_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        parsed = urlsplit(self.path)
        operation = self.server.routes.get((method, parsed.path))
        self.server.append_log(
            {
                "operationId": operation.get("operationId") if operation else None,
                "method": method,
                "target": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
        )

        if operation is None:
            self._write_json(404, {"message": "operation is not in contract"})
            return

        if method == "GET":
            query = parse_qs(parsed.query, keep_blank_values=True)
            with self.server.state_lock:
                applications = [dict(application) for application in self.server.applications]
            if not query:
                if self.server.first_page_contains_applications:
                    self._write_json(
                        200,
                        {"results": applications, "total_count": len(applications)},
                    )
                    return
                self._write_json(
                    200,
                    {"results": [], "cursor": self.server.cursor, "total_count": len(applications)},
                )
                return
            if query == {"cursor": [self.server.cursor]}:
                self._write_json(200, {"results": applications, "total_count": len(applications)})
                return
            self._write_json(400, {"message": "unsupported query"})
            return

        if method == "POST":
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write_json(400, {"message": "invalid JSON"})
                return

            schema_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            schema_name = schema_ref.rsplit("/", 1)[-1]
            allowed = set(self._contract_schema(schema_name)["properties"])
            required = set(self._contract_schema(schema_name).get("required", []))
            if not isinstance(payload, dict) or set(payload) - allowed or required - set(payload):
                self._write_json(400, {"message": "request does not match contract"})
                return

            if self.server.reject_create:
                self._write_json(500, {"message": "fixture rejected create"})
                return

            with self.server.state_lock:
                application = {
                    "entity_id": f"application-{len(self.server.applications) + 1:04d}",
                    "entity_type": "Application",
                    "entity_name": payload["name"],
                }
                self.server.applications.append(application)
                should_drop = self.server.drop_first_create_response
                self.server.drop_first_create_response = False

            if should_drop:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                return

            self._write_json(
                201,
                {
                    "entity_id": application["entity_id"],
                    "entity_type": application["entity_type"],
                    "name": application["entity_name"],
                },
            )
            return

        self._write_json(405, {"message": "unsupported contract operation"})

    def _contract_schema(self, name: str) -> dict[str, Any]:
        return self.server.contract["components"]["schemas"][name]

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def create_server(
    contract_path: Path,
    log_path: Path,
    port: int = 0,
    drop_first_create_response: bool = True,
    reject_create: bool = False,
    first_page_contains_applications: bool = False,
) -> ContractServer:
    server = ContractServer(
        ("127.0.0.1", port),
        contract_path,
        log_path,
        drop_first_create_response,
        reject_create,
        first_page_contains_applications,
    )
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = create_server(args.contract, args.log, args.port)
    print(server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
