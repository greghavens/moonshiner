"""Loopback credential proxy for the Pi/Z.ai teacher.

The sandboxed agent is configured to talk to ``http://127.0.0.1:<port>`` with a
fixed dummy bearer token. This proxy runs host-side (outside the sandbox),
swaps the dummy for the real provider key, forwards to the upstream coding API,
and records an audit of every exchange — status codes and the ``model`` field
of each upstream response. Model attestation later requires that the upstream
actually answered as the model we asked for, closing the gap where a sandbox
could otherwise claim an unverified identity.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DUMMY_TOKEN = "moonshiner-loopback-proxy-token"


@dataclass
class Audit:
    exchanges: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def response_models(self) -> list[str]:
        seen: list[str] = []
        for exchange in self.exchanges:
            model = exchange.get("response_model")
            if model and model not in seen:
                seen.append(model)
        return seen

    def had_success(self) -> bool:
        return any(200 <= exchange.get("status", 0) < 300
                   for exchange in self.exchanges)

    def snapshot(self) -> dict:
        return {
            "exchanges": list(self.exchanges),
            "errors": list(self.errors),
            "response_models": self.response_models(),
            "had_success": self.had_success(),
        }


def _extract_model(body: bytes) -> str | None:
    """Pull a ``model`` field from a JSON or SSE (streamed) response body."""
    text = body.decode("utf-8", "replace")
    try:
        return json.loads(text).get("model")
    except (json.JSONDecodeError, AttributeError):
        pass
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            model = json.loads(payload).get("model")
        except (json.JSONDecodeError, AttributeError):
            continue
        if model:
            return model
    return None


def _make_handler(upstream: str, real_key: str, audit: Audit):
    upstream_base = upstream.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # silence default stderr logging
            return

        def _relay(self, method: str):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            target = upstream_base + self.path
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in {"host", "authorization",
                                              "content-length", "connection"}}
            headers["Authorization"] = f"Bearer {real_key}"
            request = Request(target, data=body or None, headers=headers,
                              method=method)
            try:
                with urlopen(request, timeout=600) as response:
                    payload = response.read()
                    status = response.status
                    response_headers = dict(response.headers)
            except HTTPError as error:
                payload = error.read()
                status = error.code
                response_headers = dict(error.headers or {})
            except URLError as error:
                audit.errors.append(f"{method} {self.path}: {error.reason}")
                self.send_error(502, "upstream unreachable")
                return
            audit.exchanges.append({
                "method": method,
                "path": urlparse(self.path).path,
                "status": status,
                "response_model": _extract_model(payload),
            })
            self.send_response(status)
            for key, value in response_headers.items():
                if key.lower() in {"transfer-encoding", "connection",
                                   "content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):  # noqa: N802 (http.server API)
            self._relay("POST")

        def do_GET(self):  # noqa: N802
            self._relay("GET")

    return Handler


class ProxySession:
    """A running loopback proxy; ``base_url`` is what the sandbox should use."""

    def __init__(self, upstream: str, real_key: str):
        self.audit = Audit()
        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_handler(upstream, real_key, self.audit))
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "ProxySession":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def snapshot(self) -> dict:
        return self.audit.snapshot()


@contextmanager
def credential_proxy(upstream: str, real_key: str):
    session = ProxySession(upstream, real_key).start()
    try:
        yield session
    finally:
        session.stop()
