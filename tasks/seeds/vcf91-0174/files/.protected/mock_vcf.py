"""Contract-pinned loopback VCF Operations Log Management service."""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request


class _FixtureResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FixtureResponse":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _InProcessOpener:
    def __init__(self, owner: "ContractPinnedVcfMock") -> None:
        self.owner = owner

    def open(self, request: Request, *, timeout: float) -> _FixtureResponse:
        del timeout
        status, body = self.owner._dispatch_in_process(request)
        return _FixtureResponse(status, body)


class ContractPinnedVcfMock:
    """Serve only method/path pairs named by the focused OpenAPI projection."""

    def __init__(
        self,
        contract_path: Path,
        *,
        old_token: str,
        new_token: str,
        initial_forwarders: list[dict[str, Any]],
        created_id_factory: Callable[[int], str],
        expire_after_successful_posts: int,
    ) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        allowed: dict[tuple[str, str], str] = {}
        for path, path_item in contract["paths"].items():
            for method, operation in path_item.items():
                if isinstance(operation, dict) and "operationId" in operation:
                    allowed[(method.upper(), path)] = operation["operationId"]

        named = contract["x-source"]["operationIds"]
        if list(allowed.values()) != named:
            raise ValueError("contract route table and named operations disagree")

        self.allowed_operations = allowed
        self.old_token = old_token
        self.new_token = new_token
        self.expire_after_successful_posts = expire_after_successful_posts
        self.forwarders = copy.deepcopy(initial_forwarders)
        self.created_id_factory = created_id_factory
        self.request_log: list[dict[str, Any]] = []
        self._successful_posts = 0
        self._old_token_expired = False
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._in_process = False

    @property
    def base_url(self) -> str:
        if self._server is None:
            if self._in_process:
                return "http://127.0.0.1:1"
            raise RuntimeError("mock server has not been started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def in_process(self) -> bool:
        return self._in_process

    def enable_in_process_fallback(self) -> None:
        if self._server is not None:
            raise RuntimeError("cannot enable fallback after loopback startup")
        self._in_process = True

    def install_fallback_transport(self, client: Any) -> None:
        if not self._in_process:
            return
        client._opener = _InProcessOpener(self)

    def clear_request_log(self) -> None:
        with self._lock:
            self.request_log.clear()

    def __enter__(self) -> "ContractPinnedVcfMock":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
                self._dispatch()

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _dispatch(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length) if content_length else b""
                parsed = urlsplit(self.path)
                query = parse_qsl(parsed.query, keep_blank_values=True)
                operation_id = owner.allowed_operations.get(
                    (self.command, parsed.path)
                )

                status = 404
                payload: Any = {
                    "errorCode": "API_ERROR",
                    "errorMessage": "operation is not in the pinned contract",
                }
                if operation_id is not None and not parsed.query:
                    if operation_id == "getAllLogForwarders":
                        status, payload = owner._list_forwarders(
                            self.headers.get("X-JWT-Token")
                        )
                    elif operation_id == "createLogForwarder":
                        status, payload = owner._create_forwarder(
                            self.headers.get("X-JWT-Token"), body
                        )

                encoded = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                raw_headers = [
                    (name.lower(), value)
                    for name, value in self.headers.raw_items()
                ]
                headers: dict[str, list[str]] = {}
                for name, value in raw_headers:
                    headers.setdefault(name, []).append(value)
                try:
                    parsed_json = json.loads(body.decode("utf-8")) if body else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    parsed_json = None
                with owner._lock:
                    owner.request_log.append(
                        {
                            "operationId": operation_id,
                            "method": self.command,
                            "target": self.path,
                            "path": parsed.path,
                            "query": query,
                            "rawHeaders": raw_headers,
                            "headers": headers,
                            "body": body,
                            "json": parsed_json,
                            "status": status,
                        }
                    )

                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="vcf91-0174-contract-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _list_forwarders(self, token: str | None) -> tuple[int, Any]:
        if token == self.old_token and self._old_token_expired:
            return 403, {
                "errorCode": "SECURITY_ERROR",
                "errorMessage": "access token expired",
            }
        if token not in (self.old_token, self.new_token):
            return 403, {
                "errorCode": "SECURITY_ERROR",
                "errorMessage": "authentication required",
            }
        with self._lock:
            return 200, copy.deepcopy(self.forwarders)

    def _create_forwarder(
        self, token: str | None, raw_body: bytes
    ) -> tuple[int, Any]:
        with self._lock:
            if token == self.old_token:
                if self._successful_posts >= self.expire_after_successful_posts:
                    self._old_token_expired = True
                    return 403, {
                        "errorCode": "SECURITY_ERROR",
                        "errorMessage": "access token expired",
                    }
            elif token != self.new_token:
                return 403, {
                    "errorCode": "SECURITY_ERROR",
                    "errorMessage": "authentication required",
                }

            try:
                candidate = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, {
                    "errorCode": "JSON_FORMAT_ERROR",
                    "errorMessage": "request body must be JSON",
                }
            if not isinstance(candidate, dict):
                return 400, {
                    "errorCode": "FIELD_ERROR",
                    "errorMessage": "request body must be an object",
                }
            name = candidate.get("name")
            if not isinstance(name, str) or not name:
                return 400, {
                    "errorCode": "FIELD_ERROR",
                    "errorMessage": "name must be a non-empty string",
                }
            if any(item.get("name") == name for item in self.forwarders):
                return 400, {
                    "errorCode": "FIELD_ERROR",
                    "errorMessage": "forwarder name already exists",
                }

            created = copy.deepcopy(candidate)
            created["id"] = self.created_id_factory(len(self.forwarders))
            self.forwarders.append(created)
            self._successful_posts += 1
            return 201, copy.deepcopy(created)

    def _dispatch_in_process(self, request: Request) -> tuple[int, bytes]:
        parsed = urlsplit(request.full_url)
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        method = request.get_method()
        body = request.data or b""
        operation_id = self.allowed_operations.get((method, parsed.path))

        raw_headers = [
            (name.lower(), value) for name, value in request.header_items()
        ]
        if body and not any(name == "content-length" for name, _ in raw_headers):
            raw_headers.append(("content-length", str(len(body))))
        headers: dict[str, list[str]] = {}
        for name, value in raw_headers:
            headers.setdefault(name, []).append(value)
        token_values = headers.get("x-jwt-token", [])
        token = token_values[0] if len(token_values) == 1 else None

        status = 404
        payload: Any = {
            "errorCode": "API_ERROR",
            "errorMessage": "operation is not in the pinned contract",
        }
        if operation_id is not None and not parsed.query:
            if operation_id == "getAllLogForwarders":
                status, payload = self._list_forwarders(token)
            elif operation_id == "createLogForwarder":
                status, payload = self._create_forwarder(token, body)

        try:
            parsed_json = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_json = None
        with self._lock:
            self.request_log.append(
                {
                    "operationId": operation_id,
                    "method": method,
                    "target": target,
                    "path": parsed.path,
                    "query": parse_qsl(
                        parsed.query, keep_blank_values=True
                    ),
                    "rawHeaders": raw_headers,
                    "headers": headers,
                    "body": body,
                    "json": parsed_json,
                    "status": status,
                }
            )
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return status, encoded
