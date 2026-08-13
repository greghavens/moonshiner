#!/usr/bin/env python3
"""Loopback mock of the VCF 9.1 SDDC LCM service, pinned to docs/contract.json.

The routing table and the request-body validation are built entirely from the
operations and schemas the contract names: this process serves those paths and
methods and nothing else. It refuses to start if the contract omits an operation
it needs, names one it cannot implement, or fails to describe a schema it must
validate against.

It models an access token that expires part way through a run: after
``--expire-after`` successful authenticated requests it rotates its token, and
every later request still presenting the old one is answered ``401`` until the
rotated token is used.

It binds 127.0.0.1 only, contacts no network peer, and appends every request it
receives to a JSON-lines log so the exact wire shape can be inspected.

    python3 .protected/lcm_mock_server.py --contract docs/contract.json \\
        --log req.jsonl --expire-after 3

Prints a single line ``READY <base-url> <initial-token> <rotated-token>`` on
stdout once listening.

PROTECTED: do not modify.
"""

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_TOKEN = "eyJhbGciOiJIUzI1NiJ9.mock-sddc-lcm-token.initial"
ROTATED_TOKEN = "eyJhbGciOiJIUzI1NiJ9.mock-sddc-lcm-token.rotated"

SUPPORTED_OPERATION_IDS = frozenset(
    (
        "getHealth",
        "resolveDepotComponents",
        "performComponentAction",
        "getTask",
        "retryTask",
    )
)

#: Schemas the mock must be able to walk to validate the contracted bodies.
REQUIRED_SCHEMA_NAMES = frozenset(
    (
        "DepotComponentsSpec",
        "FleetDepotSpec",
        "ComponentVersionSpec",
        "ComponentUpgradeSpec",
        "ComponentDesiredSpec",
        "SoftwareSpec",
        "DepotSpec",
        "LcmPlatformSpec",
    )
)

JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}

# --- fixture inventory ------------------------------------------------------
# Each component drives one deterministic task-status sequence. The Nth read of
# a task returns sequence[N-1], clamping at the last element. A retried task
# switches to its retry sequence and its read counter starts over.

COMPONENTS = {
    # succeeds on the third read
    "d3f1a6c2-7b48-4e91-a05d-6c2e8f7b1a34": {
        "name": "vcf-ops",
        "task_id": "9a4c7e21-5d38-4f60-b1a7-2e6c9d0b3f85",
        "sequence": ["RUNNING", "RUNNING", "SUCCEEDED"],
        "retry_sequence": ["RUNNING", "SUCCEEDED"],
    },
    # fails on the second read, then succeeds once retried
    "5e2b9d84-1c6f-4a37-9e80-3b7d5a1c6e29": {
        "name": "sddc-manager",
        "task_id": "1f8d5b30-4a92-4c76-8b53-7e0a2c4d9f61",
        "sequence": ["RUNNING", "FAILED"],
        "retry_sequence": ["RUNNING", "SUCCEEDED"],
    },
    # never leaves RUNNING
    "7c0a4e59-2d81-4b63-8f47-9a1e5c3d0b72": {
        "name": "nsx",
        "task_id": "4b6e2a17-8f50-4d29-a3c6-1d8b7e5f0a94",
        "sequence": ["RUNNING"],
        "retry_sequence": ["RUNNING"],
    },
    # reaches the other non-successful terminal state
    "8d1b5f60-3e92-4c74-9a58-0b2f6d4e1c83": {
        "name": "vcenter",
        "task_id": "6c7f3b28-9a41-4e50-b2d6-8f1a5c3e7b90",
        "sequence": ["SCHEDULED", "CANCELED"],
        "retry_sequence": ["RUNNING"],
    },
}

#: What the Fleet depot resolves a component to when no version is asked for.
DEPOT_LATEST = {
    "vcf-ops": "9.1.0.0000.24178562",
    "sddc-manager": "9.1.0.0000.24178901",
    "nsx": "9.1.0.0000.24177344",
}

DEPOT_BASE_URL = "https://depot.broadcom.com/PROD/vcf/9.1.0"

FIXED_TIME = "2026-05-13T08:19:58.000Z"

