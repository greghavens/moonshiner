"""Loopback proxy relay: streaming, key swap, attestation audit. Offline.

A fake upstream on 127.0.0.1 stands in for the provider, so every test is a
real HTTP exchange through the real proxy. The streaming test pins the
regression where the relay buffered the whole upstream body: the client must
see the first SSE chunk while the upstream is still holding the connection
open, or long teacher turns die on the client's idle timeout.
"""
import http.client
import json
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from runtimes.credential_proxy import DUMMY_TOKEN, ProxySession  # noqa: E402

REAL_KEY = "unit-test-real-key-not-a-secret"
SSE_FIRST = (b'data: {"id":"1","model":"moonshotai/kimi-k3",'
             b'"choices":[{"delta":{"content":"a"}}]}\n\n')
SSE_LAST = b"data: [DONE]\n\n"


class _Upstream(BaseHTTPRequestHandler):
    """Fake provider: records auth, streams two SSE chunks with a delay."""
    protocol_version = "HTTP/1.1"
    seen_auth: list[str] = []
    chunk_delay_s = 0.0

    def log_message(self, *_args):
        return

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        if self.path == "/error":
            body = json.dumps({"error": "bad key"}).encode()
            self.send_response(401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in (SSE_FIRST, SSE_LAST):
            self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
            time.sleep(type(self).chunk_delay_s)
        self.wfile.write(b"0\r\n\r\n")


class ProxyRelay(unittest.TestCase):
    def setUp(self):
        _Upstream.seen_auth = []
        _Upstream.chunk_delay_s = 0.0
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        threading.Thread(target=self.upstream.serve_forever,
                         daemon=True).start()
        self.addCleanup(self.upstream.shutdown)
        self.session = ProxySession(
            f"http://127.0.0.1:{self.upstream.server_port}", REAL_KEY).start()
        self.addCleanup(self.session.stop)

    def _post(self, path="/v1/chat/completions"):
        conn = http.client.HTTPConnection("127.0.0.1", self.session.port,
                                          timeout=10)
        self.addCleanup(conn.close)
        conn.request("POST", path, body=b"{}",
                     headers={"Authorization": f"Bearer {DUMMY_TOKEN}",
                              "Content-Type": "application/json"})
        return conn.getresponse()

    def _wait_models(self, timeout=2.0):
        # The model lands in the audit as the relay drains the stream, a
        # hair after the client sees the last byte — poll briefly.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            models = self.session.audit.response_models()
            if models:
                return models
            time.sleep(0.01)
        return self.session.audit.response_models()

    def test_swaps_dummy_for_real_key(self):
        response = self._post()
        response.read()
        self.assertEqual(_Upstream.seen_auth, [f"Bearer {REAL_KEY}"])

    def test_relays_body_and_records_attestation(self):
        response = self._post()
        payload = response.read()
        self.assertEqual(response.status, 200)
        self.assertIn(b"moonshotai/kimi-k3", payload)
        self.assertIn(b"[DONE]", payload)
        self.assertEqual(self._wait_models(), ["moonshotai/kimi-k3"])
        self.assertTrue(self.session.audit.had_success())

    def test_streams_chunks_before_upstream_finishes(self):
        # With a buffering relay the first byte arrives only after BOTH
        # chunks (≥ the full inter-chunk delay); streamed, it arrives while
        # the upstream is still sleeping before its second chunk.
        _Upstream.chunk_delay_s = 1.0
        started = time.monotonic()
        response = self._post()
        first = response.read1(65536)
        first_byte_after = time.monotonic() - started
        self.assertIn(b"kimi-k3", first)
        self.assertLess(first_byte_after, 0.9,
                        "first chunk waited for the full upstream body")
        rest = response.read()
        self.assertIn(b"[DONE]", rest)

    def test_upstream_http_error_is_relayed_and_audited(self):
        response = self._post("/error")
        payload = response.read()
        self.assertEqual(response.status, 401)
        self.assertIn(b"bad key", payload)
        snapshot = self.session.audit.snapshot()
        self.assertFalse(snapshot["had_success"])
        self.assertEqual(snapshot["exchanges"][0]["status"], 401)


if __name__ == "__main__":
    unittest.main()
