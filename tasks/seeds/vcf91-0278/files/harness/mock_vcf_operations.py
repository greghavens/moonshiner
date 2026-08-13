#!/usr/bin/env python3
"""Loopback mock of the VMware Cloud Foundation Operations API.

The mock is pinned to docs/contract.json: it serves ONLY the operations that the
contract names, at the method/path the contract records, and it validates request
bodies against the component field sets the contract records. Nothing else is
routable. Every request (including rejected ones) is appended to a JSONL request
log so a test can inspect the exact wire shape that was sent.

Binds 127.0.0.1 only. Never contacts anything.
"""

import argparse
import copy
import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

# Component nesting inside the VCF Operations custom-group model. Used to walk a
# request body and validate each node against the field sets recorded in the
# contract. Nodes whose schema the contract does not describe are not validated.
NESTING = {
    "custom-group": {
        "resourceKey": ("resource-key", False),
        "membershipDefinition": ("custom-group-membership", False),
    },
    "custom-group-membership": {
        "rules": ("membership-rule-group", True),
    },
    "membership-rule-group": {
        "resourceKindKey": ("resource-kind-key", False),
        "resourceNameConditionRules": ("resource-name-condition-rule", True),
    },
}

FIXED_VALIDITY = 1798934400000
FIXED_EXPIRES_AT = "2027-01-01T00:00:00.000Z"
GROUP_ID_NAMESPACE = uuid.NAMESPACE_URL


class Contract:
    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise SystemExit("contract.json must be a JSON object")
        source = raw.get("source") or {}
        self.base_path = source.get("serverBasePath") or ""
        self.schemas = raw.get("schemas") or {}
        self.routes = {}
        for op in raw.get("operations") or []:
            if not isinstance(op, dict):
                continue
            method = str(op.get("method", "")).upper()
            path = op.get("path")
            if not method or not isinstance(path, str):
                continue
            self.routes[(method, self.base_path + path)] = op

    def field_sets(self, schema_name):
        entry = self.schemas.get(schema_name)
        if not isinstance(entry, dict):
            return None
        required = entry.get("required")
        optional = entry.get("optional")
        if not isinstance(required, list) or not isinstance(optional, list):
            return None
        return set(required), set(optional)


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.groups = {}
        self.tokens = set()
        self.seq = 0


