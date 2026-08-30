#!/usr/bin/env python3
"""Loopback mock of the vSAN Data Protection snapshot appliance.

Every callable route is derived at start-up from the operations named in
docs/contract.json.  Nothing else is reachable.  The mock issues session
tokens, expires the first one part way through the run, and records every
request it receives to a flushed and fsynced JSONL log.

No live VMware endpoint is contacted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=OrderedDict)


class Route:
    __slots__ = ("operation_id", "method", "pattern", "required_query",
                 "optional_query", "body_schema", "success_status", "security")

    def __init__(self, operation_id, spec):
        self.operation_id = operation_id
        self.method = spec["method"]
        template = spec["path_template"]
        regex = "^" + PATH_PARAM.sub(
            lambda m: "(?P<%s>[^/]+)" % m.group(1),
            re.sub(r"([.^$*+?()\[\]|\\])", r"\\\1", template),
        ) + "$"
        # The escaping pass above also escaped nothing inside {} because path
        # templates only contain word characters there.
        self.pattern = re.compile(regex)
        self.required_query = OrderedDict(spec.get("required_query") or {})
        self.optional_query = [q["name"] for q in spec.get("optional_query_parameters") or []]
        body = spec.get("request_body")
        self.body_schema = body["schema"] if body else None
        self.success_status = spec["success_status"]
        self.security = list(spec.get("security") or [])


class Appliance:
    """All mutable state of the simulated appliance."""

    def __init__(self, config, contract):
        self.lock = threading.Lock()
        self.contract = contract
        self.schemas = contract["schemas"]
        self.routes = [Route(op, spec) for op, spec in contract["operations"].items()]

        self.username = config["username"]
        self.password = config["password"]
        self.cluster = config["cluster"]
        self.pg_id = config["pg_id"]
        self.snapshot_id = config["snapshot_id"]
        self.create_task_id = config["create_task_id"]
        self.snapshot_task_id = config["snapshot_task_id"]
        self.pg_name = config["pg_name"]
        self.snapshot_name = config["snapshot_name"]
        self.tokens = list(config["tokens"])
        self.expire_after = int(config["expire_after"])
        self.polls_before_success = int(config["polls_before_success"])

        self.issued = []           # tokens handed out, in order
        self.token_budget = {}     # token -> remaining authenticated calls or None
        self.pg_created = False
        self.pg_ready = False
        self.snapshot_created = False
        self.snapshot_ready = False
        self.task_polls = {}       # task id -> successful poll count

        self.seq = 0
        self.log_handle = open(config["log_path"], "a", encoding="utf-8")

    # -- logging ---------------------------------------------------------
    def record(self, entry):
        self.seq += 1
        entry["seq"] = self.seq
        self.log_handle.write(json.dumps(entry, sort_keys=False) + "\n")
        self.log_handle.flush()
        os.fsync(self.log_handle.fileno())
        return self.seq

    # -- session tokens --------------------------------------------------
    def issue_token(self):
        if len(self.issued) >= len(self.tokens):
            return None
        token = self.tokens[len(self.issued)]
        self.issued.append(token)
        self.token_budget[token] = self.expire_after if len(self.issued) == 1 else None
        return token

    def spend_token(self, token):
        """Return True when the token is live; consume one unit of its budget."""
        if token not in self.token_budget:
            return False
        budget = self.token_budget[token]
        if budget is None:
            return True
        if budget <= 0:
            return False
        self.token_budget[token] = budget - 1
        return True


def error_body(error_type, message):
    return OrderedDict([
        ("error_type", error_type),
        ("messages", [OrderedDict([
            ("id", "com.vmware.snapservice." + error_type.lower()),
            ("default_message", message),
            ("args", []),
        ])]),
    ])


def task_info(status, description, result=None):
    info = OrderedDict()
    info["status"] = status
    info["cancelable"] = False
    info["description"] = OrderedDict([
        ("id", "com.vmware.snapservice.task"),
        ("default_message", description),
        ("args", []),
    ])
    info["service"] = "com.vmware.snapservice.clusters.protection_groups"
    info["operation"] = "create"
    if result is not None:
        info["result"] = result
    return info


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    appliance: Appliance = None  # type: ignore[assignment]

    def log_message(self, *args):  # silence stderr chatter
        return

    # -- plumbing --------------------------------------------------------
    def respond(self, status, payload):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_HEAD(self):
        self.dispatch("HEAD")

    # -- dispatch --------------------------------------------------------
    def dispatch(self, method):
        app = self.appliance
        raw_body = self.read_body()
        split = urlsplit(self.path)
        query = parse_qsl(split.query, keep_blank_values=True)
        headers = [[k, v] for k, v in self.headers.items()]

        with app.lock:
            operation_id, status, payload = self.route(method, split.path, query, raw_body)
            app.record(OrderedDict([
                ("method", method),
                ("target", self.path),
                ("path", split.path),
                ("query", split.query),
                ("query_pairs", [list(pair) for pair in query]),
                ("headers", headers),
                ("body", raw_body.decode("utf-8", "replace")),
                ("body_sha256", hashlib.sha256(raw_body).hexdigest()),
                ("operation_id", operation_id),
                ("status", status),
            ]))
            self.respond(status, payload)

    def route(self, method, path, query, raw_body):
        app = self.appliance
        path_matches = []
        for route in app.routes:
            match = route.pattern.match(path)
            if match:
                path_matches.append((route, match))
        if not path_matches:
            return None, 404, error_body(
                "NOT_FOUND", "no operation in docs/contract.json serves this path")
        method_matches = [(r, m) for r, m in path_matches if r.method == method]
        if not method_matches:
            return None, 405, error_body(
                "NOT_ALLOWED_IN_CURRENT_STATE", "method not part of this contract")

        # ?vmw-task=true separates the task-returning operations from the
        # plain ones that share the same path.
        chosen = None
        for route, match in method_matches:
            pairs = OrderedDict(query)
            if all(pairs.get(k) == v for k, v in route.required_query.items()):
                if chosen is None or len(route.required_query) > len(chosen[0].required_query):
                    chosen = (route, match)
        if chosen is None:
            return None, 404, error_body(
                "NOT_FOUND", "required query parameters for this operation are missing")
        route, match = chosen

        allowed = set(route.required_query) | set(route.optional_query)
        for name, _value in query:
            if name not in allowed:
                return route.operation_id, 400, error_body(
                    "INVALID_ARGUMENT",
                    "query parameter %r is not declared for %s" % (name, route.operation_id))

        auth_status, auth_payload = self.authenticate(route)
        if auth_status is not None:
            return route.operation_id, auth_status, auth_payload

        if route.body_schema is not None:
            ok, problem = self.validate_body(route, raw_body)
            if not ok:
                return route.operation_id, 400, error_body("INVALID_ARGUMENT", problem)
            body = json.loads(raw_body.decode("utf-8"), object_pairs_hook=OrderedDict)
        else:
            if raw_body:
                return route.operation_id, 400, error_body(
                    "INVALID_ARGUMENT", "%s does not accept a request body" % route.operation_id)
            body = None

        handler = {
            "Snapservice.Sessions_create": self.op_session_create,
            "Snapservice.Clusters.ProtectionGroups_create$Task": self.op_pg_create,
            "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task": self.op_snapshot_create,
            "Snapservice.Tasks_get": self.op_task_get,
            "Snapservice.Clusters.ProtectionGroups_list": self.op_pg_list,
        }[route.operation_id]
        status, payload = handler(match.groupdict(), OrderedDict(query), body)
        return route.operation_id, status, payload

    def authenticate(self, route):
        app = self.appliance
        basic = self.headers.get_all("Authorization") or []
        session = self.headers.get_all("vmware-api-session-id") or []

        if "basic_auth" in route.security or route.operation_id == "Snapservice.Sessions_create":
            if session:
                return 401, error_body(
                    "UNAUTHENTICATED", "session token supplied to a login request")
            if len(basic) != 1:
                return 401, error_body("UNAUTHENTICATED", "exactly one Authorization header required")
            value = basic[0]
            if not value.startswith("Basic "):
                return 401, error_body("UNAUTHENTICATED", "basic authentication required")
            try:
                decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
            except Exception:
                return 401, error_body("UNAUTHENTICATED", "malformed basic credentials")
            expected = "%s:%s" % (app.username, app.password)
            if decoded != expected:
                return 401, error_body("UNAUTHENTICATED", "invalid credentials")
            return None, None

        if basic:
            return 401, error_body(
                "UNAUTHENTICATED", "basic credentials replayed on a session authenticated request")
        if len(session) != 1:
            return 401, error_body(
                "UNAUTHENTICATED", "exactly one vmware-api-session-id header required")
        if not app.spend_token(session[0]):
            return 401, error_body("UNAUTHENTICATED", "the session token has expired")
        return None, None

    # -- body validation against the contract schemas --------------------
    def validate_body(self, route, raw_body):
        if not raw_body:
            return False, "%s requires a request body" % route.operation_id
        try:
            body = json.loads(raw_body.decode("utf-8"), object_pairs_hook=OrderedDict)
        except Exception as exc:
            return False, "request body is not JSON: %s" % exc
        if not isinstance(body, dict):
            return False, "request body must be a JSON object"
        return self.validate_object(body, route.body_schema, route.body_schema)

    def validate_object(self, value, schema_name, where):
        schema = self.appliance.schemas.get(schema_name)
        if schema is None:
            return True, ""
        declared = schema["properties_in_declaration_order"]
        types = schema["property_types"]
        for name in value:
            if name not in declared:
                return False, "%s is not a property of %s" % (name, where)
        for name in schema["required"]:
            if name not in value:
                return False, "%s requires %s" % (where, name)
        for name, item in value.items():
            kind = types[name]
            ok, problem = self.validate_value(item, kind, "%s.%s" % (where, name))
            if not ok:
                return False, problem
        return True, ""

    def validate_value(self, value, kind, where):
        if kind["kind"] == "schema":
            if not isinstance(value, dict):
                return False, "%s must be an object" % where
            return self.validate_object(value, kind["schema"], kind["schema"])
        if kind["kind"] == "array":
            if not isinstance(value, list):
                return False, "%s must be an array" % where
            for index, item in enumerate(value):
                ok, problem = self.validate_value(item, kind["items"], "%s[%d]" % (where, index))
                if not ok:
                    return False, problem
            return True, ""
        if kind["kind"] == "primitive":
            expected = kind.get("type")
            if expected == "string" and not isinstance(value, str):
                return False, "%s must be a string" % where
            if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                return False, "%s must be an integer" % where
            if expected == "boolean" and not isinstance(value, bool):
                return False, "%s must be a boolean" % where
        return True, ""

    # -- operations ------------------------------------------------------
    def op_session_create(self, path_params, query, body):
        token = self.appliance.issue_token()
        if token is None:
            return 503, error_body("SERVICE_UNAVAILABLE", "session token supply exhausted")
        return 201, token

    def op_pg_create(self, path_params, query, body):
        app = self.appliance
        if path_params["cluster"] != app.cluster:
            return 404, error_body("NOT_FOUND", "unknown cluster")
        if app.pg_created:
            return 400, error_body(
                "NOT_ALLOWED_IN_CURRENT_STATE",
                "a protection group named %r already exists on this cluster" % app.pg_name)
        app.pg_created = True
        return 202, app.create_task_id

    def op_snapshot_create(self, path_params, query, body):
        app = self.appliance
        if path_params["cluster"] != app.cluster:
            return 404, error_body("NOT_FOUND", "unknown cluster")
        if not app.pg_ready or path_params["pg"] != app.pg_id:
            return 404, error_body("NOT_FOUND", "unknown protection group")
        if app.snapshot_created:
            return 400, error_body(
                "NOT_ALLOWED_IN_CURRENT_STATE", "a snapshot request is already in progress")
        app.snapshot_created = True
        return 202, app.snapshot_task_id

    def op_task_get(self, path_params, query, body):
        app = self.appliance
        task = path_params["task"]
        if task == app.create_task_id and app.pg_created:
            polls = app.task_polls.get(task, 0) + 1
            app.task_polls[task] = polls
            if polls < app.polls_before_success:
                statuses = ("PENDING", "BLOCKED", "RUNNING")
                return 200, task_info(statuses[(polls - 1) % len(statuses)],
                                      "Creating protection group")
            app.pg_ready = True
            return 200, task_info("SUCCEEDED", "Creating protection group", app.pg_id)
        if task == app.snapshot_task_id and app.snapshot_created:
            polls = app.task_polls.get(task, 0) + 1
            app.task_polls[task] = polls
            if polls < app.polls_before_success:
                statuses = ("PENDING", "BLOCKED", "RUNNING")
                return 200, task_info(statuses[(polls - 1) % len(statuses)],
                                      "Taking protection group snapshot")
            app.snapshot_ready = True
            return 200, task_info("SUCCEEDED", "Taking protection group snapshot", app.snapshot_id)
        return 404, error_body("NOT_FOUND", "unknown task")

    def op_pg_list(self, path_params, query, body):
        app = self.appliance
        if path_params["cluster"] != app.cluster:
            return 404, error_body("NOT_FOUND", "unknown cluster")
        items = []
        if app.pg_ready:
            info = OrderedDict()
            info["name"] = app.pg_name
            info["status"] = "ACTIVE"
            info["locked"] = False
            info["target_entities"] = OrderedDict([("vms", [])])
            info["vms"] = []
            info["snapshot_policies"] = []
            info["snapshots"] = [app.snapshot_id] if app.snapshot_ready else []
            items.append(OrderedDict([("pg", app.pg_id), ("info", info)]))
        if "pgs" in query and query["pgs"] != app.pg_id:
            items = []
        return 200, OrderedDict([("items", items)])


def main():
    config_path = sys.argv[1]
    config = load_json(config_path)
    contract = load_json(config["contract_path"])
    appliance = Appliance(config, contract)
    Handler.appliance = appliance

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    tmp = config["port_path"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(port))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, config["port_path"])
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
