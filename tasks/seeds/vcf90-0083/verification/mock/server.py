#!/usr/bin/env python3
"""Loopback-only mock for the pinned GET_events-+path contract."""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
EVENTS_PATH = Path(__file__).with_name("events.json")
EVENT_PATH = re.compile(
    r"^/api/v2/events/timestamp/%3E%3D(-?\d+)/timestamp/%3C%3D(-?\d+)$",
    re.IGNORECASE,
)


class ContractServer(ThreadingHTTPServer):
    def __init__(
        self, address: tuple[str, int], request_log: Path, force_incomplete: bool
    ):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        operation = contract["operations"]["GET_events-+path"]
        if operation["method"] != "GET" or operation["path"] != "/events/{+path}":
            raise RuntimeError("mock contract does not contain the pinned events operation")
        self.request_log = request_log
        self.force_incomplete = force_incomplete
        self.events = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))
        super().__init__(address, ContractHandler)


class ContractHandler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _record(self, body: bytes) -> None:
        record = {
            "method": self.command,
            "target": self.path,
            "authorization": self.headers.get("Authorization"),
            "contentType": self.headers.get("Content-Type"),
            "contentLength": self.headers.get("Content-Length"),
            "bodyLength": len(body),
        }
        with self.server.request_log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._record(b"")
        split = urlsplit(self.path)
        match = EVENT_PATH.fullmatch(split.path)
        if match is None:
            self._json(404, {"errorMessage": "operation not served"})
            return

        if self.headers.get("Authorization") != "Bearer fixture-session":
            self._json(401, "Invalid session ID")
            return

        pairs = parse_qsl(split.query, keep_blank_values=True)
        allowed = {
            "limit",
            "timeout",
            "view",
            "content-pack-fields",
            "order-by-direction",
        }
        if any(name not in allowed or value == "" for name, value in pairs):
            self._json(400, {"errorMessage": "invalid query parameters"})
            return

        values: dict[str, list[str]] = {}
        for name, value in pairs:
            values.setdefault(name, []).append(value)
        if len(values.get("limit", [])) != 1:
            self._json(400, {"errorMessage": "limit must occur exactly once"})
            return
        if len(values.get("order-by-direction", [])) != 1:
            self._json(400, {"errorMessage": "sort direction must occur exactly once"})
            return

        try:
            limit = int(values["limit"][0])
        except ValueError:
            self._json(400, {"errorMessage": "invalid limit"})
            return
        if limit < 1:
            self._json(400, {"errorMessage": "invalid limit"})
            return

        direction = values["order-by-direction"][0]
        if direction not in {"ASC", "DESC"}:
            self._json(400, {"errorMessage": "invalid sort direction"})
            return

        lower, upper = (int(value) for value in match.groups())
        matches = [
            event
            for event in self.server.events
            if lower <= int(event["timestamp"]) <= upper
        ]
        reverse = direction == "DESC"
        matches.sort(key=lambda event: (int(event["timestamp"]), event["text"]), reverse=reverse)
        page = matches[:limit]
        view = values.get("view", ["DEFAULT"])[0]
        response_key = "results" if view == "SIMPLE" else "events"
        self._json(
            200,
            {
                "complete": not self.server.force_incomplete,
                "duration": 1,
                response_key: page,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self._record(body)
        self._json(405, {"errorMessage": "operation not served"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--force-incomplete", action="store_true")
    args = parser.parse_args()

    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(
        ("127.0.0.1", args.port), args.request_log, args.force_incomplete
    )
    ready_temp = args.ready_file.with_suffix(args.ready_file.suffix + ".tmp")
    ready_temp.write_text(
        json.dumps({"host": "127.0.0.1", "port": server.server_port}),
        encoding="utf-8",
    )
    ready_temp.replace(args.ready_file)
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
