# Loopback mock of the asset-inventory API used by the pagehaul acceptance
# harness. Binds 127.0.0.1 on an ephemeral port, writes the port to the file
# given as argv[1] (atomically), then serves until killed by the harness.
#
#   GET /assets?limit=N[&cursor=TOKEN]  -> {"items": [...], "next": TOKEN|null}
#   GET /__log__                        -> [{"cursor": .., "limit": ..}, ...]
#   POST /__reset__                     -> clears the request log
#
# A page carries a "next" token whenever it is full (len == limit), even when
# it happens to consume the final item — the follow-up fetch then returns an
# empty items list with next=null. Cursor tokens are opaque.
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

MAIN = [
    ("a-101", "web"), ("a-102", "db"), ("a-103", "web"), ("a-104", "edge"),
    ("a-105", "Web"), ("a-106", "db"), ("a-107", "web"), ("a-108", "cache"),
    ("a-109", "edge"), ("a-110", "web"), ("a-111", "db"), ("a-112", "Web"),
    ("a-113", "cache"), ("a-114", "web"), ("a-115", "edge"),
]

DATASETS = {"main": MAIN, "empty": []}

LOG = []


def token(offset):
    return "t%04x" % (offset * 73 + 19)


class Handler(BaseHTTPRequestHandler):
    dataset = MAIN

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/__log__":
            self._send(200, LOG)
            return
        if url.path != "/assets":
            self._send(404, {"error": "not found"})
            return
        q = parse_qs(url.query, keep_blank_values=True)
        cursor = q.get("cursor", [None])[0]
        limit_raw = q.get("limit", [None])[0]
        LOG.append({"cursor": cursor, "limit": limit_raw})
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            self._send(400, {"error": "limit required"})
            return
        if limit < 1:
            self._send(400, {"error": "limit must be positive"})
            return
        offset = 0
        if cursor is not None:
            offset = next(
                (o for o in range(len(self.dataset) + 1) if token(o) == cursor),
                None,
            )
            if offset is None:
                self._send(400, {"error": "unknown cursor"})
                return
        page = self.dataset[offset:offset + limit]
        nxt = token(offset + limit) if len(page) == limit else None
        items = [{"id": i, "site": s} for i, s in page]
        self._send(200, {"items": items, "next": nxt})

    def do_POST(self):
        if self.path == "/__reset__":
            LOG.clear()
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not found"})


def main():
    port_file = sys.argv[1]
    dataset = sys.argv[2] if len(sys.argv) > 2 else "main"
    Handler.dataset = DATASETS[dataset]
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    tmp = port_file + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(srv.server_address[1]))
    os.replace(tmp, port_file)
    srv.serve_forever()


if __name__ == "__main__":
    main()
