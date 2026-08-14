"""Contract-pinned loopback VCF Automation fixture used by acceptance checks."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit


CONTRACT_PATH = Path(__file__).with_name("docs") / "contract.json"


def _load_routes() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    operations = contract["operations"]
    names = [operation["operation"] for operation in operations]
    if names != ["Get Project", "Modify Project"]:
        raise RuntimeError(f"unexpected protected contract operations: {names!r}")

    routes: list[dict[str, Any]] = []
    for operation in operations:
        template = operation["path_template"]
        pattern = "^" + re.escape(template).replace(r"\{id\}", r"(?P<id>[^/]+)") + "$"
        routes.append({**operation, "pattern": re.compile(pattern)})
    return contract, routes


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        project: dict[str, Any] | None,
        expected_token: str,
        get_body: bytes | None,
        patch_response: tuple[int, bytes] | None,
    ) -> None:
        self.contract, self.routes = _load_routes()
        self.project = None if project is None else dict(project)
        self.expected_token = expected_token
        self.get_body = get_body
        self.patch_response = patch_response
        self.request_log: list[dict[str, Any]] = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body)

    def _send_bytes(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "target": self.path,
            "path": split.path,
            "query": parse_qsl(split.query, keep_blank_values=True),
            "headers": list(self.headers.raw_items()),
            "body": body,
        }
        self.server.request_log.append(record)

        path_matches = [
            (route, route["pattern"].match(split.path))
            for route in self.server.routes
            if route["pattern"].match(split.path)
        ]
        if not path_matches:
            self._send_json(404, {"message": "operation is not in the protected contract"})
            return

        route_match = next(
            ((route, match) for route, match in path_matches if route["method"] == self.command),
            None,
        )
        if route_match is None:
            self._send_json(405, {"message": "method is not in the protected contract"})
            return
        route, match = route_match
        assert match is not None

        expected_query = [("apiVersion", self.server.contract["api_version"])]
        if record["query"] != expected_query:
            self._send_json(400, {"message": "unexpected query"})
            return
        if self.headers.get("Authorization") != f"Bearer {self.server.expected_token}":
            self._send_json(403, {"message": "forbidden"})
            return

        requested_id = unquote(match.group("id"))
        if self.server.project is None or self.server.project.get("id") != requested_id:
            self._send_json(404, {"message": "project not found"})
            return

        if route["operation"] == "Get Project":
            if self.server.get_body is not None:
                self._send_bytes(200, self.server.get_body)
                return
            self._send_json(200, self.server.project)
            return

        if self.server.patch_response is not None:
            status, response_body = self.server.patch_response
            self._send_bytes(status, response_body)
            return

        try:
            patch = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"message": "invalid JSON"})
            return
        if not isinstance(patch, dict) or "name" not in patch:
            self._send_json(400, {"message": "invalid project update"})
            return

        allowed = {field["wire_name"] for field in route["request"]["fields"]}
        if set(patch) - allowed:
            self._send_json(400, {"message": "field is not in the protected contract"})
            return
        self.server.project.update(patch)
        self._send_json(200, self.server.project)

    do_GET = _dispatch
    do_PATCH = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


class MockVCFAutomation:
    """Context manager exposing a loopback endpoint and its in-memory log."""

    def __init__(
        self,
        project: dict[str, Any] | None,
        token: str = "test-token",
        get_body: bytes | None = None,
        patch_response: tuple[int, bytes] | None = None,
    ) -> None:
        self._server = _Server(project, token, get_body, patch_response)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def authority(self) -> str:
        return urlsplit(self.base_url).netloc

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.request_log

    @property
    def project(self) -> dict[str, Any] | None:
        return self._server.project

    def __enter__(self) -> "MockVCFAutomation":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