class Rejected(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def contains_null(node, trail="body"):
    if node is None:
        return trail
    if isinstance(node, dict):
        for key, value in node.items():
            hit = contains_null(value, trail + "." + key)
            if hit:
                return hit
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hit = contains_null(value, trail + "[%d]" % index)
            if hit:
                return hit
    return None


def validate_node(contract, node, schema_name, trail):
    fields = contract.field_sets(schema_name)
    if fields is None:
        return
    required, optional = fields
    if not isinstance(node, dict):
        raise Rejected(400, "%s must be an object" % trail)
    keys = set(node.keys())
    missing = sorted(required - keys)
    if missing:
        raise Rejected(400, "%s is missing required field(s): %s" % (trail, ", ".join(missing)))
    unknown = sorted(keys - required - optional)
    if unknown:
        raise Rejected(
            400,
            "%s carries field(s) that are not part of schema '%s': %s"
            % (trail, schema_name, ", ".join(unknown)),
        )
    for prop, (child_schema, is_array) in NESTING.get(schema_name, {}).items():
        if prop not in node:
            continue
        child = node[prop]
        if is_array:
            if not isinstance(child, list):
                raise Rejected(400, "%s.%s must be an array" % (trail, prop))
            for index, item in enumerate(child):
                validate_node(contract, item, child_schema, "%s.%s[%d]" % (trail, prop, index))
        else:
            validate_node(contract, child, child_schema, "%s.%s" % (trail, prop))


def issue_token(username, auth_source):
    seed = "%s|%s" % (username, auth_source or "")
    return "tok-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def group_id_for(name):
    return str(uuid.uuid5(GROUP_ID_NAMESPACE, "vcf-ops-group:" + name))


def make_handler(contract, state, log_path):
    log_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "MockVcfOperations/1.0"

        def log_message(self, *_args):
            pass

        # -- plumbing ---------------------------------------------------
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

        def _record(self, method, path, raw_query, body_text, body_json, status, operation_id):
            with state.lock:
                state.seq += 1
                seq = state.seq
            entry = {
                "seq": seq,
                "method": method,
                "path": path,
                "rawQuery": raw_query,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body_text,
                "bodyJson": body_json,
                "status": status,
                "operationId": operation_id,
            }
            with log_lock:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, sort_keys=True) + "\n")
                    fh.flush()

        def _respond(self, status, payload):
            body = b"" if payload is None else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _handle(self, method):
            split = urlsplit(self.path)
            path = split.path
            raw_query = split.query or ""
            body_text = self._read_body()
            body_json = None
            if body_text:
                try:
                    body_json = json.loads(body_text)
                except ValueError:
                    body_json = None

            operation = contract.routes.get((method, path))
            operation_id = operation.get("operationId") if operation else None
            try:
                if operation is None:
                    raise Rejected(404, "no operation is exposed at %s %s" % (method, path))
                status, payload = self._dispatch(operation, method, raw_query, body_text, body_json)
            except Rejected as rejected:
                status, payload = rejected.status, {"message": rejected.message}
            self._record(method, path, raw_query, body_text, body_json, status, operation_id)
            self._respond(status, payload)

        # -- operation dispatch ----------------------------------------
        def _dispatch(self, operation, method, raw_query, body_text, body_json):
            operation_id = operation.get("operationId")
            allowed_query = operation.get("optionalQueryParameters") or []
            if raw_query:
                for name, _value in parse_qsl(raw_query, keep_blank_values=True):
                    if name not in allowed_query:
                        raise Rejected(400, "unsupported query parameter '%s'" % name)

            if operation_id != "acquireToken":
                self._require_auth()

            request_schema = operation.get("requestSchema")
            if method in ("POST", "PUT"):
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if request_schema and content_type != "application/json":
                    raise Rejected(415, "Content-Type must be application/json, got '%s'" % content_type)
                if request_schema:
                    if not body_text:
                        raise Rejected(400, "a request body is required")
                    if body_json is None:
                        raise Rejected(400, "request body is not valid JSON")
                    null_at = contains_null(body_json)
                    if null_at:
                        raise Rejected(
                            400,
                            "%s is null; omit optional fields instead of sending them empty" % null_at,
                        )
                    validate_node(contract, body_json, request_schema, "body")
            elif body_text:
                raise Rejected(400, "%s must not carry a request body" % method)

            if operation_id == "acquireToken":
                return self._acquire_token(body_json)
            if operation_id == "getCustomGroups":
                return self._get_custom_groups()
            if operation_id == "createCustomGroup":
                return self._create_custom_group(body_json)
            if operation_id == "modifyCustomGroup":
                return self._modify_custom_group(body_json)
            raise Rejected(501, "operation '%s' is not implemented by this mock" % operation_id)

        def _require_auth(self):
            header = self.headers.get("Authorization")
            if not header:
                raise Rejected(401, "Authorization header is required")
            parts = header.split(" ", 1)
            if len(parts) != 2 or parts[0] != "OpsToken" or not parts[1].strip():
                raise Rejected(401, "Authorization must be 'OpsToken <token>'")
            with state.lock:
                known = parts[1].strip() in state.tokens
            if not known:
                raise Rejected(401, "unknown or expired token")

        def _acquire_token(self, body):
            token = issue_token(body.get("username"), body.get("authSource"))
            with state.lock:
                state.tokens.add(token)
            return 200, {
                "token": token,
                "validity": FIXED_VALIDITY,
                "expiresAt": FIXED_EXPIRES_AT,
                "roles": ["ContentAdmin"],
            }

        def _get_custom_groups(self):
            with state.lock:
                groups = [copy.deepcopy(g) for g in state.groups.values()]
            groups.sort(key=lambda g: g.get("resourceKey", {}).get("name", ""))
            return 200, {"groups": groups}

        def _create_custom_group(self, body):
            if "id" in body:
                raise Rejected(400, "'id' must not be supplied when creating a custom group")
            name = (body.get("resourceKey") or {}).get("name")
            if not isinstance(name, str) or not name:
                raise Rejected(400, "resourceKey.name is required")
            with state.lock:
                for existing in state.groups.values():
                    if existing.get("resourceKey", {}).get("name") == name:
                        raise Rejected(409, "a custom group named '%s' already exists" % name)
                stored = copy.deepcopy(body)
                stored["id"] = group_id_for(name)
                state.groups[stored["id"]] = stored
                return 201, copy.deepcopy(stored)

        def _modify_custom_group(self, body):
            group_id = body.get("id")
            if not isinstance(group_id, str) or not group_id:
                raise Rejected(400, "'id' is required when modifying a custom group")
            name = (body.get("resourceKey") or {}).get("name")
            with state.lock:
                existing = state.groups.get(group_id)
                if existing is None:
                    raise Rejected(404, "no custom group with id '%s'" % group_id)
                if existing.get("resourceKey", {}).get("name") != name:
                    raise Rejected(409, "a custom group cannot be renamed through this operation")
                stored = copy.deepcopy(body)
                state.groups[group_id] = stored
                return 200, copy.deepcopy(stored)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        def do_PATCH(self):
            self._handle("PATCH")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args()

    contract = Contract(args.contract)
    state = State()
    open(args.log, "w", encoding="utf-8").close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(contract, state, args.log))
    port = server.server_address[1]
    with open(args.port_file, "w", encoding="utf-8") as fh:
        fh.write(str(port))
        fh.flush()
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
