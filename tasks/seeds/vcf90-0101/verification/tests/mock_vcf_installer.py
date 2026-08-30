#!/usr/bin/env python3
"""Loopback-only VCF Installer mock pinned to docs/contract.json."""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


CREATED = "2026-01-01T00:00:00Z"


def task(task_id: str, status: str) -> dict[str, str]:
    result = {
        "id": task_id,
        "name": "Bundle download",
        "status": status,
        "creationTimestamp": CREATED,
    }
    if status == "SUCCESSFUL":
        result["completionTimestamp"] = "2026-01-01T00:00:02Z"
    return result


class State:
    def __init__(
        self,
        contract_path: Path,
        log_path: Path,
        task_id: str,
        submit_status: str,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = {
            operation["operationId"]: {
                "method": method.upper(),
                "path": path,
                "operation": operation,
            }
            for path, path_item in contract["paths"].items()
            for method, operation in path_item.items()
            if isinstance(operation, dict) and "operationId" in operation
        }
        assert set(operations) == {"startBundleDownloadByID", "getTask"}
        assert operations["startBundleDownloadByID"]["method"] == "PATCH"
        assert operations["getTask"]["method"] == "GET"
        self.bundle_template = str(operations["startBundleDownloadByID"]["path"])
        self.task_template = str(operations["getTask"]["path"])
        self.log_path = log_path
        self.task_id = task_id
        self.submit_status = submit_status
        self.polls = 0
        self.started = False
        self.lock = threading.Lock()

    def append(self, record: dict[str, object]) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                stream.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = "VCFInstallerContractMock/9.0"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _record(self) -> tuple[str, str, bytes]:
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.state.append(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
        )
        return parsed.path, parsed.query, body

    def _json(self, status: int, value: object) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, query, _body = self._record()
        template = re.escape(self.state.bundle_template).replace(r"\{id\}", r"([^/]+)")
        match = re.fullmatch(template, path)
        if match is None or query:
            self._json(404, {"message": "operation not served"})
            return
        if not unquote(match.group(1)):
            self._json(400, {"message": "bundle id required"})
            return
        with self.state.lock:
            self.state.started = True
        self._json(202, task(self.state.task_id, self.state.submit_status))

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, query, _body = self._record()
        template = re.escape(self.state.task_template).replace(r"\{id\}", r"([^/]+)")
        match = re.fullmatch(template, path)
        if match is None or query or unquote(match.group(1)) != self.state.task_id:
            self._json(404, {"message": "operation not served"})
            return
        with self.state.lock:
            if not self.state.started:
                self._json(404, {"message": "task not found"})
                return
            self.state.polls += 1
            status = "IN_PROGRESS" if self.state.polls == 1 else "SUCCESSFUL"
        self._json(200, task(self.state.task_id, status))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._record()
        self._json(404, {"message": "operation not served"})

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._record()
        self._json(404, {"message": "operation not served"})

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._record()
        self._json(404, {"message": "operation not served"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--submit-status",
        choices=("PENDING", "SUCCESSFUL"),
        default="PENDING",
    )
    args = parser.parse_args()
    args.log.write_text("", encoding="utf-8")
    state = State(args.contract, args.log, args.task_id, args.submit_status)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state  # type: ignore[attr-defined]
    host, port = server.server_address
    print(json.dumps({"baseUrl": f"http://{host}:{port}"}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
