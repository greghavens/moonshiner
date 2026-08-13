#!/usr/bin/env python3
"""Loopback mock of the vSAN Data Protection (Snapshot Appliance) API.

The server owns no route table of its own: every route it serves is read from
``docs/contract.json``.  Each contract operation must carry a ``role`` that this
mock knows how to implement; an operation with an unknown role is a fatal
startup error, and a role this mock implements but the contract does not name is
simply not served.

The mock binds to 127.0.0.1 only and appends one JSON object per request to the
request log, which the verification test reads.

Usage:
    python3 mock/snapservice_mock.py \
        --contract docs/contract.json \
        --fixtures mock/fixtures/site.json \
        --log /tmp/requests.jsonl \
        --port-file /tmp/port.txt

Exit codes:
    0  clean shutdown
    3  the contract could not be loaded / is not usable as a route table
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Roles this mock can implement.  The contract decides which operationId, method,
# path, query, success status and security scheme each role is reached through.
KNOWN_ROLES = (
    "session_create",
    "session_delete",
    "protection_groups_list",
    "snapshot_create",
    "task_get",
)

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ContractError(Exception):
    """The contract cannot be used to route requests."""


# --------------------------------------------------------------------------- #
# Contract loading
# --------------------------------------------------------------------------- #


def _require(mapping, key, kind, where):
    if not isinstance(mapping, dict) or key not in mapping:
        raise ContractError("%s: missing required key %r" % (where, key))
    value = mapping[key]
    if not isinstance(value, kind):
        raise ContractError(
            "%s: key %r must be %s, got %s"
            % (where, key, getattr(kind, "__name__", kind), type(value).__name__)
        )
    return value


class Route:
    def __init__(self, operation_id, entry):
        where = "operations[%r]" % operation_id
        self.operation_id = operation_id
        self.role = _require(entry, "role", str, where)
        if self.role not in KNOWN_ROLES:
            raise ContractError(
                "%s: role %r is not served by this mock (known roles: %s)"
                % (where, self.role, ", ".join(KNOWN_ROLES))
            )
        self.method = _require(entry, "method", str, where).upper()
        if self.method not in HTTP_METHODS:
            raise ContractError("%s: method %r is not a valid HTTP method" % (where, self.method))
        self.path = _require(entry, "path", str, where)
        if not self.path.startswith("/") or self.path.endswith("/"):
            raise ContractError(
                "%s: path must start with '/' and must not end with '/'" % where
            )
        if "?" in self.path or "#" in self.path:
            raise ContractError(
                "%s: path must not contain a query string; use 'fixed_query'" % where
            )

        self.fixed_query = _require(entry, "fixed_query", dict, where)
        for key, value in self.fixed_query.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ContractError("%s: fixed_query keys and values must be strings" % where)

        declared_params = _require(entry, "path_params", list, where)
        found = PLACEHOLDER_RE.findall(self.path)
        if sorted(str(p) for p in declared_params) != sorted(found):
            raise ContractError(
                "%s: path_params %r do not match the placeholders in path %r"
                % (where, declared_params, self.path)
            )
        self.path_params = [str(p) for p in declared_params]

        self.optional_query_params = [
            str(p) for p in _require(entry, "optional_query_params", list, where)
        ]
        self.success_status = _require(entry, "success_status", int, where)
        if not 200 <= self.success_status <= 299:
            raise ContractError("%s: success_status must be a 2xx code" % where)
        self.auth = _require(entry, "auth", str, where)

        body = entry.get("request_body", "__missing__")
        if body == "__missing__":
            raise ContractError("%s: missing required key 'request_body'" % where)
        if body is not None:
            _require(body, "schema", str, where + ".request_body")
            _require(body, "required", list, where + ".request_body")
            _require(body, "optional", list, where + ".request_body")
        self.request_body = body
        if "response_schema" not in entry:
            raise ContractError("%s: missing required key 'response_schema'" % where)

        pattern = "^"
        index = 0
        for match in PLACEHOLDER_RE.finditer(self.path):
            pattern += re.escape(self.path[index:match.start()])
            pattern += "(?P<%s>[^/]+)" % match.group(1)
            index = match.end()
        pattern += re.escape(self.path[index:]) + "$"
        self.regex = re.compile(pattern)

    def body_fields(self):
        if self.request_body is None:
            return [], []
        return (
            [str(f) for f in self.request_body["required"]],
            [str(f) for f in self.request_body["optional"]],
        )


class Contract:
    def __init__(self, document):
        if not isinstance(document, dict):
            raise ContractError("contract root must be a JSON object")
        spec = _require(document, "spec", dict, "contract")
        self.base_path = _require(spec, "server_base_path", str, "contract.spec")
        if not self.base_path.startswith("/") or self.base_path.endswith("/"):
            raise ContractError(
                "contract.spec.server_base_path must start with '/' and must not end with '/'"
            )

        self.security_schemes = _require(document, "security_schemes", dict, "contract")
        operations = _require(document, "operations", dict, "contract")
        if not operations:
            raise ContractError("contract.operations is empty")

        self.routes = []
        by_role = {}
        for operation_id, entry in operations.items():
            route = Route(operation_id, entry)
            if route.role in by_role:
                raise ContractError(
                    "role %r is claimed by both %r and %r"
                    % (route.role, by_role[route.role].operation_id, operation_id)
                )
            by_role[route.role] = route
            self.routes.append(route)

        missing = [role for role in KNOWN_ROLES if role not in by_role]
        if missing:
            raise ContractError(
                "contract does not name an operation for role(s): %s" % ", ".join(missing)
            )
        self.by_role = by_role

        for route in self.routes:
            scheme = self.security_schemes.get(route.auth)
            if not isinstance(scheme, dict):
                raise ContractError(
                    "operations[%r]: auth %r is not declared in contract.security_schemes"
                    % (route.operation_id, route.auth)
                )
            kind = scheme.get("type")
            if kind == "apiKey":
                if scheme.get("in") != "header" or not isinstance(scheme.get("name"), str):
                    raise ContractError(
                        "security_schemes[%r]: an apiKey scheme needs 'in': 'header' and a "
                        "string 'name'" % route.auth
                    )
            elif kind == "http":
                if scheme.get("scheme") != "basic":
                    raise ContractError(
                        "security_schemes[%r]: this mock only implements the 'basic' http "
                        "scheme" % route.auth
                    )
            else:
                raise ContractError(
                    "security_schemes[%r]: unsupported security scheme type %r"
                    % (route.auth, kind)
                )

    def match(self, method, path, query_pairs):
        for route in self.routes:
            if route.method != method:
                continue
            match = route.regex.match(path)
            if not match:
                continue
            if all((key, value) in query_pairs for key, value in route.fixed_query.items()):
                return route, match.groupdict()
        return None, None

    def api_key_header(self, route):
        return self.security_schemes[route.auth]["name"].lower()

    def is_basic(self, route):
        return self.security_schemes[route.auth].get("type") == "http"


# --------------------------------------------------------------------------- #
# Appliance state
# --------------------------------------------------------------------------- #


def error_body(error_type, message):
    return {
        "error_type": error_type,
        "messages": [
            {
                "id": "com.vmware.snapservice.%s" % error_type.lower(),
                "default_message": message,
                "args": [],
            }
        ],
    }


class Appliance:
    """Deterministic in-memory state for one mock run."""

    def __init__(self, fixtures):
        self.username = fixtures["credentials"]["username"]
        self.password = fixtures["credentials"]["password"]
        self.session_request_budget = int(fixtures["session_request_budget"])
        self.task_polls_before_success = int(fixtures["task_polls_before_success"])
        self.task_terminal_status = str(fixtures.get("task_terminal_status", "SUCCEEDED"))
        if self.task_terminal_status not in ("SUCCEEDED", "FAILED"):
            raise ValueError("task_terminal_status must be SUCCEEDED or FAILED")
        self.clusters = fixtures["clusters"]
        self.sessions = {}
        self.session_counter = 0
        self.task_counter = 0
        self.tasks = {}
        self.snapshots_taken = {}
        self.lock = threading.Lock()

    # -- sessions ---------------------------------------------------------- #

    def create_session(self):
        self.session_counter += 1
        token = "sess-%08d" % self.session_counter
        self.sessions[token] = {"remaining": self.session_request_budget, "active": True}
        return token

    def spend_session(self, token):
        """Return (ok, reason).  Consumes one request from the token's budget."""
        session = self.sessions.get(token)
        if session is None or not session["active"]:
            return False, "unknown"
        if session["remaining"] <= 0:
            return False, "expired"
        session["remaining"] -= 1
        return True, None

    def delete_session(self, token):
        self.sessions[token]["active"] = False

    # -- protection groups -------------------------------------------------- #

    def list_protection_groups(self, cluster):
        entry = self.clusters.get(cluster)
        if entry is None:
            return None
        return {"items": json.loads(json.dumps(entry["protection_groups"]))}

    def has_protection_group(self, cluster, pg):
        entry = self.clusters.get(cluster)
        if entry is None:
            return False
        return any(item["pg"] == pg for item in entry["protection_groups"])

    # -- snapshots and tasks ------------------------------------------------ #

    def start_snapshot(self, cluster, pg, name):
        key = (cluster, pg)
        if key in self.snapshots_taken:
            return None
        self.task_counter += 1
        task_id = "task-%08d" % self.task_counter
        snapshot_id = "pgsnap-%08d" % self.task_counter
        self.snapshots_taken[key] = snapshot_id
        self.tasks[task_id] = {
            "polls": 0,
            "snapshot": snapshot_id,
            "cluster": cluster,
            "pg": pg,
            "name": name,
        }
        return task_id

    def poll_task(self, task_id):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        task["polls"] += 1
        done = task["polls"] >= self.task_polls_before_success
        info = {
            "cancelable": False,
            "description": {
                "id": "com.vmware.snapservice.protection_group.snapshot.create",
                "default_message": "Create protection group snapshot %s" % task["name"],
                "args": [task["name"]],
            },
            "service": "com.vmware.snapservice.clusters.protection_groups.snapshots",
            "operation": "create",
            "status": self.task_terminal_status if done else "RUNNING",
            "start_time": "2026-05-13T11:27:00.000Z",
            "progress": {
                "total": 100,
                "completed": 100 if done else 40,
                "message": {
                    "id": "com.vmware.snapservice.task.progress",
                    "default_message": "Quiescing and capturing member virtual machines",
                    "args": [],
                },
            },
        }
        if done:
            info["end_time"] = "2026-05-13T11:27:04.000Z"
            if self.task_terminal_status == "SUCCEEDED":
                info["result"] = task["snapshot"]
            else:
                info["error"] = error_body(
                    "ERROR", "the simulated snapshot operation failed"
                )
        return info


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #


