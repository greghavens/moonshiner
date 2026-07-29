#!/usr/bin/env python3
"""Contract-pinned loopback mock for ListAllInfraSegments."""

from __future__ import annotations

import argparse
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise RuntimeError(f"missing environment value: {name}")
    return value


def load_segments() -> list[dict[str, str]]:
    return [
        {
            "id": required_env(f"NSX_SEGMENT_{index}_ID"),
            "display_name": required_env(f"NSX_SEGMENT_{index}_NAME"),
            "resource_type": "Segment",
        }
        for index in range(1, 5)
    ]


class State:
    def __init__(
            self,
            request_log: Path,
            initial_token: str,
            refreshed_token: str,
            cursor: str,
            segments: list[dict[str, str]]) -> None:
        self.request_log = request_log
        self.initial_token = initial_token
        self.refreshed_token = refreshed_token
        self.cursor = cursor
        self.pages = [segments[:2], segments[2:]]
        self.initial_page_served = False
        self.successful_responses = 0
        self.lock = threading.Lock()

    def append(self, event: dict[str, Any]) -> None:
        encoded = (
            json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with self.lock:
            with self.request_log.open("ab", buffering=0) as output:
                output.write(encoded)
                os.fsync(output.fileno())


def headers_for_log(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for name in handler.headers:
        result.setdefault(name.lower(), []).extend(
            handler.headers.get_all(name) or []
        )
    return result


def make_handler(
        state: State,
        method: str,
        route: str,
        operation_id: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            return

        def send_json(self, status: int, body: dict[str, Any]) -> None:
            encoded = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def route_request(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length else b""
            parsed = urllib.parse.urlsplit(self.path)
            status = 404
            response: dict[str, Any] = {
                "error_code": 40401,
                "error_message": "route not in focused contract",
            }
            response_ids: list[str] = []
            response_ordinal: int | None = None

            if self.command == method and parsed.path == route:
                try:
                    pairs = urllib.parse.parse_qsl(
                        parsed.query,
                        keep_blank_values=True,
                        strict_parsing=True,
                    )
                except ValueError:
                    pairs = []
                    status = 400
                    response = {
                        "error_code": 40001,
                        "error_message": "malformed query",
                    }
                else:
                    page = -1
                    if pairs == [("page_size", "2")]:
                        page = 0
                    elif pairs == [
                        ("cursor", state.cursor),
                        ("page_size", "2"),
                    ]:
                        page = 1

                    authorization = self.headers.get("Authorization", "")
                    expected_initial = "Bearer " + state.initial_token
                    expected_refreshed = "Bearer " + state.refreshed_token
                    with state.lock:
                        if page < 0:
                            status = 400
                            response = {
                                "error_code": 40002,
                                "error_message": "unexpected focused query",
                            }
                        elif authorization == expected_initial:
                            if page == 0 and not state.initial_page_served:
                                state.initial_page_served = True
                                status = 200
                            else:
                                status = 401
                                response = {
                                    "error_code": 40101,
                                    "error_message": "access token expired",
                                }
                        elif authorization == expected_refreshed:
                            status = 200
                        else:
                            status = 401
                            response = {
                                "error_code": 40102,
                                "error_message": "access token rejected",
                            }

                        if status == 200:
                            state.successful_responses += 1
                            response_ordinal = state.successful_responses
                            elements = list(state.pages[page])
                            if response_ordinal % 2 == 1:
                                elements.reverse()
                            response_ids = [
                                str(element["id"]) for element in elements
                            ]
                            response = {
                                "result_count": 4,
                                "results": elements,
                            }
                            if page == 0:
                                response["cursor"] = state.cursor

            state.append(
                {
                    "event": "request",
                    "operationId": (
                        operation_id
                        if self.command == method and parsed.path == route
                        else None
                    ),
                    "method": self.command,
                    "raw_target": self.path,
                    "headers": headers_for_log(self),
                    "body_hex": body.hex(),
                    "response_status": status,
                    "successful_response_ordinal": response_ordinal,
                    "response_result_ids": response_ids,
                }
            )
            self.send_json(status, response)

        do_GET = route_request
        do_POST = route_request
        do_PUT = route_request
        do_PATCH = route_request
        do_DELETE = route_request

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    operations = contract["operations"]
    if set(operations) != {"ListAllInfraSegments"}:
        raise RuntimeError("focused contract operation set changed")
    operation = operations["ListAllInfraSegments"]
    method = operation["method"]
    route = contract["basePath"] + operation["path"]
    operation_id = operation["operationId"]

    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_bytes(b"")
    state = State(
        args.log,
        required_env("NSX_INITIAL_TOKEN"),
        required_env("NSX_REFRESHED_TOKEN"),
        required_env("NSX_CURSOR"),
        load_segments(),
    )
    handler = make_handler(state, method, route, operation_id)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    args.ready.parent.mkdir(parents=True, exist_ok=True)
    args.ready.write_text(
        json.dumps(
            {"host": host, "port": port},
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
