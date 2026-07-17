# Loopback mock of the token-authenticated reporting API for the tokenpass
# acceptance harness. Binds 127.0.0.1 on an ephemeral port, writes the port
# to argv[1] (atomically), then serves until killed by the harness.
#
#   POST /login          {"account": .., "secret": ..} -> {"token": "tok-<acct>-<n>"}
#   GET  /data/summary   valid bearer token -> {"owner": acct, "serial": k}
#   GET  /data/locked    always 401
#   GET  /data/oops      always 500
#   POST /__expire__     {"account": ..} -> invalidates the account's tokens
#   GET  /__log__        -> [{"method","path","auth","status"}, ...]  (/login and /data/* only)
#   POST /__reset__      -> clears the log (token state is kept)
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRETS = {"alice": "s-alfa", "bob": "s-bravo"}

LOG = []
VALID = {}      # token -> account
LOGINS = {}     # account -> login count
SERIAL = {}     # account -> summary serial


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

    def _record(self, status):
        self.log_entry["status"] = status
        LOG.append(self.log_entry)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n) or b"{}")

    def _account(self):
        auth = self.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            return VALID.get(auth[len("Bearer "):])
        return None

    def do_POST(self):
        if self.path == "/__expire__":
            acct = self._body().get("account")
            for tok in [t for t, a in VALID.items() if a == acct]:
                del VALID[tok]
            self._send(200, {"ok": True})
            return
        if self.path == "/__reset__":
            LOG.clear()
            self._send(200, {"ok": True})
            return
        self.log_entry = {"method": "POST", "path": self.path,
                          "auth": self.headers.get("Authorization")}
        if self.path != "/login":
            self._record(404)
            self._send(404, {"error": "not found"})
            return
        creds = self._body()
        acct = creds.get("account")
        if acct not in SECRETS or creds.get("secret") != SECRETS[acct]:
            self._record(403)
            self._send(403, {"error": "denied"})
            return
        LOGINS[acct] = LOGINS.get(acct, 0) + 1
        token = "tok-%s-%d" % (acct, LOGINS[acct])
        VALID[token] = acct
        self._record(200)
        self._send(200, {"token": token})

    def do_GET(self):
        if self.path == "/__log__":
            self._send(200, LOG)
            return
        self.log_entry = {"method": "GET", "path": self.path,
                          "auth": self.headers.get("Authorization")}
        if self.path == "/data/locked":
            self._record(401)
            self._send(401, {"error": "unauthorized"})
            return
        if self.path == "/data/oops":
            self._record(500)
            self._send(500, {"error": "backend down"})
            return
        if self.path != "/data/summary":
            self._record(404)
            self._send(404, {"error": "not found"})
            return
        acct = self._account()
        if acct is None:
            self._record(401)
            self._send(401, {"error": "unauthorized"})
            return
        SERIAL[acct] = SERIAL.get(acct, 0) + 1
        self._record(200)
        self._send(200, {"owner": acct, "serial": SERIAL[acct]})


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
