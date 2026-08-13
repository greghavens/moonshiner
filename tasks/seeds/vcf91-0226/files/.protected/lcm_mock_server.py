#!/usr/bin/env python3
"""Loopback mock of the VCF 9.1 SDDC LCM service, pinned to docs/contract.json.

The routing table is built entirely from the operations the contract names: this
process serves those paths and methods and nothing else. It refuses to start if
the contract omits an operation it needs or names one it cannot implement.

It binds 127.0.0.1 only, contacts no network peer, and appends every request it
receives to a JSON-lines log so the exact wire shape can be inspected.

    python3 tools/lcm_mock_server.py --contract docs/contract.json --log req.jsonl

Prints a single line ``READY <base-url> <token>`` on stdout once listening.

PROTECTED: do not modify.
"""

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_TOKEN = "eyJhbGciOiJIUzI1NiJ9.mock-sddc-lcm-token"

SUPPORTED_OPERATION_IDS = frozenset(
    ("generateComponentSupportBundle", "getTask", "getComponentSupportBundles")
)

# --- fixture inventory ------------------------------------------------------
# Each component drives one deterministic task-status sequence. The Nth read of
# a task returns sequence[N-1], clamping at the last element.

COMPONENTS = {
    # succeeds on the third read
    "6f9a2c14-3b7d-4e58-9a10-2d5e8c7b4f31": {
        "task_id": "b1d4f8a2-5c6e-4712-8f3a-9e0d1c2b3a45",
        "bundle_id": "sb-2f4c9e17",
        "bundle_name": "support-bundle-vcf-ops-2f4c9e17.tgz",
        "sequence": ["PENDING", "RUNNING", "SUCCEEDED"],
    },
    # fails on the third read
    "0c3e7d91-8a4b-42f6-b5c8-1e9d6a0f2b73": {
        "task_id": "7e5c3a90-1f8d-4b26-9c47-0a3b5d8e6f12",
        "bundle_id": "sb-8d1a6b04",
        "bundle_name": "support-bundle-vcf-ops-8d1a6b04.tgz",
        "sequence": ["PENDING", "RUNNING", "FAILED"],
    },
    # never leaves RUNNING
    "4a8b1e60-9d27-4c3f-a6b5-7e2f0c9d8a14": {
        "task_id": "c2f7b481-6a30-4d95-8e1b-5f4c9a2d7e03",
        "bundle_id": "sb-c07e5f39",
        "bundle_name": "support-bundle-vcf-ops-c07e5f39.tgz",
        "sequence": ["PENDING", "RUNNING"],
    },
}

FIXED_TIME = "2026-05-13T08:19:58.000Z"


def _message(msg_id, default):
    return {
        "id": msg_id,
        "defaultMessage": default,
        "localizedMessage": default,
        "args": {},
    }


def _error(code, detail):
    return {
        "code": code,
        "message": _message("com.broadcom.lcm.error." + code.lower(), detail),
        "resolution": _message(
            "com.broadcom.lcm.error." + code.lower() + ".resolution",
            "Correct the request and retry.",
        ),
        "referenceId": "ref-" + code.lower(),
        "timestamp": FIXED_TIME,
        "detail": detail,
    }


class ContractError(Exception):
    """The supplied contract cannot drive this mock."""


class Route:
    def __init__(self, operation_id, method, path_template, spec):
        self.operation_id = operation_id
        self.method = method
        self.path_template = path_template
        self.spec = spec
        pattern = re.sub(
            r"\{(\w+)\}", lambda m: "(?P<%s>[^/]+)" % m.group(1), path_template
        )
        self.regex = re.compile("^" + pattern + "$")


def build_routes(contract):
    """Derive the served routes from the contract; serve nothing else."""
    if not isinstance(contract, dict):
        raise ContractError("contract must be a JSON object")
    operations = contract.get("operations")
    if not isinstance(operations, dict) or not operations:
        raise ContractError("contract has no 'operations' object")

    named = set(operations)
    unknown = named - SUPPORTED_OPERATION_IDS
    if unknown:
        raise ContractError(
            "contract names operations this mock cannot serve: %s"
            % ", ".join(sorted(unknown))
        )
    missing = SUPPORTED_OPERATION_IDS - named
    if missing:
        raise ContractError(
            "contract is missing required operations: %s" % ", ".join(sorted(missing))
        )

    routes = []
    for operation_id in sorted(named):
        spec = operations[operation_id]
        if not isinstance(spec, dict):
            raise ContractError("operation %s is not an object" % operation_id)
        method = spec.get("method")
        path = spec.get("path")
        if not isinstance(method, str) or not method.isupper():
            raise ContractError("operation %s has no uppercase method" % operation_id)
        if not isinstance(path, str) or not path.startswith("/"):
            raise ContractError("operation %s has no absolute path" % operation_id)
        if not isinstance(spec.get("success_status"), int):
            raise ContractError("operation %s has no success_status" % operation_id)
        routes.append(Route(operation_id, method, path, spec))
    return routes


