# Loopback mock of the artifact-depot upload API for the resumeput
# acceptance harness. Binds 127.0.0.1 on an ephemeral port, writes the port
# to argv[1] (atomically), then serves until killed by the harness.
#
#   POST /uploads                    -> 201 {"id": "u1"}
#   PUT  /uploads/<id>?offset=N      -> 200 {"committed": M} | 409 | 500 (scripted)
#   GET  /uploads/<id>               -> 200 {"committed": M}
#   POST /uploads/<id>/complete      {"sha256": .., "bytes": ..} -> 200 | 422
#   GET  /__state__                  -> upload bookkeeping for the harness
#
# Scripted faults (argv):
#   --fail-put N    the N-th PUT is answered 500 and its data is DISCARDED
#   --break-put N   the N-th PUT commits its data, then still answers 500
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

STATE = {
    "id": None,
    "data": bytearray(),
    "completed": False,
    "puts": [],
    "probes": 0,
    "put_count": 0,
}
FAIL_PUT = 0
BREAK_PUT = 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_bytes(self):
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n)

    def do_POST(self):
        if self.path == "/uploads":
            STATE["id"] = "u1"
            self._send(201, {"id": STATE["id"]})
            return
        if STATE["id"] and self.path == "/uploads/%s/complete" % STATE["id"]:
            req = json.loads(self._body_bytes())
            digest = hashlib.sha256(bytes(STATE["data"])).hexdigest()
            if req.get("sha256") == digest and req.get("bytes") == len(STATE["data"]):
                STATE["completed"] = True
                self._send(200, {"ok": True, "bytes": len(STATE["data"])})
            else:
                self._send(422, {"ok": False, "error": "digest mismatch"})
            return
        self._send(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/__state__":
            self._send(200, {
                "id": STATE["id"],
                "committed": len(STATE["data"]),
                "sha256": hashlib.sha256(bytes(STATE["data"])).hexdigest(),
                "completed": STATE["completed"],
                "puts": STATE["puts"],
                "probes": STATE["probes"],
            })
            return
        if STATE["id"] and self.path == "/uploads/%s" % STATE["id"]:
            STATE["probes"] += 1
            self._send(200, {"committed": len(STATE["data"])})
            return
        self._send(404, {"error": "not found"})

    def do_PUT(self):
        url = urlparse(self.path)
        if not STATE["id"] or url.path != "/uploads/%s" % STATE["id"]:
            self._send(404, {"error": "not found"})
            return
        q = parse_qs(url.query)
        try:
            offset = int(q.get("offset", ["x"])[0])
        except ValueError:
            self._send(400, {"error": "offset required"})
            return
        data = self._body_bytes()
        entry = {"offset": offset, "len": len(data)}
        if offset != len(STATE["data"]):
            entry["result"] = "409"
            STATE["puts"].append(entry)
            self._send(409, {"committed": len(STATE["data"])})
            return
        STATE["put_count"] += 1
        k = STATE["put_count"]
        if k == FAIL_PUT:
            entry["result"] = "500-drop"
            STATE["puts"].append(entry)
            self._send(500, {"error": "depot hiccup"})
            return
        STATE["data"].extend(data)
        if k == BREAK_PUT:
            entry["result"] = "500-kept"
            STATE["puts"].append(entry)
            self._send(500, {"error": "depot hiccup"})
            return
        entry["result"] = "ok"
        STATE["puts"].append(entry)
        self._send(200, {"committed": len(STATE["data"])})


def main():
    global FAIL_PUT, BREAK_PUT
    port_file = sys.argv[1]
    args = sys.argv[2:]
    while args:
        flag = args.pop(0)
        val = int(args.pop(0))
        if flag == "--fail-put":
            FAIL_PUT = val
        elif flag == "--break-put":
            BREAK_PUT = val
        else:
            raise SystemExit("unknown flag %s" % flag)
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    tmp = port_file + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(srv.server_address[1]))
    os.replace(tmp, port_file)
    srv.serve_forever()


if __name__ == "__main__":
    main()
