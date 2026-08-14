"""Protected loopback vSAN Data Protection service for acceptance checks."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qsl, unquote, urlsplit


DEFAULT_RECORDS = (
    {
        "pg": "pg-z",
        "name": "zeta",
        "snapshot": "snap-z",
        "status": "SUCCESS",
        "creation_time": "2026-01-02T10:00:00Z",
        "expiration_time": "2026-02-02T10:00:00Z",
        "snapshot_type": "SCHEDULED",
        "deleted": False,
    },
    {
        "pg": "pg-b",
        "name": "beta",
        "snapshot": "snap-b",
        "status": "WARNING",
        "warnings": [{"default_message": "one VM was quiesced late"}],
        "creation_time": "2026-01-02T11:00:00Z",
        "deleted": False,
    },
    {
        "pg": "pg-a",
        "name": "zulu",
        "snapshot": "snap-y",
        "status": "SUCCESS",
        "creation_time": "2026-01-02T11:00:00Z",
        "snapshot_type": "ONE_TIME",
        "deleted": False,
    },
    {
        "pg": "pg-a",
        "name": "alpha",
        "snapshot": "snap-z",
        "status": "SUCCESS",
        "creation_time": "2026-01-02T11:00:00Z",
        "snapshot_type": "ONE_TIME",
        "deleted": False,
    },
    {
        "pg": "pg-a",
        "name": "alpha",
        "status": "ERROR",
        "errors": [{"default_message": "snapshot creation failed before allocation"}],
        "creation_time": "2026-01-02T11:00:00Z",
        "deleted": False,
    },
    {
        "pg": "pg-c",
        "name": "gamma",
        "status": "ERROR",
        "errors": [{"default_message": "snapshot creation failed"}],
        "creation_time": "2026-01-02T12:00:00Z",
        "deleted": False,
    },
    {
        "pg": "pg-d",
        "name": "delta",
        "snapshot": "snap-d",
        "status": "SUCCESS",
        "creation_time": "2026-01-02T13:00:00Z",
        "expiration_time": "2026-03-02T13:00:00Z",
        "snapshot_type": "SYSTEM_CREATED",
        "deleted": True,
    },
)


class ContractMock:
    """Serve exactly the operations declared by a local contract document."""

    def __init__(self, contract_path="docs/contract.json", records=DEFAULT_RECORDS):
        contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        operations = contract.get("operations")
        if not isinstance(operations, list) or not operations:
            raise ValueError("contract must contain at least one operation")

        self.contract = contract
        self.operations = tuple(operations)
        self.records = tuple(dict(record) for record in records)
        self.request_log = []
        self._server = None
        self._thread = None

    @property
    def base_url(self):
        host, port = self._server.server_address
        base_path = self.contract["server"]["base_path"]
        return f"http://{host}:{port}{base_path}"

    @property
    def served_operation_ids(self):
        return tuple(operation["operationId"] for operation in self.operations)

    def _match(self, method, encoded_path):
        base_path = self.contract["server"]["base_path"].rstrip("/")
        for operation in self.operations:
            if operation["method"] != method:
                continue
            template = base_path + operation["path"]
            marker = "{cluster}"
            if marker not in template:
                if encoded_path == template:
                    return operation
                continue
            prefix, suffix = template.split(marker, 1)
            if encoded_path.startswith(prefix) and encoded_path.endswith(suffix):
                encoded_cluster = encoded_path[len(prefix):len(encoded_path) - len(suffix)]
                cluster = unquote(encoded_cluster)
                if cluster and "/" not in encoded_cluster:
                    return operation
        return None

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner._handle(self, "GET")

            def do_POST(self):
                owner._handle(self, "POST")

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()

    def _handle(self, handler, method):
        split = urlsplit(handler.path)
        pairs = parse_qsl(split.query, keep_blank_values=True)
        length = int(handler.headers.get("Content-Length", "0"))
        body = handler.rfile.read(length) if length else b""
        self.request_log.append(
            {
                "method": method,
                "raw_target": handler.path,
                "path": split.path,
                "query": pairs,
                "session_id": handler.headers.get("vmware-api-session-id"),
                "accept": handler.headers.get("Accept"),
                "content_length": length,
                "body": body,
            }
        )

        operation = self._match(method, split.path)
        if operation is None:
            self._send(handler, 404, {"error": "operation is not in the pinned contract"})
            return

        values = {}
        for key, value in pairs:
            values.setdefault(key, []).append(value)
        try:
            page_size = int(values.get("page_size", ["2"])[0])
            offset = int(values.get("offset", ["0"])[0])
        except ValueError:
            self._send(handler, 400, {"error": "invalid pagination"})
            return
        if page_size <= 0 or offset < 0:
            self._send(handler, 400, {"error": "invalid pagination"})
            return

        # page_size is an upper bound. Return a short first page to ensure the
        # client advances offsets by the number of records actually received.
        response_page_size = min(page_size, 2) if "offset" not in values else page_size

        response_contract = operation["response"]
        payload = {
            response_contract["items_field"]: [
                dict(record)
                for record in self.records[offset:offset + response_page_size]
            ],
            response_contract["total_field"]: len(self.records),
        }
        self._send(handler, response_contract["status"], payload)

    @staticmethod
    def _send(handler, status, payload):
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)
