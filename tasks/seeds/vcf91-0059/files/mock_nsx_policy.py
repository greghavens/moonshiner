"""Contract-pinned loopback NSX Policy server used by the acceptance verifier."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


CONTRACT_PATH = Path(__file__).with_name("docs") / "contract.json"


def _load_contract():
    with CONTRACT_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self):  # noqa: N802 - prove uncontracted methods are unavailable
        self._dispatch()

    def do_PATCH(self):  # noqa: N802
        self._dispatch()

    def do_DELETE(self):  # noqa: N802
        self._dispatch()

    def _dispatch(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        entry = {
            "method": self.command,
            "target": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": body,
        }
        owner = self.server.owner
        owner.request_log.append(entry)
        status, response = owner.route(self.command, self.path, body)
        encoded = (
            json.dumps(response, separators=(",", ":")).encode("utf-8")
            if response is not None
            else b""
        )
        self.send_response(status)
        if encoded:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def log_message(self, format, *args):  # noqa: A002 - inherited name
        return


class MockNsxPolicy:
    """Serve only the operationIds named by the reduced OpenAPI contract."""

    def __init__(self, status_documents):
        if not status_documents:
            raise ValueError("status_documents must contain at least one response")
        self.contract = _load_contract()
        operations = self.contract["operations"]
        expected = {"CreateOrReplaceInfraSegment", "ReadIntentStatus"}
        if set(operations) != expected:
            raise ValueError("mock contract must name exactly the two selected operations")

        base_path = self.contract["openapi"]["basePath"]
        put_operation = operations["CreateOrReplaceInfraSegment"]
        get_operation = operations["ReadIntentStatus"]
        self.named_operations = frozenset(operations)
        self._put_method = put_operation["method"]
        put_template = base_path + put_operation["path"]
        self._put_pattern = re.compile(
            "^"
            + re.escape(put_template).replace(
                re.escape("{segment-id}"), r"(?P<segment_id>[^/]+)"
            )
            + "$"
        )
        self._get_method = get_operation["method"]
        self._get_path = base_path + get_operation["path"]
        self._allowed_get_queries = {
            item["name"]
            for item in get_operation["parameters"]
            if item["in"] == "query"
        }
        self._statuses = [dict(item) for item in status_documents]
        self._status_index = 0
        self.request_log = []
        self._server = None
        self._thread = None

    @property
    def base_url(self):
        if self._server is None:
            raise RuntimeError("mock server is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.owner = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()
        self._server = None
        self._thread = None

    def route(self, method, target, body):
        parsed = urlsplit(target)
        put_match = self._put_pattern.fullmatch(parsed.path)
        if method == self._put_method and put_match and not parsed.query:
            try:
                request_document = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return 400, {
                    "error_code": 400,
                    "error_message": "request body must be JSON",
                }
            segment_id = unquote(put_match.group("segment_id"))
            response = dict(request_document)
            response.update(
                {
                    "id": segment_id,
                    "path": f"/infra/segments/{segment_id}",
                    "resource_type": "Segment",
                }
            )
            return 200, response

        if method == self._get_method and parsed.path == self._get_path:
            query = parse_qs(parsed.query, keep_blank_values=True)
            if set(query) - self._allowed_get_queries:
                return 400, {
                    "error_code": 400,
                    "error_message": "unknown query parameter",
                }
            if len(query.get("intent_path", [])) != 1 or not query["intent_path"][0]:
                return 400, {
                    "error_code": 400,
                    "error_message": "intent_path is required",
                }
            index = min(self._status_index, len(self._statuses) - 1)
            document = dict(self._statuses[index])
            self._status_index += 1
            document.setdefault("intent_path", query["intent_path"][0])
            return 200, document

        return 404, {
            "error_code": 404,
            "error_message": "operation is not present in the pinned contract",
        }