class State:
    """Mutable service state, shared across requests."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tasks = {}
        self.bundles = {}
        self.seq = 0

    def start_task(self, component_id, correlation_id):
        fixture = COMPONENTS[component_id]
        task_id = fixture["task_id"]
        self.tasks[task_id] = {
            "component_id": component_id,
            "reads": 0,
            "correlation_id": correlation_id,
        }
        self.bundles.setdefault(component_id, [])
        return self.render_task(task_id, "PENDING")

    def read_task(self, task_id):
        entry = self.tasks[task_id]
        fixture = COMPONENTS[entry["component_id"]]
        entry["reads"] += 1
        index = min(entry["reads"] - 1, len(fixture["sequence"]) - 1)
        status = fixture["sequence"][index]
        if status == "SUCCEEDED":
            listed = self.bundles.setdefault(entry["component_id"], [])
            if not listed:
                # The unrelated first entry ensures clients select the bundle
                # whose id matches Task.resourceId instead of returning index 0.
                listed.append(
                    {
                        "id": "sb-unrelated-fixture",
                        "createdTimestamp": FIXED_TIME,
                        "size": 128,
                        "name": "unrelated-support-bundle.tgz",
                        "url": "https://vmsp.broadcom.com/bundles/sb-unrelated-fixture",
                    }
                )
                listed.append(
                    {
                        "id": fixture["bundle_id"],
                        "createdTimestamp": FIXED_TIME,
                        "size": 48234501,
                        "name": fixture["bundle_name"],
                        "url": "https://vmsp.broadcom.com/bundles/"
                        + fixture["bundle_id"],
                    }
                )
        return self.render_task(task_id, status)

    def render_task(self, task_id, status):
        entry = self.tasks[task_id]
        component_id = entry["component_id"]
        fixture = COMPONENTS[component_id]
        task = {
            "id": task_id,
            "name": "Generate support bundle",
            "description": _message(
                "com.broadcom.lcm.ops.supportbundle.generate.started",
                "Support bundle generation for component %s" % component_id,
            ),
            "status": status,
            "type": "SUPPORT_BUNDLE_GENERATION",
            "createdBy": "admin",
            "resourceId": fixture["bundle_id"],
            "resourceType": "SUPPORT_BUNDLE",
            "createTime": FIXED_TIME,
            "startTime": FIXED_TIME,
            "updateTime": FIXED_TIME,
            "retriable": status == "FAILED",
            "cancellable": status not in ("SUCCEEDED", "FAILED", "CANCELED"),
            "taskSummary": {"totalSteps": 3, "totalSubTasks": 0},
        }
        if entry["correlation_id"] is not None:
            task["correlationId"] = entry["correlation_id"]
        if status in ("SUCCEEDED", "FAILED", "CANCELED"):
            task["endTime"] = FIXED_TIME
        if status == "FAILED":
            task["messages"] = [
                _message(
                    "com.broadcom.lcm.ops.supportbundle.generate.failed",
                    "Log collection timed out on one or more nodes.",
                )
            ]
        return task


def make_handler(routes, state, token, log_path):
    by_path = {}
    for route in routes:
        by_path.setdefault(route.path_template, []).append(route)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        server_version = "vcf-sddc-lcm-mock/1.0"

        def log_message(self, fmt, *args):  # silence stderr chatter
            pass

        # -- plumbing ----------------------------------------------------
        def _record(self, body_raw, status):
            with state.lock:
                state.seq += 1
                seq = state.seq
                entry = {
                    "seq": seq,
                    "method": self.command,
                    "target": self.path,
                    "path": self.path.split("?", 1)[0],
                    "query": (
                        self.path.split("?", 1)[1] if "?" in self.path else ""
                    ),
                    "headers": [[k, v] for k, v in self.headers.items()],
                    "headers_lower": {
                        k.lower(): v for k, v in self.headers.items()
                    },
                    "body_raw": body_raw,
                    "status": status,
                }
                try:
                    entry["body_json"] = (
                        json.loads(body_raw) if body_raw else None
                    )
                except ValueError:
                    entry["body_json"] = None
                    entry["body_json_error"] = True
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")

        def _respond(self, status, payload, body_raw):
            encoded = json.dumps(payload).encode("utf-8")
            self._record(body_raw, status)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_body(self):
            length = self.headers.get("Content-Length")
            if not length:
                return ""
            try:
                count = int(length)
            except ValueError:
                return ""
            if count <= 0:
                return ""
            return self.rfile.read(count).decode("utf-8", "replace")

        # -- request body validation against the contract ----------------
        def _validate_body(self, route, body_raw):
            declared = route.spec.get("request_body")
            if not declared:
                if body_raw:
                    return _error("BAD_REQUEST", "operation declares no request body")
                return None
            if body_raw == "":
                return _error("BAD_REQUEST", "request body is required")
            try:
                parsed = json.loads(body_raw)
            except ValueError:
                return _error("BAD_REQUEST", "request body is not valid JSON")
            if not isinstance(parsed, dict):
                return _error("BAD_REQUEST", "request body must be a JSON object")

            optional = {
                item.get("name"): item
                for item in declared.get("optional_properties") or []
                if isinstance(item, dict)
            }
            required = list(declared.get("required_properties") or [])
            allowed = set(optional) | set(required)
            for name in sorted(set(parsed) - allowed):
                return _error(
                    "BAD_REQUEST", "unknown property in request body: %s" % name
                )
            for name in required:
                if name not in parsed:
                    return _error(
                        "BAD_REQUEST", "missing required property: %s" % name
                    )
            for name, value in sorted(parsed.items()):
                if value is None:
                    return _error(
                        "BAD_REQUEST",
                        "property %s is null; unset optional properties must be "
                        "omitted, not sent empty" % name,
                    )
                declared_type = (optional.get(name) or {}).get("type")
                if declared_type == "integer" and not isinstance(value, int):
                    return _error(
                        "BAD_REQUEST", "property %s must be an integer" % name
                    )
                if declared_type == "string" and not isinstance(value, str):
                    return _error("BAD_REQUEST", "property %s must be a string" % name)
            return None

        # -- dispatch -----------------------------------------------------
        def _handle(self):
            body_raw = self._read_body()
            path = self.path.split("?", 1)[0]

            matched_path = None
            for route in routes:
                match = route.regex.match(path)
                if match:
                    matched_path = route.path_template
                    if route.method == self.command:
                        return self._dispatch(route, match, body_raw)
            if matched_path is not None:
                allowed = ", ".join(
                    sorted(r.method for r in by_path[matched_path])
                )
                self.send_response(405)
                self.send_header("Allow", allowed)
                self.send_header("Content-Length", "0")
                self.end_headers()
                self._record(body_raw, 405)
                return None
            return self._respond(
                404,
                _error("NOT_FOUND", "no contracted operation serves %s" % path),
                body_raw,
            )

        def _dispatch(self, route, match, body_raw):
            auth = self.headers.get("Authorization")
            if auth != "Bearer " + token:
                return self._respond(
                    401,
                    _error("UNAUTHORIZED", "missing or invalid bearer token"),
                    body_raw,
                )

            problem = self._validate_body(route, body_raw)
            if problem is not None:
                return self._respond(400, problem, body_raw)

            params = match.groupdict()
            handler = getattr(self, "_op_" + route.operation_id)
            with state.lock:
                payload, status = handler(route, params)
            return self._respond(status, payload, body_raw)

        # -- operations ---------------------------------------------------
        def _op_generateComponentSupportBundle(self, route, params):
            component_id = params.get("componentId")
            if component_id not in COMPONENTS:
                return (
                    _error("NOT_FOUND", "unknown component %s" % component_id),
                    404,
                )
            correlation_id = self.headers.get("X-Correlation-Id")
            task = state.start_task(component_id, correlation_id)
            return task, route.spec["success_status"]

        def _op_getTask(self, route, params):
            task_id = params.get("taskId")
            if task_id not in state.tasks:
                return _error("NOT_FOUND", "unknown task %s" % task_id), 404
            return state.read_task(task_id), route.spec["success_status"]

        def _op_getComponentSupportBundles(self, route, params):
            component_id = params.get("componentId")
            if component_id not in COMPONENTS:
                return (
                    _error("NOT_FOUND", "unknown component %s" % component_id),
                    404,
                )
            return (
                list(state.bundles.get(component_id, [])),
                route.spec["success_status"],
            )

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle
        do_HEAD = _handle

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        parser.error("this mock binds loopback only")

    try:
        with open(args.contract, encoding="utf-8") as handle:
            contract = json.load(handle)
    except FileNotFoundError:
        sys.stderr.write("contract not found: %s\n" % args.contract)
        return 2
    except ValueError as exc:
        sys.stderr.write("contract is not valid JSON: %s\n" % exc)
        return 2

    try:
        routes = build_routes(contract)
    except ContractError as exc:
        sys.stderr.write("refusing to start: %s\n" % exc)
        return 3

    open(args.log, "w", encoding="utf-8").close()

    state = State()
    handler = make_handler(routes, state, args.token, args.log)
    httpd = HTTPServer((args.host, args.port), handler)
    host, port = httpd.socket.getsockname()[:2]
    sys.stdout.write("READY http://%s:%d %s\n" % (host, port, args.token))
    sys.stdout.flush()
    try:
        httpd.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
