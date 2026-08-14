#!/usr/bin/env python3
"""Contract-pinned loopback mock for two VCF Installer 9.0 operations."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


TASK_ID = "task alpha/9.0"
ALTERNATE_TASK_ID = "alternate task/accepted?yes#100%"
CREATED = "2026-08-13T12:00:00Z"


def compile_path(template: str) -> re.Pattern[str]:
    escaped = re.escape(template)
    return re.compile("^" + escaped.replace(re.escape("{id}"), "([^/]+)") + "$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    operations = contract["operations"]
    if set(operations) != {"startBundleDownloadByID", "getTask"}:
        raise SystemExit("focused contract must name exactly two operations")

    start = operations["startBundleDownloadByID"]
    poll = operations["getTask"]
    routes = {
        "startBundleDownloadByID": (start["method"], compile_path(start["path"])),
        "getTask": (poll["method"], compile_path(poll["path"])),
    }
    statuses = {
        "success": ["PENDING", "IN_PROGRESS", "SUCCESSFUL"],
        "accepted-terminal": ["SUCCESSFUL"],
        "mixed-case": ["Pending", "In Progress", "Successful"],
        "failed-upper": ["FAILED"],
        "failed": ["IN_PROGRESS", "Failed"],
        "cancelled-upper": ["CANCELLED"],
        "cancelled": ["Cancelled"],
        "warning": ["COMPLETED_WITH_WARNING"],
        "skipped": ["SKIPPED"],
        "unknown-status": ["QUEUED"],
        "accepted-unknown-status": ["SUCCESSFUL"],
        "start-http-error": [],
        "start-malformed-json": ["SUCCESSFUL"],
        "start-non-object": ["SUCCESSFUL"],
        "start-missing-field": ["SUCCESSFUL"],
        "start-wrong-type": ["SUCCESSFUL"],
        "poll-http-error": ["SUCCESSFUL"],
        "poll-malformed-json": ["SUCCESSFUL"],
        "poll-non-object": ["SUCCESSFUL"],
        "poll-missing-field": ["SUCCESSFUL"],
        "poll-wrong-type": ["SUCCESSFUL"],
    }
    if args.scenario not in statuses:
        raise SystemExit(f"unknown scenario: {args.scenario}")

    log_path = Path(args.log)
    poll_count = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *values: object) -> None:
            return

        def record(self) -> tuple[str | None, bytes]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            path = urlsplit(self.path).path
            operation_id = None
            for candidate, (method, pattern) in routes.items():
                if self.command == method and pattern.fullmatch(path):
                    operation_id = candidate
                    break
            entry = {
                "operationId": operation_id,
                "method": self.command,
                "target": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return operation_id, body

        def send_json(self, status: int, payload: dict[str, object]) -> None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_payload(status, data)

        def send_payload(self, status: int, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_not_found(self) -> None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_PATCH(self) -> None:
            operation_id, _ = self.record()
            if operation_id != "startBundleDownloadByID":
                self.send_not_found()
                return
            if args.scenario == "start-http-error":
                self.send_json(409, {"message": "bundle download already active"})
                return
            if args.scenario == "start-malformed-json":
                self.send_payload(202, b'{"id":')
                return
            if args.scenario == "start-non-object":
                self.send_payload(202, b"[]")
                return
            task_id = ALTERNATE_TASK_ID if args.scenario == "accepted-terminal" else TASK_ID
            accepted_status = {
                "accepted-terminal": "SUCCESSFUL",
                "accepted-unknown-status": "QUEUED",
            }.get(args.scenario, "PENDING")
            accepted = {
                "id": task_id,
                "name": "Download bundle",
                "status": accepted_status,
                "creationTimestamp": CREATED,
            }
            if args.scenario == "start-missing-field":
                del accepted["name"]
            elif args.scenario == "start-wrong-type":
                accepted["status"] = 17
            self.send_json(
                202,
                accepted,
            )

        def do_GET(self) -> None:
            nonlocal poll_count
            operation_id, _ = self.record()
            if operation_id != "getTask":
                self.send_not_found()
                return
            if args.scenario == "poll-http-error":
                self.send_json(503, {"message": "task service unavailable"})
                return
            if args.scenario == "poll-malformed-json":
                self.send_payload(200, b'{"status":')
                return
            if args.scenario == "poll-non-object":
                self.send_payload(200, b"[]")
                return
            sequence = statuses[args.scenario]
            status = sequence[min(poll_count, len(sequence) - 1)]
            poll_count += 1
            task_id = ALTERNATE_TASK_ID if args.scenario == "accepted-terminal" else TASK_ID
            task = {
                "creationTimestamp": CREATED,
                "status": status,
                "name": "Download bundle",
                "id": task_id,
            }
            if args.scenario == "poll-missing-field":
                del task["creationTimestamp"]
            elif args.scenario == "poll-wrong-type":
                task["name"] = ["Download bundle"]
            self.send_json(
                200,
                task,
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever(poll_interval=0.02)


if __name__ == "__main__":
    main()
