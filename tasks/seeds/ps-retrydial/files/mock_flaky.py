# Loopback mock of a flaky gateway for the retrydial acceptance harness.
# Binds 127.0.0.1 on an ephemeral port, writes the port to argv[1]
# (atomically), then serves until killed by the harness.
#
# Each path answers from a fixed script of (status, retry_after) steps,
# advancing one step per request; past the end the last step repeats.
#
#   GET /<scripted path>  -> {"path": .., "call": n, "status": s}
#   GET /__log__          -> [{"path": .., "status": ..}, ...]
#   POST /__reset__       -> clears the log and all per-path counters
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SCRIPTS = {
    "/ok": [(200, None)],
    "/flaky": [(500, None), (503, 7), (200, None)],
    "/wall": [(500, None)],
    "/throttle": [(429, 3), (200, None)],
    "/throttle-plain": [(429, None), (429, None), (200, None)],
    "/reject": [(400, None)],
    "/missing": [(404, None)],
}

LOG = []
CALLS = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload, retry_after=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/__log__":
            self._send(200, LOG)
            return
        script = SCRIPTS.get(self.path)
        if script is None:
            self._send(410, {"error": "unscripted path"})
            return
        n = CALLS.get(self.path, 0) + 1
        CALLS[self.path] = n
        status, retry_after = script[min(n - 1, len(script) - 1)]
        LOG.append({"path": self.path, "status": status})
        self._send(status, {"path": self.path, "call": n, "status": status},
                   retry_after)

    def do_POST(self):
        if self.path == "/__reset__":
            LOG.clear()
            CALLS.clear()
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})


def main():
    port_file = sys.argv[1]
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    tmp = port_file + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(srv.server_address[1]))
    os.replace(tmp, port_file)
    srv.serve_forever()


if __name__ == "__main__":
    main()
