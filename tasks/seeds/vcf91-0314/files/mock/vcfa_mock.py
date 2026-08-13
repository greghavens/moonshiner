#!/usr/bin/env python3
"""Loopback mock of the VCF Automation 9.1 deployment-triage operations.

The mock is *pinned to docs/contract.json*: it loads the contract at startup and
will only serve the operations the contract names. Anything else - an undeclared
path, a method the contract does not list for that path, an undeclared query
parameter, an undeclared request-body field - is rejected rather than guessed at.
It also enforces the contract's wire rules, so an optional parameter or field
that is sent empty instead of being omitted is rejected with 400.

Every received request is appended to a JSON Lines request log so a test can
assert on the exact wire shape after the fact.

Binds to 127.0.0.1 only. It never contacts a VMware endpoint.

Usage:
    python3 mock/vcfa_mock.py --port 0 --request-log /tmp/requests.jsonl

With --port 0 the chosen port is printed to stdout as "LISTENING <port>" once
the socket is ready.
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures  # noqa: E402

DEFAULT_TOKEN = "mock-access-token"


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


class Contract:
    """The subset of docs/contract.json the mock enforces."""

    def __init__(self, doc):
        self.doc = doc
        self.operations = doc["operations"]
        self.body_schemas = doc["commonSchemas"]
        for op in self.operations:
            op["_segments"] = [s for s in op["path"].split("/") if s != ""]
            op["_queryNames"] = {p["name"] for p in op.get("queryParams", [])}

    def match(self, method, path):
        """Return (operation, path_params, path_matched_any_method)."""
        segments = [s for s in path.split("/") if s != ""]
        path_hit = False
        for op in self.operations:
            if len(op["_segments"]) != len(segments):
                continue
            params = {}
            ok = True
            for tmpl, actual in zip(op["_segments"], segments):
                if tmpl.startswith("{") and tmpl.endswith("}"):
                    params[tmpl[1:-1]] = actual
                elif tmpl != actual:
                    ok = False
                    break
            if not ok:
                continue
            path_hit = True
            if op["method"] == method:
                return op, params, True
        return None, None, path_hit

    def body_fields(self, schema_name):
        return self.body_schemas[schema_name]["fields"]


# ---------------------------------------------------------------------------
# Response helpers matching the envelopes described in the contract
# ---------------------------------------------------------------------------


def _sort_block():
    return {"empty": True, "sorted": False, "unsorted": True}


def _pageable(page, size):
    return {
        "offset": page * size,
        "pageNumber": page,
        "pageSize": size,
        "paged": True,
        "unpaged": False,
        "sort": _sort_block(),
    }


def paged(items, page, size):
    total = len(items)
    start = page * size
    window = items[start:start + size]
    total_pages = (total + size - 1) // size if size else 0
    return {
        "content": window,
        "empty": len(window) == 0,
        "first": page == 0,
        "last": start + size >= total,
        "number": page,
        "numberOfElements": len(window),
        "size": size,
        "totalElements": total,
        "totalPages": total_pages,
        "pageable": _pageable(page, size),
        "sort": _sort_block(),
    }


def sliced(items, offset, size):
    window = items[offset:offset + size]
    return {
        "content": window,
        "empty": len(window) == 0,
        "first": offset == 0,
        "last": offset + size >= len(items),
        "number": 0,
        "numberOfElements": len(window),
        "size": size,
        "pageable": _pageable(0, size),
        "sort": _sort_block(),
    }


# ---------------------------------------------------------------------------
# Request log
# ---------------------------------------------------------------------------


class RequestLog:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.seq = 0
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("")

    def reserve(self, entry):
        """Assign arrival order before a response can release the client."""
        if not self.path:
            return
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq

    def record(self, entry):
        if not self.path:
            return
        with self.lock:
            if "seq" not in entry:
                self.seq += 1
                entry["seq"] = self.seq
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-mock/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # silence stderr access logging
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        # Persist the completed request before releasing the response to the
        # client. The verifier reads the log as soon as the PowerShell process
        # exits, so logging after wfile.write() leaves a real completeness race
        # on the final request.
        entry = getattr(self, "_current_request_entry", None)
        if entry is not None:
            entry["status"] = status
            self.server.request_log.record(entry)
            self._current_request_entry = None
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _error(self, status, message):
        return self._send(status, {"status": status, "message": message})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_HEAD(self):
        self._handle("HEAD")

    # -- request handling -------------------------------------------------

    def _handle(self, method):
        contract = self.server.contract
        parsed = urlparse(self.path)
        raw_query = parsed.query
        query = parse_qs(raw_query, keep_blank_values=True)

        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length).decode("utf-8") if length else None

        entry = {
            "method": method,
            "path": parsed.path,
            "rawQuery": raw_query,
            "query": query,
            "headers": {
                "authorization": self.headers.get("Authorization"),
                "accept": self.headers.get("Accept"),
                "content-type": self.headers.get("Content-Type"),
                "user-agent": self.headers.get("User-Agent"),
            },
            "rawBody": raw_body,
            "operationId": None,
            "status": None,
        }
        # Reserve sequence at receipt. _dispatch sends the response before it
        # returns; assigning sequence afterwards lets a client on a new
        # connection race its next request ahead in the log.
        self.server.request_log.reserve(entry)
        self._current_request_entry = entry

        try:
            status = self._dispatch(method, parsed.path, query, raw_body, entry, contract)
        except Exception as exc:  # pragma: no cover - defensive
            status = self._error(500, "mock failure: %s" % exc)

        # Every normal response is recorded by _send before its bytes are
        # released. Keep this fallback for a future handler that returns a
        # status without using _send.
        if self._current_request_entry is not None:
            entry["status"] = status
            self.server.request_log.record(entry)
            self._current_request_entry = None

    def _dispatch(self, method, path, query, raw_body, entry, contract):
        op, path_params, path_hit = contract.match(method, path)

        if op is None:
            if path_hit:
                return self._error(
                    405,
                    "Method %s is not defined for %s in the pinned contract." % (method, path),
                )
            return self._error(
                404,
                "No operation in the pinned contract serves %s. This mock serves only "
                "the operations named in docs/contract.json." % path,
            )

        entry["operationId"] = op["id"]

        # --- auth and negotiation -------------------------------------
        auth = self.headers.get("Authorization")
        expected = "Bearer %s" % self.server.token
        if not auth:
            return self._error(401, "Missing Authorization header. Expected 'Bearer <access_token>'.")
        if auth != expected:
            return self._error(401, "Invalid bearer token.")

        accept = self.headers.get("Accept")
        if not accept or "application/json" not in accept:
            return self._error(
                406,
                "Accept must request application/json; the contract's required request "
                "headers are Authorization and Accept: application/json.",
            )

        # --- query parameters against the contract --------------------
        err = self._check_query(op, query)
        if err:
            return self._error(400, err)

        # --- request body against the contract ------------------------
        body = None
        if op.get("requestBody"):
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                return self._error(415, "Content-Type must be application/json.")
            if raw_body is None or raw_body.strip() == "":
                return self._error(400, "A JSON request body is required.")
            try:
                body = json.loads(raw_body)
            except ValueError as exc:
                return self._error(400, "Request body is not valid JSON: %s" % exc)
            if not isinstance(body, dict):
                return self._error(400, "Request body must be a JSON object.")
            err = self._check_body(op, body, contract)
            if err:
                return self._error(400, err)
        elif raw_body:
            return self._error(400, "%s takes no request body." % op["name"])

        handler = getattr(self, "_op_" + op["id"])
        return handler(path_params, query, body)

    def _check_query(self, op, query):
        allowed = op["_queryNames"]
        for name, values in query.items():
            if name not in allowed:
                return (
                    "Query parameter '%s' is not declared for %s in the pinned contract. "
                    "Declared parameters: %s."
                    % (name, op["name"], ", ".join(sorted(allowed)) or "(none)")
                )
            for value in values:
                if value == "":
                    return (
                        "Query parameter '%s' was sent with an empty value. The contract's "
                        "wire rules require an unsupplied optional parameter to be omitted "
                        "from the query string, not sent empty." % name
                    )
        return None

    def _check_body(self, op, body, contract):
        schema_name = op["requestBody"]["schema"]
        fields = contract.body_fields(schema_name)
        for key, value in body.items():
            if key not in fields:
                return (
                    "Request body field '%s' is not declared on %s in the pinned contract. "
                    "Declared fields: %s." % (key, schema_name, ", ".join(sorted(fields)))
                )
            if value is None:
                return (
                    "Request body field '%s' was sent as null. The contract's wire rules "
                    "require an unsupplied optional field to be omitted from the body, not "
                    "sent as null." % key
                )
            if value == "" or value == {} or value == []:
                return (
                    "Request body field '%s' was sent empty. The contract's wire rules "
                    "require an unsupplied optional field to be omitted from the body, not "
                    "sent as an empty value." % key
                )
        return None

    # -- paging helpers ---------------------------------------------------

    def _page_args(self, query, default_size=20):
        page = int(query.get("page", ["0"])[0])
        size = int(query.get("size", [str(default_size)])[0])
        if page < 0:
            raise ValueError("page must be >= 0")
        if size < 1:
            raise ValueError("size must be >= 1")
        return page, min(size, 2000)

    # -- operations -------------------------------------------------------

    def _op_getDeployments(self, path_params, query, body):
        try:
            page, size = self._page_args(query)
        except ValueError as exc:
            return self._error(400, str(exc))

        items = list(fixtures.OTHER_DEPLOYMENTS)

        if "name" in query:
            wanted = query["name"][0]
            items = [d for d in items if d["name"] == wanted]
        if "ids" in query:
            wanted = set()
            for value in query["ids"]:
                wanted.update(v for v in value.split(",") if v)
            items = [d for d in items if d["id"] in wanted]
        if "status" in query:
            wanted = set()
            for value in query["status"]:
                wanted.update(v for v in value.split(",") if v)
            items = [d for d in items if d.get("status") in wanted]
        if "projects" in query:
            wanted = set()
            for value in query["projects"]:
                wanted.update(v for v in value.split(",") if v)
            items = [d for d in items if d.get("projectId") in wanted]
        if "search" in query:
            needle = query["search"][0].lower()
            items = [d for d in items if needle in json.dumps(d).lower()]

        items = sorted(items, key=lambda d: d.get("createdAt", ""), reverse=True)
        return self._send(200, paged(items, page, size))

    def _op_getDeploymentRequests(self, path_params, query, body):
        if path_params["deploymentId"] != fixtures.DEPLOYMENT_ID:
            return self._error(404, "Deployment %s not found." % path_params["deploymentId"])
        try:
            page, size = self._page_args(query)
        except ValueError as exc:
            return self._error(400, str(exc))

        items = list(fixtures.DEPLOYMENT_REQUESTS)
        items += list(self.server.created_requests)
        items = sorted(items, key=lambda r: r["createdAt"], reverse=True)
        return self._send(200, paged(items, page, size))

    def _op_getRequestEvents(self, path_params, query, body):
        request_id = path_params["requestId"]
        events = fixtures.events_for_request(request_id)
        if events is None and request_id not in self.server.created_by_id:
            return self._error(404, "Request %s not found." % request_id)
        if events is None:
            events = []
        try:
            page, size = self._page_args(query)
        except ValueError as exc:
            return self._error(400, str(exc))
        return self._send(200, paged(events, page, size))

    def _op_getEventLogs(self, path_params, query, body):
        request_id = path_params["requestId"]
        event_id = path_params["eventId"]

        events = fixtures.events_for_request(request_id)
        if events is None and request_id not in self.server.created_by_id:
            return self._error(404, "Request %s not found." % request_id)

        logs = fixtures.logs_for_event(request_id, event_id)
        if logs is None:
            return self._error(
                404,
                "No logs for event %s on request %s. Logs exist only for events whose "
                "hasLogs field is true." % (event_id, request_id),
            )

        offset = 0
        if "sinceRow" in query:
            try:
                since = int(query["sinceRow"][0])
            except ValueError:
                return self._error(400, "sinceRow must be an integer.")
            if since < 1:
                return self._error(400, "sinceRow must be a positive row number.")
            offset = since - 1
        return self._send(200, sliced(logs, offset, max(len(logs), 1)))

    def _op_submitDeploymentActionRequest(self, path_params, query, body):
        deployment_id = path_params["deploymentId"]
        if deployment_id != fixtures.DEPLOYMENT_ID:
            return self._error(404, "Deployment %s not found." % deployment_id)

        action_id = body.get("actionId")
        if not action_id:
            return self._error(
                400,
                "actionId is required to submit a deployment action request; the body "
                "carried no action to perform.",
            )
        if action_id not in fixtures.SUPPORTED_ACTIONS:
            return self._error(
                400,
                "Action '%s' is not available on this deployment. Available actions: %s."
                % (action_id, ", ".join(fixtures.SUPPORTED_ACTIONS)),
            )

        if self.server.created_requests:
            return self._error(
                409,
                "A request is already in progress for deployment %s." % deployment_id,
            )

        created = {
            "id": fixtures.REMEDIATION_REQUEST_ID,
            "name": "Power On" if action_id == "Deployment.PowerOn" else action_id,
            "details": "Request accepted.",
            "status": "PENDING",
            "actionId": action_id,
            "deploymentId": deployment_id,
            "requestedBy": fixtures.REQUESTED_BY,
            "createdAt": "2026-07-29T06:00:00.000Z",
            "completedTasks": 0,
            "totalTasks": 7,
            "cancelable": True,
            "dismissed": False,
        }
        if "reason" in body:
            created["details"] = body["reason"]
        if "inputs" in body:
            created["inputs"] = body["inputs"]

        self.server.created_requests.append(created)
        self.server.created_by_id[created["id"]] = created
        return self._send(200, created)

    def _op_getRequest(self, path_params, query, body):
        request_id = path_params["requestId"]
        created = self.server.created_by_id.get(request_id)
        if created is not None:
            # The submitted action has been picked up by the appliance.
            observed = dict(created)
            observed["status"] = "INPROGRESS"
            observed["initializedAt"] = "2026-07-29T06:00:04.118Z"
            observed["completedTasks"] = 1
            return self._send(200, observed)

        existing = fixtures.find_request(request_id)
        if existing is None:
            return self._error(404, "Request %s not found." % request_id)
        return self._send(200, existing)


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, contract, request_log, token):
        super().__init__(addr, Handler)
        self.contract = contract
        self.request_log = request_log
        self.token = token
        self.created_requests = []
        self.created_by_id = {}


def build_server(port=0, contract_path=None, request_log_path=None, token=DEFAULT_TOKEN):
    if contract_path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        contract_path = os.path.join(os.path.dirname(here), "docs", "contract.json")
    with open(contract_path, "r", encoding="utf-8") as fh:
        contract = Contract(json.load(fh))
    return MockServer(("127.0.0.1", port), contract, RequestLog(request_log_path), token)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="port to bind (0 picks a free one)")
    parser.add_argument("--contract", default=None, help="path to docs/contract.json")
    parser.add_argument("--request-log", default=None, help="path to the JSON Lines request log")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="bearer token the mock accepts")
    args = parser.parse_args()

    server = build_server(args.port, args.contract, args.request_log, args.token)
    print("LISTENING %d" % server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
