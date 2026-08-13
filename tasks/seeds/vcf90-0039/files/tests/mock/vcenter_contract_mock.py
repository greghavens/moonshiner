#!/usr/bin/env python3
"""Loopback mock of the five vCenter Automation API operations named by docs/contract.json.

The route table is built from the contract file itself: an operation the contract does not
name is not served, it answers 404 {"error": "not_in_contract"}. Every request is appended
to a JSON Lines log so a test can assert the exact wire shape that was produced.

This process never talks to a vCenter Server. It listens on 127.0.0.1 with an ephemeral
port and a caller-supplied TLS key pair.
"""

import argparse
import base64
import http.server
import json
import os
import ssl
import sys
import threading
from urllib.parse import urlsplit, parse_qsl

# ---------------------------------------------------------------- fixture data

ACCOUNT = "svc-automation@vsphere.local"

# The identity provider has already been rotated: only the new secret authenticates.
NEW_SECRET = "N3w-Secret-Rotate!"

# A session minted from the retired secret, still valid until it is explicitly deleted.
OLD_SESSION_ID = "0ldsess1on0000000000000000000000"
NEW_SESSION_ID = "n3wsess1on1111111111111111111111"

CREATED_TIME = "2026-03-04T09:00:00.000Z"
ACCESSED_TIME = "2026-03-04T09:41:12.000Z"

NON_TERMINAL = ("PENDING", "RUNNING", "BLOCKED")

# scenario -> task id -> (initial status, polls before it settles, terminal status)
SCENARIOS = {
    "nominal": {
        "task-9001": ("RUNNING", 2, "SUCCEEDED"),
        "task-9002": ("PENDING", 3, "FAILED"),
    },
    "idle": {},
    "stuck": {
        "task-9003": ("RUNNING", None, None),
    },
    "identity-mismatch": {
        "task-9004": ("RUNNING", 1, "SUCCEEDED"),
    },
}

FILTER_PROPERTIES = ("tasks", "services", "operations", "status", "targets", "users")
GET_SPEC_PROPERTIES = ("return_all", "exclude_result")
LOGGED_HEADERS = ("authorization", "vmware-api-session-id", "content-type", "accept")


class Violation(Exception):
    """A request that reached a contracted operation with a wire shape the contract forbids."""

    def __init__(self, rule, detail):
        super().__init__(detail)
        self.rule = rule
        self.detail = detail


class Denied(Exception):
    def __init__(self, status, code, detail):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- server state


class State:
    def __init__(self, scenario):
        self.lock = threading.Lock()
        self.scenario = scenario
        self.seq = 0
        self.sessions = {OLD_SESSION_ID: {"user": ACCOUNT, "origin": "retired-secret"}}
        self.tasks = {}
        for task_id, (status, settles_after, terminal_status) in SCENARIOS[scenario].items():
            self.tasks[task_id] = {
                "status": status,
                "settles_after": settles_after,
                "terminal_status": terminal_status,
                "polls": 0,
                "service": "com.vmware.vcenter.vm",
                "operation": "clone$task",
                "user": ACCOUNT,
            }

    def task_info(self, task_id):
        task = self.tasks[task_id]
        info = {
            "status": task["status"],
            "cancelable": task["status"] in NON_TERMINAL,
            "service": task["service"],
            "operation": task["operation"],
            "user": task["user"],
            "description": {
                "id": "com.vmware.vcenter.vm.clone",
                "default_message": "Clone virtual machine",
                "args": [],
            },
        }
        if task["status"] in NON_TERMINAL:
            info["progress"] = {
                "total": 100,
                "completed": 40,
                "message": {
                    "id": "com.vmware.cis.task.progress",
                    "default_message": "In progress",
                    "args": [],
                },
            }
        return info


# ---------------------------------------------------------------- wire checking


