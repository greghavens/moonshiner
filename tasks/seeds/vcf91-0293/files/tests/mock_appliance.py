"""Loopback mock of a VCF Operations for Networks 9.1 appliance.

The mock is pinned to docs/contract.json: it builds its route table from that
file and serves only the operations the contract names. Anything else -- a
different path, a different method, a stray query parameter route -- comes back
as an unrouted 404 and is still recorded, so a test can prove the client did not
invent traffic.

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
    """Turn contract operations into (method, compiled path regex, operationId)."""
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
            }
        )
    # Literal paths win over templated ones so /settings/syslog/send-test-log is
    # never mistaken for /settings/syslog/{ip-or-fqdn}.
    routes.sort(key=lambda route: route["has_params"])
    return routes


class ApplianceConfig:
    """Fixture state for one scenario."""

    def __init__(
        self,
        username,
        password,
        token="Mgs2YX0ZSY+gHW6RYypeeA==",
        expiry=1605201960327,
        existing_targets=(),
        unresolvable_hosts=(),
        test_results=None,
    ):
        self.username = username
        self.password = password
        self.token = token
        self.expiry = expiry
        self.existing_targets = [dict(entry) for entry in existing_targets]
        self.unresolvable_hosts = set(unresolvable_hosts)
        self.test_results = dict(test_results or {})


class _Appliance:
    """Mutable server-side state, guarded by a lock."""

    def __init__(self, config, log_path):
        self.config = config
        self.log_path = log_path
        self.lock = threading.Lock()
        self.targets = [dict(entry) for entry in config.existing_targets]
        self.issued_token = None
        self.seq = 0
        with open(self.log_path, "w", encoding="utf-8"):
            pass

    def record(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()

    def find_target(self, ip_or_fqdn):
        for entry in self.targets:
            if entry.get("ip_or_fqdn") == ip_or_fqdn:
                return entry
        return None


def _api_error(code, message, details=None):
    body = {"code": code, "message": message}
    if details:
        body["details"] = details
    return body


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
        if payload is None:
            data = b""
        else:
            data = json.dumps(payload).encode("utf-8")
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

        entry = {
            "operation_id": route["operation_id"] if route else None,
            "method": self.command,
            "path": split.path,
            "query": split.query,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body_raw": raw_body.decode("utf-8", "replace"),
            "body_json": body_json,
        }
        self.appliance.record(entry)

        if route is None:
            self._respond(
                404,
                _api_error(404, "operation not served by this mock: %s %s" % (self.command, split.path)),
            )
            return

        handler = getattr(self, "_op_" + route["operation_id"])
        handler(params, body_json)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch

    # -- authentication ------------------------------------------------------
    def _authenticated(self):
        expected = self.appliance.issued_token
        header = self.headers.get("Authorization")
        if expected is None or header != "NetworkInsight " + expected:
            self._respond(401, _api_error(401, "Invalid or expired token"))
            return False
        return True

    def _op_create(self, params, body):
        if self.headers.get("Authorization") is not None:
            self._respond(400, _api_error(400, "Authorization header must not be sent when creating a token"))
            return
        config = self.appliance.config
        if not isinstance(body, dict) or not body.get("username") or not body.get("password"):
            self._respond(400, _api_error(400, "username and password are required"))
            return
        if body["username"] != config.username or body["password"] != config.password:
            self._respond(401, _api_error(401, "Invalid credentials"))
            return
        self.appliance.issued_token = config.token
        self._respond(200, {"token": config.token, "expiry": config.expiry})

    def _op_delete(self, params, body):
        if not self._authenticated():
            return
        self.appliance.issued_token = None
        self._respond(204)

    # -- syslog settings -----------------------------------------------------
    def _op_getSyslogTargetList(self, params, body):
        if not self._authenticated():
            return
        self._respond(200, {"data": [dict(entry) for entry in self.appliance.targets]})

    def _validate_target_body(self, body):
        if not isinstance(body, dict):
            return _api_error(400, "A SyslogTarget body is required")
        for name in ("ip_or_fqdn", "port", "protocol"):
            if name not in body:
                return _api_error(400, "Missing required field '%s'" % name)
        if not isinstance(body["port"], int) or isinstance(body["port"], bool):
            return _api_error(400, "Field 'port' must be an integer")
        if body["protocol"] != "UDP":
            return _api_error(400, "Only the UDP protocol is supported")
        host = body["ip_or_fqdn"]
        if host in self.appliance.config.unresolvable_hosts:
            return _api_error(
                400,
                "Cannot resolve syslog target host '%s'" % host,
                details=[{"code": 4001, "message": "DNS lookup failed", "target": ["ip_or_fqdn"]}],
            )
        return None

    def _op_addSyslogTarget(self, params, body):
        if not self._authenticated():
            return
        error = self._validate_target_body(body)
        if error is not None:
            self._respond(400, error)
            return
        with self.appliance.lock:
            if self.appliance.find_target(body["ip_or_fqdn"]) is not None:
                self._respond(409, _api_error(409, "Syslog target '%s' already exists" % body["ip_or_fqdn"]))
                return
            self.appliance.targets.append(dict(body))
        self._respond(201)

    def _op_updateSyslogTarget(self, params, body):
        if not self._authenticated():
            return
        host = params[0]
        error = self._validate_target_body(body)
        if error is not None:
            self._respond(400, error)
            return
        if body["ip_or_fqdn"] != host:
            self._respond(400, _api_error(400, "Path segment and ip_or_fqdn disagree"))
            return
        with self.appliance.lock:
            existing = self.appliance.find_target(host)
            if existing is None:
                self._respond(404, _api_error(404, "Syslog target '%s' not found" % host))
                return
            existing.clear()
            existing.update(body)
        self._respond(200)

    def _op_sendSyslogTestMessage(self, params, body):
        if not self._authenticated():
            return
        error = self._validate_target_body(body)
        if error is not None:
            self._respond(400, error)
            return
        host = body["ip_or_fqdn"]
        status, message = self.appliance.config.test_results.get(host, (True, "Test log sent"))
        self._respond(200, {"status": status, "message": message})


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
    def targets(self):
        return [dict(entry) for entry in self.appliance.targets]

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
