#!/usr/bin/env python3
"""Contract-pinned loopback mock for the VCF Installer getTasks operation."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import threading
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class Scenario:
    def __init__(self, contract_path: Path, log_path: Path) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("contract has no operations")
        self.routes = {
            (str(item["method"]).upper(), str(item["path"]))
            for item in operations
        }
        if self.routes != {("GET", "/v1/tasks")}:
            raise ValueError("mock contract must name only getTasks")

        nonce = secrets.token_hex(7)
        self.nonce = nonce
        statuses = ["SUCCESSFUL", "IN_PROGRESS", "FAILED", "PENDING", "SKIPPED"]
        timestamps = [
            "2025-06-18T09:15:00.00000001Z",
            "2025-06-18T10:15:00.00000002+01:00",
            "2025-06-17T22:40:10Z",
            "2025-06-19T01:00:00Z",
            "2025-06-18t09:15:00z",
        ]
        self.tasks = [
            {
                "id": f"{nonce}-{suffix}",
                "name": f"runtime-task-{nonce}-{index}",
                "status": statuses[index],
                "creationTimestamp": timestamps[index],
            }
            for index, suffix in enumerate(["z", "A", "m", "b", "a"])
        ]
        self.page_variants = [
            [[3, 0], [4, 1], [2]],
            [[1, 2], [0, 3], [4]],
        ]
        self.total_pages = 3
        self.page_size = 2
        self.request_count = 0
        self.lock = threading.Lock()
        self.log_path = log_path
        self.log_path.write_bytes(b"")

    def make_tasks(self, count: int, start: int = 0) -> list[dict[str, str]]:
        return [
            {
                "id": f"{self.nonce}-case-{index}",
                "name": f"case-task-{self.nonce}-{index}",
                "status": "SUCCESSFUL",
                "creationTimestamp": "2025-06-18T09:15:00Z",
            }
            for index in range(start, start + count)
        ]

    def expected(self) -> list[dict[str, str]]:
        def represented_instant(value: str) -> tuple[datetime, Decimal]:
            match = re.fullmatch(
                r"(?P<date>\d{4}-\d{2}-\d{2})[Tt]"
                r"(?P<time>\d{2}:\d{2}:\d{2})"
                r"(?:\.(?P<fraction>\d+))?"
                r"(?P<zone>[Zz]|[+-]\d{2}:\d{2})",
                value,
            )
            if match is None:
                raise ValueError("scenario timestamp is not RFC 3339")
            zone = match.group("zone")
            if zone in ("Z", "z"):
                zone = "+00:00"
            whole = datetime.fromisoformat(
                match.group("date") + "T" + match.group("time") + zone
            )
            fraction = match.group("fraction")
            return whole, Decimal("0." + fraction) if fraction else Decimal(0)

        ordered = sorted(
            self.tasks,
            key=lambda item: (
                represented_instant(item["creationTimestamp"]),
                item["id"].encode("utf-8"),
            ),
        )
        return [
            {
                "Id": item["id"],
                "Name": item["name"],
                "Status": item["status"],
                "CreationTimestamp": item["creationTimestamp"],
            }
            for item in ordered
        ]

    def append_log(self, record: dict[str, object]) -> int:
        with self.lock:
            ordinal = self.request_count
            self.request_count += 1
            record["ordinal"] = ordinal
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return ordinal

    @staticmethod
    def page(
        elements: object,
        page_number: object,
        page_size: object,
        total_elements: object,
        total_pages: object,
    ) -> dict:
        return {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": page_number,
                "pageSize": page_size,
                "totalElements": total_elements,
                "totalPages": total_pages,
            },
        }

    def response_for(
        self, ordinal: int, page_number: int, page_size: int
    ) -> tuple[int, object, str, dict[str, str]] | None:
        content_type = "application/json"
        headers: dict[str, str] = {}

        if page_size == 2 and 0 <= page_number < self.total_pages:
            round_number = (ordinal // self.total_pages) % len(self.page_variants)
            indexes = self.page_variants[round_number][page_number]
            elements = [self.tasks[index] for index in indexes]
            value = self.page(
                elements,
                page_number,
                len(elements),
                len(self.tasks),
                self.total_pages,
            )
            return 200, value, content_type, headers

        if page_size == 100 and page_number == 0:
            return 200, self.page([], 0, 0, 0, 1), content_type, headers
        if page_size == 3 and page_number == 0:
            return (
                302,
                {"error": "redirects are forbidden"},
                content_type,
                {"Location": "/redirect-target"},
            )
        if page_size == 4 and page_number in (0, 1):
            if page_number == 0:
                return (
                    200,
                    self.page(self.tasks[:4], 0, 4, 5, 2),
                    content_type,
                    headers,
                )
            return 500, {"error": "late page failure"}, content_type, headers
        if page_size == 5 and page_number == 0:
            return (
                200,
                self.page([self.tasks[0], self.tasks[0]], 0, 2, 2, 1),
                content_type,
                headers,
            )
        if page_size == 6 and page_number == 0:
            malformed = dict(self.tasks[0], creationTimestamp="not-a-timestamp")
            return 200, self.page([malformed], 0, 1, 1, 1), content_type, headers
        if page_size == 7 and page_number == 0:
            malformed = dict(self.tasks[0], name="   ")
            return 200, self.page([malformed], 0, 1, 1, 1), content_type, headers
        if page_size == 8 and page_number == 0:
            return 200, self.page([], 0.5, 0, 0, 1), content_type, headers
        if page_size == 9 and page_number == 0:
            elements = self.make_tasks(9)
            return 200, self.page(elements, 0, 9, 10, 1), content_type, headers
        if page_size == 10 and page_number == 0:
            return (
                200,
                self.page([self.tasks[0]], 1, 1, 1, 1),
                content_type,
                headers,
            )
        if page_size == 11 and page_number == 0:
            elements = self.make_tasks(9)
            return 200, self.page(elements, 0, 9, 12, 2), content_type, headers
        if page_size == 12 and page_number in (0, 1):
            if page_number == 0:
                elements = self.make_tasks(12)
                return 200, self.page(elements, 0, 12, 13, 2), content_type, headers
            elements = self.make_tasks(2, 12)
            return 200, self.page(elements, 1, 2, 14, 2), content_type, headers
        if page_size == 13 and page_number in (0, 1):
            elements = self.make_tasks(13)
            if page_number == 0:
                return 200, self.page(elements, 0, 13, 14, 2), content_type, headers
            return 200, self.page([elements[0]], 1, 1, 14, 2), content_type, headers
        if page_size == 14 and page_number == 0:
            return 200, self.page([], 0, 0, 0, 1), "Application/JSON", headers
        if page_size == 15 and page_number == 0:
            return 201, self.page([], 0, 0, 0, 1), content_type, headers
        if page_size == 16 and page_number == 0:
            return 200, self.page({}, 0, 0, 0, 1), content_type, headers
        if page_size == 17 and page_number == 0:
            return (
                200,
                self.page([self.tasks[0]], 0, 17, 1, 1),
                content_type,
                headers,
            )
        if page_size == 18 and page_number in (0, 1):
            if page_number == 0:
                elements = self.make_tasks(18)
                return 200, self.page(elements, 0, 18, 19, 2), content_type, headers
            return 200, self.page([], 1, 0, 19, 2), content_type, headers
        if page_size == 19 and page_number == 0:
            return 200, b"{broken-json", content_type, headers
        if page_size == 20 and page_number == 0:
            value = self.page([], 0, 0, 0, 1)
            del value["pageMetadata"]["totalPages"]
            return 200, value, content_type, headers
        if page_size == 21 and page_number == 0:
            malformed = dict(self.tasks[0], status=42)
            return 200, self.page([malformed], 0, 1, 1, 1), content_type, headers
        if page_size == 22 and page_number == 0:
            return 200, self.page([], 0, 0, 0, 1), "text/json", headers
        if page_size == 23 and page_number == 0:
            return 200, self.page([], 0, 0, -1, 1), content_type, headers
        if page_size == 24 and page_number == 0:
            return 200, [], content_type, headers
        if page_size == 25 and page_number == 0:
            value = {"elements": [], "pageMetadata": []}
            return 200, value, content_type, headers
        if page_size == 26 and page_number == 0:
            return 200, self.page(["not-an-object"], 0, 1, 1, 1), content_type, headers

        return None


def make_handler(scenario: Scenario):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _record(self) -> tuple[int, bytes, list[tuple[str, str]]]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""
            headers = list(self.headers.raw_items())
            ordinal = scenario.append_log(
                {
                    "method": self.command,
                    "rawTarget": self.path,
                    "headers": headers,
                    "bodyHex": body.hex(),
                }
            )
            return ordinal, body, headers

        def _send_payload(
            self,
            status: int,
            value: object,
            content_type: str = "application/json",
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            payload = (
                value
                if isinstance(value, bytes)
                else json.dumps(value, separators=(",", ":")).encode("utf-8")
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, header_value in (extra_headers or {}).items():
                self.send_header(name, header_value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

        def _dispatch(self) -> None:
            ordinal, body, _headers = self._record()
            parsed = urlsplit(self.path)
            if (self.command, parsed.path) not in scenario.routes:
                self._send_payload(404, {"error": "operation not in contract"})
                return
            if self.command != "GET":
                self._send_payload(405, {"error": "method not allowed"})
                return
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
            if len(pairs) != 2 or [name for name, _ in pairs] != [
                "pageNumber",
                "pageSize",
            ]:
                self._send_payload(400, {"error": "invalid query shape"})
                return
            if any(value == "" for _, value in pairs):
                self._send_payload(400, {"error": "empty query value"})
                return
            try:
                page_number = int(pairs[0][1])
                page_size = int(pairs[1][1])
            except ValueError:
                self._send_payload(400, {"error": "query values must be integers"})
                return
            if body:
                self._send_payload(400, {"error": "GET must be bodyless"})
                return
            response = scenario.response_for(ordinal, page_number, page_size)
            if response is None:
                self._send_payload(400, {"error": "page outside scenario"})
                return
            status, value, content_type, extra_headers = response
            self._send_payload(status, value, content_type, extra_headers)

        def do_GET(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_PUT(self) -> None:
            self._dispatch()

        def do_PATCH(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    args = parser.parse_args()

    scenario = Scenario(args.contract, args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(scenario))
    server.daemon_threads = True
    server.allow_reuse_address = False

    atomic_json(args.scenario, {"expected": scenario.expected()})
    atomic_json(
        args.ready,
        {"baseUri": f"http://127.0.0.1:{server.server_address[1]}"},
    )

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
