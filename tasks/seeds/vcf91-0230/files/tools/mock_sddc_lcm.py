#!/usr/bin/env python3
"""Loopback mock of the VCF 9.1 SDDC LCM service.

The mock is pinned to ``docs/contract.json``: its route table is built entirely
from the ``operations`` block of that file.  It serves only the operations the
contract names, and an operation the contract does not describe is answered with
404 and an ``ErrorResponse`` body.  Required query parameters and required
top-level request-body fields are enforced from the contract as well, so a
contract that disagrees with the specification produces a service that rejects
the client.

Every request is appended to a JSON Lines request log so a test can inspect the
exact wire shape that was sent.

Usage:
    python3 tools/mock_sddc_lcm.py --contract docs/contract.json \
        --log /tmp/requests.jsonl [--host 127.0.0.1] [--port 0]

The mock prints a single line ``LISTENING <port>`` to stdout once it is ready.
It binds a loopback address only and contacts nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# --------------------------------------------------------------------------
# Scripted service state.  Fixed identifiers and timestamps keep runs
# byte-for-byte reproducible.
# --------------------------------------------------------------------------

T_CREATE = "2026-02-11T09:14:02.000Z"
T_START = "2026-02-11T09:14:03.000Z"
T_UPDATE = "2026-02-11T09:19:41.000Z"
T_END = "2026-02-11T09:22:57.000Z"

COMPONENT_IDS = {
    "VCF_OPERATIONS": "c0a80101-0000-4000-8000-00000000000a",
    "VCF_AUTOMATION": "c0a80101-0000-4000-8000-00000000000b",
    "VCF_IDENTITY_BROKER": "c0a80101-0000-4000-8000-00000000000c",
    "VCF_OPERATIONS_COLLECTOR": "c0a80101-0000-4000-8000-00000000000d",
}

DEPOT_TASK_ID = "1f0a5c2d-0000-4000-8000-000000000001"

# (componentId, action) -> task id
ACTION_TASK_IDS = {
    (COMPONENT_IDS["VCF_OPERATIONS"], "precheck"): "1f0a5c2d-0000-4000-8000-000000000002",
    (COMPONENT_IDS["VCF_OPERATIONS"], "apply"): "1f0a5c2d-0000-4000-8000-000000000003",
    (COMPONENT_IDS["VCF_AUTOMATION"], "precheck"): "1f0a5c2d-0000-4000-8000-000000000004",
    (COMPONENT_IDS["VCF_AUTOMATION"], "apply"): "1f0a5c2d-0000-4000-8000-000000000005",
    (COMPONENT_IDS["VCF_IDENTITY_BROKER"], "precheck"): "1f0a5c2d-0000-4000-8000-000000000006",
    (COMPONENT_IDS["VCF_IDENTITY_BROKER"], "apply"): "1f0a5c2d-0000-4000-8000-000000000007",
}

# The one task in the scripted rollout that ends badly.
FAILING_TASK_ID = ACTION_TASK_IDS[(COMPONENT_IDS["VCF_AUTOMATION"], "apply")]

# Inventory returned by getComponents, deliberately not in plan order.
INVENTORY = [
    {
        "id": COMPONENT_IDS["VCF_IDENTITY_BROKER"],
        "componentType": "VCF_IDENTITY_BROKER",
        "deploymentType": "OVA",
        "version": "9.0.1.0",
        "size": "Small",
        "fqdn": "idb-01.vcf.example.com",
        "scope": "FLEET",
    },
    {
        "id": COMPONENT_IDS["VCF_OPERATIONS_COLLECTOR"],
        "componentType": "VCF_OPERATIONS_COLLECTOR",
        "deploymentType": "OVA",
        "version": "9.0.1.0",
        "size": "Small",
        "fqdn": "opscol-01.vcf.example.com",
        "scope": "INSTANCE",
    },
    {
        "id": COMPONENT_IDS["VCF_AUTOMATION"],
        "componentType": "VCF_AUTOMATION",
        "deploymentType": "VSP",
        "version": "9.0.1.0",
        "size": "Medium",
        "fqdn": "auto-01.vcf.example.com",
        "scope": "FLEET",
    },
    {
        "id": COMPONENT_IDS["VCF_OPERATIONS"],
        "componentType": "VCF_OPERATIONS",
        "deploymentType": "OVA",
        "version": "9.0.1.0",
        "size": "Medium",
        "fqdn": "ops-01.vcf.example.com",
        "scope": "FLEET",
    },
]


def _msg(msg_id, default):
    return {"id": msg_id, "defaultMessage": default, "localizedMessage": default}


def _error(code, default, resolution):
    return {
        "code": code,
        "message": _msg("com.broadcom.lcm.error." + code.lower(), default),
        "resolution": _msg("com.broadcom.lcm.resolution." + code.lower(), resolution),
        "referenceId": "b7f1c9d4-0000-4000-8000-0000000000ff",
        "timestamp": T_UPDATE,
    }


def _stage(stage_id, name, status, messages=None):
    stage = {
        "id": stage_id,
        "name": name,
        "status": status,
        "startTime": T_START,
        "updateTime": T_UPDATE,
    }
    if status in ("SUCCEEDED", "FAILED", "CANCELED", "SKIPPED"):
        stage["endTime"] = T_END
    if messages:
        stage["messages"] = messages
    return stage


def _task(task_id, name, task_type, status, resource_id, resource_type):
    task = {
        "id": task_id,
        "name": name,
        "type": task_type,
        "status": status,
        "description": _msg(
            "com.broadcom.lcm.task." + task_type,
            "%s operation on %s" % (task_type, resource_type.lower()),
        ),
        "createdBy": "administrator@vsphere.local",
        "updatedBy": "administrator@vsphere.local",
        "resourceId": resource_id,
        "resourceType": resource_type,
        "createTime": T_CREATE,
        "startTime": T_START,
        "updateTime": T_UPDATE,
        "retriable": status == "FAILED",
        "cancellable": status == "RUNNING",
        "taskSummary": {"totalSubTasks": 0, "totalSteps": 2},
    }
    if status in ("SUCCEEDED", "FAILED", "CANCELED"):
        task["endTime"] = T_END
    return task


def depot_task(status):
    task = _task(
        DEPOT_TASK_ID,
        "fleet_depot_registration",
        "validate",
        status,
        "fleet-depot",
        "DEPOT",
    )
    if status == "RUNNING":
        task["stages"] = [
            _stage("stage-depot-reachability", "depot-reachability", "SUCCEEDED"),
            _stage("stage-depot-trust", "depot-trust", "RUNNING"),
        ]
        task["messages"] = [
            {
                "timestamp": T_START,
                "level": "INFO",
                "message": _msg(
                    "com.broadcom.lcm.depot.registration.started",
                    "Registering Fleet depot with SDDC LCM",
                ),
            }
        ]
    elif status == "FAILED":
        failure = _msg(
            "com.broadcom.lcm.depot.registration.failed",
            "Fleet depot certificate validation failed",
        )
        task["stages"] = [
            _stage("stage-depot-reachability", "depot-reachability", "SUCCEEDED"),
            _stage(
                "stage-depot-trust",
                "depot-trust",
                "FAILED",
                messages=[
                    {
                        "timestamp": T_END,
                        "level": "ERROR",
                        "stageId": "stage-depot-trust",
                        "message": failure,
                    }
                ],
            ),
        ]
        task["messages"] = [
            {
                "timestamp": T_START,
                "level": "INFO",
                "message": _msg(
                    "com.broadcom.lcm.depot.registration.started",
                    "Registering Fleet depot with SDDC LCM",
                ),
            },
            {"timestamp": T_END, "level": "ERROR", "message": failure},
        ]
    else:
        task["stages"] = [
            _stage("stage-depot-reachability", "depot-reachability", "SUCCEEDED"),
            _stage("stage-depot-trust", "depot-trust", "SUCCEEDED"),
        ]
        task["messages"] = [
            {
                "timestamp": T_START,
                "level": "INFO",
                "message": _msg(
                    "com.broadcom.lcm.depot.registration.started",
                    "Registering Fleet depot with SDDC LCM",
                ),
            },
            {
                "timestamp": T_END,
                "level": "INFO",
                "message": _msg(
                    "com.broadcom.lcm.depot.registration.completed",
                    "Fleet depot registered",
                ),
            },
        ]
    return task


def action_task(task_id, component_id, component_type, action, status):
    task = _task(
        task_id,
        "%s_%s" % (component_type.lower(), action),
        action,
        status,
        component_id,
        "COMPONENT",
    )
    started = {
        "timestamp": T_START,
        "level": "INFO",
        "message": _msg(
            "com.broadcom.lcm.ops.component.%s.started" % action,
            "Started %s for component %s" % (action, component_type),
        ),
    }
    if status == "RUNNING":
        task["stages"] = [
            _stage("stage-depot-download", "depot-download", "SUCCEEDED"),
            _stage("stage-component-%s" % action, "component-%s" % action, "RUNNING"),
        ]
        task["messages"] = [started]
        return task

    if status == "FAILED":
        failure = _msg(
            "com.broadcom.lcm.ops.component.%s.failed" % action,
            "Apply failed for component %s: service vcf-automation-api did not "
            "reach RUNNING state within 900 seconds" % component_type,
        )
        failure["args"] = {"componentType": component_type, "timeoutSeconds": "900"}
        warning = _msg(
            "com.broadcom.lcm.ops.component.%s.retrying" % action,
            "Retrying %s for component %s" % (action, component_type),
        )
        task["stages"] = [
            _stage("stage-depot-download", "depot-download", "SUCCEEDED"),
            _stage(
                "stage-component-%s" % action,
                "component-%s" % action,
                "FAILED",
                messages=[
                    {
                        "timestamp": T_END,
                        "level": "ERROR",
                        "stageId": "stage-component-%s" % action,
                        "message": failure,
                    }
                ],
            ),
        ]
        task["messages"] = [
            started,
            {"timestamp": T_UPDATE, "level": "WARN", "message": warning},
            {"timestamp": T_END, "level": "ERROR", "message": failure},
        ]
        return task

    task["stages"] = [
        _stage("stage-depot-download", "depot-download", "SUCCEEDED"),
        _stage("stage-component-%s" % action, "component-%s" % action, "SUCCEEDED"),
    ]
    task["messages"] = [
        started,
        {
            "timestamp": T_END,
            "level": "INFO",
            "message": _msg(
                "com.broadcom.lcm.ops.component.%s.completed" % action,
                "Completed %s for component %s" % (action, component_type),
            ),
        },
    ]
    return task


# --------------------------------------------------------------------------
# Contract-pinned routing
# --------------------------------------------------------------------------


class Contract:
    def __init__(self, data):
        self.data = data
        self.routes = []
        operations = data.get("operations")
        if not isinstance(operations, dict) or not operations:
            raise ValueError("contract has no 'operations' object")
        for op_id, op in operations.items():
            method = str(op.get("method", "")).upper()
            path = op.get("path")
            if not method or not isinstance(path, str) or not path.startswith("/"):
                raise ValueError("operation %r has no usable method/path" % op_id)
            self.routes.append((method, path, op_id, op))
        self.schemas = data.get("schemas") or {}

    def match(self, method, path):
        segments = [s for s in path.split("/") if s]
        for route_method, template, op_id, op in self.routes:
            if route_method != method:
                continue
            parts = [s for s in template.split("/") if s]
            if len(parts) != len(segments):
                continue
            params = {}
            for expected, actual in zip(parts, segments):
                if expected.startswith("{") and expected.endswith("}"):
                    params[expected[1:-1]] = actual
                elif expected != actual:
                    break
            else:
                return op_id, op, params
        return None, None, None

    def required_query(self, op):
        query = op.get("queryParams") or {}
        if isinstance(query, dict):
            return list(query.get("required") or [])
        return []

    def required_body_fields(self, op):
        fields = op.get("requiredBodyFields")
        if isinstance(fields, list):
            return list(fields)
        schema = self.schemas.get(op.get("requestSchema"))
        if isinstance(schema, dict):
            return list(schema.get("required") or [])
        return []


class State:
    """Mutable, per-process service state."""

    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.depot = None
        self.poll_counts = {}

    def next_seq(self):
        self.seq += 1
        return self.seq

    def poll(self, task_id):
        count = self.poll_counts.get(task_id, 0) + 1
        self.poll_counts[task_id] = count
        return count


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockSddcLcm/9.1"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # keep stderr quiet and deterministic
        pass

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            return self.rfile.read(int(length))
        except (TypeError, ValueError):
            return b""

    def _write(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _record(self, entry):
        line = json.dumps(entry, sort_keys=True)
        with self.server.state.lock:
            with open(self.server.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    # -- dispatch ---------------------------------------------------------

    def _handle(self, method):
        parsed = urlsplit(self.path)
        raw_body = self._read_body()
        body = None
        body_parse_error = None
        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                body_parse_error = str(exc)

        contract = self.server.contract
        op_id, op, path_params = contract.match(method, parsed.path)
        query = {}
        for pair in parsed.query.split("&"):
            if not pair:
                continue
            name, _, value = pair.partition("=")
            query.setdefault(name, []).append(value)

        entry = {
            "seq": self.server.state.next_seq(),
            "operationId": op_id,
            "method": method,
            "path": parsed.path,
            "query": parsed.query,
            "queryParams": query,
            "pathParams": path_params or {},
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "bodyRaw": raw_body.decode("utf-8", "replace"),
            "body": body,
            "bodyParseError": body_parse_error,
        }

        try:
            status, payload = self._respond(op_id, op, path_params, query, body)
        except Exception as exc:  # pragma: no cover - defensive
            status, payload = 500, _error("INTERNAL_ERROR", str(exc), "Inspect the mock")
        entry["status"] = status
        self._record(entry)
        self._write(status, payload)

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

    # -- operations -------------------------------------------------------

    def _respond(self, op_id, op, path_params, query, body):
        if op_id is None:
            return 404, _error(
                "OPERATION_NOT_SERVED",
                "%s %s is not one of the operations named in the contract"
                % (self.command, urlsplit(self.path).path),
                "Serve only the operations listed in docs/contract.json",
            )

        auth = self.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer ") or not auth[7:].strip():
            return 401, _error(
                "UNAUTHORIZED",
                "Missing or malformed Authorization header",
                "Send 'Authorization: Bearer <token>'",
            )

        contract = self.server.contract
        for name in contract.required_query(op):
            if name not in query:
                return 400, _error(
                    "MISSING_QUERY_PARAMETER",
                    "Required query parameter %r is missing" % name,
                    "Send the query parameters the specification requires",
                )

        if op.get("requestSchema"):
            if not isinstance(body, dict):
                return 400, _error(
                    "MALFORMED_BODY",
                    "Expected a JSON object body for %s" % op_id,
                    "Send an application/json object body",
                )
            for name in contract.required_body_fields(op):
                if name not in body:
                    return 400, _error(
                        "MISSING_BODY_FIELD",
                        "Required field %r is missing from the %s body"
                        % (name, op.get("requestSchema")),
                        "Send every field the specification marks required",
                    )

        handler = {
            "setDepot": self._set_depot,
            "resolveDepotComponents": self._resolve_depot_components,
            "getComponents": self._get_components,
            "performComponentAction": self._perform_component_action,
            "getTask": self._get_task,
        }.get(op_id)
        if handler is None:
            return 501, _error(
                "OPERATION_NOT_IMPLEMENTED",
                "The contract names operation %r, which this service does not "
                "implement" % op_id,
                "Limit the contract to the operations the rollout uses",
            )
        status, payload = handler(path_params, query, body)
        if 200 <= status < 300:
            try:
                status = int(op["successStatus"])
            except (KeyError, TypeError, ValueError):
                return 500, _error(
                    "INVALID_CONTRACT_STATUS",
                    "The matched operation has no usable successStatus",
                    "Derive successStatus from the OpenAPI response",
                )
        return status, payload

    def _set_depot(self, path_params, query, body):
        fqdn = body.get("fqdn")
        if not isinstance(fqdn, str) or not fqdn:
            return 400, _error(
                "INVALID_DEPOT", "fqdn must be a non-empty string", "Send the depot FQDN"
            )
        if not isinstance(body.get("certificate"), str) or not body["certificate"]:
            return 400, _error(
                "INVALID_DEPOT",
                "certificate must be a non-empty PEM string",
                "Send the PEM encoded depot certificate",
            )
        with self.server.state.lock:
            self.server.state.depot = fqdn
        return 202, depot_task("RUNNING")

    def _resolve_depot_components(self, path_params, query, body):
        spec = body.get("fleetDepotSpec")
        if not isinstance(spec, dict) or not spec.get("fqdn"):
            return 400, _error(
                "INVALID_DEPOT",
                "fleetDepotSpec.fqdn is required",
                "Send the Fleet depot spec",
            )
        requested = body.get("componentVersions")
        if not isinstance(requested, list) or not requested:
            return 400, _error(
                "INVALID_COMPONENT_VERSIONS",
                "componentVersions must be a non-empty array",
                "Request at least one component version",
            )
        resolved = []
        for item in requested:
            if not isinstance(item, dict) or not item.get("component"):
                return 400, _error(
                    "INVALID_COMPONENT_VERSIONS",
                    "each componentVersions entry needs a component",
                    "Send the component identifier",
                )
            component = item["component"]
            version = item.get("version") or "9.1.0.0"
            resolved.append(
                {
                    "component": component,
                    "version": version,
                    "binaryUrl": "https://%s/depot/PROD/COMP/%s/%s/upgrade-manifest"
                    % (spec["fqdn"], component, version),
                }
            )
        # Answer in reverse order: a client must key by component, not position.
        return 200, {"componentVersions": list(reversed(resolved))}

    def _get_components(self, path_params, query, body):
        scope_values = query.get("scope") or []
        components = INVENTORY
        if scope_values:
            wanted = scope_values[-1]
            if wanted not in ("FLEET", "INSTANCE"):
                return 400, _error(
                    "INVALID_SCOPE",
                    "scope must be FLEET or INSTANCE",
                    "Use a scope value from the specification enum",
                )
            components = [c for c in INVENTORY if c["scope"] == wanted]
        return 200, {"components": components}

    def _perform_component_action(self, path_params, query, body):
        component_id = path_params.get("componentId")
        actions = query.get("action") or []
        action = actions[-1] if actions else None
        allowed = (
            "shutdown",
            "restart",
            "start",
            "refresh",
            "precheck",
            "apply",
        )
        if action not in allowed:
            return 400, _error(
                "INVALID_ACTION",
                "action %r is not in the specification enum" % action,
                "Use one of: %s" % ", ".join(allowed),
            )
        component_type = None
        for name, ident in COMPONENT_IDS.items():
            if ident == component_id:
                component_type = name
                break
        if component_type is None:
            return 404, _error(
                "COMPONENT_NOT_FOUND",
                "No component with id %r" % component_id,
                "Use an id returned by the component inventory",
            )
        task_id = ACTION_TASK_IDS.get((component_id, action))
        if task_id is None:
            return 404, _error(
                "ACTION_NOT_AVAILABLE",
                "Action %r is not available for component %s" % (action, component_type),
                "Choose an action the component supports",
            )
        return 202, action_task(task_id, component_id, component_type, action, "RUNNING")

    def _get_task(self, path_params, query, body):
        task_id = path_params.get("taskId")
        if task_id == DEPOT_TASK_ID:
            count = self.server.state.poll(task_id)
            terminal = "FAILED" if self.server.fail_depot else "SUCCEEDED"
            return 200, depot_task("RUNNING" if count < 2 else terminal)
        for (component_id, action), ident in ACTION_TASK_IDS.items():
            if ident != task_id:
                continue
            component_type = next(
                name for name, cid in COMPONENT_IDS.items() if cid == component_id
            )
            count = self.server.state.poll(task_id)
            if count < 2:
                status = "RUNNING"
            elif task_id == FAILING_TASK_ID:
                status = "FAILED"
            else:
                status = "SUCCEEDED"
            return 200, action_task(
                task_id, component_id, component_type, action, status
            )
        return 404, _error(
            "TASK_NOT_FOUND",
            "No task with id %r" % task_id,
            "Poll a task id returned by the service",
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="docs/contract.json")
    parser.add_argument("--log", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--fail-depot",
        action="store_true",
        help="make the depot registration task fail after its first poll",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.contract, encoding="utf-8") as handle:
            contract = Contract(json.load(handle))
    except FileNotFoundError:
        sys.stderr.write("contract not found: %s\n" % args.contract)
        return 2
    except (ValueError, TypeError) as exc:
        sys.stderr.write("unusable contract %s: %s\n" % (args.contract, exc))
        return 2

    open(args.log, "w", encoding="utf-8").close()

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        sys.stderr.write("refusing to bind a non-loopback host: %s\n" % args.host)
        return 2

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.contract = contract
    httpd.state = State()
    httpd.log_path = args.log
    httpd.fail_depot = args.fail_depot
    sys.stdout.write("LISTENING %d\n" % httpd.server_address[1])
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
