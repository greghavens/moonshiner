#!/usr/bin/env python3
"""Loopback fixture for the VCF Operations for Networks API.

The server is pinned to ``docs/contract.json``: it builds its route table from
the operations that contract names and serves nothing else.  Every request is
appended to a JSON Lines log so a test can assert the exact wire shape that a
client produced.

This is a local fixture standing in for an appliance.  It is a real HTTP server
reached over a real socket; nothing about the caller's tooling is intercepted.

Usage:
    ni_mock.py --contract docs/contract.json --log /tmp/requests.jsonl [--port 0]
               [--fail-tier db] [--fail-operation addApplication]
               [--fail-status 400] [--fail-message "..."]

Writes ``PORT <n>`` to stdout once bound, then serves until SIGTERM.
"""

import argparse
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --------------------------------------------------------------------------
# Contract loading
# --------------------------------------------------------------------------


class Contract:
    def __init__(self, doc):
        self.doc = doc
        self.base_path = doc["base_path"].rstrip("/")
        self.rules = doc["wire_rules"]
        self.auth_modes = doc["auth_modes"]
        self.routes = []
        for op in doc["operations"]:
            pattern = re.escape(self.base_path + op["path"])
            for param in op.get("path_parameters", []):
                pattern = pattern.replace(
                    re.escape("{" + param + "}"), "(?P<%s>[^/]+)" % param
                )
            self.routes.append((op["method"], re.compile("^" + pattern + "$"), op))

    def match(self, method, path):
        """Return (op, path_params) or (None, reason)."""
        path_exists = False
        for m, rx, op in self.routes:
            hit = rx.match(path)
            if hit:
                path_exists = True
                if m == method:
                    return op, hit.groupdict()
        if path_exists:
            return None, "method not in contract for this path"
        return None, "path not in contract"


# --------------------------------------------------------------------------
# Body validation against the contract
# --------------------------------------------------------------------------


