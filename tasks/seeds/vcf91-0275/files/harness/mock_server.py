#!/usr/bin/env python3
"""Loopback mock for the four VCF Operations report operations named in docs/contract.json.

The route table is built *from* the contract: an operation the contract does not name is
not served, and any request that does not match a contract route is answered 404. Every
request is appended to a JSON Lines request log that the verifier reads.

This process never talks to a VMware endpoint. It binds 127.0.0.1 only.
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

# --- fixtures -----------------------------------------------------------------

USERNAME = "svc-report"
PASSWORD = "R3port!Pass"

# reportDefinitionId -> ordered status timeline observed by successive getReport calls.
# The last element repeats for every further poll.
DEFINITION_TIMELINES = {
    # completes on the third poll
    "97417a6d-708d-4b12-9142-484b5a0df4dc": ["Queued", "Running", "Completed"],
    # reaches a terminal FAILED on the third poll
    "1c0b9c1e-8f4a-4f52-9d6a-2b7c5e3a91fd": ["Queued", "Running", "Failed"],
    # never reaches a terminal status
    "5f2d7a34-6b19-4c88-a0e3-9d41f7b26c50": ["Running"],
}

CSV_BODY = "Cluster,Capacity Remaining %,Time Remaining (days)\r\nvcf-m01-cl01,42,118\r\nvcf-w01-cl01,17,26\r\n"
PDF_BODY = b"%PDF-1.4\n% VCF Operations report fixture\n%%EOF\n"

AUTH_SCHEME = "vRealizeOpsToken"


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.token_seq = 0
        self.report_seq = 0
        self.tokens = set()
        self.reports = {}  # id -> {"definition": str, "resource": str, "polls": int}


STATE = State()


def _next_token():
    STATE.token_seq += 1
    return "vcfops-tkn-%012d" % STATE.token_seq


def _next_report_id():
    STATE.report_seq += 1
    return "11111111-2222-4333-8444-%012d" % STATE.report_seq


def _status_for(report):
    timeline = DEFINITION_TIMELINES.get(report["definition"])
    if timeline is None:
        return "Failed"
    idx = min(report["polls"], len(timeline)) - 1
    return timeline[max(idx, 0)]


# --- contract-driven routing --------------------------------------------------


def load_routes(contract_path):
    with open(contract_path, "r", encoding="utf-8") as fh:
        contract = json.load(fh)
    base = contract["basePath"]
    routes = []
    for op in contract["operations"]:
        segments = (base + op["path"]).strip("/").split("/")
        routes.append(
            {
                "operationId": op["operationId"],
                "method": op["method"].upper(),
                "segments": segments,
                "authRequired": bool(op.get("authRequired")),
            }
        )
    known = {r["operationId"] for r in routes}
    missing = known - set(HANDLERS)
    if missing:
        raise SystemExit("contract names operations with no mock handler: %s" % sorted(missing))
    return contract, routes


def match_route(routes, method, path):
    got = path.strip("/").split("/")
    for route in routes:
        want = route["segments"]
        if route["method"] != method or len(want) != len(got):
            continue
        params = {}
        ok = True
        for w, g in zip(want, got):
            if w.startswith("{") and w.endswith("}"):
                if not g:
                    ok = False
                    break
                params[w[1:-1]] = g
            elif w != g:
                ok = False
                break
        if ok:
            return route, params
    return None, None


# --- operation handlers -------------------------------------------------------
# Each returns (status, content_type, body_bytes, extra_log_fields).


def op_acquire_token(ctx):
    try:
        body = json.loads(ctx["raw_body"] or "null")
    except ValueError:
        return 400, "application/json", b'{"message":"malformed JSON"}', {}
    if not isinstance(body, dict):
        return 400, "application/json", b'{"message":"expected a JSON object"}', {}
    if body.get("username") != USERNAME or body.get("password") != PASSWORD:
        return 401, "application/json", b'{"message":"Invalid credentials"}', {}
    token = _next_token()
    STATE.tokens.add(token)
    payload = {
        "token": token,
        "validity": 1810000000000,
        "expiresAt": "2027-05-13T05:32:36.000Z",
        "roles": ["ReadOnly", "ReportAdmin"],
    }
    return 200, "application/json", json.dumps(payload).encode("utf-8"), {"issuedToken": token}


def op_create_report(ctx):
    try:
        body = json.loads(ctx["raw_body"] or "null")
    except ValueError:
        return 400, "application/json", b'{"message":"malformed JSON"}', {}
    if not isinstance(body, dict):
        return 400, "application/json", b'{"message":"expected a JSON object"}', {}
    for required in ("reportDefinitionId", "resourceId"):
        if not isinstance(body.get(required), str) or not body[required]:
            msg = {"message": "missing required property %s" % required}
            return 400, "application/json", json.dumps(msg).encode("utf-8"), {}
    report_id = _next_report_id()
    STATE.reports[report_id] = {
        "definition": body["reportDefinitionId"],
        "resource": body["resourceId"],
        "polls": 0,
    }
    payload = {
        "id": report_id,
        "resourceId": body["resourceId"],
        "reportDefinitionId": body["reportDefinitionId"],
        "name": "Cluster Capacity Risk Forecast Report",
        "description": "Cluster Capacity Risk Forecast Report",
        "owner": USERNAME,
        "status": "Queued",
        "subject": [],
        "publish": False,
        "links": [
            {"href": "/suite-api/api/reports/%s" % report_id, "rel": "SELF", "name": "linkToSelf"}
        ],
    }
    return 200, "application/json", json.dumps(payload).encode("utf-8"), {"assignedReportId": report_id}


def op_get_report(ctx):
    report_id = ctx["params"]["id"]
    report = STATE.reports.get(report_id)
    if report is None:
        return 404, "application/json", b'{"message":"No such Report"}', {}
    report["polls"] += 1
    status = _status_for(report)
    payload = {
        "id": report_id,
        "resourceId": report["resource"],
        "reportDefinitionId": report["definition"],
        "name": "Cluster Capacity Risk Forecast Report",
        "owner": USERNAME,
        "status": status,
        "subject": [],
        "publish": False,
        "links": [
            {"href": "/suite-api/api/reports/%s" % report_id, "rel": "SELF", "name": "linkToSelf"},
            {
                "href": "/suite-api/api/reports/%s/download" % report_id,
                "rel": "RELATED",
                "name": "linkToDownload",
            },
        ],
    }
    if status.upper() == "COMPLETED":
        payload["completionTime"] = "1 minute ago"
    return 200, "application/json", json.dumps(payload).encode("utf-8"), {"servedStatus": status}


def op_download_report(ctx):
    report_id = ctx["params"]["id"]
    report = STATE.reports.get(report_id)
    if report is None:
        return 404, "application/json", b'{"message":"No such Report"}', {}
    status = _status_for(report) if report["polls"] else "Queued"
    if status.upper() != "COMPLETED":
        msg = {"message": "Report %s is not ready for download (status %s)" % (report_id, status)}
        return 409, "application/json", json.dumps(msg).encode("utf-8"), {"servedStatus": status}
    fmt = dict(ctx["query"]).get("format", "CSV")
    if fmt.upper() == "PDF":
        return 200, "application/pdf", PDF_BODY, {"servedFormat": "PDF"}
    return 200, "text/csv", CSV_BODY.encode("utf-8"), {"servedFormat": "CSV"}


HANDLERS = {
    "acquireToken": op_acquire_token,
    "createReport": op_create_report,
    "getReport": op_get_report,
    "downloadReport": op_download_report,
}


# --- server -------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # An idle keep-alive connection must never wedge the server: connections are served on
    # their own threads and a stalled one is dropped rather than blocking the next request.
    timeout = 15
    routes = []
    log_path = None

    def log_message(self, fmt, *args):  # silence stderr access log
        pass

    def _record(self, entry):
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()

    def _handle(self, method):
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8", "replace") if length else ""

        with STATE.lock:
            STATE.seq += 1
            seq = STATE.seq

            route, params = match_route(self.routes, method, split.path)
            headers = {}
            for key, value in self.headers.items():
                headers.setdefault(key.lower(), value)

            extra = {}
            if route is None:
                status, ctype, body = 404, "application/json", b'{"message":"Not Found"}'
                operation_id = None
            else:
                operation_id = route["operationId"]
                auth = headers.get("authorization")
                token = None
                if auth and auth.startswith(AUTH_SCHEME + " "):
                    token = auth[len(AUTH_SCHEME) + 1 :]
                if route["authRequired"] and (token is None or token not in STATE.tokens):
                    status, ctype, body = (
                        401,
                        "application/json",
                        b'{"message":"Invalid or missing authorization token"}',
                    )
                else:
                    ctx = {
                        "params": params,
                        "query": parse_qsl(split.query, keep_blank_values=True),
                        "raw_body": raw_body,
                        "headers": headers,
                    }
                    status, ctype, body, extra = HANDLERS[operation_id](ctx)

            parsed_body = None
            if raw_body:
                try:
                    parsed_body = json.loads(raw_body)
                except ValueError:
                    parsed_body = None

            entry = {
                "seq": seq,
                "operationId": operation_id,
                "method": method,
                "target": self.path,
                "path": split.path,
                "rawQuery": split.query,
                "query": parse_qsl(split.query, keep_blank_values=True),
                "pathParams": params or {},
                "headers": headers,
                "hasBody": bool(raw_body),
                "rawBody": raw_body,
                "jsonBody": parsed_body,
                "responseStatus": status,
            }
            entry.update(extra)
            self._record(entry)

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_HEAD(self):
        self._handle("HEAD")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--port-file", required=True)
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()

    _, routes = load_routes(args.contract)
    Handler.routes = routes
    Handler.log_path = os.path.abspath(args.log)

    open(Handler.log_path, "w", encoding="utf-8").close()

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    with open(args.port_file, "w", encoding="utf-8") as fh:
        fh.write(str(port))
    sys.stderr.write("mock listening on http://127.0.0.1:%d (%d contract routes)\n" % (port, len(routes)))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
