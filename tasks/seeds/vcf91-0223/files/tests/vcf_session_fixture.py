#!/usr/bin/env python3
"""Session-bootstrap fixture for the acceptance test.

This is NOT the contract mock and it serves none of the SDDC LCM operations.
Its only job is to stand in for the VCF appliance endpoint that the real
`Connect-VcfInstallerServer` cmdlet from VMware.Sdk.Vcf.Installer authenticates
against, so the test can hand a genuine PowerCLI connection object to the module
under test. The SDDC LCM service is a separate service with its own base URL,
and it is served by tests/sddc_lcm_contract_mock.py on a separate port.

The access token it mints is derived from VCF_FIXTURE_SESSION_TOKEN, which the
verifier randomises per run. The contract mock rejects any other bearer value,
so the module under test cannot hard-code a token: it has to read the session
secret off the connection object.

The listener binds 127.0.0.1 only. No VMware endpoint is contacted.
"""

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_log_lock = threading.Lock()
_seq = [0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfSessionFixture/1.0"

    log_path = None
    token = None

    def log_message(self, *args):
        pass

    def _record_and_reply(self, status, payload):
        raw = self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with _log_lock:
            _seq[0] += 1
            entry = {
                "seq": _seq[0],
                "method": self.command,
                "target": raw,
                "status": status,
                "userAgent": self.headers.get("User-Agent"),
                "authorization": self.headers.get("Authorization"),
                "requestBodyBytes": len(body),
                "clientAddress": self.client_address[0],
            }
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self):
        if self.path.partition("?")[0] == "/v1/tokens":
            self._record_and_reply(200, {
                "accessToken": self.token,
                "refreshToken": {"id": "fixture-refresh-token"},
            })
            return
        self._record_and_reply(404, {"message": "not found"})

    def do_GET(self):
        if self.path.partition("?")[0] == "/v1/sddc-manager":
            self._record_and_reply(200, {
                "id": "fixture-sddc-manager",
                "fqdn": "sddc-manager.vcf.sddc.lab",
                "version": "9.1.0.0",
            })
            return
        self._record_and_reply(404, {"message": "not found"})

    def do_DELETE(self):
        self._record_and_reply(204 if self.path.startswith("/v1/tokens") else 404, {})

    def do_PATCH(self):
        self._record_and_reply(404, {"message": "not found"})

    def do_PUT(self):
        self._record_and_reply(404, {"message": "not found"})


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: vcf_session_fixture.py <ready-file> <log-file>\n")
        return 2
    ready_file, log_path = sys.argv[1], sys.argv[2]

    token = os.environ.get("VCF_FIXTURE_SESSION_TOKEN")
    if not token:
        sys.stderr.write("VCF_FIXTURE_SESSION_TOKEN is not set\n")
        return 2

    Handler.log_path = log_path
    Handler.token = token
    open(log_path, "w", encoding="utf-8").close()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]

    tmp = ready_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"host": "127.0.0.1", "port": port}, handle)
    os.replace(tmp, ready_file)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
