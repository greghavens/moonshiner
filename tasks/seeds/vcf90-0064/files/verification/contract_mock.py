#!/usr/bin/env python3
"""Loopback VMware Cloud Foundation Operations service, pinned to docs/contract.json.

The route table is built from the contract alone: an operation that the contract
does not name is not servable, and any request that misses the table is answered
404 and recorded with "off_contract": true.

The service is genuinely stateful. It stores the maintenance schedules that are
POSTed to it, assigns the identifiers, and enforces the uniqueness rule that the
pinned specification states for createMaintenanceSchedules:

    422 - A maintenance schedule with the same key already exists

Nothing is pre-seeded. Every schedule this process knows about got there because
the module under test created it.

Standard library only. Binds 127.0.0.1 on an ephemeral port and writes the chosen
port to --port-file once it is listening. Every request is appended to --log as
one JSON object per line.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SESSION_TOKEN = "0b7c4e1a9d3f4a2b8c6e5d0f1a2b3c4d::7e19"
TOKEN_VALIDITY_MS = 1893456000000
TOKEN_EXPIRES_AT = "Tuesday, January 1, 2030 12:00:00 AM UTC"
TOKEN_ROLES = ["Administrator"]

SERVER_VERSION = {
    "releaseName": "VCF Operations 9.0.0.0",
    "major": 9,
    "minor": 0,
    "minorMinor": 0,
    "buildNumber": 24000000,
    "releasedDate": 1750143600231,
    "humanlyReadableReleaseDate": "Tuesday, June 17, 2025 at 12:00:00 AM UTC",
}

# acquireToken's request body is composed by Connect-VcfOpsServer inside the
# PowerCLI SDK, not by the module under test, and that cmdlet sends authSource as
# an explicit null when no auth source was supplied. The omission rule is
# therefore enforced only on the bodies the module itself composes.
STRICT_OMISSION_OPERATIONS = {
    "createMaintenanceSchedules",
    "updateMaintenanceSchedules",
}


def load_contract(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def build_routes(contract):
    """Every servable route comes from the contract. Nothing else exists."""
    base = contract["basePath"].rstrip("/")
    routes = []
    for op in contract["operations"]:
        template = base + op["specPath"]
        pattern = "^" + re.sub(
            r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: "(?P<%s>[^/]+)" % m.group(1),
            template,
        ) + "$"
        routes.append(
            {
                "operationId": op["operationId"],
                "method": op["method"].upper(),
                "regex": re.compile(pattern),
                "allowedQuery": set((op.get("query") or {}).keys()),
                "authenticated": bool(op.get("authenticated")),
                "bodySchema": (op.get("requestBody") or {}).get("schema"),
            }
        )
    auth = contract["auth"]
    return {
        "routes": routes,
        "authHeader": auth["header"],
        "authPrefix": auth["headerPrefix"],
        "schemas": contract["schemas"],
    }


class Recorder:
    """Append-only JSON Lines request log."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()
        with open(path, "w", encoding="utf-8"):
            pass

    def record(self, entry):
        line = json.dumps(entry, sort_keys=True)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())


