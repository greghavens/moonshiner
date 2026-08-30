"""Loopback mock of a VCF Operations for Networks 9.1 appliance.

The mock is pinned to docs/contract.json. It builds its route table and its
request validators out of that file, so it serves only the four operations the
contract names and it accepts only the properties those operations declare.
Anything else -- another path, another method, a property that belongs to a
neighbouring schema, an optional field sent as null or empty -- is rejected and
still recorded, so a test can prove the client did not invent traffic.

Every request is appended to a JSON Lines log that the test reads back.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

_PATH_PARAM = re.compile(r"\{[^/}]+\}")


def load_contract(path=CONTRACT_PATH):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_routes(contract):
    """Turn contract operations into method + compiled path regex + operationId."""
    routes = []
    for operation in contract["operations"]:
        literals = _PATH_PARAM.split(operation["path"])
        pattern = "^" + "([^/]+)".join(re.escape(literal) for literal in literals) + "$"
        routes.append(
            {
                "operation_id": operation["operationId"],
                "method": operation["method"].upper(),
                "path_template": operation["path"],
                "regex": re.compile(pattern),
                "has_params": "{" in operation["path"],
                "request": operation.get("request"),
            }
        )
    # Literal paths win over templated ones, so a fixed sub-resource is never
    # swallowed by a sibling path parameter.
    routes.sort(key=lambda route: route["has_params"])
    return routes


# ---------------------------------------------------------------------------
# contract-driven body validation
# ---------------------------------------------------------------------------


def _api_error(code, message, details=None):
    body = {"code": code, "message": message}
    if details:
        body["details"] = details
    return body


def _detail(code, message, target):
    return [{"code": code, "message": message, "target": [target]}]


def _allowed_keys(schema):
    closed = schema.get("closed_property_set")
    if closed:
        return list(closed)
    return list(schema.get("properties", {}))


def _check_object(value, schema, where):
    """Validate one JSON object against a contract request/property node.

    Returns an ApiError body, or None when the object is acceptable.
    """
    if not isinstance(value, dict):
        return _api_error(400, "%s must be a JSON object" % where)

    properties = schema.get("properties", {})
    allowed = set(_allowed_keys(schema))

    for key, item in sorted(value.items()):
        label = "%s.%s" % (where, key) if where else key
        if key not in allowed:
            return _api_error(
                400,
                "Field '%s' is not a member of schema '%s'" % (key, schema.get("schema", where)),
                _detail(4004, "unknown field for this operation", label),
            )
        if item is None:
            return _api_error(
                400,
                "Field '%s' was sent as null; an unset optional field must be omitted" % label,
                _detail(4005, "null is not an assignment", label),
            )
        if item == "" or item == {} or item == []:
            return _api_error(
                400,
                "Field '%s' was sent empty; an unset optional field must be omitted" % label,
                _detail(4006, "empty value is not an assignment", label),
            )
        declared = properties.get(key, {})
        if declared.get("type") == "object":
            nested = _check_object(item, declared, label)
            if nested is not None:
                return nested
        elif declared.get("type") == "boolean" and not isinstance(item, bool):
            return _api_error(
                400,
                "Field '%s' must be a JSON boolean" % label,
                _detail(4007, "wrong JSON type", label),
            )
        elif declared.get("type") == "string" and not isinstance(item, str):
            return _api_error(
                400,
                "Field '%s' must be a JSON string" % label,
                _detail(4007, "wrong JSON type", label),
            )
        enum = declared.get("enum")
        if enum and item not in enum:
            return _api_error(
                400,
                "Field '%s' must be one of %s" % (label, ", ".join(enum)),
                _detail(4008, "value outside enum", label),
            )

    for key, declared in sorted(properties.items()):
        if declared.get("required") and key not in value:
            label = "%s.%s" % (where, key) if where else key
            return _api_error(
                400,
                "Missing required field '%s'" % label,
                _detail(4003, "required field absent", label),
            )
    return None


def _check_host_choice(body):
    """One of IP or FQDN, never both, never neither."""
    has_ip = "ip" in body
    has_fqdn = "fqdn" in body
    if has_ip and has_fqdn:
        return _api_error(
            400,
            "Provide one of IP or FQDN field in the request body, not both",
            _detail(4002, "ip and fqdn are mutually exclusive", "fqdn"),
        )
    if not has_ip and not has_fqdn:
        return _api_error(
            400,
            "You must provide one of IP or FQDN field in the request body",
            _detail(4002, "ip or fqdn is required", "ip"),
        )
    return None


# ---------------------------------------------------------------------------
# fixture state
# ---------------------------------------------------------------------------


class ApplianceConfig:
    """Fixture state for one scenario.

    ``vcenter_passwords`` maps a vCenter host to the password the simulated
    vCenter actually accepts. A plan that presents a different password gets the
    appliance's soft rejection: HTTP 200 carrying a validation body whose own
    ``code`` is not 200.
    """

    def __init__(
        self,
        username,
        password,
        domain=None,
        token="Mgs2YX0ZSY+gHW6RYypeeA==",
        expiry=1605201960327,
        known_proxy_ids=(),
        vcenter_passwords=None,
        registered_hosts=(),
        first_entity_id=993642895,
    ):
        self.username = username
        self.password = password
        self.domain = domain
        self.token = token
        self.expiry = expiry
        self.known_proxy_ids = set(known_proxy_ids)
        self.vcenter_passwords = dict(vcenter_passwords or {})
        self.registered_hosts = list(registered_hosts)
        self.first_entity_id = first_entity_id


class _Appliance:
    """Mutable server-side state, guarded by a lock."""

    def __init__(self, config, log_path):
        self.config = config
        self.log_path = log_path
        self.lock = threading.Lock()
        self.datasources = []
        self.hosts = list(config.registered_hosts)
        self.issued_token = None
        self.seq = 0
        with open(self.log_path, "w", encoding="utf-8"):
            pass

    def record(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            line = json.dumps(entry, sort_keys=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def next_entity_id(self):
        return "18230:902:%d" % (self.config.first_entity_id + len(self.datasources))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VCFOperationsForNetworks/9.1.0.0"
    sys_version = ""

    # -- plumbing ------------------------------------------------------------
    def log_message(self, format, *args):  # noqa: A002 - signature fixed by base class
        pass

    @property
    def appliance(self):
        return self.server.appliance

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def _respond(self, status, payload=None):
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _dispatch(self):
        split = urlsplit(self.path)
        raw_body = self._read_body()
        try:
            body_json = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except ValueError:
            body_json = None

        route = None
        params = ()
        for candidate in self.server.routes:
            match = candidate["regex"].match(split.path)
            if match and candidate["method"] == self.command:
                route = candidate
                params = tuple(unquote(value) for value in match.groups())
                break

        self.appliance.record(
            {
                "operation_id": route["operation_id"] if route else None,
                "method": self.command,
                "path": split.path,
                "query": split.query,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body_raw": raw_body.decode("utf-8", "replace"),
                "body_json": body_json,
            }
        )

        if route is None:
            self._respond(
                404,
                _api_error(
                    404,
                    "Operation not served by this appliance mock: %s %s"
                    % (self.command, split.path),
                ),
            )
            return

        getattr(self, "_op_" + route["operation_id"])(route, params, body_json)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch

    # -- shared checks -------------------------------------------------------
    def _authenticated(self):
        expected = self.appliance.issued_token
        header = self.headers.get("Authorization")
        if expected is None or header != "NetworkInsight " + expected:
            self._respond(401, _api_error(401, "Invalid or expired token"))
            return False
        return True

    def _validated_body(self, route, body):
        """Contract-shape check. Responds and returns None when it fails."""
        schema = route.get("request") or {}
        error = _check_object(body, schema, "")
        if error is None and "ip" in _allowed_keys(schema):
            error = _check_host_choice(body)
        if error is not None:
            self._respond(400, error)
            return None
        return body

    # -- Authentication ------------------------------------------------------
    def _op_create(self, route, params, body):
        if self.headers.get("Authorization") is not None:
            self._respond(
                400,
                _api_error(400, "Authorization header must not be sent when creating a token"),
            )
            return
        if self._validated_body(route, body) is None:
            return
        config = self.appliance.config
        expected_domain = config.domain
        if body.get("domain") != expected_domain:
            self._respond(401, _api_error(401, "Unknown authentication domain"))
            return
        if body["username"] != config.username or body["password"] != config.password:
            self._respond(401, _api_error(401, "Invalid credentials"))
            return
        self.appliance.issued_token = config.token
        self._respond(200, {"token": config.token, "expiry": config.expiry})

    def _op_delete(self, route, params, body):
        if not self._authenticated():
            return
        if body is not None:
            self._respond(400, _api_error(400, "Token deletion takes no request body"))
            return
        self.appliance.issued_token = None
        self._respond(204)

    # -- Data Sources --------------------------------------------------------
    def _host_of(self, body):
        return body.get("ip") if "ip" in body else body.get("fqdn")

    def _proxy_error(self, body):
        proxy_id = body.get("proxy_id")
        if proxy_id not in self.appliance.config.known_proxy_ids:
            return _api_error(
                400,
                "Proxy node '%s' was not found in /infra/nodes" % proxy_id,
                _detail(4101, "unknown collector VM", "proxy_id"),
            )
        return None

    def _op_validateVCenter(self, route, params, body):
        if not self._authenticated():
            return
        if self._validated_body(route, body) is None:
            return
        error = self._proxy_error(body)
        if error is not None:
            # A malformed request is a transport-level rejection, not a verdict.
            self._respond(400, error)
            return
        host = self._host_of(body)
        expected = self.appliance.config.vcenter_passwords.get(host)
        presented = body.get("credentials", {}).get("password")
        if expected is not None and presented != expected:
            # Well-formed request, failed verdict: HTTP 200, body code 401.
            self._respond(
                200,
                {
                    "code": 401,
                    "message": "Cannot complete login to '%s' due to an incorrect user name or password."
                    % host,
                },
            )
            return
        self._respond(200, {"code": 200, "message": "Validation successful."})

    def _op_addVcenterDatasource(self, route, params, body):
        if not self._authenticated():
            return
        if self._validated_body(route, body) is None:
            return
        error = self._proxy_error(body)
        if error is not None:
            self._respond(400, error)
            return
        host = self._host_of(body)
        with self.appliance.lock:
            if host in self.appliance.hosts:
                self._respond(
                    400,
                    _api_error(
                        400,
                        "A data source for '%s' is already registered" % host,
                        _detail(4102, "duplicate data source", "ip"),
                    ),
                )
                return
            entity_id = self.appliance.next_entity_id()
            record = {
                "entity_id": entity_id,
                "entity_type": "VCenterDataSource",
                "proxy_id": body["proxy_id"],
                "nickname": body["nickname"],
                "enabled": body.get("enabled", True),
            }
            if "ip" in body:
                record["ip"] = body["ip"]
            else:
                record["fqdn"] = body["fqdn"]
            if "notes" in body:
                record["notes"] = body["notes"]
            if "is_vmc" in body:
                record["is_vmc"] = body["is_vmc"]
            record["credentials"] = {
                "username": body.get("credentials", {}).get("username", ""),
                "password": "",
            }
            if "ipfix_request" in body:
                enabled_for = body["ipfix_request"].get("enable_for_dvs")
                if body["ipfix_request"].get("enable_all"):
                    enabled_for = "ALL"
                record["ipfix_response"] = {"ipfix_enabled_for": enabled_for or ""}
            self.appliance.datasources.append(record)
            self.appliance.hosts.append(host)
        self._respond(201, record)


class MockAppliance:
    """A running loopback appliance. Use as a context manager."""

    def __init__(self, config, log_path, contract_path=CONTRACT_PATH):
        self.contract = load_contract(contract_path)
        self.routes = build_routes(self.contract)
        self.appliance = _Appliance(config, log_path)
        self.log_path = log_path
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.routes = self.routes
        self._server.appliance = self.appliance
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return "http://%s:%d" % (host, port)

    @property
    def datasources(self):
        """Everything addVcenterDatasource actually created, in creation order."""
        return [dict(entry) for entry in self.appliance.datasources]

    @property
    def token_outstanding(self):
        return self.appliance.issued_token is not None

    def requests(self):
        """Read the request log back, in arrival order."""
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        entries.sort(key=lambda entry: entry["seq"])
        return entries

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False
