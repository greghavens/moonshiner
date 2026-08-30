"""Loopback-only mock derived from docs/contract.json."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread
from typing import Any
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class RequestRecord:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes


class ContractMock:
    """Serve exactly the operations named by the protected local contract."""

    def __init__(self, *, failures_after_apply: int = 1):
        contract_path = Path(__file__).resolve().parent / "docs" / "contract.json"
        self.contract = json.loads(contract_path.read_text(encoding="utf-8"))
        operations = self.contract["operations"]
        if [item["operationId"] for item in operations] != [
            "updateSearchBasedAlertConfig"
        ]:
            raise ValueError("mock contract operation set changed")
        self.operation = operations[0]
        template = self.contract["server_base_path"] + self.operation["path"]
        escaped = re.escape(template).replace(r"\{id\}", r"(?P<id>[^/]+)")
        self._route = re.compile(f"^{escaped}$")
        self.failures_after_apply = failures_after_apply
        self.requests: list[RequestRecord] = []
        self.state: dict[str, dict[str, Any]] = {}
        self.effect_count = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("mock is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "ContractMock":
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _empty_reply(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_PUT(self) -> None:
                parsed = urlsplit(self.path)
                match = owner._route.fullmatch(parsed.path)
                if match is None or parsed.query or parsed.fragment:
                    self._reply(404, {"error": "operation not in contract"})
                    return

                length_text = self.headers.get("Content-Length")
                try:
                    length = int(length_text or "")
                except ValueError:
                    self._reply(400, {"error": "invalid content length"})
                    return
                raw = self.rfile.read(length)
                record = RequestRecord(
                    method=self.command,
                    target=self.path,
                    headers={key.lower(): value for key, value in self.headers.items()},
                    body=raw,
                )
                owner.requests.append(record)

                expected_auth = (
                    owner.contract["security_schemes"]["ApiKeyAuth"]["value_prefix"]
                    + "fixture-token"
                )
                if self.headers.get("Authorization") != expected_auth:
                    self._reply(401, {"error": "wrong authorization wire value"})
                    return
                if self.headers.get("Content-Type") != "application/json":
                    self._reply(400, {"error": "wrong content type"})
                    return
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._reply(400, {"error": "invalid json"})
                    return
                if not isinstance(payload, dict):
                    self._reply(400, {"error": "body must be an object"})
                    return

                alert_id = unquote(match.group("id"))
                before = owner.state.get(alert_id)
                if before != payload:
                    owner.effect_count += 1
                owner.state[alert_id] = payload

                if owner.failures_after_apply:
                    owner.failures_after_apply -= 1
                    self._empty_reply(500)
                    return
                response = {"entity_id": alert_id, "enabled": True, **payload}
                self._reply(200, response)

            def do_GET(self) -> None:
                self._reply(405, {"error": "method not in contract"})

            do_POST = do_GET
            do_PATCH = do_GET
            do_DELETE = do_GET

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        assert self._server is not None
        assert self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
        self._server = None
        self._thread = None
