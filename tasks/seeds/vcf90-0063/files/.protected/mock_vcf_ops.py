#!/usr/bin/env python3
"""Loopback stand-in for the VCF Operations 9.0 suite-api, pinned to docs/contract.json.

The route table is built from the contract, so the only operations this server answers
are the four the contract names. Anything else is recorded as an off-contract call and
answered 404. Every request is appended to the request log as one JSON object per line;
the log preserves the raw query string, repeated query keys, the request headers and the
verbatim request body so a test can assert the exact wire shape.

Binds 127.0.0.1 only. Prints "PORT <n>" on stdout once listening.
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# A page index this far out means the client is looping instead of terminating; answer
# 400 so a runaway pager fails loudly rather than hanging the suite.
PAGE_GUARD = 200


def load_contract():
    with open(os.path.join(ROOT, "docs", "contract.json")) as fh:
        return json.load(fh)


def load_fixtures():
    with open(os.path.join(HERE, "fixtures", "alerts.json")) as fh:
        return json.load(fh)


class Mock:
    def __init__(self, contract, fixtures, token, fail_page):
        self.contract = contract
        self.fixtures = fixtures
        self.token = token
        self.fail_page = fail_page
        self.expected_auth = contract["security"]["valuePrefix"] + token
        self.auth_header = contract["security"]["header"].lower()
        self.token_released = False
        self.lock = threading.Lock()

        # Route table straight from the contract: (METHOD, url) -> operation entry.
        self.routes = {}
        for oid, op in contract["operations"].items():
            self.routes[(op["method"], op["url"])] = op

    def query_names(self, op):
        return [p["name"] for p in op["parameters"] if p["in"] == "query"]

    def alerts_for(self, resource_ids):
        alerts = self.fixtures["alerts"]
        if resource_ids:
            wanted = set(resource_ids)
            alerts = [a for a in alerts if a["resourceId"] in wanted]
        return alerts

    def alerts_page(self, base_url, resource_ids, page, page_size):
        pool = self.alerts_for(resource_ids)
        start = page * page_size
        window = pool[start:start + page_size]
        # The appliance advertises a NEXT link on every page, including the last one, so
        # the link relation cannot be used to decide when the collection is exhausted.
        links = [
            {"href": "%s?page=%d&pageSize=%d" % (base_url, page, page_size), "rel": "SELF"},
            {"href": "%s?page=%d&pageSize=%d" % (base_url, page + 1, page_size), "rel": "NEXT"},
        ]
        if page > 0:
            links.append({"href": "%s?page=%d&pageSize=%d" % (base_url, page - 1, page_size),
                          "rel": "PREVIOUS"})
        return {
            "pageInfo": {"totalCount": len(pool), "page": page, "pageSize": page_size},
            "links": links,
            "alerts": window,
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcf-ops-mock"
    sys_version = ""

    @property
    def mock(self):
        return self.server.mock

    # ---- logging -------------------------------------------------------------
    def record(self, entry):
        with self.mock.lock:
            with open(self.server.log_path, "a") as fh:
                fh.write(json.dumps(entry, sort_keys=False) + "\n")
                fh.flush()

    def log_message(self, *args):
        pass  # keep stderr clean; the request log is the record

    # ---- helpers -------------------------------------------------------------
    def read_body(self):
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return None
        return self.rfile.read(length).decode("utf-8")

    def respond(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("content-type", "application/json;charset=UTF-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def handle_request(self):
        parsed = urlparse(self.path)
        body = self.read_body()
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        op = self.mock.routes.get((self.command, parsed.path))

        entry = {
            "method": self.command,
            "path": parsed.path,
            "rawQuery": parsed.query,
            "queryPairs": [[k, v] for k, v in pairs],
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
            "operationId": op["operationId"] if op else None,
            "offContract": op is None,
        }

        status, payload = self.dispatch(op, parsed, pairs, body, entry)
        entry["status"] = status
        self.record(entry)
        self.respond(status, payload)

    def dispatch(self, op, parsed, pairs, body, entry):
        if op is None:
            return 404, {"message": "no operation in docs/contract.json serves %s %s"
                                    % (self.command, parsed.path)}

        oid = op["operationId"]

        if op["secured"]:
            supplied = self.headers.get(self.mock.contract["security"]["header"])
            if supplied != self.mock.expected_auth:
                entry["authRejected"] = True
                return 401, {"message": "missing or invalid %s header"
                                        % self.mock.contract["security"]["header"]}

        # Query keys the contract does not define for this operation are recorded and
        # rejected; keys it does define are recorded verbatim, empty values included, so
        # the verifier decides whether an unset optional was wrongly put on the wire.
        allowed = set(self.mock.query_names(op))
        unknown = sorted({k for k, _ in pairs} - allowed)
        if unknown:
            entry["unknownQueryKeys"] = unknown
            return 400, {"message": "query parameters not defined for %s: %s"
                                    % (oid, ", ".join(unknown))}

        if oid == "acquireToken":
            return self.op_acquire_token(body)
        if oid == "releaseToken":
            self.mock.token_released = True
            return 200, None
        if oid == "getCurrentVersionOfServer":
            return 200, {
                "releaseName": "VCF Operations 9.0.0.0",
                "major": 9, "minor": 0, "minorMinor": 0,
                "releasedDate": 1750143600231,
                "humanlyReadableReleaseDate": "Tuesday, June 17, 2025 at 12:00:00 AM UTC",
                "description": None,
            }
        if oid == "getAlerts":
            return self.op_get_alerts(op, pairs, entry)
        return 500, {"message": "unrouted operation " + oid}

    def op_acquire_token(self, body):
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            return 400, {"message": "request body is not valid JSON"}
        if not isinstance(parsed, dict):
            return 400, {"message": "request body must be a username-password object"}
        for field in ("username", "password"):
            if not parsed.get(field):
                return 400, {"message": "username-password.%s is required" % field}
        return 200, {
            "token": self.mock.token,
            "validity": 1799999999999,
            "expiresAt": "Wednesday, January 1, 2027 at 12:00:00 AM UTC",
            "roles": ["ReadOnly"],
        }

    def op_get_alerts(self, op, pairs, entry):
        defaults = {p["name"]: p.get("default") for p in op["parameters"]}
        values = {}
        for key, value in pairs:
            values.setdefault(key, []).append(value)

        def as_int(name):
            raw = values.get(name)
            if raw is None:
                return defaults.get(name)
            try:
                return int(raw[-1])
            except ValueError:
                return None

        page = as_int("page")
        page_size = as_int("pageSize")
        if page is None or page < 0:
            return 400, {"message": "page must be a non-negative integer"}
        if page_size is None or page_size < 1:
            return 400, {"message": "pageSize must be a positive integer"}
        if page > PAGE_GUARD:
            entry["pageGuardTripped"] = True
            return 400, {"message": "page %d is far beyond the collection; the client is not "
                                    "terminating its pager" % page}
        if self.mock.fail_page is not None and page == self.mock.fail_page:
            entry["injectedFailure"] = True
            return 500, {"message": "Error occurred while retrieving the Alert with the "
                                    "specified identifier"}
        return 200, self.mock.alerts_page(op["url"], values.get("resourceId") or [],
                                          page, page_size)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="0 picks an ephemeral port")
    ap.add_argument("--log", required=True, help="request log path (JSON lines)")
    ap.add_argument("--token", default="ops-session-4f2c1d8a")
    ap.add_argument("--fail-page", type=int, default=None,
                    help="answer 500 for this getAlerts page index")
    args = ap.parse_args()

    open(args.log, "w").close()

    mock = Mock(load_contract(), load_fixtures(), args.token, args.fail_page)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.mock = mock
    httpd.log_path = args.log
    sys.stdout.write("PORT %d\n" % httpd.server_address[1])
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
