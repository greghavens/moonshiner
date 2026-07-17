# Loopback mock JSON-RPC 2.0 endpoint for the rpcbatch acceptance harness.
# Binds 127.0.0.1 on an ephemeral port, writes the port to argv[1]
# (atomically), then serves until killed by the harness.
#
#   POST /rpc      JSON-RPC 2.0 batch (must be a JSON array)
#   GET  /__log__  -> list of the raw request bodies received (parsed)
#   POST /__reset__
#
# Responses are returned in REVERSE build order on purpose: the protocol
# says clients correlate by id, not by position. Some methods misbehave on
# purpose so the client's bookkeeping can be exercised:
#   drop  -> its response entry is omitted
#   dupe  -> its response entry is emitted twice
#   stray -> answers normally, plus an extra response with an unknown id
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = []


def handle_entry(entry, out):
    method = entry.get("method")
    params = entry.get("params", [])
    has_id = "id" in entry
    rid = entry.get("id")

    def ok(result):
        if has_id:
            out.append({"jsonrpc": "2.0", "id": rid, "result": result})

    def err(code, message, data=None):
        if has_id:
            e = {"code": code, "message": message}
            if data is not None:
                e["data"] = data
            out.append({"jsonrpc": "2.0", "id": rid, "error": e})

    if method == "sum":
        ok(sum(params))
    elif method == "upper":
        ok(params[0].upper())
    elif method == "fail":
        err(-32050, "scripted failure", {"method": "fail"})
    elif method == "drop":
        pass  # scripted: response goes missing
    elif method == "dupe":
        ok("dup")
        ok("dup")
    elif method == "stray":
        ok("ok")
        out.append({"jsonrpc": "2.0", "id": 9999, "result": "stray"})
    else:
        err(-32601, "method not found")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/__log__":
            self._send(200, LOG)
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/__reset__":
            LOG.clear()
            self._send(200, {"ok": True})
            return
        if self.path != "/rpc":
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length", "0"))
        try:
            batch = json.loads(self.rfile.read(n))
        except ValueError:
            self._send(400, {"error": "invalid json"})
            return
        if not isinstance(batch, list):
            self._send(400, {"error": "batch must be an array"})
            return
        if not all(isinstance(e, dict) for e in batch):
            self._send(400, {"error": "batch entries must be objects"})
            return
        LOG.append(batch)
        out = []
        for entry in batch:
            handle_entry(entry, out)
        if not out:
            self._send(204, None)
            return
        out.reverse()
        self._send(200, out)


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
