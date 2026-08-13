#!/usr/bin/env python3
"""Loopback stand-in for a VCF Automation 9.1 appliance.

It is pinned to docs/contract.json: the route table, the accepted query
parameter names, the accepted request body properties and the authentication
requirement of every operation are all read out of the contract at startup.
Anything the contract does not name gets a 404, including the operations on the
contract's own ``excludedOperations`` list.

Every request it receives is appended to the request log as one JSON object per
line, whether it was accepted or refused.

    python3 mock/vcfa_mock.py --port 0 --state <in.json> --state-out <out.json> \
                              --log <requests.jsonl>

With ``--port 0`` it binds an ephemeral port and prints ``LISTENING <port>`` on
stdout before serving. It binds 127.0.0.1 and nothing else.
"""

import argparse
import json
import os
import re
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Contract:
    """The subset of docs/contract.json the stand-in enforces."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            self.raw = json.load(fh)
        self.schemas = self.raw["schemas"]
        self.routes = []
        for name, op in self.raw["operations"].items():
            pattern = "^" + re.sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", op["path"]) + "$"
            self.routes.append(
                {
                    "operation": name,
                    "method": op["method"],
                    "regex": re.compile(pattern),
                    "query": {p["name"] for p in op.get("queryParameters", [])},
                    "secured": bool(op.get("security")),
                    "body_schema": (op.get("requestBody") or {}).get("schema"),
                }
            )

    def match(self, method, path):
        """Return (route, path_params) or (None, None)."""
        for route in self.routes:
            m = route["regex"].match(path)
            if m and route["method"] == method:
                return route, m.groupdict()
        return None, None

    def body_properties(self, schema_name):
        schema = self.schemas[schema_name]
        return set(schema.get("properties", {})), set(schema.get("required", []))


class State:
    def __init__(self, path, out_path):
        with open(path, encoding="utf-8") as fh:
            self.data = json.load(fh)
        self.out_path = out_path
        self.data.setdefault("deployments", [])
        self.data.setdefault("faults", [])
        self.lock = threading.Lock()
        self.flush()

    def flush(self):
        if not self.out_path:
            return
        tmp = self.out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.out_path)

    def take_fault(self, operation, catalog_item_id):
        """Pop and return the next armed fault for this call, if any."""
        for fault in self.data["faults"]:
            if fault.get("operation") != operation:
                continue
            if fault.get("catalogItemId") not in (None, catalog_item_id):
                continue
            if fault.get("times", 1) <= 0:
                continue
            fault["times"] = fault.get("times", 1) - 1
            return fault
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfAutomationStandIn/9.1"
    sys_version = ""

    contract = None
    state = None
    log_path = None
    seq = 0
    seq_lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def log_message(self, *args):
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _record(self, entry):
        with Handler.seq_lock:
            Handler.seq += 1
            entry["seq"] = Handler.seq
        with open(Handler.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def _record_current(self, status):
        if not self._recorded:
            self._current_entry["status"] = status
            self._record(self._current_entry)
            self._recorded = True

    def _send(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        # Persist the request before making the response visible to the client.
        # The verifier can therefore read the completed log as soon as its
        # PowerShell child returns, without racing this handler.
        self._record_current(status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _error(self, status, message):
        return self._send(
            status,
            {"message": message, "statusCode": status, "serverErrorId": str(uuid.uuid4())},
        )

    def _disconnect(self):
        """Drop the connection without an HTTP response (unknown outcome)."""
        self._record_current(None)
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.connection.close()
        return None

    # -- dispatch ---------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        split = urlsplit(self.path)
        raw_body = self._read_body()
        try:
            body_json = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (ValueError, UnicodeDecodeError):
            body_json = None

        entry = {
            "method": method,
            "path": split.path,
            "query": split.query,
            "queryParams": parse_qs(split.query, keep_blank_values=True),
            "headers": {k: v for k, v in self.headers.items()},
            "bodyRaw": raw_body.decode("utf-8", "replace"),
            "bodyJson": body_json,
            "operation": None,
            "status": None,
        }
        self._current_entry = entry
        self._recorded = False

        route, path_params = Handler.contract.match(method, split.path)
        try:
            if route is None:
                status = self._error(
                    404, "No operation on this contract matches %s %s" % (method, split.path)
                )
            else:
                entry["operation"] = route["operation"]
                status = self._handle(route, path_params, entry)
        except Exception as exc:  # pragma: no cover - stand-in should not crash
            status = self._error(500, "stand-in failure: %s" % exc)
        self._record_current(status)

    def _validate(self, route, entry):
        """Contract-level checks shared by every operation. Returns a status or None."""
        unknown = sorted(set(entry["queryParams"]) - route["query"])
        if unknown:
            return self._error(
                400, "Query parameter(s) not on this contract: %s" % ", ".join(unknown)
            )

        auth = self.headers.get("Authorization")
        if route["secured"]:
            expected = self._expected_authorization()
            if not auth:
                return self._error(401, "Missing Authorization header")
            if auth != expected:
                return self._error(401, "Invalid or expired access token")
        elif auth:
            return self._error(
                400, "%s is the token-minting call and must not carry an Authorization header"
                % route["operation"]
            )

        schema_name = route["body_schema"]
        if schema_name:
            body = entry["bodyJson"]
            if not isinstance(body, dict):
                return self._error(400, "Request body must be a JSON object")
            known, required = Handler.contract.body_properties(schema_name)
            unknown = sorted(set(body) - known)
            if unknown:
                return self._error(
                    400,
                    "Propert(y|ies) not on schema %s: %s" % (schema_name, ", ".join(unknown)),
                )
            missing = sorted(required - set(body))
            if missing:
                return self._error(
                    400, "Missing required propert(y|ies): %s" % ", ".join(missing)
                )
        return None

    def _expected_authorization(self):
        session = Handler.state.data.get("session", {})
        return "%s %s" % (session.get("tokenType", "Bearer"), session.get("accessToken", ""))

    def _handle(self, route, path_params, entry):
        refused = self._validate(route, entry)
        if refused is not None:
            return refused
        return getattr(self, "_op_" + route["operation"])(path_params, entry)

    # -- operations -------------------------------------------------------

    def _op_retrieveAuthToken(self, path_params, entry):
        session = Handler.state.data.get("session", {})
        if entry["bodyJson"].get("refreshToken") != session.get("refreshToken"):
            return self._error(403, "Invalid refresh token")
        return self._send(
            200,
            {
                "tokenType": session.get("tokenType", "Bearer"),
                "token": session.get("accessToken", ""),
            },
        )

    def _op_getDeployments(self, path_params, entry):
        params = entry["queryParams"]
        rows = [d for d in Handler.state.data["deployments"] if not d.get("deleted")]

        if "name" in params:
            wanted = params["name"][-1]
            rows = [d for d in rows if d.get("name") == wanted]
        if "projects" in params:
            wanted = set()
            for value in params["projects"]:
                wanted.update(part for part in value.split(",") if part)
            rows = [d for d in rows if d.get("projectId") in wanted]
        if "ids" in params:
            wanted = set()
            for value in params["ids"]:
                wanted.update(part for part in value.split(",") if part)
            rows = [d for d in rows if d.get("id") in wanted]

        size = int(params.get("size", ["20"])[-1])
        page = int(params.get("page", ["0"])[-1])
        window = rows[page * size : page * size + size]
        total_pages = (len(rows) + size - 1) // size if rows else 0
        return self._send(
            200,
            {
                "content": window,
                "empty": not window,
                "first": page == 0,
                "last": page + 1 >= max(total_pages, 1),
                "number": page,
                "numberOfElements": len(window),
                "pageable": {"pageNumber": page, "pageSize": size},
                "size": size,
                "sort": {"sorted": False, "unsorted": True, "empty": True},
                "totalElements": len(rows),
                "totalPages": total_pages,
            },
        )

    def _op_requestCatalogItemInstances(self, path_params, entry):
        item_id = path_params["id"]
        items = {i["id"]: i for i in Handler.state.data.get("catalogItems", [])}
        if item_id not in items:
            return self._error(404, "Catalog item %s not found" % item_id)
        item = items[item_id]

        body = entry["bodyJson"]
        project_id = body.get("projectId")
        projects = {p["id"] for p in Handler.state.data.get("projects", [])}
        if project_id is not None and project_id not in projects:
            return self._error(400, "Project %s not found" % project_id)

        count = body.get("bulkRequestCount", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            return self._error(400, "bulkRequestCount must be a positive integer")

        with Handler.state.lock:
            fault = Handler.state.take_fault("requestCatalogItemInstances", item_id)
            mode = fault.get("mode") if fault else None
            if mode in ("fail-before-commit", "disconnect-before-commit"):
                Handler.state.flush()
                if mode == "disconnect-before-commit":
                    return self._disconnect()
                return self._error(503, "Service temporarily unavailable")

            created = []
            base = body.get("deploymentName") or item.get("name", "deployment")
            for index in range(count):
                name = base if count == 1 else "%s-%d" % (base, index + 1)
                record = {
                    "id": str(uuid.uuid4()),
                    "name": name,
                    "status": "CREATE_INPROGRESS",
                    "catalogItemId": item_id,
                    "catalogItemVersion": body.get("version") or item.get("version"),
                    "projectId": project_id,
                    "createdAt": _now(),
                    "deleted": False,
                    "ownedBy": Handler.state.data.get("session", {}).get("user", "unknown"),
                }
                Handler.state.data["deployments"].append(record)
                created.append({"deploymentId": record["id"], "deploymentName": name})
            Handler.state.flush()

        if mode == "commit-then-503":
            # The deployment exists; the caller never learns its id.
            return self._error(503, "Service temporarily unavailable")
        if mode == "commit-then-disconnect":
            # The deployment exists; the connection closes before any response.
            return self._disconnect()
        return self._send(200, created)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--state", default=os.path.join(REPO_ROOT, "mock", "fixtures", "appliance-state.json"))
    parser.add_argument("--state-out", default=None)
    parser.add_argument("--log", default=os.path.join(REPO_ROOT, "mock", "requests.jsonl"))
    parser.add_argument("--contract", default=os.path.join(REPO_ROOT, "docs", "contract.json"))
    args = parser.parse_args()

    open(args.log, "w", encoding="utf-8").close()

    Handler.contract = Contract(args.contract)
    Handler.state = State(args.state, args.state_out)
    Handler.log_path = args.log

    httpd = HTTPServer(("127.0.0.1", args.port), Handler)
    print("LISTENING %d" % httpd.server_address[1], flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