class RequestLog:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.seq = 0
        with open(path, "w", encoding="utf-8"):
            pass

    def append(self, record):
        with self.lock:
            self.seq += 1
            record["seq"] = self.seq
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SnapserviceMock/1.0"

    # -- helpers ------------------------------------------------------------ #

    def log_message(self, *args):  # silence stderr access logging
        pass

    def _send(self, status, payload):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return None
        try:
            size = int(length)
        except ValueError:
            return None
        if size <= 0:
            return ""
        return self.rfile.read(size).decode("utf-8", "replace")

    # -- dispatch ----------------------------------------------------------- #

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
        contract = self.server.contract
        appliance = self.server.appliance
        split = urlsplit(self.path)
        raw_query = split.query
        query_pairs = parse_qsl(raw_query, keep_blank_values=True)
        body_raw = self._read_body()

        authorization = self.headers.get("Authorization")
        record = {
            "method": method,
            "target": self.path,
            "path": split.path,
            "raw_query": raw_query,
            "query_pairs": [list(pair) for pair in query_pairs],
            "content_type": self.headers.get("Content-Type"),
            "accept": self.headers.get("Accept"),
            "authorization_scheme": authorization.split(" ", 1)[0].lower() if authorization else None,
            "basic_username": None,
            "session_header_name": None,
            "session_token": None,
            "body_raw": body_raw,
            "role": None,
            "operation_id": None,
            "status": None,
        }

        try:
            status = self._route(contract, appliance, method, split.path, query_pairs, body_raw, record)
        except Exception as exc:  # pragma: no cover - defensive
            status = self._send(500, error_body("ERROR", "mock failure: %s" % exc))
        record["status"] = status
        self.server.request_log.append(record)

    def _route(self, contract, appliance, method, path, query_pairs, body_raw, record):
        base = contract.base_path
        if not (path == base or path.startswith(base + "/")):
            return self._send(404, error_body("NOT_FOUND", "no such resource: %s" % path))
        relative = path[len(base):] or "/"

        route, params = contract.match(method, relative, query_pairs)
        if route is None:
            return self._send(
                404,
                error_body("NOT_FOUND", "no operation is mapped to %s %s" % (method, path)),
            )
        record["role"] = route.role
        record["operation_id"] = route.operation_id

        with appliance.lock:
            allowed_query = set(route.fixed_query) | set(route.optional_query_params)
            stray = sorted({key for key, _ in query_pairs} - allowed_query)
            if stray:
                return self._send(
                    400,
                    error_body(
                        "INVALID_ARGUMENT",
                        "unknown query parameter(s): %s" % ", ".join(repr(k) for k in stray),
                    ),
                )

            if contract.is_basic(route):
                authorization = self.headers.get("Authorization") or ""
                if not authorization.lower().startswith("basic "):
                    return self._send(
                        401, error_body("UNAUTHENTICATED", "credentials are required")
                    )
                try:
                    decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
                    username, password = decoded.split(":", 1)
                except Exception:
                    return self._send(
                        401, error_body("UNAUTHENTICATED", "malformed basic credentials")
                    )
                record["basic_username"] = username
                if username != appliance.username or password != appliance.password:
                    return self._send(
                        401, error_body("UNAUTHENTICATED", "invalid credentials")
                    )
            else:
                header_name = contract.api_key_header(route)
                record["session_header_name"] = header_name
                token = self.headers.get(header_name)
                record["session_token"] = token
                if not token:
                    return self._send(
                        401, error_body("UNAUTHENTICATED", "session identifier is missing")
                    )
                ok, reason = appliance.spend_session(token)
                if not ok:
                    message = (
                        "the session identifier has expired"
                        if reason == "expired"
                        else "the session identifier is not valid"
                    )
                    return self._send(401, error_body("UNAUTHENTICATED", message))

            return self._handle(route, params, query_pairs, body_raw, appliance)

    # -- role implementations ----------------------------------------------- #

    def _handle(self, route, params, query_pairs, body_raw, appliance):
        role = route.role

        if role == "session_create":
            return self._send(route.success_status, appliance.create_session())

        if role == "session_delete":
            appliance.delete_session(self.headers.get(self.server.contract.api_key_header(route)))
            return self._send(route.success_status, None)

        if role == "protection_groups_list":
            cluster = next(iter(params.values()), None)
            result = appliance.list_protection_groups(cluster)
            if result is None:
                return self._send(
                    404, error_body("NOT_FOUND", "no cluster %r in the system" % cluster)
                )
            return self._send(route.success_status, result)

        if role == "snapshot_create":
            # Path parameters are ordered as they appear in the operation's path.
            cluster, pg = (list(params.values()) + [None, None])[:2]
            if not appliance.has_protection_group(cluster, pg):
                return self._send(
                    404,
                    error_body("NOT_FOUND", "no protection group %r on cluster %r" % (pg, cluster)),
                )
            required, optional = route.body_fields()
            spec, failure = parse_spec(body_raw, required, optional)
            if failure is not None:
                return self._send(400, error_body("INVALID_ARGUMENT", failure))
            label = str(spec.get(required[0])) if required else ""
            task_id = appliance.start_snapshot(cluster, pg, label)
            if task_id is None:
                return self._send(
                    400,
                    error_body(
                        "NOT_ALLOWED_IN_CURRENT_STATE",
                        "a snapshot operation has already been submitted for protection "
                        "group %r; another operation is in progress" % pg,
                    ),
                )
            return self._send(route.success_status, task_id)

        if role == "task_get":
            task_id = next(iter(params.values()), None)
            info = appliance.poll_task(task_id)
            if info is None:
                return self._send(404, error_body("NOT_FOUND", "no task %r" % task_id))
            return self._send(route.success_status, info)

        return self._send(500, error_body("ERROR", "unimplemented role %r" % role))


