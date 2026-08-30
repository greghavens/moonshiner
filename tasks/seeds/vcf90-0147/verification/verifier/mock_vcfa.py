from __future__ import annotations

import json
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


class MockState:
    def __init__(
        self,
        contract_path: Path,
        request_log: Path,
        forced_update_status: int | None = None,
    ) -> None:
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.request_log = request_log
        self.lock = threading.Lock()
        self.login_count = 0
        self.token_one_patch_count = 0
        self.forced_update_status = forced_update_status
        self.projects: dict[str, dict[str, Any]] = {
            "project-alpha": {"id": "project-alpha", "name": "legacy-alpha"},
            "project beta/blue": {"id": "project beta/blue", "name": "legacy-beta"},
        }

        operations = {item["id"]: item for item in self.contract["operations"]}
        self.login_operation = operations["retrieveAuthToken"]
        self.update_operation = operations["updateProject"]
        self.api_version = self.contract["wireProfile"]["apiVersion"]
        self.content_type = self.contract["wireProfile"]["jsonContentType"]
        self.expired_token_status = self.contract["scenarioProfile"]["expiredAccessTokenStatus"]
        self.named_operations = {
            (item["method"], item["path"]) for item in self.contract["operations"]
        }

    def log_request(self, entry: dict[str, Any]) -> None:
        with self.lock:
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return deepcopy(self.projects)


def _handler_for(state: MockState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "VCFAContractMock/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PATCH(self) -> None:
            self._dispatch("PATCH")

        def do_GET(self) -> None:
            self._reject_unknown()

        def do_PUT(self) -> None:
            self._reject_unknown()

        def do_DELETE(self) -> None:
            self._reject_unknown()

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length)

        def _dispatch(self, method: str) -> None:
            body = self._read_body()
            state.log_request(
                {
                    "method": method,
                    "target": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body.decode("utf-8"),
                }
            )

            parsed = urlsplit(self.path)
            if method == state.login_operation["method"] and parsed.path == state.login_operation["path"]:
                self._login(parsed.query, body)
                return

            update_path = state.update_operation["path"]
            prefix, suffix = update_path.split("{id}")
            if method == state.update_operation["method"] and parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
                encoded_id = parsed.path[len(prefix) : len(parsed.path) - len(suffix) if suffix else None]
                if encoded_id and "/" not in encoded_id:
                    self._update_project(parsed.query, unquote(encoded_id), body)
                    return

            self._json_response(404, {"message": "operation not named by contract"})

        def _query_is_pinned(self, query: str) -> bool:
            return parse_qs(query, keep_blank_values=True) == {"apiVersion": [state.api_version]}

        def _json_body(self, body: bytes) -> Any:
            if self.headers.get("Content-Type") != state.content_type:
                raise ValueError("wrong content type")
            return json.loads(body.decode("utf-8"))

        def _login(self, query: str, body: bytes) -> None:
            try:
                payload = self._json_body(body)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._json_response(400, {"message": "invalid login body"})
                return
            if not self._query_is_pinned(query) or payload != {"refreshToken": "fixture-refresh-token"}:
                self._json_response(400, {"message": "login contract mismatch"})
                return

            with state.lock:
                state.login_count += 1
                login_count = state.login_count
            if login_count == 1:
                token = "access-token-one"
            elif login_count == 2:
                token = "access-token-two"
            else:
                self._json_response(403, {"message": "unexpected token exchange"})
                return
            self._json_response(200, {"tokenType": "Bearer", "token": token})

        def _update_project(self, query: str, project_id: str, body: bytes) -> None:
            if not self._query_is_pinned(query):
                self._json_response(400, {"message": "query contract mismatch"})
                return

            if state.forced_update_status is not None:
                self._json_response(
                    state.forced_update_status,
                    {
                        "message": "forced update failure",
                        "statusCode": state.forced_update_status,
                    },
                )
                return

            authorization = self.headers.get("Authorization")
            if authorization == "Bearer access-token-one":
                with state.lock:
                    state.token_one_patch_count += 1
                    patch_count = state.token_one_patch_count
                if patch_count > 1:
                    self._json_response(
                        state.expired_token_status,
                        {
                            "message": "access token expired",
                            "statusCode": state.expired_token_status,
                        },
                    )
                    return
            elif authorization != "Bearer access-token-two":
                self._json_response(
                    state.expired_token_status,
                    {
                        "message": "invalid access token",
                        "statusCode": state.expired_token_status,
                    },
                )
                return

            try:
                payload = self._json_body(body)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                self._json_response(400, {"message": "invalid project body"})
                return

            request_contract = state.update_operation["request"]
            allowed = set(request_contract["required"] + request_contract["optional"])
            if (
                project_id not in state.projects
                or not isinstance(payload, dict)
                or set(payload) - allowed
                or any(key not in payload for key in request_contract["required"])
            ):
                self._json_response(400, {"message": "project contract mismatch"})
                return

            with state.lock:
                state.projects[project_id].update(payload)
                response = deepcopy(state.projects[project_id])
            self._json_response(200, response)

        def _reject_unknown(self) -> None:
            body = self._read_body()
            state.log_request(
                {
                    "method": self.command,
                    "target": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body.decode("utf-8"),
                }
            )
            self._json_response(404, {"message": "operation not named by contract"})

        def _json_response(self, status: int, payload: Any) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def start_mock(
    contract_path: Path,
    request_log: Path,
    forced_update_status: int | None = None,
) -> tuple[ThreadingHTTPServer, MockState]:
    state = MockState(contract_path, request_log, forced_update_status)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state
