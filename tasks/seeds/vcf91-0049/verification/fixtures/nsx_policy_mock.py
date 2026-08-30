#!/usr/bin/env python3
"""Loopback NSX Policy mock whose routes are loaded from the reduced contract."""

from __future__ import annotations

import argparse
import json
import re
import signal
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    template: str
    pattern: re.Pattern[str]


def compile_route(operation: dict[str, Any], base_path: str) -> Route:
    template = base_path.rstrip("/") + operation["path"]
    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([^{}]+)\}", template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(f"(?P<{match.group(1).replace('-', '_')}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return Route(
        operation_id=operation["operationId"],
        method=operation["method"].upper(),
        template=template,
        pattern=re.compile("^" + "".join(pieces) + "$"),
    )


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        routes: list[Route],
        request_log: Path,
    ) -> None:
        super().__init__(address, ContractHandler)
        self.routes = routes
        self.request_log = request_log
        self.state_lock = threading.Lock()
        self.intent_polls: dict[str, int] = {}
        self.sequence = 0

    def identify(self, method: str, path: str) -> tuple[Route | None, dict[str, str]]:
        for route in self.routes:
            if route.method != method:
                continue
            matched = route.pattern.fullmatch(path)
            if matched:
                return route, matched.groupdict()
        return None, {}

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.state_lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(entry, separators=(",", ":"), sort_keys=True))
                stream.write("\n")


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - explicit rejection for non-contract methods
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - explicit rejection for non-contract methods
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - explicit rejection for non-contract methods
        self._dispatch()

    def _read_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return b""
        try:
            length = int(raw_length)
        except ValueError:
            return b""
        return self.rfile.read(max(length, 0))

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        route, path_values = self.server.identify(self.command, split.path)
        body = self._read_body()
        entry: dict[str, Any] = {
            "operationId": route.operation_id if route else None,
            "method": self.command,
            "raw_target": self.path,
            "path": split.path,
            "query": parse_qs(split.query, keep_blank_values=True),
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body_utf8": body.decode("utf-8", errors="strict") if body else "",
            "response_state": None,
        }

        if route is None:
            self.server.append_log(entry)
            self._json_response(404, {"error": "operation is not in the pinned contract"})
            return

        if route.operation_id == "PatchInfraSegment":
            self._patch_segment(entry, path_values, body)
            return

        if route.operation_id == "ReadIntentStatus":
            self._read_intent_status(entry, split.query, body)
            return

        self.server.append_log(entry)
        self._json_response(501, {"error": "contract operation has no mock behavior"})

    def _patch_segment(
        self,
        entry: dict[str, Any],
        path_values: dict[str, str],
        body: bytes,
    ) -> None:
        if self.headers.get_content_type() != "application/json":
            self.server.append_log(entry)
            self._json_response(415, {"error": "application/json required"})
            return
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.server.append_log(entry)
            self._json_response(400, {"error": "invalid JSON"})
            return
        if not isinstance(payload, dict):
            self.server.append_log(entry)
            self._json_response(400, {"error": "segment body must be an object"})
            return

        segment_id = unquote(path_values["segment_id"])
        intent_path = f"/infra/segments/{segment_id}"
        with self.server.state_lock:
            self.server.intent_polls[intent_path] = 0
        self.server.append_log(entry)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_intent_status(
        self,
        entry: dict[str, Any],
        raw_query: str,
        body: bytes,
    ) -> None:
        if body:
            self.server.append_log(entry)
            self._json_response(400, {"error": "GET request body is not allowed"})
            return
        query = parse_qs(raw_query, keep_blank_values=True)
        intent_values = query.get("intent_path", [])
        if len(intent_values) != 1 or not intent_values[0]:
            self.server.append_log(entry)
            self._json_response(400, {"error": "one intent_path is required"})
            return
        intent_path = intent_values[0]
        with self.server.state_lock:
            if intent_path not in self.server.intent_polls:
                known = False
                poll_count = 0
            else:
                known = True
                self.server.intent_polls[intent_path] += 1
                poll_count = self.server.intent_polls[intent_path]
        if not known:
            self.server.append_log(entry)
            self._json_response(404, {"error": "intent has not been submitted"})
            return

        state = "SUCCESS" if poll_count >= 3 else "IN_PROGRESS"
        entry["response_state"] = state
        self.server.append_log(entry)
        self._json_response(
            200,
            {
                "intent_path": intent_path,
                "consolidated_status": {"consolidated_status": state},
                "publish_status": "REALIZED" if state == "SUCCESS" else "UNREALIZED",
            },
        )

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    routes = [
        compile_route(operation, contract["basePath"])
        for operation in contract["operations"]
    ]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", args.port), routes, args.log)
    base_uri = f"http://127.0.0.1:{server.server_address[1]}{contract['basePath']}"
    args.ready_file.write_text(base_uri, encoding="utf-8")

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
