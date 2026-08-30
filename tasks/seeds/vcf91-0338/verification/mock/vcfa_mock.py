#!/usr/bin/env python3
"""Loopback mock of the VCF Automation 9.1 deployment API.

Pinned to docs/contract.json: it serves the seven operations that contract
names and nothing else. Any other path is a 404, exactly as the real service
would answer. Every request is appended to a JSONL request log so the verifier
can assert the wire shape after the run.

Binds 127.0.0.1 on an ephemeral port and writes the chosen port to
$VCFA_MOCK_STATE/endpoint.json once it is accepting connections.

PROTECTED FILE - part of the graded harness, do not modify.
"""

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("VCFA_MOCK_STATE") or os.path.join(HERE, "..", ".run")
STATE = os.path.abspath(STATE)
TOKEN = os.environ.get("VCFA_MOCK_TOKEN", "")

LOG_SLICE_SIZE = 10

with open(os.path.join(HERE, "fixtures.json")) as fh:
    FIX = json.load(fh)

_log_lock = threading.Lock()
_seq = [0]


def request_log_path():
    return os.path.join(STATE, "requests.jsonl")


def record(entry):
    with _log_lock:
        _seq[0] += 1
        entry["seq"] = _seq[0]
        with open(request_log_path(), "a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


# --- routing table: one entry per operation named by docs/contract.json ------

ROUTES = [
    ("getDeploymentById", "GET",
     r"^/deployment/api/deployments/(?P<deploymentId>[^/]+)$"),
    ("getDeploymentRequests", "GET",
     r"^/deployment/api/deployments/(?P<deploymentId>[^/]+)/requests$"),
    ("getDeploymentResources", "GET",
     r"^/deployment/api/deployments/(?P<deploymentId>[^/]+)/resources$"),
    ("getRequestEvents", "GET",
     r"^/deployment/api/requests/(?P<requestId>[^/]+)/events$"),
    ("getEventLogs", "GET",
     r"^/deployment/api/requests/(?P<requestId>[^/]+)/events/(?P<eventId>[^/]+)/logs$"),
    ("getResourceActions", "GET",
     r"^/deployment/api/resources/(?P<resourceId>[^/]+)/actions$"),
    ("submitResourceActionRequest", "POST",
     r"^/deployment/api/resources/(?P<resourceId>[^/]+)/requests$"),
]
ROUTES = [(op, method, re.compile(pat)) for op, method, pat in ROUTES]


def match_route(method, path):
    path_matched = False
    for op, m, pat in ROUTES:
        hit = pat.match(path)
        if hit:
            path_matched = True
            if m == method:
                return op, hit.groupdict()
    return (None, {"pathExistsForOtherMethod": path_matched})


# --- response builders -------------------------------------------------------

def page_envelope(content, number, size, total_elements):
    total_pages = max(1, -(-total_elements // size))
    return {
        "content": content,
        "pageable": {
            "offset": number * size, "pageNumber": number, "pageSize": size,
            "paged": True, "unpaged": False,
            "sort": {"empty": False, "sorted": True, "unsorted": False},
        },
        "number": number, "size": size,
        "totalElements": total_elements, "totalPages": total_pages,
        "numberOfElements": len(content),
        "first": number == 0, "last": number >= total_pages - 1,
        "empty": len(content) == 0,
        "sort": {"empty": False, "sorted": True, "unsorted": False},
    }


def slice_envelope(content, number, size):
    return {
        "content": content,
        "pageable": {
            "offset": 0, "pageNumber": number, "pageSize": size,
            "paged": True, "unpaged": False,
            "sort": {"empty": True, "sorted": False, "unsorted": True},
        },
        "number": number, "size": size, "numberOfElements": len(content),
        "first": number == 0,
        "last": bool(content and content[-1]["eof"]),
        "empty": len(content) == 0,
        "sort": {"empty": True, "sorted": False, "unsorted": True},
    }


def int_param(query, name, default):
    """Reject anything the contract could not have produced."""
    if name not in query:
        return default, None
    raw = query[name]
    if raw == "" or not re.match(r"^-?\d+$", raw):
        return None, "'%s' must be an integer, got %r" % (name, raw)
    return int(raw), None


def sorted_requests():
    # Server default sort for this operation is createdAt,DESC.
    return sorted(FIX["requests"]["all"], key=lambda r: r["createdAt"], reverse=True)


def handle(op, params, query, body, headers):
    if op == "getDeploymentById":
        if params["deploymentId"] != FIX["ids"]["deploymentId"]:
            return 404, {"message": "Deployment not found"}
        return 200, FIX["deployment"]

    if op == "getDeploymentRequests":
        if params["deploymentId"] != FIX["ids"]["deploymentId"]:
            return 404, {"message": "Deployment not found"}
        page, err = int_param(query, "page", 0)
        if err:
            return 400, {"message": err}
        size, err = int_param(query, "size", 20)
        if err:
            return 400, {"message": err}
        if page < 0 or size < 1:
            return 400, {"message": "page must be >= 0 and size must be >= 1"}
        items = sorted_requests()
        window = items[page * size:page * size + size]
        return 200, page_envelope(window, page, size, len(items))

    if op == "getDeploymentResources":
        if params["deploymentId"] != FIX["ids"]["deploymentId"]:
            return 404, {"message": "Deployment not found"}
        page, err = int_param(query, "page", 0)
        if err:
            return 400, {"message": err}
        size, err = int_param(query, "size", 20)
        if err:
            return 400, {"message": err}
        if page < 0 or size < 1:
            return 400, {"message": "page must be >= 0 and size must be >= 1"}
        items = FIX["resources"].get(params["deploymentId"], [])
        if "names" in query and query["names"] != "":
            wanted = set(query["names"].split(","))
            items = [r for r in items if r["name"] in wanted]
        window = items[page * size:page * size + size]
        return 200, page_envelope(window, page, size, len(items))

    if op == "getRequestEvents":
        items = FIX["events"].get(params["requestId"])
        if items is None:
            return 404, {"message": "Request not found"}
        page, err = int_param(query, "page", 0)
        if err:
            return 400, {"message": err}
        size, err = int_param(query, "size", 20)
        if err:
            return 400, {"message": err}
        if page < 0 or size < 1:
            return 400, {"message": "page must be >= 0 and size must be >= 1"}
        window = items[page * size:page * size + size]
        return 200, page_envelope(window, page, size, len(items))

    if op == "getEventLogs":
        if params["requestId"] not in FIX["events"]:
            return 404, {"message": "Request not found"}
        rows = FIX["logs"].get(params["eventId"])
        if rows is None:
            return 404, {"message": "No logs for event"}
        since, err = int_param(query, "sinceRow", 1)
        if err:
            return 400, {"message": err}
        if since < 1:
            return 400, {"message": "sinceRow must be a positive row number"}
        remaining = [r for r in rows if r["rownum"] >= since]
        window = [dict(r) for r in remaining[:LOG_SLICE_SIZE]]
        for r in window:
            r["eof"] = False
        if window and len(remaining) <= LOG_SLICE_SIZE:
            window[-1]["eof"] = True
        number = (since - 1) // LOG_SLICE_SIZE
        return 200, slice_envelope(window, number, LOG_SLICE_SIZE)

    if op == "getResourceActions":
        acts = FIX["actions"].get(params["resourceId"])
        if acts is None:
            return 404, {"message": "Resource not found"}
        return 200, acts

    if op == "submitResourceActionRequest":
        ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return 415, {"message": "Unsupported Media Type: expected application/json"}
        if params["resourceId"] not in FIX["actions"]:
            return 404, {"message": "Resource not found"}
        try:
            payload = json.loads(body.decode("utf-8")) if body else None
        except Exception as exc:
            return 400, {"message": "Malformed JSON body: %s" % exc}
        if not isinstance(payload, dict):
            return 400, {"message": "Body must be a ResourceActionRequest object"}
        unknown = sorted(set(payload) - {"actionId", "inputs", "reason"})
        if unknown:
            return 400, {"message": "Unknown ResourceActionRequest fields: %s" % ", ".join(unknown)}
        action_id = payload.get("actionId")
        available = {a["id"]: a for a in FIX["actions"][params["resourceId"]]}
        if action_id not in available:
            return 404, {"message": "Action '%s' is not available on this resource" % action_id}
        if not available[action_id]["valid"]:
            return 409, {"message": "Action '%s' is not valid in the current resource state" % action_id}
        resp = dict(FIX["submitResponse"])
        resp["actionId"] = action_id
        return 200, resp

    return 500, {"message": "unrouted operation"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-mock/1.0"

    def log_message(self, fmt, *args):  # silence stderr access log
        pass

    def _headers_of_interest(self):
        keep = ("authorization", "accept", "content-type", "user-agent")
        out = {}
        for name in keep:
            out[name] = self.headers.get(name)
        return out

    def _respond(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        raw_query = parsed.query
        # keep_blank_values so an empty optional parameter is visible, not silently dropped
        pairs = parse_qsl(raw_query, keep_blank_values=True)
        query = {}
        for k, v in pairs:
            query[k] = v

        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""

        entry = {
            "method": method,
            "path": path,
            "rawQuery": raw_query,
            "queryPairs": [[k, v] for k, v in pairs],
            "headers": self._headers_of_interest(),
            "body": body.decode("utf-8", "replace") if body else None,
        }

        if path == "/__shutdown":
            entry["operationId"] = None
            entry["status"] = 200
            entry["outcome"] = "control"
            record(entry)
            self._respond(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        op, extra = match_route(method, path)
        if op is None:
            entry["operationId"] = None
            entry["status"] = 404
            entry["outcome"] = "not_in_contract"
            entry["detail"] = extra
            record(entry)
            self._respond(404, {"message": "No such operation in this API"})
            return

        entry["operationId"] = op
        auth = self.headers.get("authorization")
        if not TOKEN or auth != "Bearer " + TOKEN:
            entry["status"] = 401
            entry["outcome"] = "unauthorized"
            record(entry)
            self._respond(401, {"message": "Unauthorized"})
            return

        _, params = match_route(method, path)
        status, payload = handle(op, params, query, body, self._headers_of_interest())
        entry["status"] = status
        entry["outcome"] = "served" if status < 400 else "rejected"
        entry["pathParams"] = params
        record(entry)
        self._respond(status, payload)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def do_PUT(self):
        self._serve("PUT")

    def do_PATCH(self):
        self._serve("PATCH")

    def do_DELETE(self):
        self._serve("DELETE")


def main():
    if not TOKEN:
        sys.stderr.write("VCFA_MOCK_TOKEN must be set\n")
        return 2
    os.makedirs(STATE, exist_ok=True)
    open(request_log_path(), "w").close()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    tmp = os.path.join(STATE, "endpoint.json.tmp")
    with open(tmp, "w") as fh:
        json.dump({"host": "127.0.0.1", "port": port,
                   "baseUrl": "http://127.0.0.1:%d" % port}, fh)
    os.replace(tmp, os.path.join(STATE, "endpoint.json"))
    sys.stderr.write("vcfa mock listening on 127.0.0.1:%d\n" % port)
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
