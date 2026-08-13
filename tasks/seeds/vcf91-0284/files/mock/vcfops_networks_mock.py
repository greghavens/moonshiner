#!/usr/bin/env python3
"""Loopback stand-in for a VCF Operations for Networks 9.1 appliance.

Routing and request validation are driven entirely by ``docs/contract.json``,
which is derived from the upstream OpenAPI document (see
``docs/official_sources.json``).  Only the operations that the contract names
are served; every other path is a 404.  The appliance state -- credentials,
the tokens it hands out, when it revokes the first one, the applications that
already exist -- comes from ``mock/fixtures/appliance-state.json``.

Every request is appended to a JSON Lines log so a test can assert the exact
wire shape a client produced.  Binds to the loopback interface only.

Usage:
    python3 mock/vcfops_networks_mock.py \
        --contract docs/contract.json \
        --state mock/fixtures/appliance-state.json \
        --log /tmp/requests.jsonl [--port 0] [--port-file /tmp/port]
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# The mock rejects request properties the contract does not define.  OpenAPI
# object schemas here do not set additionalProperties, but an appliance that
# silently swallowed unknown fields would make this a useless stand-in.
UNKNOWN_PROPERTY_IS_AN_ERROR = True


class SchemaError(Exception):
    pass


class Contract:
    def __init__(self, path):
        with open(path, encoding="utf-8") as fh:
            self.doc = json.load(fh)
        self.schemas = self.doc["schemas"]
        self.base = self.doc["serverBasePath"].rstrip("/")
        self.header_name = self.doc["securityScheme"]["headerName"]
        fmt = self.doc["securityScheme"]["headerValueFormat"]
        if "{token}" not in fmt:
            raise SystemExit("contract security scheme has no {token} placeholder")
        self.auth_prefix = fmt.split("{token}")[0]
        self.routes = {}
        for op_id, op in self.doc["operations"].items():
            self.routes[(op["method"], self.base + op["path"])] = op

    def resolve(self, schema):
        seen = 0
        while isinstance(schema, dict) and "$ref" in schema:
            seen += 1
            if seen > 32:
                raise SchemaError("$ref cycle")
            name = schema["$ref"].rsplit("/", 1)[1]
            if name not in self.schemas:
                raise SchemaError("unknown schema %s" % name)
            schema = self.schemas[name]
        return schema

    def validate(self, value, schema, where="body"):
        """Minimal OpenAPI 3.0 validation, enough to keep a client honest."""
        schema = self.resolve(schema)
        if not isinstance(schema, dict):
            return

        if "allOf" in schema:
            for sub in schema["allOf"]:
                self.validate(value, sub, where)
            return

        if value is None:
            if schema.get("nullable"):
                return
            # A JSON null for a typed property is the client asserting "this
            # field has no value" -- the contract's way to say that is to leave
            # the property out.  Accepted here so the wire log stays faithful;
            # it is the test's job to object.
            return

        expected = schema.get("type")
        if expected == "object":
            if not isinstance(value, dict):
                raise SchemaError("%s: expected an object" % where)
            props = schema.get("properties", {})
            for key, sub in value.items():
                if key not in props:
                    if UNKNOWN_PROPERTY_IS_AN_ERROR:
                        raise SchemaError(
                            "%s: property '%s' is not defined by the contract" % (where, key)
                        )
                    continue
                self.validate(value[key], sub, "%s.%s" % (where, key))
        elif expected == "array":
            if not isinstance(value, list):
                raise SchemaError("%s: expected an array" % where)
            for i, item in enumerate(value):
                self.validate(item, schema.get("items", {}), "%s[%d]" % (where, i))
        elif expected == "string":
            if not isinstance(value, str):
                raise SchemaError("%s: expected a string" % where)
        elif expected == "boolean":
            if not isinstance(value, bool):
                raise SchemaError("%s: expected a boolean, got %r" % (where, value))
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise SchemaError("%s: expected an integer" % where)
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError("%s: expected a number" % where)

        enum = schema.get("enum")
        if enum is not None and value not in enum:
            raise SchemaError(
                "%s: %r is not one of the permitted values" % (where, value)
            )


class Appliance:
    """Mutable appliance state.  Seeded from the fixture, never written back."""

    def __init__(self, fixture_path):
        with open(fixture_path, encoding="utf-8") as fh:
            self.fixture = json.load(fh)
        creds = self.fixture["credentials"]
        self.username = creds["username"]
        self.password = creds["password"]
        self.domain_type = creds["domain_type"]
        self.domain_value = creds.get("domain_value")

        tokens = self.fixture["tokens"]
        self.issue_order = list(tokens["issue_order"])
        self.expiry = tokens["expiry"]
        self.revoke_after = tokens["revoke_after_successful_creates"]

        self.max_page_size = self.fixture["pagination"]["max_page_size"]

        self.applications = [dict(a) for a in self.fixture["existing_applications"]]
        template = self.fixture["created_application"]
        self.next_seq = template["next_entity_id_seq"]
        self.template = template

        self.issued = []          # tokens handed out, in order
        self.revoked = set()
        self.creates_served = {}  # token -> successful addApplicationWithTiers count
        self.lock = threading.Lock()

    def issue_token(self):
        if len(self.issued) >= len(self.issue_order):
            raise SchemaError("client asked for more tokens than the fixture provides")
        token = self.issue_order[len(self.issued)]
        self.issued.append(token)
        self.creates_served[token] = 0
        return {"token": token, "expiry": self.expiry}

    def token_is_live(self, token):
        return token in self.issued and token not in self.revoked

    def note_successful_create(self, token):
        self.creates_served[token] += 1
        # Only the first session the appliance hands out is cut short.
        if self.issued and token == self.issued[0]:
            if self.creates_served[token] >= self.revoke_after:
                self.revoked.add(token)

    def find_application(self, name):
        for app in self.applications:
            if app["entity_name"] == name:
                return app
        return None

    def add_application(self, name):
        entity_id = self.template["entity_id_format"].format(seq=self.next_seq)
        self.next_seq += 1
        record = {
            "entity_id": entity_id,
            "entity_type": "Application",
            "entity_name": name,
        }
        self.applications.append(record)
        return record


class RequestLog:
    def __init__(self, path):
        self.path = path
        self.seq = 0
        self.lock = threading.Lock()
        open(self.path, "w", encoding="utf-8").close()

    def append(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()


def api_error(code, message, target=None):
    body = {"code": code, "message": message}
    if target:
        body["details"] = [{"code": code, "message": message, "target": list(target)}]
    return body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VcfOpsForNetworks/9.1.0.0"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # keep stderr quiet
        pass

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            return self.rfile.read(int(length))
        except (TypeError, ValueError):
            return b""

    def _respond(self, status, payload, log_entry):
        log_entry["status"] = status
        self.server.request_log.append(log_entry)
        if payload is None:
            data = b""
        else:
            data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _captured_headers(self):
        out = {}
        for key, value in self.headers.items():
            key = key.lower()
            if key in out:
                out[key] = out[key] + ", " + value
            else:
                out[key] = value
        return out

    # -- dispatch ---------------------------------------------------------
    def handle_one(self, method):
        parts = urlsplit(self.path)
        raw_body = self._read_body()
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else None
            body_parse_error = None
        except (UnicodeDecodeError, ValueError) as exc:
            body, body_parse_error = None, str(exc)

        entry = {
            "method": method,
            "path": parts.path,
            "query": parse_qs(parts.query, keep_blank_values=True),
            "queryRaw": parts.query,
            "headers": self._captured_headers(),
            "bodyRaw": raw_body.decode("utf-8", "replace"),
            "body": body,
        }

        contract = self.server.contract
        op = contract.routes.get((method, parts.path))
        if op is None:
            entry["operationId"] = None
            self._respond(
                404,
                api_error(404, "No operation is served at %s %s" % (method, parts.path)),
                entry,
            )
            return
        entry["operationId"] = op["operationId"]

        if body_parse_error is not None:
            self._respond(400, api_error(400, "Malformed JSON body: %s" % body_parse_error), entry)
            return

        with self.server.appliance.lock:
            try:
                handler = getattr(self, "op_" + op["operationId"])
                status, payload = handler(op, entry)
            except SchemaError as exc:
                status, payload = 400, api_error(400, str(exc))
        self._respond(status, payload, entry)

    def do_GET(self):
        self.handle_one("GET")

    def do_POST(self):
        self.handle_one("POST")

    def do_DELETE(self):
        self.handle_one("DELETE")

    def do_PUT(self):
        self.handle_one("PUT")

    def do_PATCH(self):
        self.handle_one("PATCH")

    # -- shared checks ----------------------------------------------------
    def _authenticate(self):
        """Return (token, None) or (None, (status, payload))."""
        contract = self.server.contract
        appliance = self.server.appliance
        header = self.headers.get(contract.header_name)
        if not header:
            return None, (401, api_error(401, "Missing %s header" % contract.header_name))
        if not header.startswith(contract.auth_prefix):
            return None, (
                401,
                api_error(
                    401,
                    "%s must be '%s<token>'"
                    % (contract.header_name, contract.auth_prefix),
                ),
            )
        token = header[len(contract.auth_prefix):]
        if token not in appliance.issued:
            return None, (401, api_error(401, "Unknown API token"))
        if token in appliance.revoked:
            return None, (
                401,
                api_error(401, "The API token is no longer valid; acquire a new one"),
            )
        return token, None

    def _check_query(self, op, entry):
        allowed = {p["name"] for p in op["parameters"] if p["in"] == "query"}
        for name, values in entry["query"].items():
            if name not in allowed:
                raise SchemaError("query parameter '%s' is not defined by the contract" % name)
            if len(values) > 1:
                raise SchemaError("query parameter '%s' was sent more than once" % name)

    # -- operations -------------------------------------------------------
    def op_create(self, op, entry):
        contract = self.server.contract
        appliance = self.server.appliance
        body = entry["body"]
        if not isinstance(body, dict):
            raise SchemaError("a UserCredential body is required")
        contract.validate(body, op["requestBody"]["schema"], "UserCredential")

        domain = body.get("domain") or {}
        supplied_type = domain.get("domain_type")
        if (
            body.get("username") != appliance.username
            or body.get("password") != appliance.password
            or supplied_type != appliance.domain_type
            or domain.get("value") != appliance.domain_value
        ):
            return 401, api_error(401, "Invalid username, password or domain")
        return 200, appliance.issue_token()

    def op_delete(self, op, entry):
        token, failure = self._authenticate()
        if failure:
            return failure
        self.server.appliance.revoked.add(token)
        return 204, None

    def op_listApplications(self, op, entry):
        token, failure = self._authenticate()
        if failure:
            return failure
        appliance = self.server.appliance
        self._check_query(op, entry)

        query = {k: v[0] for k, v in entry["query"].items()}
        size = 10
        if "size" in query:
            try:
                size = int(float(query["size"]))
            except ValueError:
                raise SchemaError("query parameter 'size' must be a number")
            if size < 1:
                raise SchemaError("query parameter 'size' must be positive")
        if "modifiedAfter" in query:
            try:
                float(query["modifiedAfter"])
            except ValueError:
                raise SchemaError("query parameter 'modifiedAfter' must be a number")

        offset = 0
        if "cursor" in query:
            try:
                offset = int(base64.b64decode(query["cursor"]).decode("ascii"))
            except Exception:
                raise SchemaError("query parameter 'cursor' is not a cursor this appliance issued")
            if offset < 0 or offset > len(appliance.applications):
                raise SchemaError("query parameter 'cursor' is out of range")

        limit = min(size, appliance.max_page_size)
        page = appliance.applications[offset:offset + limit]
        payload = {
            "results": [dict(a) for a in page],
            "total_count": len(appliance.applications),
        }
        nxt = offset + len(page)
        if nxt < len(appliance.applications):
            payload["cursor"] = base64.b64encode(str(nxt).encode("ascii")).decode("ascii")
        return 200, payload

    def op_addApplicationWithTiers(self, op, entry):
        token, failure = self._authenticate()
        if failure:
            return failure
        contract = self.server.contract
        appliance = self.server.appliance
        self._check_query(op, entry)

        if_match = self.headers.get("If-Match")
        if if_match is not None:
            if not re.fullmatch(r"-?\d+", if_match.strip()):
                raise SchemaError("If-Match must be the int64 lastModifiedTimestamp of the definition being modified")

        body = entry["body"]
        if not isinstance(body, dict):
            raise SchemaError("an AppWithTiersRequest body is required")
        contract.validate(body, op["requestBody"]["schema"], "AppWithTiersRequest")

        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SchemaError("AppWithTiersRequest.name is required")
        tiers = body.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            raise SchemaError("AppWithTiersRequest.tiers must contain at least one tier")
        for i, tier in enumerate(tiers):
            if not isinstance(tier, dict) or not isinstance(tier.get("name"), str) or not tier["name"].strip():
                raise SchemaError("AppWithTiersRequest.tiers[%d].name is required" % i)
            for crit in tier.get("group_membership_criteria") or []:
                kind = crit.get("membership_type")
                needed = {
                    "SearchMembershipCriteria": "search_membership_criteria",
                    "IPAddressMembershipCriteria": "ip_address_membership_criteria",
                }.get(kind)
                if needed and not crit.get(needed):
                    raise SchemaError(
                        "membership_type '%s' requires '%s'" % (kind, needed)
                    )

        if appliance.find_application(name) is not None:
            return 400, api_error(400, "An application named '%s' already exists" % name, ["name"])

        record = appliance.add_application(name)
        appliance.note_successful_create(token)

        template = appliance.template
        return 201, {
            "entity_id": record["entity_id"],
            "entity_type": "Application",
            "name": name,
            "create_time": template["create_time"],
            "created_by": template["created_by"],
            "last_modified_time": template["last_modified_time"],
            "last_modified_by": template["last_modified_by"],
            "last_modified_by_service": template["last_modified_by_service"],
            "tier_count": len(tiers),
            "member_count": sum(
                len(t.get("member_list", {}).get(k, []) or [])
                for t in tiers
                for k in ("vms", "physical_ips", "kubernetes_services")
            ),
            "update_status": template["update_status"],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit("this stand-in only binds the loopback interface")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.contract = Contract(args.contract)
    server.appliance = Appliance(args.state)
    server.request_log = RequestLog(args.log)

    port = server.server_address[1]
    if args.port_file:
        with open(args.port_file, "w", encoding="utf-8") as fh:
            fh.write(str(port))
    print("listening on http://%s:%d%s" % (args.host, port, server.contract.base), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
