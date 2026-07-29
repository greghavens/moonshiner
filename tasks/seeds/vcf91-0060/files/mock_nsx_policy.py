"""Contract-pinned loopback NSX Policy service for protected verification.

This is an API fixture, not a harness-tool replacement.  It has no default
appliance state: tests explicitly script each operation response.  Its route
table is built solely from the operationIds in docs/contract.json.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

_ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((_ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))


def _compile_path(template: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    parts: list[str] = []
    names: list[str] = []
    cursor = 0
    for index, match in enumerate(re.finditer(r"\{([^{}]+)\}", template)):
        parts.append(re.escape(template[cursor : match.start()]))
        parts.append(f"(?P<p{index}>[^/]+)")
        names.append(match.group(1))
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$"), tuple(names)


class MockNsxPolicy:
    """Scriptable localhost service with a verifier-readable request log."""

    def __init__(self):
        self.request_log: list[dict] = []
        self._scripts: dict[str, list[tuple[int, object]]] = {}
        self._lock = threading.RLock()
        self._routes = []
        for operation_id, operation in CONTRACT["operations"].items():
            if operation_id != operation["operationId"]:
                raise ValueError("contract operation key/operationId mismatch")
            pattern, parameter_names = _compile_path(
                CONTRACT["basePath"] + operation["path"]
            )
            self._routes.append(
                (
                    operation["method"],
                    pattern,
                    parameter_names,
                    operation_id,
                )
            )
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def operation_ids(self) -> frozenset[str]:
        return frozenset(route[3] for route in self._routes)

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("mock server is not running")
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def script(
        self, operation_id: str, responses: list[tuple[int, object]]
    ) -> None:
        if operation_id not in self.operation_ids:
            raise KeyError(f"operation is not in the pinned contract: {operation_id}")
        if not responses:
            raise ValueError("responses must not be empty")
        with self._lock:
            self._scripts[operation_id] = list(responses)

    def _match(self, method: str, path: str):
        for route_method, pattern, names, operation_id in self._routes:
            match = pattern.fullmatch(path)
            if method == route_method and match:
                values = {
                    name: unquote(match.group(f"p{index}"))
                    for index, name in enumerate(names)
                }
                return operation_id, values
        return None, {}

    def _take_response(self, operation_id: str) -> tuple[int, object]:
        with self._lock:
            responses = self._scripts.get(operation_id)
            if not responses:
                return 500, {
                    "error_code": 99001,
                    "error_message": f"No response scripted for {operation_id}",
                    "module_name": "mock",
                }
            return responses.pop(0)

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                return

            def _handle(self):
                parsed = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                try:
                    decoded_json = json.loads(body) if body else None
                except json.JSONDecodeError:
                    decoded_json = None

                operation_id, path_parameters = fixture._match(
                    self.command, parsed.path
                )
                entry = {
                    "operationId": operation_id,
                    "method": self.command,
                    "path": parsed.path,
                    "query": parsed.query,
                    "path_parameters": path_parameters,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                    "body": body,
                    "json": decoded_json,
                }
                with fixture._lock:
                    fixture.request_log.append(entry)

                authorization = self.headers.get("Authorization", "")
                if operation_id is None:
                    status, payload = 404, {
                        "error_code": 99002,
                        "error_message": (
                            f"No contract route for {self.command} {parsed.path}"
                        ),
                        "module_name": "mock",
                    }
                elif not authorization.startswith("Bearer ") or not authorization[7:]:
                    status, payload = 401, {
                        "error_code": 403,
                        "error_message": "A non-empty Bearer token is required",
                        "module_name": "common-services",
                    }
                else:
                    status, payload = fixture._take_response(operation_id)

                response = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            do_GET = _handle
            do_PATCH = _handle

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._httpd = None
        self._thread = None