def reject_unset_placeholders(where, obj):
    """The contract forbids sending a key whose value stands in for "not set"."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = "{0}.{1}".format(where, key)
            if value is None:
                raise Violation("omit_unset_optional_properties", "%s is null" % path)
            if value == "" or value == [] or value == {}:
                raise Violation(
                    "omit_unset_optional_properties",
                    "%s is empty (%s)" % (path, json.dumps(value)),
                )
            reject_unset_placeholders(path, value)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            reject_unset_placeholders("{0}[{1}]".format(where, index), value)


def check_filter_spec(filter_spec):
    if not isinstance(filter_spec, dict):
        raise Violation("no_extra_body_properties", "filter_spec is not an object")
    extra = sorted(set(filter_spec) - set(FILTER_PROPERTIES))
    if extra:
        raise Violation(
            "no_extra_body_properties",
            "filter_spec carries properties outside Cis.Tasks.FilterSpec: %s" % ", ".join(extra),
        )
    if not filter_spec.get("tasks") and not filter_spec.get("services"):
        raise Violation(
            "filter_spec_selector",
            "Cis.Tasks.FilterSpec requires a non-empty tasks or services",
        )
    for name in ("tasks", "services", "operations", "users"):
        value = filter_spec.get(name)
        if value is not None and not (
            isinstance(value, list) and all(isinstance(v, str) for v in value)
        ):
            raise Violation("schema_type", "filter_spec.%s must be an array of strings" % name)
    status = filter_spec.get("status")
    if status is not None:
        allowed = set(NON_TERMINAL) | {"SUCCEEDED", "FAILED"}
        bad = [s for s in status if s not in allowed]
        if bad:
            raise Violation("schema_type", "filter_spec.status has unknown values: %s" % bad)


def check_get_spec_query(query_pairs):
    seen = {}
    for name, value in query_pairs:
        if name not in GET_SPEC_PROPERTIES:
            raise Violation(
                "spec_query_serialization",
                "unexpected query parameter %r; style=form explode=true sends %s"
                % (name, " and ".join(GET_SPEC_PROPERTIES)),
            )
        if name in seen:
            raise Violation("spec_query_serialization", "query parameter %r repeated" % name)
        if value in ("", "null"):
            raise Violation(
                "omit_unset_optional_properties",
                "query parameter %r was sent with no value instead of being omitted" % name,
            )
        if value not in ("true", "false"):
            raise Violation(
                "boolean_query_encoding",
                "query parameter %r must be true or false, got %r" % (name, value),
            )
        seen[name] = value == "true"
    return seen


def session_token(headers):
    token = headers.get("vmware-api-session-id")
    if headers.get("authorization"):
        raise Violation(
            "api_key_requests_carry_no_authorization_header",
            "an api_key_auth operation also sent an Authorization header",
        )
    if not token:
        raise Denied(401, "unauthenticated", "vmware-api-session-id header is missing")
    return token


def authorize(state, headers):
    token = session_token(headers)
    session = state.sessions.get(token)
    if session is None:
        raise Denied(
            401,
            "unauthenticated",
            "session token is not valid; it was never issued or has been deleted",
        )
    return token, session


# ---------------------------------------------------------------- operations


def op_session_create(state, request):
    if request["query_pairs"]:
        raise Violation("no_extra_query_parameters", "Cis.Session_create declares no query parameters")
    if request["headers"].get("vmware-api-session-id"):
        raise Violation(
            "basic_auth_requests_carry_no_session_header",
            "Cis.Session_create also sent a vmware-api-session-id header",
        )
    if request["body"]:
        raise Violation("no_extra_body_properties", "Cis.Session_create declares no request body")
    header = request["headers"].get("authorization") or ""
    if not header.lower().startswith("basic "):
        raise Denied(401, "unauthenticated", "Authorization: Basic credentials are required")
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
        user, _, password = decoded.partition(":")
    except Exception:
        raise Denied(401, "unauthenticated", "Authorization header is not decodable")
    if user != ACCOUNT or password != NEW_SECRET:
        raise Denied(401, "unauthenticated", "credentials rejected by the identity provider")
    with state.lock:
        state.sessions[NEW_SESSION_ID] = {"user": ACCOUNT, "origin": "rotated-secret"}
    return 201, NEW_SESSION_ID


def op_session_get(state, request):
    if request["query_pairs"]:
        raise Violation("no_extra_query_parameters", "Cis.Session_get declares no query parameters")
    if request["body"]:
        raise Violation("no_extra_body_properties", "Cis.Session_get declares no request body")
    token, session = authorize(state, request["headers"])
    user = session["user"]
    if state.scenario == "identity-mismatch" and token == NEW_SESSION_ID:
        user = "someone-else@vsphere.local"
    return 200, {
        "user": user,
        "created_time": CREATED_TIME,
        "last_accessed_time": ACCESSED_TIME,
    }


def op_session_delete(state, request):
    if request["query_pairs"]:
        raise Violation("no_extra_query_parameters", "Cis.Session_delete declares no query parameters")
    if request["body"]:
        raise Violation("no_extra_body_properties", "Cis.Session_delete declares no request body")
    token, _ = authorize(state, request["headers"])
    with state.lock:
        state.sessions.pop(token, None)
    return 204, None


def op_tasks_list(state, request):
    if request["query_pairs"]:
        raise Violation("no_extra_query_parameters", "Cis.Tasks_list carries unexpected query parameters")
    raw = request["body"]
    if not raw:
        raise Violation("filter_spec_selector", "Cis.Tasks_list needs a filter_spec")
    content_type = (request["headers"].get("content-type") or "").split(";")[0].strip()
    if content_type != "application/json":
        raise Violation(
            "request_content_type",
            "Content-Type must be application/json, got %r" % content_type,
        )
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise Violation("schema_type", "request body is not valid JSON: %s" % exc)
    if not isinstance(body, dict):
        raise Violation("schema_type", "request body must be a JSON object")
    extra = sorted(set(body) - {"filter_spec", "result_spec"})
    if extra:
        raise Violation(
            "no_extra_body_properties",
            "Cis.Tasks_list body carries unknown properties: %s" % ", ".join(extra),
        )
    reject_unset_placeholders("body", body)
    filter_spec = body.get("filter_spec")
    if filter_spec is None:
        raise Violation("filter_spec_selector", "Cis.Tasks_list needs a filter_spec")
    check_filter_spec(filter_spec)
    authorize(state, request["headers"])

    wanted_status = filter_spec.get("status")
    wanted_users = filter_spec.get("users")
    wanted_services = filter_spec.get("services")
    wanted_tasks = filter_spec.get("tasks")

    result = {}
    with state.lock:
        for task_id, task in state.tasks.items():
            if wanted_tasks and task_id not in wanted_tasks:
                continue
            if wanted_services and task["service"] not in wanted_services:
                continue
            if wanted_users and task["user"] not in wanted_users:
                continue
            if wanted_status and task["status"] not in wanted_status:
                continue
            result[task_id] = state.task_info(task_id)
    return 200, result


def op_tasks_get(state, request):
    task_id = request["path_params"]["task"]
    check_get_spec_query(request["query_pairs"])
    if request["body"]:
        raise Violation("no_extra_body_properties", "Cis.Tasks_get declares no request body")
    authorize(state, request["headers"])
    with state.lock:
        task = state.tasks.get(task_id)
        if task is None:
            raise Denied(404, "not_found", "no task with identifier %r" % task_id)
        task["polls"] += 1
        settles_after = task["settles_after"]
        if settles_after is not None and task["polls"] >= settles_after:
            task["status"] = task["terminal_status"]
        info = state.task_info(task_id)
    return 200, info


HANDLERS = {
    "Cis.Session_create": op_session_create,
    "Cis.Session_get": op_session_get,
    "Cis.Session_delete": op_session_delete,
    "Cis.Tasks_list": op_tasks_list,
    "Cis.Tasks_get": op_tasks_get,
}


# ---------------------------------------------------------------- routing


def build_routes(contract):
    base = contract["api"]["base_path"]
    routes = []
    for operation_id, operation in contract["operations"].items():
        if operation_id not in HANDLERS:
            raise SystemExit("contract names %s but the mock has no handler" % operation_id)
        segments = (base + operation["path"]).strip("/").split("/")
        routes.append(
            {
                "operationId": operation_id,
                "method": operation["method"],
                "segments": segments,
                "required_query": operation.get("query") or {},
                "handler": HANDLERS[operation_id],
            }
        )
    return routes


def match_route(routes, method, path, query_pairs):
    query = dict(query_pairs)
    segments = path.strip("/").split("/")
    for route in routes:
        if route["method"] != method or len(route["segments"]) != len(segments):
            continue
        params = {}
        for pattern, actual in zip(route["segments"], segments):
            if pattern.startswith("{") and pattern.endswith("}"):
                params[pattern[1:-1]] = actual
            elif pattern != actual:
                break
        else:
            if all(query.get(k) == v for k, v in route["required_query"].items()):
                return route, params
    return None, None


def route_query_pairs(route, query_pairs):
    """Query parameters that address the operation are not part of its parameter set."""
    remaining = list(query_pairs)
    for fixed in route["required_query"].items():
        remaining.remove(fixed)
    return remaining


# ---------------------------------------------------------------- http plumbing


def make_handler(state, routes, log_path):
    log_lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _write(self, status, payload):
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _record(self, entry):
            with log_lock:
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")

        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length).decode("utf-8") if length else ""
            headers = {k.lower(): v for k, v in self.headers.items()}
            split = urlsplit(self.path)
            query_pairs = parse_qsl(split.query, keep_blank_values=True)

            with state.lock:
                state.seq += 1
                seq = state.seq

            entry = {
                "seq": seq,
                "method": self.command,
                "target": self.path,
                "path": split.path,
                "query": split.query,
                "headers": {k: v for k, v in headers.items() if k in LOGGED_HEADERS},
                "body": raw_body,
                "operationId": None,
                "status": None,
                "violation": None,
            }

            route, params = match_route(routes, self.command, split.path, query_pairs)
            if route is None:
                entry["status"] = 404
                entry["violation"] = "not_in_contract"
                self._record(entry)
                return self._write(404, {"error": "not_in_contract", "target": self.path})

            entry["operationId"] = route["operationId"]
            request = {
                "headers": headers,
                "body": raw_body,
                "query_pairs": route_query_pairs(route, query_pairs),
                "path_params": params,
            }
            try:
                status, payload = route["handler"](state, request)
            except Violation as exc:
                entry["status"] = 400
                entry["violation"] = exc.rule
                entry["violation_detail"] = exc.detail
                self._record(entry)
                return self._write(
                    400,
                    {
                        "error": "contract_violation",
                        "rule": exc.rule,
                        "detail": exc.detail,
                        "operationId": route["operationId"],
                    },
                )
            except Denied as exc:
                entry["status"] = exc.status
                self._record(entry)
                return self._write(
                    exc.status,
                    {"error": exc.code, "messages": [{"default_message": exc.detail}]},
                )
            entry["status"] = status
            self._record(entry)
            return self._write(status, payload)

        do_GET = _dispatch
        do_POST = _dispatch
        do_PUT = _dispatch
        do_PATCH = _dispatch
        do_DELETE = _dispatch

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--scenario", default="nominal", choices=sorted(SCENARIOS))
    args = parser.parse_args(argv)

    with open(args.contract, encoding="utf-8") as handle:
        contract = json.load(handle)
    routes = build_routes(contract)
    state = State(args.scenario)

    open(args.log, "w", encoding="utf-8").close()

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(state, routes, args.log)
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    port = server.server_address[1]
    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(str(port))
    os.replace(tmp, args.port_file)
    sys.stderr.write("mock listening on 127.0.0.1:%d scenario=%s\n" % (port, args.scenario))
    sys.stderr.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
