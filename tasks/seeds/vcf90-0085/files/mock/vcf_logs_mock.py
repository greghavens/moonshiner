#!/usr/bin/env python3
"""Loopback mock restricted to the operations named by docs/contract.json."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def load_routes(contract_path: Path) -> dict[tuple[str, str], str]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    base = contract["servers"][0]["url"].rstrip("/")
    routes: dict[tuple[str, str], str] = {}
    for path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            pattern = re.sub(r"\{[^/{}]+\}", r"([^/]+)", base + path)
            routes[(method.upper(), f"^{pattern}$")] = operation["operationId"]
    return routes


def make_handler(routes: dict[tuple[str, str], str], request_log: Path):
    class ContractHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        put_count = 0
        count_lock = threading.Lock()

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_json(self, status: int, value: object, record: dict[str, object]) -> None:
            record["responseStatus"] = status
            record["responseBody"] = value
            with request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")

            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _dispatch(self) -> None:
            parsed = urlsplit(self.path)
            operation_id = None
            for (method, pattern), candidate in routes.items():
                if self.command == method and re.fullmatch(pattern, parsed.path):
                    operation_id = candidate
                    break

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw_body) if raw_body else None
            except json.JSONDecodeError:
                body = None

            record = {
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "operationId": operation_id,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body,
            }
            if operation_id is None:
                self._send_json(
                    404,
                    {"errorMessage": "Operation is not in the pinned contract."},
                    record,
                )
                return

            if operation_id == "POST_sessions":
                self._send_json(
                    200,
                    {
                        "userId": "e34ef2dc-39ac-491d-8451-89c84f702b82",
                        "sessionId": "fixture-session-token",
                        "ttl": 1800,
                    },
                    record,
                )
                return

            with self.count_lock:
                type(self).put_count += 1
                put_number = type(self).put_count
            if put_number == 1:
                response = dict(body or {})
                response.update(
                    {
                        "name": "primary",
                        "diskCacheSize": 1000000000,
                        "tags": {},
                        "filter": "",
                        "forwardComplementaryFields": False,
                        "id": parsed.path.rsplit("/", 1)[-1],
                    }
                )
                self._send_json(200, response, record)
                return

            self._send_json(
                500,
                {
                    "errorMessage": "The destination rejected the staged change.",
                    "errorCode": "FIELD_ERROR",
                },
                record,
            )

        do_POST = _dispatch
        do_PUT = _dispatch
        do_GET = _dispatch
        do_PATCH = _dispatch
        do_DELETE = _dispatch

    return ContractHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    args.request_log.write_text("", encoding="utf-8")
    routes = load_routes(args.contract)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(routes, args.request_log))
    args.ready_file.write_text(str(server.server_port), encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
