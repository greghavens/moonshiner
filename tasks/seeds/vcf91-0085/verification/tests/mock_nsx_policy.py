#!/usr/bin/env python3
"""Contract-pinned loopback service for the protected Java harness."""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LOG_LOCK = threading.Lock()


def append_event(path: Path, event: dict[str, Any]) -> None:
    encoded = json.dumps(
        event, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    with LOG_LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def basic_authorization(username: str, password: str) -> str:
    import base64

    token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")
    return "Basic " + token


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        routes: dict[tuple[str, str], dict[str, Any]],
        query_names: set[str],
        log_path: Path,
        retired_path: Path,
        authorizations: dict[str, str],
        timeout_cursor: str,
        release_cursor: str,
        error_cursor: str,
    ) -> None:
        super().__init__(address, handler)
        self.routes = routes
        self.query_names = query_names
        self.log_path = log_path
        self.retired_path = retired_path
        self.authorizations = authorizations
        self.timeout_cursor = timeout_cursor
        self.release_cursor = release_cursor
        self.error_cursor = error_cursor
        self.central_new_completed = threading.Event()
        self.timeout_new_completed = threading.Event()
        self.state_lock = threading.Lock()
        self.central_old_claimed = False
        self.timeout_old_claimed = False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler hook
        self._handle_contract_request()

    def do_POST(self) -> None:  # noqa: N802
        self._not_found()

    def do_PUT(self) -> None:  # noqa: N802
        self._not_found()

    def do_PATCH(self) -> None:  # noqa: N802
        self._not_found()

    def do_DELETE(self) -> None:  # noqa: N802
        self._not_found()

    def _not_found(self) -> None:
        self._send_json(404, b'{"error_message":"operation not declared"}')

    def _headers_for_log(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for name, value in self.headers.items():
            result.setdefault(name.lower(), []).append(value)
        return result

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            return b""
        return self.rfile.read(max(length, 0))

    def _handle_contract_request(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        operation = self.server.routes.get((self.command, parsed.path))
        if operation is None:
            self._not_found()
            return

        body = self._read_body()
        operation_id = operation["operationId"]
        append_event(
            self.server.log_path,
            {
                "body_hex": body.hex(),
                "event": "request",
                "headers": self._headers_for_log(),
                "method": self.command,
                "operationId": operation_id,
                "raw_target": self.path,
            },
        )

        try:
            pairs = urllib.parse.parse_qsl(
                parsed.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError:
            self._respond(
                operation_id,
                400,
                b'{"error_message":"malformed query"}',
            )
            return
        if any(name not in self.server.query_names for name, _ in pairs):
            self._respond(
                operation_id,
                400,
                b'{"error_message":"undeclared query field"}',
            )
            return

        query = dict(pairs)
        authorization = self.headers.get("Authorization")
        role = next(
            (
                name
                for name, expected in self.server.authorizations.items()
                if authorization == expected
            ),
            None,
        )
        if role is None or (
            role == "central_old" and self.server.retired_path.exists()
        ):
            self._respond(
                operation_id,
                401,
                b'{"error_message":"credential rejected"}',
            )
            return

        central_gate = False
        timeout_gate = False
        with self.server.state_lock:
            if (
                role == "central_old"
                and parsed.query == ""
                and not self.server.central_old_claimed
            ):
                self.server.central_old_claimed = True
                central_gate = True
            elif (
                role == "timeout_old"
                and query.get("cursor") == self.server.timeout_cursor
                and not self.server.timeout_old_claimed
            ):
                self.server.timeout_old_claimed = True
                timeout_gate = True

        if central_gate:
            if not self.server.central_new_completed.wait(8):
                self._respond(
                    operation_id,
                    504,
                    b'{"error_message":"new generation never arrived"}',
                )
                return
            if self.server.retired_path.exists():
                self._respond(
                    operation_id,
                    401,
                    b'{"error_message":"old credential retired too early"}',
                )
                return
            self._respond(operation_id, 200, self._page_body("central-old"))
            return

        if timeout_gate:
            if not self.server.timeout_new_completed.wait(8):
                self._respond(
                    operation_id,
                    504,
                    b'{"error_message":"timeout generation never released"}',
                )
                return
            self._respond(operation_id, 200, self._page_body("timeout-old"))
            return

        if query.get("cursor") == self.server.error_cursor:
            self._respond(
                operation_id,
                503,
                b'{"error_code":50301,"error_message":"fixture unavailable"}',
            )
            return

        if role == "central_new":
            self._respond(operation_id, 200, self._page_body("central-new"))
            self.server.central_new_completed.set()
            return

        if (
            role == "timeout_new"
            and query.get("cursor") == self.server.release_cursor
        ):
            self._respond(operation_id, 200, self._page_body("timeout-new"))
            self.server.timeout_new_completed.set()
            return

        self._respond(operation_id, 200, self._page_body(role))

    @staticmethod
    def _page_body(marker: str) -> bytes:
        return json.dumps(
            {
                "result_count": 1,
                "results": [
                    {
                        "display_name": marker,
                        "id": marker,
                        "resource_type": "Tier1",
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def _respond(self, operation_id: str, status: int, body: bytes) -> None:
        append_event(
            self.server.log_path,
            {
                "event": "response",
                "operationId": operation_id,
                "raw_target": self.path,
                "status": status,
            },
        )
        self._send_json(status, body)

    def _send_json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--retired", required=True, type=Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    base_path = contract["basePath"]
    operations = contract["operations"]
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    query_names: set[str] = set()
    for operation in operations.values():
        routes[
            (operation["method"], base_path + operation["path"])
        ] = operation
        query_names.update(
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter.get("in") == "query"
        )

    if set(operations) != {"ListTier1"} or len(routes) != 1:
        raise SystemExit("contract must declare only ListTier1")

    authorizations = {
        "central_old": basic_authorization(
            required_environment("NSX_OLD_USERNAME"),
            required_environment("NSX_OLD_PASSWORD"),
        ),
        "central_new": basic_authorization(
            required_environment("NSX_NEW_USERNAME"),
            required_environment("NSX_NEW_PASSWORD"),
        ),
        "timeout_old": basic_authorization(
            required_environment("NSX_TIMEOUT_OLD_USERNAME"),
            required_environment("NSX_TIMEOUT_OLD_PASSWORD"),
        ),
        "timeout_new": basic_authorization(
            required_environment("NSX_TIMEOUT_NEW_USERNAME"),
            required_environment("NSX_TIMEOUT_NEW_PASSWORD"),
        ),
    }
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", 0),
        Handler,
        routes=routes,
        query_names=query_names,
        log_path=args.log,
        retired_path=args.retired,
        authorizations=authorizations,
        timeout_cursor=required_environment("NSX_TIMEOUT_CURSOR"),
        release_cursor=required_environment("NSX_RELEASE_CURSOR"),
        error_cursor=required_environment("NSX_ERROR_CURSOR"),
    )

    temporary_ready = args.ready.with_suffix(".tmp")
    with temporary_ready.open("w", encoding="utf-8") as stream:
        json.dump({"host": "127.0.0.1", "port": server.server_port}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary_ready, args.ready)
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
