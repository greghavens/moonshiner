"""Loopback mock for only the operations named in docs/contract.json."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import importlib
from io import BytesIO
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
import urllib.request as urllib_request
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
PINNED_OPERATIONS = {"createLogForwarder", "patchLogForwarder"}


class _MemoryResponse:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_MemoryResponse":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "VCFContractMock/1"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _record(self, body: bytes) -> None:
        self.server.fixture.requests.append(
            {
                "method": self.command,
                "rawPath": self.path,
                "headers": list(self.headers.items()),
                "body": body,
            }
        )

    def _json_response(self, status: int, body: Any | None = None) -> None:
        payload = b""
        if body is not None:
            payload = json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
        else:
            self.send_response(status)
            self.send_header("Content-Length", "0")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _dispatch(self) -> None:
        body = self._body()
        self._record(body)
        split = urlsplit(self.path)

        if (
            self.command == "POST"
            and split.path == "/api/v2/logs/forwarders"
            and not split.query
        ):
            self._create(body)
            return

        prefix = "/api/v2/logs/forwarders/"
        if (
            self.command == "PATCH"
            and split.path.startswith(prefix)
            and len(split.path) > len(prefix)
            and "/" not in split.path[len(prefix) :]
            and not split.query
        ):
            self._patch(split.path[len(prefix) :], body)
            return

        self._json_response(
            404,
            {
                "errorCode": "API_ERROR",
                "errorMessage": "operation is not in the pinned contract",
                "errorDetails": {},
            },
        )

    def _decode_object(self, body: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_response(
                400,
                {
                    "errorCode": "JSON_FORMAT_ERROR",
                    "errorMessage": "request body is not a JSON object",
                    "errorDetails": {},
                },
            )
            return None
        if not isinstance(value, dict):
            self._json_response(
                400,
                {
                    "errorCode": "JSON_FORMAT_ERROR",
                    "errorMessage": "request body is not a JSON object",
                    "errorDetails": {},
                },
            )
            return None
        return value

    def _create(self, body: bytes) -> None:
        value = self._decode_object(body)
        if value is None:
            return
        if value.get("name") == "secondary-siem":
            self._json_response(
                502,
                {
                    "errorCode": "SSL_ERROR",
                    "errorDetails": {"destination": "secondary-siem"},
                    "errorMessage": "certificate is not trusted",
                },
            )
            return
        resource_id = f"fw-{len(self.server.fixture.resources) + 1:03d}"
        stored = dict(value)
        stored["id"] = resource_id
        self.server.fixture.resources[resource_id] = stored
        self._json_response(201, stored)

    def _patch(self, resource_id: str, body: bytes) -> None:
        value = self._decode_object(body)
        if value is None:
            return
        current = self.server.fixture.resources.get(resource_id)
        if current is None:
            self._json_response(
                404,
                {
                    "errorCode": "API_ERROR",
                    "errorDetails": {"id": resource_id},
                    "errorMessage": "log forwarder not found",
                },
            )
            return
        current.update(value)
        current["id"] = resource_id
        self._json_response(200, current)

    do_POST = _dispatch
    do_PATCH = _dispatch
    do_GET = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


class ContractMock:
    """Context-managed loopback service with an inspectable request log."""

    def __init__(self) -> None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        if contract["derivedFrom"]["commit"] != PINNED_COMMIT:
            raise RuntimeError("contract commit does not match the fixture pin")
        if set(contract["operations"]) != PINNED_OPERATIONS:
            raise RuntimeError("fixture only supports the pinned operation set")
        self.requests: list[dict[str, Any]] = []
        self.resources: dict[str, dict[str, Any]] = {}
        try:
            self._server: ThreadingHTTPServer | None = ThreadingHTTPServer(
                ("127.0.0.1", 0), _Handler
            )
        except PermissionError:
            self._server = None
        if self._server is None:
            self._thread: Thread | None = None
            self.base_url = "http://127.0.0.1"
        else:
            self._server.fixture = self
            self._thread = Thread(target=self._server.serve_forever, daemon=True)
            self.base_url = f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "ContractMock":
        if self._thread is not None:
            self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._server is None or self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    @contextmanager
    def route_client_requests(self):
        """Route urllib calls in memory only when the sandbox forbids sockets."""

        if self._server is not None:
            yield
            return
        client_module = importlib.import_module("vcf_log_forwarder.client")
        original_urlopen = urllib_request.urlopen
        aliases = [
            name
            for name, value in vars(client_module).items()
            if value is original_urlopen
        ]
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(urllib_request, "urlopen", self._memory_urlopen)
            )
            for name in aliases:
                stack.enter_context(
                    patch.object(client_module, name, self._memory_urlopen)
                )
            yield

    def open_url(self, request: Request, timeout: float) -> Any:
        """Open a verifier request through the active mock transport."""

        if self._server is not None:
            return urlopen(request, timeout=timeout)
        return self._memory_urlopen(request, timeout=timeout)

    def _memory_urlopen(self, request: Request, timeout: float) -> _MemoryResponse:
        del timeout
        split = urlsplit(request.full_url)
        raw_path = split.path + (f"?{split.query}" if split.query else "")
        body = request.data or b""
        headers = list(request.header_items())
        names = {name.lower() for name, _ in headers}
        if body and "content-length" not in names:
            headers.append(("Content-Length", str(len(body))))
        self.requests.append(
            {
                "method": request.get_method(),
                "rawPath": raw_path,
                "headers": headers,
                "body": body,
            }
        )

        prefix = "/api/v2/logs/forwarders/"
        if (
            request.get_method() == "POST"
            and split.path == "/api/v2/logs/forwarders"
            and not split.query
        ):
            value = self._decode_memory_object(body)
            if value.get("name") == "secondary-siem":
                return self._memory_result(
                    request,
                    502,
                    {
                        "errorCode": "SSL_ERROR",
                        "errorDetails": {"destination": "secondary-siem"},
                        "errorMessage": "certificate is not trusted",
                    },
                )
            resource_id = f"fw-{len(self.resources) + 1:03d}"
            stored = dict(value)
            stored["id"] = resource_id
            self.resources[resource_id] = stored
            return self._memory_result(request, 201, stored)

        if (
            request.get_method() == "PATCH"
            and split.path.startswith(prefix)
            and len(split.path) > len(prefix)
            and "/" not in split.path[len(prefix) :]
            and not split.query
        ):
            value = self._decode_memory_object(body)
            resource_id = split.path[len(prefix) :]
            current = self.resources.get(resource_id)
            if current is None:
                return self._memory_result(
                    request,
                    404,
                    {
                        "errorCode": "API_ERROR",
                        "errorDetails": {"id": resource_id},
                        "errorMessage": "log forwarder not found",
                    },
                )
            current.update(value)
            current["id"] = resource_id
            return self._memory_result(request, 200, current)

        return self._memory_result(
            request,
            404,
            {
                "errorCode": "API_ERROR",
                "errorMessage": "operation is not in the pinned contract",
                "errorDetails": {},
            },
        )

    @staticmethod
    def _decode_memory_object(body: bytes) -> dict[str, Any]:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AssertionError("client sent invalid JSON to contract mock") from error
        if not isinstance(value, dict):
            raise AssertionError("client did not send a JSON object to contract mock")
        return value

    @staticmethod
    def _memory_result(
        request: Request, status: int, body: dict[str, Any]
    ) -> _MemoryResponse:
        payload = json.dumps(
            body, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        if status >= 400:
            raise HTTPError(
                request.full_url,
                status,
                body.get("errorMessage", "contract mock error"),
                None,
                BytesIO(payload),
            )
        return _MemoryResponse(status, payload)