class Store:
    """The maintenance schedules this service is holding."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []
        self._next = 0

    def _new_id(self):
        self._next += 1
        return "0b1e5a70-0000-4000-8000-%012d" % self._next

    def list(self, ids, names):
        with self._lock:
            items = list(self._items)
        if ids:
            items = [i for i in items if i["id"] in ids]
        if names:
            # The API calls this a name filter rather than an exact-key lookup.
            # Return containing names as well so the module must perform the
            # exact key match required by the reconciliation contract.
            items = [
                i for i in items
                if any(name in i["key"] for name in names)
            ]
        return [json.loads(json.dumps(i)) for i in items]

    def create(self, key, schedule):
        with self._lock:
            if any(i["key"] == key for i in self._items):
                return None
            item = {"id": self._new_id(), "key": key, "schedule": schedule}
            self._items.append(item)
            return json.loads(json.dumps(item))

    def update(self, identifier, key, schedule):
        with self._lock:
            for item in self._items:
                if item["id"] == identifier:
                    if item["key"] != key:
                        return "key-mismatch"
                    item["schedule"] = schedule
                    return "updated"
            return None

    def count(self):
        with self._lock:
            return len(self._items)


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config, recorder):
        super().__init__(address, Handler)
        self.config = config
        self.recorder = recorder
        self.store = Store()
        self._seq = 0
        self._seq_lock = threading.Lock()

    def next_sequence(self):
        with self._seq_lock:
            self._seq += 1
            return self._seq


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOpsContractMock/1.0"

    def log_message(self, *args):  # silence stderr chatter
        pass

    # ---------- plumbing ----------

    def _respond(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _fail(self, status, message):
        self._respond(status, {"message": message, "httpStatusCode": status})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8")

    # ---------- schema checks ----------

    def _check_object(self, schema_name, value, path, strict):
        """Validate one object against the projected schema in the contract."""
        schema = self.server.config["schemas"][schema_name]
        fields = schema["fields"]
        if not isinstance(value, dict):
            return "%s must be a JSON object" % path
        for name in value:
            if name not in fields:
                return "%s.%s is not a member of %s" % (path, name, schema_name)
        for name in schema["required"]:
            if name not in value:
                return "%s.%s is required by %s" % (path, name, schema_name)
        for name, item in value.items():
            spec = fields[name]
            where = "%s.%s" % (path, name)
            if item is None:
                if strict:
                    return ("%s was sent as null; an optional member the caller did "
                            "not supply must be omitted" % where)
                continue
            if strict and item == "":
                return ("%s was sent as an empty string; an optional member the "
                        "caller did not supply must be omitted" % where)
            if strict and isinstance(item, list) and not item:
                return ("%s was sent as an empty array; an optional member the "
                        "caller did not supply must be omitted" % where)
            if spec.get("schema"):
                nested = self._check_object(spec["schema"], item, where, strict)
                if nested:
                    return nested
                continue
            expected = spec.get("type")
            if expected == "string" and not isinstance(item, str):
                return "%s must be a string" % where
            if expected == "integer" and not isinstance(item, int):
                return "%s must be an integer" % where
            if expected == "array" and not isinstance(item, list):
                return "%s must be an array" % where
            if spec.get("enum") and item not in spec["enum"]:
                return "%s is not one of %s" % (where, spec["enum"])
            items_spec = spec.get("items") or {}
            if expected == "array" and items_spec.get("enum"):
                for element in item:
                    if element not in items_spec["enum"]:
                        return "%s contains %r, which is not one of %s" % (
                            where, element, items_spec["enum"])
            if "minimum" in spec and isinstance(item, int) and item < spec["minimum"]:
                return "%s must be at least %s" % (where, spec["minimum"])
        return None

    # ---------- operations ----------

    def _op_acquire_token(self, body_json):
        problem = self._check_object("username-password", body_json,
                                     "username-password", strict=False)
        if problem:
            return self._fail(400, problem)
        if not body_json.get("username") or not body_json.get("password"):
            return self._respond(401, {"message": "Authentication failed"})
        return self._respond(200, {
            "token": SESSION_TOKEN,
            "validity": TOKEN_VALIDITY_MS,
            "expiresAt": TOKEN_EXPIRES_AT,
            "roles": list(TOKEN_ROLES),
        })

    def _op_get_version(self):
        return self._respond(200, dict(SERVER_VERSION))

    def _op_get_schedules(self, query):
        ids = set(query.get("id", []))
        names = set(query.get("name", []))
        matches = self.server.store.list(ids, names)
        return self._respond(200, {
            "schedules": matches,
            "pageInfo": {
                "totalCount": len(matches),
                "page": 0,
                "pageSize": 1000,
            },
        })

    def _op_create_schedule(self, body_json):
        problem = self._check_object(
            "maintenance-schedule", body_json, "maintenance-schedule",
            strict="createMaintenanceSchedules" in STRICT_OMISSION_OPERATIONS)
        if problem:
            return self._fail(400, problem)
        if "id" in body_json:
            return self._fail(400, "maintenance-schedule.id is assigned by the "
                                   "server and must not be sent on create")
        created = self.server.store.create(body_json["key"], body_json["schedule"])
        if created is None:
            return self._fail(422, "A maintenance schedule with the same key "
                                   "already exists")
        return self._respond(201, created)

    def _op_update_schedule(self, body_json):
        problem = self._check_object(
            "maintenance-schedule", body_json, "maintenance-schedule",
            strict="updateMaintenanceSchedules" in STRICT_OMISSION_OPERATIONS)
        if problem:
            return self._fail(400, problem)
        if "id" not in body_json:
            return self._fail(400, "maintenance-schedule.id identifies the "
                                   "schedule to update and is required here")
        outcome = self.server.store.update(
            body_json["id"], body_json["key"], body_json["schedule"])
        if outcome is None:
            return self._fail(404, "The maintenance schedule does not exist")
        if outcome == "key-mismatch":
            return self._fail(400, "maintenance-schedule.key does not match the "
                                   "stored key for that id")
        return self._respond(200, None)

    # ---------- dispatch ----------

    def _dispatch(self, method):
        config = self.server.config
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        body_text = self._read_body()
        try:
            body_json = json.loads(body_text) if body_text else None
        except ValueError:
            body_json = None

        matched = None
        path_known = False
        for route in config["routes"]:
            if route["regex"].match(parsed.path):
                path_known = True
                if route["method"] == method:
                    matched = route
                    break

        entry = {
            "sequence": self.server.next_sequence(),
            "method": method,
            "target": self.path,
            "path": parsed.path,
            "rawQuery": parsed.query,
            "hasQueryDelimiter": "?" in self.path,
            "query": {k: list(v) for k, v in query.items()},
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "bodyText": body_text,
            "bodyJson": body_json,
            "operationId": matched["operationId"] if matched else None,
            "offContract": matched is None,
            "storeCount": self.server.store.count(),
        }
        self.server.recorder.record(entry)

        if matched is None:
            if path_known:
                return self._fail(405, "%s is not a method the contract names for "
                                       "%s" % (method, parsed.path))
            return self._fail(404, "%s %s is not an operation named by "
                                   "docs/contract.json" % (method, parsed.path))

        unexpected = sorted(set(query) - matched["allowedQuery"])
        if unexpected:
            return self._fail(400, "%s does not accept the query parameter(s) %s"
                              % (matched["operationId"], ", ".join(unexpected)))
        for name, values in query.items():
            if any(v == "" for v in values):
                return self._fail(400, "%s was sent as an empty query parameter; "
                                       "an unsupplied optional parameter must be "
                                       "absent from the request target" % name)

        if matched["authenticated"]:
            presented = self.headers.get(config["authHeader"])
            expected = config["authPrefix"] + SESSION_TOKEN
            if presented != expected:
                return self._respond(401, {"message": "Authentication failed"})

        if matched["bodySchema"] and body_json is None:
            return self._fail(400, "%s requires a JSON request body"
                              % matched["operationId"])

        operation = matched["operationId"]
        if operation == "acquireToken":
            return self._op_acquire_token(body_json)
        if operation == "getCurrentVersionOfServer":
            return self._op_get_version()
        if operation == "getMaintenanceSchedules":
            return self._op_get_schedules(query)
        if operation == "createMaintenanceSchedules":
            return self._op_create_schedule(body_json)
        if operation == "updateMaintenanceSchedules":
            return self._op_update_schedule(body_json)
        return self._fail(501, "%s is named by the contract but not implemented "
                               "by this mock" % operation)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_HEAD(self):
        self._dispatch("HEAD")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args()

    config = build_routes(load_contract(args.contract))
    recorder = Recorder(args.log)
    server = MockServer(("127.0.0.1", 0), config, recorder)
    port = server.server_address[1]

    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(port))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, args.port_file)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
