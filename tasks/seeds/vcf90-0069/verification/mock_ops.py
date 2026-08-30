"""Contract-pinned loopback stand-in for a VCF 9.0 Operations (Suite API) endpoint.

The routing table, the accepted request-body members and the response shapes are
read out of docs/contract.json, which is itself a projection of
specifications/vcf-operations/vcf-operations-openapi.json at tag 9.0.0.0 of
vmware/vcf-api-specs. Only the four operations that contract names are served;
every other target answers 404 out of the document's own `error` envelope.

The fixture is deliberately faithful about one thing: `createCustomGroup` is not
idempotent. The specification gives it no idempotency key, no conditional-request
header and no 409 for a duplicate name, so this fixture files a brand new group
with a brand new identifier on every POST, even when a group with the same
resource key already exists. Making the operation safe to repeat is the client's
job, not the appliance's.

Two more details are modelled on purpose, because a real appliance behaves this
way and a naive client mistakes them for drift:

  * a group read back from the appliance always carries all four
    `custom-group-membership` members, empty collections included, whatever the
    create body omitted; and
  * a `resource-key` read back always carries `resourceIdentifiers` and `links`,
    which no client ever sent.

Every request is appended to a JSONL log so a test can assert the exact wire
shape a client produced.

This module is part of the protected acceptance harness. It binds 127.0.0.1 only
and never talks to a real VMware endpoint.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT_PATH = os.path.join(HERE, "docs", "contract.json")


def _load_contract():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


CONTRACT = _load_contract()
BASE_PATH = CONTRACT["server"]["basePath"]

# (METHOD, target) -> operationId, taken from the contract and nowhere else.
ROUTES = {}
for _op in CONTRACT["operations"]:
    ROUTES[(_op["method"], BASE_PATH + _op["path"])] = _op["operationId"]

# Query parameters getCustomGroups is allowed to receive, per the contract.
_LIST_OP = next(o for o in CONTRACT["operations"] if o["operationId"] == "getCustomGroups")
LIST_QUERY_PARAMS = frozenset(p["name"] for p in _LIST_OP["parameters"])

_SCHEMAS = CONTRACT["schemas"]
CUSTOM_GROUP_MEMBERS = frozenset(_SCHEMAS["custom-group"]["properties"])
CUSTOM_GROUP_REQUIRED = frozenset(_SCHEMAS["custom-group"]["required"])
RESOURCE_KEY_MEMBERS = frozenset(_SCHEMAS["resource-key"]["properties"])
RESOURCE_KEY_REQUIRED = frozenset(_SCHEMAS["resource-key"]["required"])
MEMBERSHIP_MEMBERS = frozenset(_SCHEMAS["custom-group-membership"]["properties"])
CREDENTIAL_MEMBERS = frozenset(_SCHEMAS["username-password"]["properties"])
CREDENTIAL_REQUIRED = frozenset(_SCHEMAS["username-password"]["required"])

AUTH_HEADER = CONTRACT["securitySchemes"]["Token-based-authorization"]["name"]
TOKEN_PREFIX = CONTRACT["clientConventions"]["authorizationValueTemplate"].split("{")[0]

# Credentials the fixture accepts. Dummy values, local to this harness.
VALID_USER = "svc-groupsync"
VALID_PASSWORD = "Fixture-Passw0rd!"
VALID_AUTH_SOURCE = "corp-ldap"

# Identifiers the fixture hands out, in order. Deterministic so a test can name them.
ID_TEMPLATE = "00000000-0000-4000-8000-%012d"


class _Rejected(Exception):
    def __init__(self, status, message, violation_path=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.violation_path = violation_path


def _error(status, message, violation_path=None):
    """An `error` as the pinned document declares it."""
    payload = {
        "message": message,
        "httpStatusCode": status,
        "apiErrorCode": status * 100,
        "type": "Error",
    }
    if violation_path:
        payload["validationFailures"] = [
            {"failureMessage": message, "violationPath": violation_path}
        ]
    return payload


def _reject_nulls(node, path):
    """A typed member sent as null is not a member the caller left unset."""
    if isinstance(node, dict):
        for key, value in node.items():
            if value is None:
                raise _Rejected(400, "Member %s.%s was sent as null. An unset optional "
                                     "member must be omitted." % (path, key),
                                "%s.%s" % (path, key))
            _reject_nulls(value, "%s.%s" % (path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_nulls(value, "%s[%d]" % (path, index))


def _validate_custom_group(body, expect_id):
    if not isinstance(body, dict):
        raise _Rejected(400, "The request body must be a custom-group object.", "$")
    _reject_nulls(body, "custom-group")

    unknown = sorted(set(body) - CUSTOM_GROUP_MEMBERS)
    if unknown:
        raise _Rejected(400, "custom-group carries members the specification does not "
                             "declare: %s." % ", ".join(unknown), "custom-group")
    missing = sorted(CUSTOM_GROUP_REQUIRED - set(body))
    if missing:
        raise _Rejected(400, "custom-group is missing required member(s): %s."
                        % ", ".join(missing), "custom-group")
    if expect_id and "id" not in body:
        raise _Rejected(400, "modifyCustomGroup addresses an existing group by its id.",
                        "custom-group.id")
    if not expect_id and "id" in body:
        raise _Rejected(400, "createCustomGroup assigns the identifier; the request must "
                             "not carry custom-group.id.", "custom-group.id")

    key = body["resourceKey"]
    if not isinstance(key, dict):
        raise _Rejected(400, "custom-group.resourceKey must be a resource-key object.",
                        "custom-group.resourceKey")
    unknown = sorted(set(key) - RESOURCE_KEY_MEMBERS)
    if unknown:
        raise _Rejected(400, "resource-key carries members the specification does not "
                             "declare: %s." % ", ".join(unknown), "custom-group.resourceKey")
    missing = sorted(RESOURCE_KEY_REQUIRED - set(key))
    if missing:
        raise _Rejected(400, "resource-key is missing required member(s): %s."
                        % ", ".join(missing), "custom-group.resourceKey")

    membership = body["membershipDefinition"]
    if not isinstance(membership, dict):
        raise _Rejected(400, "custom-group.membershipDefinition must be a "
                             "custom-group-membership object.",
                        "custom-group.membershipDefinition")
    unknown = sorted(set(membership) - MEMBERSHIP_MEMBERS)
    if unknown:
        raise _Rejected(400, "custom-group-membership carries members the specification "
                             "does not declare: %s." % ", ".join(unknown),
                        "custom-group.membershipDefinition")
    for rule in membership.get("rules", []):
        if not isinstance(rule, dict) or "resourceKindKey" not in rule:
            raise _Rejected(400, "membership-rule-group requires resourceKindKey.",
                            "custom-group.membershipDefinition.rules")
    return body


def _stored(record):
    """Render a stored group the way the appliance reports one.

    All four membership members come back, empty collections included, and the
    resource key gains the identifiers and links the client never sent.
    """
    key = record["resourceKey"]
    membership = record["membershipDefinition"]
    out = {
        "id": record["id"],
        "resourceKey": {
            "name": key["name"],
            "adapterKindKey": key["adapterKindKey"],
            "resourceKindKey": key["resourceKindKey"],
            "resourceIdentifiers": [],
            "links": [],
        },
        "autoResolveMembership": bool(record.get("autoResolveMembership", False)),
        "membershipDefinition": {
            "includedResources": list(membership.get("includedResources", [])),
            "excludedResources": list(membership.get("excludedResources", [])),
            "custom-group-properties": list(membership.get("custom-group-properties", [])),
            "rules": json.loads(json.dumps(membership.get("rules", []))),
        },
        "links": [
            {
                "href": "%s/api/resources/groups/%s" % (BASE_PATH, record["id"]),
                "rel": "SELF",
                "name": "linkToSelf",
            }
        ],
    }
    if record.get("policy"):
        out["policy"] = record["policy"]
    return out


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockVcfOperations/9.0.0.0"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def _respond(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _fail(self, exc):
        self._respond(exc.status, _error(exc.status, exc.message, exc.violation_path))

    def _record(self, method, parsed, raw_body, operation_id):
        try:
            parsed_body = json.loads(raw_body.decode("utf-8")) if raw_body else None
            body_is_json = True
        except (ValueError, UnicodeDecodeError):
            parsed_body = None
            body_is_json = False
        self.server.append_log({
            "seq": self.server.next_seq(),
            "operationId": operation_id,
            "method": method,
            "target": self.path,
            "path": parsed.path,
            "query": parsed.query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "raw_body": raw_body.decode("utf-8", "replace"),
            "body": parsed_body,
            "body_is_json": body_is_json,
        })

    # -- dispatch ---------------------------------------------------------
    def _dispatch(self, method):
        parsed = urlparse(self.path)
        raw_body = self._read_body()
        operation_id = ROUTES.get((method, parsed.path))
        self._record(method, parsed, raw_body, operation_id)

        try:
            if operation_id is None:
                raise _Rejected(404, "No operation in the pinned 9.0.0.0 contract serves "
                                     "%s %s." % (method, self.path))
            self._check_negotiation(raw_body)
            forced = self.server.pop_forced_error(operation_id)
            if forced is not None:
                status, message = forced
                message = message.replace(
                    "{authorization}", self.headers.get(AUTH_HEADER, ""))
                raise _Rejected(status, message)
            handler = {
                "acquireToken": self._acquire_token,
                "getCustomGroups": self._get_custom_groups,
                "createCustomGroup": self._create_custom_group,
                "modifyCustomGroup": self._modify_custom_group,
            }[operation_id]
            handler(parsed, raw_body)
        except _Rejected as exc:
            self._fail(exc)

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

    # -- shared preconditions ---------------------------------------------
    def _check_negotiation(self, raw_body):
        accept = self.headers.get("Accept")
        if accept is not None:
            offered = {part.split(";")[0].strip().lower() for part in accept.split(",")}
            if not offered & {"application/json", "application/*", "*/*"}:
                raise _Rejected(406, "Every operation offers application/json and "
                                     "application/xml; %r accepts neither." % accept)
        if raw_body:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise _Rejected(415, "A JSON request body must be sent as "
                                     "application/json; got %r." % ctype)

    def _parse_json_body(self, raw_body):
        if not raw_body:
            raise _Rejected(400, "A request body is required.", "$")
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise _Rejected(400, "The request body is not well-formed JSON.", "$")

    def _require_token(self):
        header = self.headers.get(AUTH_HEADER)
        if not header:
            raise _Rejected(401, "The Token-based-authorization header is missing.")
        if not header.startswith(TOKEN_PREFIX):
            raise _Rejected(401, "The Authorization header must carry a "
                                 "%stoken." % TOKEN_PREFIX)
        token = header[len(TOKEN_PREFIX):].strip()
        if not self.server.token_is_live(token):
            raise _Rejected(401, "The token is not valid.")
        return token

    # -- operations -------------------------------------------------------
    def _acquire_token(self, parsed, raw_body):
        if self.headers.get(AUTH_HEADER):
            raise _Rejected(400, "acquireToken is what mints the token; it must not "
                                 "carry an Authorization header.")
        body = self._parse_json_body(raw_body)
        if not isinstance(body, dict):
            raise _Rejected(400, "The request body must be a username-password object.", "$")
        _reject_nulls(body, "username-password")
        unknown = sorted(set(body) - CREDENTIAL_MEMBERS)
        if unknown:
            raise _Rejected(400, "username-password carries members the specification does "
                                 "not declare: %s." % ", ".join(unknown), "username-password")
        missing = sorted(CREDENTIAL_REQUIRED - set(body))
        if missing:
            raise _Rejected(400, "username-password is missing required member(s): %s."
                            % ", ".join(missing), "username-password")

        auth_source = body.get("authSource")
        if auth_source is not None and auth_source != VALID_AUTH_SOURCE:
            raise _Rejected(401, "Authentication failed.")
        if body["username"] != VALID_USER or body["password"] != VALID_PASSWORD:
            raise _Rejected(401, "Authentication failed.")

        token, validity = self.server.issue_token()
        self._respond(200, {
            "token": token,
            "validity": validity,
            "expiresAt": "2026-01-01T00:00:00.000",
            "roles": ["ContentAdmin"],
        })

    def _get_custom_groups(self, parsed, raw_body):
        self._require_token()
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        unknown = sorted({name for name, _ in pairs} - LIST_QUERY_PARAMS)
        if unknown:
            raise _Rejected(400, "getCustomGroups declares only %s; got %s."
                            % (", ".join(sorted(LIST_QUERY_PARAMS)), ", ".join(unknown)))
        for name, value in pairs:
            if value == "":
                raise _Rejected(400, "Query parameter %s was sent empty. An unset optional "
                                     "parameter must be absent from the query string."
                                % name)
        wanted = [value for name, value in pairs if name == "groupId"]
        groups = self.server.list_groups()
        if wanted:
            groups = [g for g in groups if g["id"] in wanted]
        self._respond(200, {"groups": [_stored(g) for g in groups]})

    def _create_custom_group(self, parsed, raw_body):
        self._require_token()
        body = _validate_custom_group(self._parse_json_body(raw_body), expect_id=False)
        # No idempotency key exists in the 9.0.0.0 document, so this always files
        # a new group, duplicate resource key or not.
        record = self.server.add_group(body)
        self._respond(201, _stored(record))

    def _modify_custom_group(self, parsed, raw_body):
        self._require_token()
        body = _validate_custom_group(self._parse_json_body(raw_body), expect_id=True)
        record = self.server.replace_group(body)
        if record is None:
            raise _Rejected(404, "No custom group with id %s." % body["id"],
                            "custom-group.id")
        self._respond(200, _stored(record))


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, log_path):
        super().__init__(address, handler)
        self.log_path = log_path
        self._lock = threading.Lock()
        self._seq = 0
        self._tokens = set()
        self._issued = 0
        self._forced_errors = {}
        self._groups = []
        self._next_group = 0

    # log ------------------------------------------------------------------
    def next_seq(self):
        with self._lock:
            self._seq += 1
            return self._seq

    def append_log(self, entry):
        line = json.dumps(entry, sort_keys=True)
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    # tokens ---------------------------------------------------------------
    def issue_token(self):
        with self._lock:
            self._issued += 1
            token = "fixture-token-%04d" % self._issued
            self._tokens.add(token)
            # Fixed instant so the response body is byte-stable across runs.
            return token, 1767225600000

    def token_is_live(self, token):
        with self._lock:
            return token in self._tokens

    # deterministic fault injection ---------------------------------------
    def fail_next(self, operation_id, status, message):
        with self._lock:
            self._forced_errors[operation_id] = (status, message)

    def pop_forced_error(self, operation_id):
        with self._lock:
            return self._forced_errors.pop(operation_id, None)

    # groups ---------------------------------------------------------------
    def reset_groups(self, seeds):
        with self._lock:
            self._forced_errors.clear()
            self._groups = []
            self._next_group = 0
            for seed in seeds or []:
                self._next_group += 1
                record = json.loads(json.dumps(seed))
                record["id"] = ID_TEMPLATE % self._next_group
                self._groups.append(record)

    def add_group(self, body):
        with self._lock:
            self._next_group += 1
            record = json.loads(json.dumps(body))
            record["id"] = ID_TEMPLATE % self._next_group
            self._groups.append(record)
            return record

    def replace_group(self, body):
        with self._lock:
            for index, existing in enumerate(self._groups):
                if existing["id"] == body["id"]:
                    record = json.loads(json.dumps(body))
                    self._groups[index] = record
                    return record
            return None

    def list_groups(self):
        with self._lock:
            return json.loads(json.dumps(self._groups))


class MockOperations:
    """Loopback VCF Operations fixture. Use as a context manager."""

    def __init__(self, log_path):
        self.log_path = log_path
        self._server = None
        self._thread = None

    def __enter__(self):
        open(self.log_path, "w", encoding="utf-8").close()
        self._server = _Server(("127.0.0.1", 0), _Handler, self.log_path)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)
        return False

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def base_url(self):
        """The specification's server template, rooted at the loopback fixture."""
        return "http://127.0.0.1:%d%s" % (self.port, BASE_PATH)

    def requests(self):
        """Every request the fixture has seen, in arrival order."""
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        entries.sort(key=lambda entry: entry["seq"])
        return entries

    def truncate_log(self):
        open(self.log_path, "w", encoding="utf-8").close()

    def reset(self, groups=None):
        """Seed the appliance state. Seeded groups get ids in the order given."""
        self._server.reset_groups(groups)

    def fail_next(self, operation_id, status, message):
        """Make the next named operation answer one deterministic error."""
        self._server.fail_next(operation_id, status, message)

    def groups(self):
        """The appliance's own view of every group it currently holds."""
        return [_stored(record) for record in self._server.list_groups()]


if __name__ == "__main__":
    import tempfile
    import time

    with MockOperations(os.path.join(tempfile.gettempdir(), "mock_ops.log")) as mock:
        mock.reset()
        print(mock.base_url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