HEALTH_PAYLOAD = {
    "status": "HEALTHY",
    "components": [
        {"name": "sddc-lcm", "status": "UP"},
        {"name": "task-scheduler", "status": "UP"},
    ],
}


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
    def __init__(self, operation_id, spec):
        self.operation_id = operation_id
        self.spec = spec
        self.method = spec["method"]
        self.path_template = spec["path"]
        self.authenticated = spec["authenticated"]
        self.query_parameters = spec.get("query_parameters") or []
        pattern = re.sub(
            r"\{(\w+)\}", lambda m: "(?P<%s>[^/]+)" % m.group(1), self.path_template
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

    schemas = contract.get("schemas")
    if not isinstance(schemas, dict):
        raise ContractError("contract has no 'schemas' object")
    missing_schemas = REQUIRED_SCHEMA_NAMES - set(schemas)
    if missing_schemas:
        raise ContractError(
            "contract is missing schemas needed to validate request bodies: %s"
            % ", ".join(sorted(missing_schemas))
        )
    for name in sorted(REQUIRED_SCHEMA_NAMES):
        entry = schemas[name]
        if not isinstance(entry, dict):
            raise ContractError("schema %s is not an object" % name)
        if not isinstance(entry.get("properties"), list):
            raise ContractError("schema %s has no 'properties' list" % name)
        if not isinstance(entry.get("required"), list):
            raise ContractError("schema %s has no 'required' list" % name)

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
        if "?" in path:
            raise ContractError(
                "operation %s: 'path' must not carry a query string (that "
                "belongs in query_parameters)" % operation_id
            )
        if not isinstance(spec.get("success_status"), int):
            raise ContractError("operation %s has no success_status" % operation_id)
        if not isinstance(spec.get("authenticated"), bool):
            raise ContractError(
                "operation %s has no boolean 'authenticated' flag" % operation_id
            )
        for item in spec.get("query_parameters") or []:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise ContractError(
                    "operation %s has a malformed query parameter" % operation_id
                )
        routes.append(Route(operation_id, spec))
    return routes


class BodyValidator:
    """Validates a request body against the contract's schema descriptions."""

    def __init__(self, schemas):
        self.schemas = schemas

    def validate(self, schema_name, value, where):
        entry = self.schemas.get(schema_name)
        if not isinstance(entry, dict):
            return "contract does not describe schema %s" % schema_name
        if not isinstance(value, dict):
            return "%s must be a JSON object" % where

        properties = {}
        for item in entry.get("properties") or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                properties[item["name"]] = item

        for name in sorted(set(value) - set(properties)):
            return "%s.%s is not a property of %s" % (where, name, schema_name)

        for name in entry.get("required") or []:
            if name not in value:
                return "%s is missing required property %s" % (where, name)

        for name in sorted(value):
            problem = self._check_property(properties[name], value[name],
                                           "%s.%s" % (where, name))
            if problem is not None:
                return problem
        return None

    def _check_property(self, prop, value, where):
        if value is None:
            return (
                "%s is null; an unset optional property must be omitted, not "
                "sent empty" % where
            )
        declared = prop.get("type")
        expected = JSON_TYPES.get(declared)
        if expected is not None:
            if declared == "integer" and isinstance(value, bool):
                return "%s must be an integer" % where
            if declared != "boolean" and isinstance(value, bool):
                return "%s must be a %s" % (where, declared)
            if not isinstance(value, expected):
                return "%s must be a %s" % (where, declared)
        if declared == "object":
            nested = prop.get("schema")
            if nested:
                return self.validate(nested, value, where)
            return None
        if declared == "array":
            items = prop.get("items") or {}
            item_schema = items.get("schema")
            item_type = items.get("type")
            for index, element in enumerate(value):
                spot = "%s[%d]" % (where, index)
                if element is None:
                    return "%s is null" % spot
                if item_schema:
                    problem = self.validate(item_schema, element, spot)
                    if problem is not None:
                        return problem
                elif item_type in JSON_TYPES and not isinstance(
                    element, JSON_TYPES[item_type]
                ):
                    return "%s must be a %s" % (spot, item_type)
        return None


class State:
    """Mutable service state, shared across requests."""

    def __init__(self, token, rotated_token, expire_after, authorization_scheme):
        self.lock = threading.Lock()
        self.tasks = {}
        self.seq = 0
        self.active_token = token
        self.rotated_token = rotated_token
        self.expire_after = expire_after
        self.authorization_scheme = authorization_scheme
        self.authenticated_ok = 0
        self.rotated = False

    # -- token lifecycle -------------------------------------------------
    def note_authenticated_success(self):
        self.authenticated_ok += 1
        if (
            not self.rotated
            and self.expire_after is not None
            and self.authenticated_ok >= self.expire_after
        ):
            self.active_token = self.rotated_token
            self.rotated = True

    # -- tasks -----------------------------------------------------------
    def start_task(self, component_id, correlation_id, target_version):
        fixture = COMPONENTS[component_id]
        task_id = fixture["task_id"]
        self.tasks[task_id] = {
            "component_id": component_id,
            "reads": 0,
            "retried": False,
            "correlation_id": correlation_id,
            "target_version": target_version,
        }
        return self.render_task(task_id, "PENDING")

    def read_task(self, task_id):
        entry = self.tasks[task_id]
        fixture = COMPONENTS[entry["component_id"]]
        key = "retry_sequence" if entry["retried"] else "sequence"
        sequence = fixture[key]
        entry["reads"] += 1
        index = min(entry["reads"] - 1, len(sequence) - 1)
        return self.render_task(task_id, sequence[index])

    def retry_task(self, task_id):
        entry = self.tasks[task_id]
        entry["retried"] = True
        entry["reads"] = 0
        return self.render_task(task_id, "PENDING")

    def render_task(self, task_id, status):
        entry = self.tasks[task_id]
        component_id = entry["component_id"]
        fixture = COMPONENTS[component_id]
        task = {
            "id": task_id,
            "name": "upgrade_%s_%s" % (fixture["name"], entry["target_version"]),
            "description": _message(
                "com.broadcom.lcm.ops.component.upgrade.started",
                "Upgrade of component %s to %s"
                % (fixture["name"], entry["target_version"]),
            ),
            "status": status,
            "type": "apply",
            "createdBy": "admin",
            "resourceId": component_id,
            "resourceType": "COMPONENT",
            "createTime": FIXED_TIME,
            "startTime": FIXED_TIME,
            "updateTime": FIXED_TIME,
            "retriable": status == "FAILED",
            "cancellable": status not in ("SUCCEEDED", "FAILED", "CANCELED"),
            "taskSummary": {"totalSteps": 6, "totalSubTasks": 0},
        }
        if entry["correlation_id"] is not None:
            task["correlationId"] = entry["correlation_id"]
        if status in ("SUCCEEDED", "FAILED", "CANCELED"):
            task["endTime"] = FIXED_TIME
        if status == "FAILED":
            task["messages"] = [
                {
                    "level": "ERROR",
                    "message": _message(
                        "com.broadcom.lcm.ops.component.upgrade.failed",
                        "Stage package-deploy did not converge on all nodes.",
                    ),
                    "timestamp": FIXED_TIME,
                }
            ]
        return task


def make_handler(routes, validator, state, log_path):
    by_path = {}
    for route in routes:
        by_path.setdefault(route.path_template, []).append(route)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        server_version = "vcf-sddc-lcm-mock/1.1"

        def log_message(self, fmt, *args):  # silence stderr chatter
            pass

        # -- plumbing ----------------------------------------------------
        def _record(self, body_raw, status, operation_id):
            state.seq += 1
            entry = {
                "seq": state.seq,
                "operation_id": operation_id,
                "method": self.command,
                "target": self.path,
                "path": self.path.split("?", 1)[0],
                "query": self.path.split("?", 1)[1] if "?" in self.path else "",
                "headers": [[k, v] for k, v in self.headers.items()],
                "headers_lower": {k.lower(): v for k, v in self.headers.items()},
                "body_raw": body_raw,
                "status": status,
            }
            try:
                entry["body_json"] = json.loads(body_raw) if body_raw else None
            except ValueError:
                entry["body_json"] = None
                entry["body_json_error"] = True
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

        def _respond(self, status, payload, body_raw, operation_id=None):
            encoded = json.dumps(payload).encode("utf-8")
            self._record(body_raw, status, operation_id)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_body(self):
            """Consume the request body once, exactly as the sender framed it."""
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

        def _query(self):
            if "?" not in self.path:
                return {}
            raw = self.path.split("?", 1)[1]
            found = {}
            for chunk in raw.split("&"):
                if not chunk:
                    continue
                name, _, value = chunk.partition("=")
                found.setdefault(name, []).append(value)
            return found

        # -- request validation against the contract ---------------------
        def _check_query(self, route):
            found = self._query()
            declared = {item["name"]: item for item in route.query_parameters}
            for name in sorted(set(found) - set(declared)):
                return _error(
                    "BAD_REQUEST",
                    "%s declares no query parameter %s" % (route.operation_id, name),
                )
            for name, item in sorted(declared.items()):
                values = found.get(name)
                if not values:
                    if item.get("required"):
                        return _error(
                            "BAD_REQUEST", "missing required query parameter %s" % name
                        )
                    continue
                if len(values) > 1:
                    return _error(
                        "BAD_REQUEST", "query parameter %s repeated" % name
                    )
                value = values[0]
                if value == "":
                    return _error(
                        "BAD_REQUEST",
                        "query parameter %s was sent empty; an unset optional "
                        "parameter must be omitted" % name,
                    )
                fixed = item.get("fixed_value")
                if fixed is not None and value != fixed:
                    return _error(
                        "BAD_REQUEST",
                        "query parameter %s must be %r for %s"
                        % (name, fixed, route.operation_id),
                    )
                allowed = item.get("enum")
                if allowed and value not in allowed:
                    return _error(
                        "BAD_REQUEST",
                        "query parameter %s=%r is not one of %s"
                        % (name, value, ", ".join(allowed)),
                    )
            return None

        def _check_body(self, route, body_raw):
            declared = route.spec.get("request_body")
            if not declared:
                if body_raw:
                    return _error(
                        "BAD_REQUEST",
                        "%s declares no request body" % route.operation_id,
                    )
                return None
            if body_raw == "":
                return _error("BAD_REQUEST", "request body is required")
            content_type = self.headers.get("Content-Type", "")
            if content_type.split(";")[0].strip() != declared.get("content_type"):
                return _error(
                    "BAD_REQUEST",
                    "Content-Type must be %s" % declared.get("content_type"),
                )
            try:
                parsed = json.loads(body_raw)
            except ValueError:
                return _error("BAD_REQUEST", "request body is not valid JSON")
            problem = validator.validate(declared.get("schema"), parsed, "body")
            if problem is not None:
                return _error("BAD_REQUEST", problem)
            return None

        # -- dispatch -----------------------------------------------------
        def _handle(self):
            body_raw = self.body_raw
            path = self.path.split("?", 1)[0]
            query = self._query()

            candidates = []
            matched_path = None
            for route in routes:
                match = route.regex.match(path)
                if not match:
                    continue
                matched_path = route.path_template
                if route.method == self.command:
                    candidates.append((route, match))

            if not candidates:
                if matched_path is not None:
                    allowed = ", ".join(sorted(r.method for r in by_path[matched_path]))
                    self._record(body_raw, 405, None)
                    self.send_response(405)
                    self.send_header("Allow", allowed)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                return self._respond(
                    404,
                    _error("NOT_FOUND", "no contracted operation serves %s" % path),
                    body_raw,
                )

            # More than one operation can share a method and path template. The
            # one whose specification-fixed query values are all present wins;
            # otherwise the one that fixes no query values does.
            pinned = []
            unpinned = []
            for route, match in candidates:
                fixed = {
                    item["name"]: item["fixed_value"]
                    for item in route.query_parameters
                    if item.get("fixed_value") is not None
                }
                if not fixed:
                    unpinned.append((route, match))
                elif all(query.get(k, [None])[0] == v for k, v in fixed.items()):
                    pinned.append((route, match))
            chosen = (pinned or unpinned or candidates)[0]
            return self._dispatch(chosen[0], chosen[1], body_raw)

        def _dispatch(self, route, match, body_raw):
            with state.lock:
                if route.authenticated:
                    auth = self.headers.get("Authorization")
                    if auth != state.authorization_scheme + " " + state.active_token:
                        return self._respond(
                            401,
                            _error(
                                "UNAUTHORIZED",
                                "the access token is missing, malformed or expired",
                            ),
                            body_raw,
                            route.operation_id,
                        )

                problem = self._check_query(route)
                if problem is None:
                    problem = self._check_body(route, body_raw)
                if problem is not None:
                    return self._respond(400, problem, body_raw, route.operation_id)

                if route.authenticated:
                    state.note_authenticated_success()

                handler = getattr(self, "_op_" + route.operation_id)
                payload, status = handler(route, match.groupdict())
            return self._respond(status, payload, body_raw, route.operation_id)

        # -- operations ---------------------------------------------------
        def _op_getHealth(self, route, params):
            return dict(HEALTH_PAYLOAD), route.spec["success_status"]

        def _op_resolveDepotComponents(self, route, params):
            body = json.loads(self.body_raw)
            resolved = []
            for item in body.get("componentVersions") or []:
                component = item.get("component")
                if component not in DEPOT_LATEST:
                    return (
                        _error(
                            "NOT_FOUND", "component %s is not in the depot" % component
                        ),
                        404,
                    )
                version = item.get("version") or DEPOT_LATEST[component]
                resolved.append(
                    {
                        "component": component,
                        "version": version,
                        "binaryUrl": "%s/%s/%s/manifest.json"
                        % (DEPOT_BASE_URL, component, version),
                    }
                )
            return {"componentVersions": resolved}, route.spec["success_status"]

        def _op_performComponentAction(self, route, params):
            component_id = next(iter(params.values()), None)
            if component_id not in COMPONENTS:
                return _error("NOT_FOUND", "unknown component %s" % component_id), 404
            query_name = route.query_parameters[0]["name"]
            action = self._query().get(query_name, [""])[0]
            if action != "apply":
                return (
                    _error(
                        "BAD_REQUEST",
                        "this fixture only implements the apply action, not %r"
                        % action,
                    ),
                    400,
                )
            body = json.loads(self.body_raw)
            target_version = (
                body.get("componentSpec", {}).get("software", {}).get("version")
            )
            correlation_id = self.headers.get("X-Correlation-Id")
            task = state.start_task(component_id, correlation_id, target_version)
            return task, route.spec["success_status"]

        def _op_getTask(self, route, params):
            task_id = next(iter(params.values()), None)
            if task_id not in state.tasks:
                return _error("NOT_FOUND", "unknown task %s" % task_id), 404
            return state.read_task(task_id), route.spec["success_status"]

        def _op_retryTask(self, route, params):
            task_id = next(iter(params.values()), None)
            if task_id not in state.tasks:
                return _error("NOT_FOUND", "unknown task %s" % task_id), 404
            entry = state.tasks[task_id]
            fixture = COMPONENTS[entry["component_id"]]
            sequence = fixture["retry_sequence" if entry["retried"] else "sequence"]
            reached = sequence[min(max(entry["reads"] - 1, 0), len(sequence) - 1)]
            if reached != "FAILED":
                return (
                    _error(
                        "BAD_REQUEST",
                        "task %s is %s and cannot be retried" % (task_id, reached),
                    ),
                    400,
                )
            return state.retry_task(task_id), route.spec["success_status"]

        # -- entry points --------------------------------------------------
        def _entry(self):
            self.body_raw = self._read_body()
            return self._handle()

        do_GET = _entry
        do_POST = _entry
        do_PUT = _entry
        do_PATCH = _entry
        do_DELETE = _entry
        do_HEAD = _entry

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Loopback SDDC LCM mock")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--rotated-token", default=ROTATED_TOKEN)
    parser.add_argument(
        "--expire-after",
        type=int,
        default=0,
        help="rotate the token after this many successful authenticated "
        "requests; 0 means the token never expires",
    )
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

    authorization_scheme = (contract.get("security") or {}).get("scheme")
    if not isinstance(authorization_scheme, str) or not authorization_scheme:
        sys.stderr.write("contract has no usable HTTP authorization scheme\n")
        return 3

    state = State(
        args.token,
        args.rotated_token,
        args.expire_after if args.expire_after > 0 else None,
        authorization_scheme,
    )
    validator = BodyValidator(contract["schemas"])
    handler = make_handler(routes, validator, state, args.log)
    httpd = HTTPServer((args.host, args.port), handler)
    host, port = httpd.socket.getsockname()[:2]
    sys.stdout.write(
        "READY http://%s:%d %s %s\n" % (host, port, args.token, args.rotated_token)
    )
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