def find_nulls(node, trail=""):
    out = []
    if node is None:
        out.append(trail or "<body>")
    elif isinstance(node, dict):
        for k, v in node.items():
            out.extend(find_nulls(v, "%s.%s" % (trail, k) if trail else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(find_nulls(v, "%s[%d]" % (trail, i)))
    return out


def validate_object(node, spec, trail):
    """Validate one object node against a contract property spec."""
    errors = []
    if not isinstance(node, dict):
        return ["%s: expected a JSON object" % (trail or "<body>")]

    required = spec.get("required", [])
    optional = spec.get("optional", [])
    unsupported = spec.get("unsupported_by_this_client", [])
    known = set(required) | set(optional)
    props = spec.get("properties", {})

    for name in required:
        if name not in node:
            errors.append("%s: missing required property '%s'" % (trail or "<body>", name))

    for name in node:
        where = "%s.%s" % (trail, name) if trail else name
        if name in unsupported:
            errors.append(
                "%s: property '%s' is not supported by this client contract and must be omitted"
                % (where, name)
            )
            continue
        if name not in known:
            errors.append("%s: property '%s' is not defined in the contract" % (where, name))
            continue

        value = node[name]
        pspec = props.get(name, {})

        if name in optional and isinstance(value, (dict, list, str)) and len(value) == 0:
            errors.append(
                "%s: optional property '%s' is empty; unset optional fields must be omitted, not sent empty"
                % (where, name)
            )
            continue

        errors.extend(validate_value(value, pspec, where))

    return errors


def validate_value(value, pspec, where):
    errors = []
    ptype = pspec.get("type")

    if ptype == "object":
        errors.extend(validate_object(value, pspec, where))
    elif ptype == "array":
        if not isinstance(value, list):
            return ["%s: expected an array" % where]
        item = pspec.get("items", {})
        for i, entry in enumerate(value):
            slot = "%s[%d]" % (where, i)
            if item.get("type") == "object":
                errors.extend(validate_object(entry, item, slot))
                errors.extend(check_criteria_rule(entry, item, slot))
            elif item.get("type") == "string" and not isinstance(entry, str):
                errors.append("%s: expected a string" % slot)
    elif ptype == "string":
        if not isinstance(value, str):
            errors.append("%s: expected a string" % where)
        elif "enum" in pspec and value not in pspec["enum"]:
            errors.append("%s: '%s' is not one of %s" % (where, value, pspec["enum"]))
        elif "const" in pspec and value != pspec["const"]:
            errors.append("%s: expected the constant '%s'" % (where, pspec["const"]))
    elif ptype == "integer" and not isinstance(value, int):
        errors.append("%s: expected an integer" % where)

    return errors


CRITERIA_FOR = {
    "SearchMembershipCriteria": "search_membership_criteria",
    "IPAddressMembershipCriteria": "ip_address_membership_criteria",
}


def check_criteria_rule(entry, item, slot):
    """Enforce the one-of rule on GroupMembershipCriteria entries."""
    if "membership_type" not in item.get("properties", {}):
        return []
    if not isinstance(entry, dict):
        return []
    kind = entry.get("membership_type")
    expected = CRITERIA_FOR.get(kind)
    if expected is None:
        return []
    errors = []
    if expected not in entry:
        errors.append("%s: membership_type '%s' requires '%s'" % (slot, kind, expected))
    for other in CRITERIA_FOR.values():
        if other != expected and other in entry:
            errors.append(
                "%s: membership_type '%s' must not carry '%s'" % (slot, kind, other)
            )
    return errors


# --------------------------------------------------------------------------
# Appliance state
# --------------------------------------------------------------------------


class Appliance:
    def __init__(self, opts):
        self.lock = threading.Lock()
        self.opts = opts
        self.tokens = set()
        self.applications = {}
        self.next_app = 1
        self.next_tier = 1

    def issue_token(self):
        with self.lock:
            token = "ni-session-token-%04d" % (len(self.tokens) + 1)
            self.tokens.add(token)
            return token

    def add_application(self, name):
        with self.lock:
            entity_id = "18230:561:%09d" % (100000000 + self.next_app)
            self.next_app += 1
            self.applications[entity_id] = {"name": name, "tiers": []}
            return entity_id

    def add_tier(self, app_id, name):
        with self.lock:
            entity_id = "18230:562:%09d" % (200000000 + self.next_tier)
            self.next_tier += 1
            self.applications[app_id]["tiers"].append({"entity_id": entity_id, "name": name})
            return entity_id


# --------------------------------------------------------------------------
# Request handling
# --------------------------------------------------------------------------

LOGGED_HEADERS = ("authorization", "content-type", "accept")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcf-ops-networks-fixture/1.0"

    contract = None
    appliance = None
    log_path = None
    log_lock = threading.Lock()
    seq = [0]

    def log_message(self, fmt, *args):  # silence stderr access log
        pass

    # -- helpers ----------------------------------------------------------

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _record(self, method, path, raw):
        with Handler.log_lock:
            Handler.seq[0] += 1
            entry = {
                "seq": Handler.seq[0],
                "method": method,
                "path": path,
                "headers": {
                    h: self.headers.get(h)
                    for h in LOGGED_HEADERS
                    if self.headers.get(h) is not None
                },
                "body_raw": raw.decode("utf-8", "replace"),
            }
            try:
                entry["body_json"] = json.loads(raw.decode("utf-8")) if raw else None
                entry["body_parse_error"] = None
            except ValueError as exc:
                entry["body_json"] = None
                entry["body_parse_error"] = str(exc)
            with open(Handler.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

    def _send(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _error(self, status, code, message):
        self._send(status, {"code": code, "message": message})

    def _auth_mode(self):
        value = self.headers.get("Authorization")
        if not value:
            return None, None
        for name, mode in Handler.contract.auth_modes.items():
            prefix = mode["value_format"].split("{")[0]
            if value.startswith(prefix):
                return name, value[len(prefix):]
        return "Unknown", value

    # -- dispatch ---------------------------------------------------------

    def _handle(self, method):
        raw = self._read_body()
        path = self.path.split("?", 1)[0]
        self._record(method, self.path, raw)

        op, extra = Handler.contract.match(method, path)
        if op is None:
            self._error(
                404,
                40400,
                "%s %s: %s. This fixture serves only the operations named in docs/contract.json."
                % (method, path, extra),
            )
            return

        if op.get("requires_auth_header"):
            mode, token = self._auth_mode()
            if mode is None:
                self._error(401, 40100, "missing Authorization header")
                return
            if mode == "Unknown":
                self._error(401, 40101, "Authorization header does not match a contract auth mode")
                return
            allowed = op.get("auth_modes")
            if allowed and mode not in allowed:
                self._error(
                    401,
                    40102,
                    "operationId '%s' accepts only %s, got %s" % (op["operationId"], allowed, mode),
                )
                return
            if mode == "ApiKeyAuth" and token not in Handler.appliance.tokens:
                self._error(401, 40103, "unknown session token")
                return

        body_spec = op.get("request_body")
        parsed = None
        if body_spec is None:
            if raw:
                self._error(400, 40004, "operationId '%s' takes no request body" % op["operationId"])
                return
        else:
            if not raw:
                self._error(400, 40005, "missing request body")
                return
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != Handler.contract.rules["content_type"]:
                self._error(415, 41500, "expected Content-Type %s, got '%s'"
                            % (Handler.contract.rules["content_type"], ctype))
                return
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError as exc:
                self._error(400, 40000, "body is not valid JSON: %s" % exc)
                return

            nulls = find_nulls(parsed)
            if nulls:
                self._error(
                    400,
                    40001,
                    "null values are not permitted; omit unset fields instead (at %s)"
                    % ", ".join(nulls),
                )
                return

            errors = validate_object(parsed, body_spec, "")
            if errors:
                self._error(400, 40002, "contract violation: " + "; ".join(errors))
                return

        getattr(self, "op_" + op["operationId"])(parsed, extra)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    # -- operations -------------------------------------------------------

    def op_create(self, body, extra):
        opts = Handler.appliance.opts
        if opts.fail_operation == "create":
            self._error(opts.fail_status, 40110, opts.fail_message)
            return
        token = Handler.appliance.issue_token()
        self._send(200, {"token": token, "expiry": 1793491200000})

    def op_delete(self, body, extra):
        _, token = self._auth_mode()
        Handler.appliance.tokens.discard(token)
        self._send(204)

    def op_addApplication(self, body, extra):
        opts = Handler.appliance.opts
        if opts.fail_operation == "addApplication":
            self._error(opts.fail_status, 40020, opts.fail_message)
            return
        entity_id = Handler.appliance.add_application(body["name"])
        self._send(
            201,
            {
                "entity_id": entity_id,
                "name": body["name"],
                "entity_type": "Application",
                "create_time": 1793491200000,
                "created_by": "fixture@local",
            },
        )

    def op_addTier(self, body, extra):
        app_id = extra["id"]
        if app_id not in Handler.appliance.applications:
            self._error(404, 40401, "no such application '%s'" % app_id)
            return
        opts = Handler.appliance.opts
        if opts.fail_tier and body["name"] == opts.fail_tier:
            self._error(opts.fail_status, 40010, opts.fail_message)
            return
        entity_id = Handler.appliance.add_tier(app_id, body["name"])
        self._send(201, {"entity_id": entity_id, "name": body["name"], "entity_type": "Tier"})

    def op_listApplicationTiers(self, body, extra):
        app_id = extra["id"]
        app = Handler.appliance.applications.get(app_id)
        if app is None:
            self._error(404, 40401, "no such application '%s'" % app_id)
            return
        self._send(
            200,
            {
                "results": [
                    {"entity_id": t["entity_id"], "name": t["name"], "entity_type": "Tier"}
                    for t in app["tiers"]
                ]
            },
        )


# --------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--fail-tier")
    ap.add_argument("--fail-operation", choices=("create", "addApplication"))
    ap.add_argument("--fail-status", type=int, default=400)
    ap.add_argument(
        "--fail-message",
        default="Tier membership criteria could not be resolved against the current inventory.",
    )
    opts = ap.parse_args()

    with open(opts.contract, encoding="utf-8") as fh:
        contract = Contract(json.load(fh))

    open(opts.log, "w", encoding="utf-8").close()

    Handler.contract = contract
    Handler.appliance = Appliance(opts)
    Handler.log_path = opts.log

    httpd = ThreadingHTTPServer(("127.0.0.1", opts.port), Handler)
    sys.stdout.write("PORT %d\n" % httpd.server_address[1])
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
