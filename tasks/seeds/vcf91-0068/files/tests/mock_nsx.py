"""Loopback NSX Policy mock derived from the protected contract.

The HTTP server intentionally exposes only the operations named by
docs/contract.json. Token renewal is an injected client callback, not another
mock endpoint. Every request is retained in request_log for verifier checks.
"""

from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
from urllib.parse import unquote, urlsplit


OLD_TOKEN = "fixture-access-token-v1"
FRESH_TOKEN = "fixture-access-token-v2"


class ContractNsxMock:
    """State and contract router shared by loopback request handlers."""

    def __init__(self, contract):
        self.contract = contract
        self.request_log = []
        self.segments = {}
        self.collection_response_count = 0
        self.old_token_successes = 0
        self.lock = threading.RLock()

        operations = contract["operations"]
        self.allowed_operation_ids = frozenset(operations)
        if self.allowed_operation_ids != {
            "CreateOrReplaceInfraSegment",
            "ListAllInfraSegments",
        }:
            raise AssertionError("mock contract must name exactly the two segment operations")

        base = contract["base_path"]
        self.list_operation = operations["ListAllInfraSegments"]
        self.put_operation = operations["CreateOrReplaceInfraSegment"]
        self.list_path = base + self.list_operation["path"]
        item_template = base + self.put_operation["path"]
        prefix, suffix = item_template.split("{segment-id}")
        self.item_pattern = re.compile(
            "^" + re.escape(prefix) + r"(?P<segment_id>[^/]+)" + re.escape(suffix) + "$"
        )

    def operation_for(self, method, path):
        if method == self.list_operation["method"] and path == self.list_path:
            return self.list_operation["operationId"], None
        match = self.item_pattern.fullmatch(path)
        if match and method == self.put_operation["method"]:
            return self.put_operation["operationId"], unquote(match.group("segment_id"))
        return None, None

    def handle(self, method, raw_path, headers, raw_body):
        parsed = urlsplit(raw_path)
        operation_id, segment_id = self.operation_for(method, parsed.path)
        try:
            logged_body = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            logged_body = None
        event = {
            "method": method,
            "path": parsed.path,
            "query": parsed.query,
            "operation_id": operation_id,
            "authorization": headers.get("authorization"),
            "accept": headers.get("accept"),
            "content_type": headers.get("content-type"),
            "body": logged_body,
        }
        with self.lock:
            self.request_log.append(event)

            if operation_id is None:
                return self._finish(
                    event,
                    404,
                    {
                        "error_code": 404001,
                        "error_message": "Operation is not present in the pinned contract",
                        "details": f"No contract route for {method} {parsed.path}",
                        "module_name": "policy",
                    },
                )

            authorization = headers.get("authorization")
            if authorization == f"Bearer {OLD_TOKEN}":
                if self.old_token_successes >= 1:
                    return self._finish(
                        event,
                        401,
                        {
                            "error_code": 401003,
                            "error_message": "Access token has expired",
                            "details": "Obtain a fresh access token and retry the failed request",
                            "module_name": "authentication",
                        },
                    )
            elif authorization != f"Bearer {FRESH_TOKEN}":
                return self._finish(
                    event,
                    401,
                    {
                        "error_code": 401002,
                        "error_message": "Authentication required",
                        "details": "A valid bearer access token is required",
                        "module_name": "authentication",
                    },
                )

            if operation_id == "CreateOrReplaceInfraSegment":
                result = self._put_segment(event, segment_id, raw_body)
            else:
                result = self._list_segments(event)

            if authorization == f"Bearer {OLD_TOKEN}" and result[0] < 400:
                self.old_token_successes += 1
            return result

    def _put_segment(self, event, segment_id, raw_body):
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._finish(
                event,
                400,
                {
                    "error_code": 400074,
                    "error_message": "Malformed JSON request body",
                    "details": "The Segment request body must be a JSON object",
                    "module_name": "policy",
                },
            )
        event["body"] = deepcopy(body)
        if (
            not isinstance(body, dict)
            or body.get("resource_type") != "Segment"
            or body.get("id") != segment_id
        ):
            return self._finish(
                event,
                400,
                {
                    "error_code": 400012,
                    "error_message": "Invalid Segment",
                    "details": "resource_type must be Segment and id must match segment-id",
                    "module_name": "policy",
                },
            )
        self.segments[segment_id] = deepcopy(body)
        return self._finish(event, self.put_operation["success"]["status"], deepcopy(body))

    def _list_segments(self, event):
        ids = sorted(self.segments)
        self.collection_response_count += 1
        if self.collection_response_count % 2:
            ids.reverse()
        event["response_ids"] = list(ids)
        payload = {
            "result_count": len(ids),
            "results": [deepcopy(self.segments[segment_id]) for segment_id in ids],
            "sort_by": "display_name",
            "sort_ascending": self.collection_response_count % 2 == 0,
        }
        return self._finish(event, self.list_operation["success"]["status"], payload)

    @staticmethod
    def _finish(event, status, payload):
        event["status"] = status
        return status, payload


def _handler_for(mock):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or "0")
            raw_body = self.rfile.read(length) if length else b""
            headers = {key.lower(): value for key, value in self.headers.items()}
            status, payload = mock.handle(self.command, self.path, headers, raw_body)
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _dispatch
        do_PUT = _dispatch

    return Handler


@contextmanager
def running_mock(contract):
    """Yield (origin_url, mock_state) for a temporary loopback server."""

    mock = ContractNsxMock(contract)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(mock))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", mock
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
