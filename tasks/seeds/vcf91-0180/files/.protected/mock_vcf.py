"""Contract-pinned loopback VCF Operations Log Management mock.

The route table is derived exclusively from docs/contract.json.  Tests supply
runtime response scripts and can inspect ``MockVcfOperations.requests`` for the
exact received wire requests.
"""

from __future__ import annotations

import json
import io
import threading
import urllib.error
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"


class MockVcfOperations:
    """Ephemeral loopback service limited to contract-named operations."""

    def __init__(self, response_scripts):
        self.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.routes = {
            (operation["method"], operation["path"]): operation["operationId"]
            for operation in self.contract["operations"]
        }
        self._responses = defaultdict(deque)
        for operation_id, responses in response_scripts.items():
            self._responses[operation_id].extend(responses)
        self.requests = []
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None
        self._adapter = None
        self._patches = []

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def do_DELETE(self):
                self._dispatch()

            def do_GET(self):
                self._dispatch()

            def do_HEAD(self):
                self._dispatch()

            def do_PATCH(self):
                self._dispatch()

            def do_POST(self):
                self._dispatch()

            def do_PUT(self):
                self._dispatch()

            def _dispatch(self):
                parsed = urlsplit(self.path)
                length_values = self.headers.get_all("Content-Length") or []
                try:
                    length = int(length_values[-1]) if length_values else 0
                except ValueError:
                    length = 0
                raw_body = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw_body.decode("utf-8")) if raw_body else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = "__invalid_json__"

                route_key = (self.command, parsed.path)
                operation_id = owner.routes.get(route_key)
                header_log = {
                    name.lower(): list(self.headers.get_all(name) or [])
                    for name in self.headers.keys()
                }
                record = {
                    "method": self.command,
                    "raw_target": self.path,
                    "path": parsed.path,
                    "query": parsed.query,
                    "headers": header_log,
                    "raw_body": raw_body,
                    "body": body,
                    "operationId": operation_id,
                }
                with owner._lock:
                    owner.requests.append(record)

                if operation_id is None:
                    self._reply(
                        404,
                        {
                            "errorCode": "API_ERROR",
                            "errorMessage": "operation is not present in the pinned contract",
                        },
                    )
                    return
                with owner._lock:
                    queue = owner._responses[operation_id]
                    scripted = queue.popleft() if queue else None
                if scripted is None:
                    self._reply(
                        500,
                        {
                            "errorCode": "INTERNAL_SERVER_ERROR",
                            "errorMessage": "no runtime response scripted",
                        },
                    )
                    return
                status, payload = scripted
                self._reply(status, payload)

            def _reply(self, status, payload):
                data = (
                    json.dumps(payload, separators=(",", ":")).encode("utf-8")
                    if payload is not None
                    else b""
                )
                self.send_response(status)
                if data:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD" and data:
                    self.wfile.write(data)

        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except (OSError, PermissionError):
            # Some reference-validation sandboxes deny socket creation.  Keep
            # the real loopback server as the normal path, but use the same
            # contract-derived route table and request recorder in process so
            # the protected verifier remains deterministic there.
            self._adapter = _InProcessAdapter(self)
            self._patches = [
                mock.patch(
                    "urllib.request.build_opener",
                    return_value=self._adapter,
                ),
                mock.patch(
                    "urllib.request.urlopen",
                    side_effect=self._adapter.open,
                ),
            ]
            for patcher in self._patches:
                patcher.start()
            return self
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def base_url(self):
        if self._adapter is not None:
            return "http://127.0.0.1:1"
        if self._httpd is None:
            raise RuntimeError("mock is not running")
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc, tb):
        for patcher in reversed(self._patches):
            patcher.stop()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False

    def dispatch_in_process(self, request):
        """Dispatch one urllib Request through the contract route table."""
        parsed = urlsplit(request.full_url)
        raw_body = request.data or b""
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = "__invalid_json__"
        headers = defaultdict(list)
        for name, value in request.header_items():
            headers[name.lower()].append(value)
        operation_id = self.routes.get((request.get_method(), parsed.path))
        record = {
            "method": request.get_method(),
            "raw_target": parsed.path + (("?" + parsed.query) if parsed.query else ""),
            "path": parsed.path,
            "query": parsed.query,
            "headers": dict(headers),
            "raw_body": raw_body,
            "body": body,
            "operationId": operation_id,
        }
        with self._lock:
            self.requests.append(record)
        if operation_id is None:
            return 404, {
                "errorCode": "API_ERROR",
                "errorMessage": "operation is not present in the pinned contract",
            }
        with self._lock:
            queue = self._responses[operation_id]
            scripted = queue.popleft() if queue else None
        if scripted is None:
            return 500, {
                "errorCode": "INTERNAL_SERVER_ERROR",
                "errorMessage": "no runtime response scripted",
            }
        return scripted


class _MemoryResponse:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _InProcessAdapter:
    """urllib-compatible fallback backed by the same mock dispatch state."""

    def __init__(self, owner):
        self.owner = owner

    def open(self, request, *args, **kwargs):
        if isinstance(request, str):
            request = urllib.request.Request(request)
        status, payload = self.owner.dispatch_in_process(request)
        data = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else b""
        )
        if not 200 <= status < 300:
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "contract mock response",
                {},
                io.BytesIO(data),
            )
        return _MemoryResponse(status, data)
