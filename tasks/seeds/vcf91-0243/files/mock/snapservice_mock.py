#!/usr/bin/env python3
"""Loopback mock of the vSAN Data Protection (snapservice) appliance.

Serves only the four operations named in docs/contract.json and rejects
everything else. Every request is appended to a JSONL log so a test can assert
the exact wire shape that a client produced.

Usage:
    python3 mock/snapservice_mock.py --port-file mock/port.txt --log mock/requests.jsonl

Binds to 127.0.0.1 on an ephemeral port and writes the chosen port to
--port-file once it is accepting connections.
"""

import argparse
import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

BASE_PATH = "/api"

# Query keys each operation accepts on the wire, per contract.json. Object-typed
# parameters (filter, iterate) are form/explode=true, so their *properties* are
# the wire keys; the parameter name itself must never appear.
PG_LIST_KEYS = {"pgs", "names", "states", "vms", "cluster_pairs"}
PG_LIST_MULTI = set(PG_LIST_KEYS)

REPORT_KEYS = {"start_time", "end_time", "pgs", "page_size", "offset"}
REPORT_MULTI = {"pgs"}
REPORT_SCALAR_INT = {"page_size", "offset"}

# Parameter names that must never reach the wire: they are exploded away.
FORBIDDEN_KEYS = {"filter", "iterate"}

PG_LIST_RE = re.compile(r"^/snapservice/clusters/([^/]+)/protection-groups$")
REPORT_RE = re.compile(
    r"^/snapservice/reports/clusters/([^/]+)/protection-groups/snapshots$"
)


class Fixture:
    def __init__(self, path):
        with open(path) as fh:
            data = json.load(fh)
        self.cluster = data["cluster"]
        self.username = data["credentials"]["username"]
        self.password = data["credentials"]["password"]
        self.token = data["sessionToken"]
        self.protection_groups = data["protectionGroups"]
        self.snapshots = data["snapshots"]


