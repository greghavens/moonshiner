"""Contract-pinned loopback VCF Automation service used only by the verifier."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


EXPECTED_OPERATIONS = {
    ("POST", "/tm/oauth/tenant/{tenant}/token", "exchangeRefreshToken"),
    ("GET", "/project-service/api/projects", "getAllProjects"),
}


class ContractMock:
    def __init__(self, contract_path: Path):
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            (entry["method"], entry["path"], entry["operationId"])
            for entry in contract["operations"]
        }
        if actual != EXPECTED_OPERATIONS:
            raise AssertionError(f"mock/contract operation mismatch: {actual!r}")
        if contract["source"]["kind"] != "reference-documentation":
            raise AssertionError("contract must identify itself as reference-derived")

        self.request_log: list[dict[str, object]] = []
        self._lock = threading.Lock()
        self._token_serial = 0
        self._current_token: str | None = None
        self._expired_tokens: set[str] = set()
        self._successful_collections = 0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_type())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_uri(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/"

    def __enter__(self) -> "ContractMock":
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def _append_log(self, entry: dict[str, object]) -> None:
        with self._lock:
            self.request_log.append(entry)

    def _handler_type(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ContractPinnedVcfAutomation/1"

            def log_message(self, format, *args):
                return

            def _json(self, status: int, value: object) -> None:
                body = json.dumps(value, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _record(self, status: int, body: str = "") -> None:
                split = urlsplit(self.path)
                owner._append_log({
                    "method": self.command,
                    "path": split.path,
                    "query": parse_qs(split.query, keep_blank_values=True),
                    "authorization": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": body,
                    "status": status,
                })

            def do_POST(self) -> None:
                split = urlsplit(self.path)
                pieces = split.path.split("/")
                is_token = (
                    len(pieces) == 6
                    and pieces[1:4] == ["tm", "oauth", "tenant"]
                    and pieces[5:] == ["token"]
                    and bool(unquote(pieces[4]))
                )
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                form = parse_qs(body, keep_blank_values=True)
                valid_form = form == {
                    "grant_type": ["refresh_token"],
                    "refresh_token": ["fixture-refresh-token"],
                }
                valid_headers = (
                    self.headers.get("Accept") == "application/json"
                    and self.headers.get("Content-Type", "").split(";", 1)[0]
                    == "application/x-www-form-urlencoded"
                )
                if not is_token:
                    self._record(404, body)
                    self._json(404, {"error": "operation not in contract"})
                    return
                if not valid_form or not valid_headers:
                    self._record(400, body)
                    self._json(400, {"error": "request does not match contract"})
                    return

                with owner._lock:
                    owner._token_serial += 1
                    owner._current_token = f"fixture-access-{owner._token_serial}"
                    token = owner._current_token
                self._record(200, body)
                self._json(200, {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                })

            def do_GET(self) -> None:
                split = urlsplit(self.path)
                if split.path != "/project-service/api/projects":
                    self._record(404)
                    self._json(404, {"error": "operation not in contract"})
                    return

                authorization = self.headers.get("Authorization")
                token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
                with owner._lock:
                    authorized = token == owner._current_token and token not in owner._expired_tokens
                if not authorized:
                    self._record(401)
                    self._json(401, {"error": "access token expired"})
                    return

                query = parse_qs(split.query, keep_blank_values=True)
                try:
                    page = int(query.get("page", ["0"])[0])
                except ValueError:
                    self._record(400)
                    self._json(400, {"error": "page must be an integer"})
                    return
                pages = [
                    [{"id": "project-z", "name": "zulu"}, {"id": "project-a", "name": "alpha"}],
                    [{"id": "project-b", "name": "bravo"}, {"id": "project-m", "name": "mike"}],
                ]
                if page < 0 or page >= len(pages):
                    self._record(400)
                    self._json(400, {"error": "page outside fixture"})
                    return

                with owner._lock:
                    response_index = owner._successful_collections
                    owner._successful_collections += 1
                    if response_index == 0 and token is not None:
                        owner._expired_tokens.add(token)
                content = list(pages[page])
                if response_index % 2 == 0:
                    content.reverse()

                self._record(200)
                self._json(200, {
                    "content": content,
                    "number": page,
                    "numberOfElements": len(content),
                    "size": len(content),
                    "totalElements": sum(len(items) for items in pages),
                    "totalPages": len(pages),
                    "first": page == 0,
                    "last": page == len(pages) - 1,
                    "empty": not content,
                })

        return Handler