def parse_spec(body_raw, required, optional):
    """Validate a request body against the contract's field lists.

    Returns ``(document, None)`` or ``(None, message)``.  Optional fields that the
    caller does not want must be omitted: a ``null`` or empty value is rejected,
    which is how the appliance distinguishes "leave unset" from "set to nothing".
    """
    if body_raw is None or body_raw == "":
        return None, "a request body is required"
    try:
        document = json.loads(body_raw)
    except ValueError as exc:
        return None, "request body is not valid JSON: %s" % exc
    if not isinstance(document, dict):
        return None, "request body must be a JSON object"

    allowed = set(required) | set(optional)
    unknown = sorted(set(document) - allowed)
    if unknown:
        return None, "unknown propert%s: %s" % (
            "y" if len(unknown) == 1 else "ies",
            ", ".join(repr(name) for name in unknown),
        )
    for name in required:
        if name not in document:
            return None, "required property %r is missing" % name
        if document[name] is None:
            return None, "required property %r must not be null" % name
    for name in optional:
        if name in document and _is_empty(document[name]):
            return None, (
                "optional property %r must be omitted when it is not set; sending it as "
                "null or empty is rejected" % name
            )
    for name, value in document.items():
        if isinstance(value, dict):
            empty = sorted(key for key, nested in value.items() if _is_empty(nested))
            if empty:
                return None, "propert%s %s of %r must be omitted rather than sent empty" % (
                    "y" if len(empty) == 1 else "ies",
                    ", ".join(repr(key) for key in empty),
                    name,
                )
    return document, None


def _is_empty(value):
    return value is None or value == "" or value == {} or value == []


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--contract", default="docs/contract.json")
    parser.add_argument("--fixtures", default="mock/fixtures/site.json")
    parser.add_argument("--log", required=True, help="path of the JSONL request log")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file", help="file to write the bound port to")
    args = parser.parse_args(argv)

    try:
        with open(args.contract, encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        sys.stderr.write("contract not found: %s\n" % args.contract)
        return 3
    except ValueError as exc:
        sys.stderr.write("contract is not valid JSON: %s\n" % exc)
        return 3

    try:
        contract = Contract(document)
    except ContractError as exc:
        sys.stderr.write("contract is not usable as a route table: %s\n" % exc)
        return 3

    with open(args.fixtures, encoding="utf-8") as handle:
        fixtures = json.load(handle)

    server = MockServer(("127.0.0.1", args.port), Handler)
    server.contract = contract
    server.appliance = Appliance(fixtures)
    server.request_log = RequestLog(args.log)

    port = server.server_address[1]
    if args.port_file:
        with open(args.port_file, "w", encoding="utf-8") as handle:
            handle.write(str(port))
    sys.stdout.write("listening on 127.0.0.1:%d\n" % port)
    sys.stdout.flush()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        stop.wait()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