class RequestLog:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        with open(self.path, "w"):
            pass

    def append(self, entry):
        with self.lock:
            with open(self.path, "a") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "snapservice-mock/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # keep stderr clean
        pass

    def _send(self, status, payload=None):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status, kind, message):
        self._send(status, {"error_type": kind, "messages": [{"default_message": message}]})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    # -- logging ----------------------------------------------------------

    def _record(self, split, raw_body, outcome, status):
        pairs = parse_qsl(split.query, keep_blank_values=True)
        query_multi = {}
        for k, v in pairs:
            query_multi.setdefault(k, []).append(v)
        entry = {
            "seq": self.server.next_seq(),
            "method": self.command,
            "path": split.path,
            "raw_query": split.query,
            "query_pairs": [list(p) for p in pairs],
            "query_multi": query_multi,
            "query_keys": [k for k, _ in pairs],
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw_body.decode("utf-8", "replace"),
            "outcome": outcome,
            "status": status,
        }
        self.server.request_log.append(entry)

    # -- auth -------------------------------------------------------------

    def _check_session(self):
        """Token auth. Returns None when the caller is authenticated."""
        fx = self.server.fixture
        token = self.headers.get("vmware-api-session-id")
        if not token:
            return ("unauthenticated", "Missing vmware-api-session-id header.")
        if token != fx.token:
            return ("unauthenticated", "Invalid session token.")
        if token in self.server.revoked:
            return ("unauthenticated", "Session has been deleted.")
        return None

    # -- query validation -------------------------------------------------

    def _validate_query(self, pairs, allowed, multi, scalar_int):
        seen = set()
        for key, value in pairs:
            if key in FORBIDDEN_KEYS:
                return (
                    "Query key %r must not be sent. It is a form/explode=true object "
                    "parameter and is serialized as one key per property." % key
                )
            if key not in allowed:
                return "Unknown query key %r." % key
            if value == "":
                return (
                    "Query key %r was sent with an empty value. Optional parameters "
                    "that are not set must be omitted entirely." % key
                )
            if key not in multi and key in seen:
                return "Query key %r was repeated but is not an array parameter." % key
            if key in scalar_int:
                try:
                    n = int(value)
                except ValueError:
                    return "Query key %r must be an integer, got %r." % (key, value)
                if n < 0:
                    return "Query key %r must not be negative." % key
            seen.add(key)
        return None

    # -- dispatch ---------------------------------------------------------

    def do_POST(self):
        self._dispatch()

    def do_GET(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def _dispatch(self):
        split = urlsplit(self.path)
        body = self._read_body()
        try:
            outcome, status, payload = self._route(split, body)
        except Exception as exc:  # pragma: no cover - defensive
            outcome, status, payload = ("internal-error", 500, {"error": str(exc)})
        self._record(split, body, outcome, status)
        self._send(status, payload)

    def _route(self, split, body):
        path = split.path
        if not path.startswith(BASE_PATH + "/"):
            return (
                "unrouted",
                404,
                {"error_type": "NOT_FOUND", "messages": [{"default_message":
                    "Requests must target the %s base path." % BASE_PATH}]},
            )
        rel = path[len(BASE_PATH):]
        pairs = parse_qsl(split.query, keep_blank_values=True)
        fx = self.server.fixture

        # --- Snapservice.Sessions_create / _delete -----------------------
        if rel == "/snapservice/sessions":
            if pairs:
                return ("bad-request", 400, self._err("INVALID_ARGUMENT",
                        "The sessions operations accept no query parameters."))

            if self.command == "POST":
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Basic "):
                    return ("unauthenticated", 401, self._err("UNAUTHENTICATED",
                            "Snapservice.Sessions_create requires HTTP Basic credentials."))
                try:
                    decoded = base64.b64decode(auth[6:].strip()).decode("utf-8")
                    user, _, pwd = decoded.partition(":")
                except Exception:
                    return ("unauthenticated", 401, self._err("UNAUTHENTICATED",
                            "Malformed Basic credentials."))
                if user != fx.username or pwd != fx.password:
                    return ("unauthenticated", 401, self._err("UNAUTHENTICATED",
                            "Invalid credentials."))
                if body:
                    return ("bad-request", 400, self._err("INVALID_ARGUMENT",
                            "Snapservice.Sessions_create takes no request body."))
                self.server.revoked.discard(fx.token)
                return ("Snapservice.Sessions_create", 201, fx.token)

            if self.command == "DELETE":
                failure = self._check_session()
                if failure:
                    return ("unauthenticated", 401, self._err("UNAUTHENTICATED", failure[1]))
                self.server.revoked.add(fx.token)
                return ("Snapservice.Sessions_delete", 204, None)

            return ("method-not-allowed", 405, self._err("NOT_ALLOWED",
                    "%s is not defined for /snapservice/sessions." % self.command))

        # --- Snapservice.Clusters.ProtectionGroups_list ------------------
        m = PG_LIST_RE.match(rel)
        if m:
            if self.command != "GET":
                return ("method-not-allowed", 405, self._err("NOT_ALLOWED",
                        "%s is not defined for this path." % self.command))
            failure = self._check_session()
            if failure:
                return ("unauthenticated", 401, self._err("UNAUTHENTICATED", failure[1]))
            if self.headers.get("Authorization"):
                return ("bad-request", 400, self._err("INVALID_ARGUMENT",
                        "Do not send Basic credentials once a session token exists."))
            cluster = m.group(1)
            if cluster != fx.cluster:
                return ("not-found", 404, self._err("NOT_FOUND",
                        "No cluster %r in the system." % cluster))
            bad = self._validate_query(pairs, PG_LIST_KEYS, PG_LIST_MULTI, set())
            if bad:
                return ("bad-request", 400, self._err("INVALID_ARGUMENT", bad))

            items = fx.protection_groups["items"]
            wanted_pgs = [v for k, v in pairs if k == "pgs"]
            wanted_names = [v for k, v in pairs if k == "names"]
            if wanted_pgs:
                items = [i for i in items if i["pg"] in wanted_pgs]
            if wanted_names:
                items = [i for i in items if i["info"]["name"] in wanted_names]
            return ("Snapservice.Clusters.ProtectionGroups_list", 200, {"items": items})

        # --- Snapservice.Reports...Snapshots_list ------------------------
        m = REPORT_RE.match(rel)
        if m:
            if self.command != "GET":
                return ("method-not-allowed", 405, self._err("NOT_ALLOWED",
                        "%s is not defined for this path." % self.command))
            failure = self._check_session()
            if failure:
                return ("unauthenticated", 401, self._err("UNAUTHENTICATED", failure[1]))
            if self.headers.get("Authorization"):
                return ("bad-request", 400, self._err("INVALID_ARGUMENT",
                        "Do not send Basic credentials once a session token exists."))
            cluster = m.group(1)
            if cluster != fx.cluster:
                return ("not-found", 404, self._err("NOT_FOUND",
                        "No cluster %r in the system." % cluster))
            bad = self._validate_query(pairs, REPORT_KEYS, REPORT_MULTI, REPORT_SCALAR_INT)
            if bad:
                return ("bad-request", 400, self._err("INVALID_ARGUMENT", bad))

            qs = {}
            for k, v in pairs:
                qs.setdefault(k, []).append(v)

            rows = list(fx.snapshots)
            start = qs.get("start_time", [None])[0]
            end = qs.get("end_time", [None])[0]
            if start:
                rows = [r for r in rows if r["creation_time"] >= start]
            if end:
                rows = [r for r in rows if r["creation_time"] <= end]
            if "pgs" in qs:
                allowed = set(qs["pgs"])
                rows = [r for r in rows if r["pg"] in allowed]

            total = len(rows)
            offset = int(qs.get("offset", ["0"])[0])
            if "page_size" in qs:
                page_size = int(qs["page_size"][0])
                if page_size == 0:
                    return ("bad-request", 400, self._err("INVALID_ARGUMENT",
                            "page_size must be greater than zero."))
            else:
                page_size = self.server.default_page_size
            page = rows[offset:offset + page_size]
            return (
                "Snapservice.Reports.Clusters.ProtectionGroups.Snapshots_list",
                200,
                {"snapshots": page, "total_count": total},
            )

        return ("unrouted", 404, self._err("NOT_FOUND",
                "%s %s is not one of the four operations this mock serves." % (self.command, rel)))

    @staticmethod
    def _err(kind, message):
        return {"error_type": kind, "messages": [{"default_message": message}]}


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, fixture, request_log, default_page_size):
        super().__init__(addr, handler)
        self.fixture = fixture
        self.request_log = request_log
        self.default_page_size = default_page_size
        self.revoked = set()
        self._seq = 0
        self._seq_lock = threading.Lock()

    def next_seq(self):
        with self._seq_lock:
            self._seq += 1
            return self._seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=os.path.join(HERE, "fixture.json"))
    ap.add_argument("--port-file", default=os.path.join(HERE, "port.txt"))
    ap.add_argument("--log", default=os.path.join(HERE, "requests.jsonl"))
    ap.add_argument("--default-page-size", type=int, default=25)
    args = ap.parse_args()

    fixture = Fixture(args.fixture)
    log = RequestLog(args.log)
    server = MockServer(("127.0.0.1", 0), Handler, fixture, log, args.default_page_size)
    port = server.server_address[1]
    with open(args.port_file, "w") as fh:
        fh.write(str(port))
    sys.stderr.write("snapservice mock listening on 127.0.0.1:%d\n" % port)
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
