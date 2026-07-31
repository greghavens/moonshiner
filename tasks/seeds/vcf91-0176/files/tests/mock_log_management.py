"""Loopback-only mock derived from docs/contract.json."""

import json
import http.client
import io
import re
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class ContractLogManagementMock:
    """Serve only the operations named by the focused contract."""

    def __init__(self, *, expected_token, drop_first_response=False):
        self.expected_token = expected_token
        self.drop_first_response = drop_first_response
        self._thread = None
        self._server = None

        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        operations = []
        for path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if isinstance(operation, dict) and "operationId" in operation:
                    operations.append((method.upper(), path, operation["operationId"]))
        if operations != [
            (
                "PUT",
                "/api/v2/logs/forwarders/{id}",
                "updateLogForwarder",
            )
        ]:
            raise AssertionError("mock contract operation set changed")
        self.method, self.path_template, self.operation_id = operations[0]
        pattern = re.escape(self.path_template).replace(
            re.escape("{id}"),
            r"(?P<id>[^/?]+)",
        )
        self._path_pattern = re.compile(pattern)

    @property
    def base_url(self):
        host, port = self._server.server_address
        return "http://{}:{}".format(host, port)

    @property
    def requests(self):
        with self._server.state_lock:
            return deepcopy(self._server.request_log)

    @property
    def effect_count(self):
        with self._server.state_lock:
            return self._server.effect_count

    @property
    def resource(self):
        with self._server.state_lock:
            return deepcopy(self._server.resource)

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format_string, *args):
                return

            def _json_response(self, status, value):
                body = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PUT(self):
                split = urlsplit(self.path)
                match = fixture._path_pattern.fullmatch(split.path)
                if not match or split.query:
                    self._json_response(
                        404,
                        {
                            "errorCode": "API_ERROR",
                            "errorMessage": "operation is not in contract",
                        },
                    )
                    return

                try:
                    content_length = int(self.headers["Content-Length"])
                    raw_body = self.rfile.read(content_length)
                    body = json.loads(raw_body.decode("utf-8"))
                except (KeyError, TypeError, ValueError, UnicodeDecodeError):
                    self._json_response(
                        400,
                        {
                            "errorCode": "JSON_FORMAT_ERROR",
                            "errorMessage": "invalid JSON request",
                        },
                    )
                    return

                record = {
                    "operationId": fixture.operation_id,
                    "method": self.command,
                    "raw_path": split.path,
                    "query": split.query,
                    "headers": {
                        name.lower(): value
                        for name, value in self.headers.items()
                    },
                    "raw_body": raw_body,
                    "body": body,
                }
                with self.server.state_lock:
                    self.server.request_log.append(record)

                if self.headers.get("X-JWT-Token") != fixture.expected_token:
                    self._json_response(
                        403,
                        {
                            "errorCode": "SECURITY_ERROR",
                            "errorMessage": "authorization required",
                        },
                    )
                    return
                if self.headers.get("Content-Type") != "application/json":
                    self._json_response(
                        400,
                        {
                            "errorCode": "JSON_FORMAT_ERROR",
                            "errorMessage": "application/json required",
                        },
                    )
                    return
                if not isinstance(body, dict):
                    self._json_response(
                        400,
                        {
                            "errorCode": "JSON_FORMAT_ERROR",
                            "errorMessage": "JSON object required",
                        },
                    )
                    return

                forwarder_id = unquote(match.group("id"))
                new_resource = {"id": forwarder_id}
                new_resource.update(body)
                with self.server.state_lock:
                    if self.server.resource != new_resource:
                        self.server.resource = new_resource
                        self.server.effect_count += 1
                    should_drop = (
                        fixture.drop_first_response
                        and not self.server.dropped_response
                    )
                    if should_drop:
                        self.server.dropped_response = True

                if should_drop:
                    self.close_connection = True
                    self.connection.close()
                    return
                self._json_response(200, new_resource)

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._server.state_lock = threading.Lock()
        self._server.request_log = []
        self._server.resource = None
        self._server.effect_count = 0
        self._server.dropped_response = False
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="contract-log-management-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


class ContractRequestMock:
    """Contract-derived fallback for sandboxes that prohibit loopback sockets."""

    def __init__(self, *, expected_token, drop_first_response=False):
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        path_item = contract["paths"]["/api/v2/logs/forwarders/{id}"]
        if list(path_item) != ["put"]:
            raise AssertionError("fallback contract operation set changed")
        operation = path_item["put"]
        if operation["operationId"] != "updateLogForwarder":
            raise AssertionError("fallback contract operation changed")
        self.operation_id = operation["operationId"]
        self.expected_token = expected_token
        self.drop_first_response = drop_first_response
        self.base_url = "http://127.0.0.1:8787"
        self.requests = []
        self.effect_count = 0
        self.resource = None
        self._dropped_response = False
        self._path_pattern = re.compile(
            r"/api/v2/logs/forwarders/(?P<id>[^/?]+)"
        )

    def urlopen(self, request, timeout):
        del timeout
        split = urlsplit(request.full_url)
        match = self._path_pattern.fullmatch(split.path)
        headers = {
            name.lower(): value
            for name, value in request.header_items()
        }
        raw_body = request.data
        headers.setdefault("content-length", str(len(raw_body)))
        body = json.loads(raw_body.decode("utf-8"))
        record = {
            "operationId": self.operation_id,
            "method": request.get_method(),
            "raw_path": split.path,
            "query": split.query,
            "headers": headers,
            "raw_body": raw_body,
            "body": body,
        }
        self.requests.append(record)

        if (
            request.get_method() != "PUT"
            or not match
            or split.query
            or headers.get("x-jwt-token") != self.expected_token
        ):
            error_body = (
                b'{"errorCode":"SECURITY_ERROR",'
                b'"errorMessage":"authorization required"}'
            )
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {"Content-Type": "application/json"},
                io.BytesIO(error_body),
            )

        forwarder_id = unquote(match.group("id"))
        new_resource = {"id": forwarder_id}
        new_resource.update(body)
        if self.resource != new_resource:
            self.resource = new_resource
            self.effect_count += 1
        if self.drop_first_response and not self._dropped_response:
            self._dropped_response = True
            raise http.client.RemoteDisconnected("response lost after apply")

        response_body = json.dumps(
            new_resource,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return response_body

        return Response()


@contextmanager
def contract_transport_mock(*, expected_token, drop_first_response=False):
    """Prefer the real loopback server, with a request-level sandbox fallback."""

    loopback = ContractLogManagementMock(
        expected_token=expected_token,
        drop_first_response=drop_first_response,
    )
    try:
        entered = loopback.__enter__()
    except PermissionError:
        fallback = ContractRequestMock(
            expected_token=expected_token,
            drop_first_response=drop_first_response,
        )
        with patch(
            "vcf_operations.client.urlopen",
            fallback.urlopen,
            create=True,
        ):
            yield fallback
    else:
        try:
            yield entered
        finally:
            loopback.__exit__(None, None, None)
