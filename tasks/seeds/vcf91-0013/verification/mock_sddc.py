"""Contract-pinned loopback SDDC Manager used by the protected verifier.

The mock is an API fixture, not a harness-tool replacement.  It has no default
appliance state: each test scripts responses explicitly.  Its route table is
built solely from the operationIds in docs/contract.json.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

_ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((_ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))


def _path_pattern(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9_]*)\}", template):
        parts.append(re.escape(template[cursor:match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("^" + "".join(parts) + "$")


class MockSddcManager:
    """Scriptable localhost service with a verifier-readable request log."""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.request_log: list[dict] = []
        self._scripts: dict[str, list[tuple[int, dict]]] = {}
        self._lock = threading.RLock()
        self._routes = []
        for operation_id, operation in CONTRACT["operations"].items():
            if operation_id != operation["operationId"]:
                raise ValueError("contract operation key/operationId mismatch")
            self._routes.append(
                (
                    operation["method"],
                    _path_pattern(operation["path"]),
                    operation_id,
                )
            )
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def operation_ids(self) -> frozenset[str]:
        return frozenset(route[2] for route in self._routes)

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("mock server is not running")
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def script(self, operation_id: str, responses: list[tuple[int, dict]]) -> None:
        if operation_id not in self.operation_ids:
            raise KeyError(f"operation is not in the pinned contract: {operation_id}")
        if not responses:
            raise ValueError("responses must not be empty")
        with self._lock:
            self._scripts[operation_id] = list(responses)

    def _match(self, method: str, path: str):
        for route_method, pattern, operation_id in self._routes:
            match = pattern.fullmatch(path)
            if method == route_method and match:
                return operation_id, match.groupdict()
        return None, {}

    def _take_response(self, operation_id: str) -> tuple[int, dict]:
        with self._lock:
            responses = self._scripts.get(operation_id)
            if not responses:
                return 500, {
                    "errorCode": "MOCK_RESPONSE_NOT_SCRIPTED",
                    "message": f"No response scripted for {operation_id}",
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
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body,
                    "json": decoded_json,
                }
                with fixture._lock:
                    fixture.request_log.append(entry)

                if operation_id is None:
                    status, payload = 404, {
                        "errorCode": "MOCK_ROUTE_NOT_IN_CONTRACT",
                        "message": f"No contract route for {self.command} {parsed.path}",
                    }
                elif self.headers.get("Authorization") != (
                    f"Bearer {fixture.access_token}"
                ):
                    status, payload = 401, {
                        "errorCode": "UNAUTHORIZED",
                        "message": "Bearer token required",
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
