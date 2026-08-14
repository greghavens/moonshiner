#!/usr/bin/env python3
"""Contract-pinned loopback mock for the certificate update operation pair."""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


EXPECTED_OPERATIONS = {
    "updateCertificate": ("PUT", "/settings/certificates/{id}"),
    "fetchCertificateUpdateStatusForUpdateId": (
        "GET",
        "/settings/certificates/status/{id}",
    ),
}


def load_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    operations = contract.get("operations", {})
    observed = {
        name: (definition.get("method"), definition.get("path"))
        for name, definition in operations.items()
    }
    if observed != EXPECTED_OPERATIONS:
        raise RuntimeError(f"mock contract operation mismatch: {observed!r}")
    return contract


class ContractServer(ThreadingHTTPServer):
    def __init__(self, address, handler, contract: dict, request_log: Path):
        super().__init__(address, handler)
        self.contract = contract
        self.request_log = request_log
        self.lock = threading.Lock()
        self.next_update = 1
        self.poll_counts: dict[str, int] = {}
        self.failures: set[str] = set()

    def record(self, entry: dict) -> None:
        with self.lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")


class Handler(BaseHTTPRequestHandler):
    server: ContractServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _record(self, body: bytes) -> None:
        split = urlsplit(self.path)
        self.server.record(
            {
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
        )

    def _json(self, status: int, document: dict) -> None:
        payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _not_found(self) -> None:
        self._json(404, {"message": "operation is not in the pinned contract"})

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        self._record(body)
        split = urlsplit(self.path)
        prefix = self.server.contract["server_url"] + "/settings/certificates/"
        if split.query or not split.path.startswith(prefix):
            self._not_found()
            return
        suffix = split.path[len(prefix) :]
        if not suffix or "/" in suffix:
            self._not_found()
            return

        with self.server.lock:
            sequence = self.server.next_update
            update_id = "update id/0001" if sequence == 1 else f"update-{sequence:04d}"
            self.server.next_update += 1
            self.server.poll_counts[update_id] = 0
            if unquote(suffix) == "failed-target":
                self.server.failures.add(update_id)
        self._json(202, {"id": update_id, "name": unquote(suffix), "status": "SUBMITTED"})

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        self._record(body)
        split = urlsplit(self.path)
        prefix = self.server.contract["server_url"] + "/settings/certificates/status/"
        if split.query or not split.path.startswith(prefix):
            self._not_found()
            return
        update_id = unquote(split.path[len(prefix) :])
        with self.server.lock:
            if update_id not in self.server.poll_counts:
                self._not_found()
                return
            self.server.poll_counts[update_id] += 1
            poll_count = self.server.poll_counts[update_id]
            should_fail = update_id in self.server.failures

        if should_fail:
            self._json(
                200,
                {
                    "id": update_id,
                    "status": "FAILED",
                    "error_message": "fixture certificate update failed",
                },
            )
            return
        status = "SUBMITTED" if poll_count == 1 else "IN_PROGRESS" if poll_count == 2 else "SUCCESS"
        self._json(200, {"id": update_id, "status": status})

    def do_POST(self) -> None:  # noqa: N802 - reject operations outside contract
        body = self._read_body()
        self._record(body)
        self._not_found()

    def do_DELETE(self) -> None:  # noqa: N802 - reject operations outside contract
        body = self._read_body()
        self._record(body)
        self._not_found()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--request-log", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    args = parser.parse_args()

    contract = load_contract(args.contract)
    args.request_log.write_text("", encoding="utf-8")
    server = ContractServer(("127.0.0.1", 0), Handler, contract, args.request_log)
    host, port = server.server_address
    args.ready_file.write_text(
        json.dumps({"base_url": f"http://{host}:{port}"}), encoding="utf-8"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
