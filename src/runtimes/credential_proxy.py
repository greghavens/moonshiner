"""Loopback credential proxy for sandboxed agent runtimes.

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
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

DUMMY_TOKEN = "moonshiner-loopback-proxy-token"

# Attestation only needs the model field from the first chunks of a response;
# cap what the audit keeps so a long stream never accumulates in memory.
_AUDIT_CAPTURE_BYTES = 262144


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
                response = urlopen(request, timeout=600)
            except HTTPError as error:
                response = error          # readable like a response
            except URLError as error:
                audit.errors.append(f"{method} {self.path}: {error.reason}")
                self.send_error(502, "upstream unreachable")
                return
            # Relay the body chunk-by-chunk as the upstream produces it.
            # Buffering the whole response here starves the client of the SSE
            # stream for the entire upstream turn — long teacher turns then
            # die on the client's idle timeout and get re-billed on retry.
            with response:
                status = getattr(response, "status", None) or response.code
                # Recorded before any byte goes back to the client, so a
                # mid-stream disconnect cannot lose the exchange; the model
                # field is filled in from the first chunks as they relay.
                exchange = {"method": method,
                            "path": urlparse(self.path).path,
                            "status": status, "response_model": None}
                audit.exchanges.append(exchange)
                self.send_response(status)
                for key, value in dict(response.headers or {}).items():
                    if key.lower() in {"transfer-encoding", "connection",
                                       "content-length"}:
                        continue
                    self.send_header(key, value)
                bodyless = status in (204, 304)
                if not bodyless:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                captured = b""
                read = getattr(response, "read1", response.read)
                try:
                    while not bodyless:
                        chunk = read(65536)
                        if not chunk:
                            self.wfile.write(b"0\r\n\r\n")
                            break
                        if len(captured) < _AUDIT_CAPTURE_BYTES:
                            captured += chunk
                        self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                        self.wfile.flush()
                finally:
                    exchange["response_model"] = _extract_model(captured)

        def do_POST(self):  # noqa: N802 (http.server API)
            self._relay("POST")

        def do_GET(self):  # noqa: N802
            self._relay("GET")

    return Handler


class _QuietDisconnectServer(ThreadingHTTPServer):
    """Suppress tracebacks for client disconnects, keep everything else loud.

    The sandboxed agent may abort or retry a request while the relay is
    streaming the response back; the audit entry is recorded in a ``finally``
    around the stream, so a broken pipe here loses nothing and is not an
    error of the proxy.
    """

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class ProxySession:
    """A running loopback proxy; ``base_url`` is what the sandbox should use."""

    def __init__(self, upstream: str, real_key: str):
        self.audit = Audit()
        self._server = _QuietDisconnectServer(
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
