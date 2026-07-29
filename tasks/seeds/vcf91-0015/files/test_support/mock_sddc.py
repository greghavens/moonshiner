"""Loopback mock serving only the operation named by docs/contract.json."""

import json
import math
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "docs", "contract.json"), encoding="utf-8") as handle:
    CONTRACT = json.load(handle)

if len(CONTRACT["operations"]) != 1:
    raise RuntimeError("the loopback mock requires exactly one named operation")
GET_DOMAINS = CONTRACT["operations"][0]
if GET_DOMAINS["operationId"] != "getDomains":
    raise RuntimeError("the loopback mock is pinned to operationId getDomains")


class MockSddcManager:
    """Context-managed loopback server with a readable request log."""

    def __init__(self, domains):
        self.domains = [dict(domain) for domain in domains]
        self.request_log = []
        self._queued = []
        self._lock = threading.RLock()
        self._server = None
        self._thread = None

    def queue_response(self, status, payload):
        """Override the next valid getDomains response."""
        with self._lock:
            self._queued.append(("json", status, payload))

    def queue_raw_response(self, status, body):
        """Override the next valid response with deliberately non-JSON bytes."""
        if not isinstance(body, bytes):
            raise TypeError("raw response body must be bytes")
        with self._lock:
            self._queued.append(("raw", status, body))

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _record(self):
                split = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or "0")
                body = self.rfile.read(length) if length else b""
                record = {
                    "method": self.command,
                    "target": self.path,
                    "path": split.path,
                    "query_pairs": parse_qsl(
                        split.query, keep_blank_values=True, strict_parsing=True
                    ),
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body,
                }
                with fixture._lock:
                    fixture.request_log.append(record)
                return split, record

            def _send_json(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._send_bytes(status, data)

            def _send_bytes(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                split, record = self._record()
                if (
                    self.command != GET_DOMAINS["method"]
                    or split.path != GET_DOMAINS["path"]
                ):
                    self._send_json(
                        404,
                        {
                            "errorCode": "UNKNOWN_OPERATION",
                            "message": "operation is not named by the pinned contract",
                        },
                    )
                    return

                with fixture._lock:
                    if fixture._queued:
                        kind, status, payload = fixture._queued.pop(0)
                        if kind == "raw":
                            self._send_bytes(status, payload)
                        else:
                            self._send_json(status, payload)
                        return

                pairs = record["query_pairs"]
                query = {}
                for key, value in pairs:
                    if key in query:
                        self._send_json(
                            400,
                            {
                                "errorCode": "DUPLICATE_QUERY",
                                "message": "query parameters must be singular",
                            },
                        )
                        return
                    query[key] = value

                allowed = {
                    item["name"] for item in GET_DOMAINS["query_parameters"]
                }
                if set(query) - allowed:
                    self._send_json(
                        400,
                        {
                            "errorCode": "UNKNOWN_QUERY",
                            "message": "query parameter is not in the pinned contract",
                        },
                    )
                    return
                try:
                    page_number = int(query["pageNumber"])
                    requested_size = int(query["pageSize"])
                except (KeyError, ValueError):
                    self._send_json(
                        400,
                        {
                            "errorCode": "BAD_PAGINATION",
                            "message": "pageNumber and pageSize are required integers",
                        },
                    )
                    return
                if page_number < 0 or requested_size <= 0:
                    self._send_json(
                        400,
                        {
                            "errorCode": "BAD_PAGINATION",
                            "message": "pagination values are out of range",
                        },
                    )
                    return

                selected = list(fixture.domains)
                if "type" in query:
                    selected = [d for d in selected if d.get("type") == query["type"]]
                if "name" in query:
                    selected = [d for d in selected if d.get("name") == query["name"]]
                if "isManagementSsoDomain" in query:
                    wanted = query["isManagementSsoDomain"].lower() == "true"
                    selected = [
                        d for d in selected if d.get("isManagementSsoDomain") is wanted
                    ]

                total = len(selected)
                total_pages = math.ceil(total / requested_size) if total else 0
                start = page_number * requested_size
                elements = selected[start : start + requested_size]
                self._send_json(
                    200,
                    {
                        "elements": elements,
                        "pageMetadata": {
                            "pageNumber": page_number,
                            "pageSize": len(elements),
                            "totalElements": total,
                            "totalPages": total_pages,
                        },
                    },
                )

            def _unsupported(self):
                self._record()
                self._send_json(
                    404,
                    {
                        "errorCode": "UNKNOWN_OPERATION",
                        "message": "operation is not named by the pinned contract",
                    },
                )

            do_POST = _unsupported
            do_PUT = _unsupported
            do_PATCH = _unsupported
            do_DELETE = _unsupported

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mock-sddc", daemon=True
        )
        self._thread.start()
        return self

    @property
    def base_url(self):
        if self._server is None:
            raise RuntimeError("mock server is not running")
        return "http://127.0.0.1:%d" % self._server.server_address[1]

    def __exit__(self, exc_type, exc, traceback):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
