"""Contract-pinned loopback mock of a VCF Operations for Networks 9.1 appliance.

The mock loads its only callable routes, methods, accepted query parameter
names, accepted request-body properties and authorization shape from
``docs/contract.json``.  Anything the contract does not name is answered with
``404``; a known path reached with an unnamed method is answered with ``405``.
It listens on an ephemeral ``127.0.0.1`` port and speaks real HTTP, and it
records every request it receives in a synchronized log the verifier reads.

Nothing here is VMware software and no live endpoint is contacted.
"""

import base64
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTRACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "contract.json"
)

#: Fixed token the mock hands out, so the verifier can assert the exact header.
ISSUED_TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA=="
TOKEN_EXPIRY = 1605201960327

#: Deterministic clock stamped into created applications.
CREATE_TIME = 1509410056733


def load_contract():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


class _Fault:
    """One scripted fault, consumed the n-th time its operation is reached."""

    def __init__(self, operation_id, occurrence, mode):
        self.operation_id = operation_id
        self.occurrence = occurrence
        self.mode = mode


def fault(operation_id, occurrence, mode):
    return _Fault(operation_id, occurrence, mode)


class ApplianceState:
    """Mutable appliance state shared by every request handler thread."""

    def __init__(
        self,
        applications=(),
        username="admin@local",
        password="s3cr3t-pa55phrase",
        domain_type=None,
        domain_value=None,
        faults=(),
        hidden_names=(),
    ):
        self.lock = threading.Lock()
        self.applications = []
        self._next_id = 1
        for name in applications:
            self._insert(name)
        self.username = username
        self.password = password
        self.domain_type = domain_type
        self.domain_value = domain_value
        self.faults = list(faults)
        # Names present in the uniqueness index but withheld from the first
        # summaries sweep, modelling a read-after-write lag.
        self.hidden_names = set(hidden_names)
        self.live_tokens = set()
        self.requests = []
        self._counts = {}
        self._summary_sweeps = 0

    # -- state helpers ---------------------------------------------------

    def _insert(self, name):
        entity_id = "18230:561:%d" % (271275000 + self._next_id,)
        self._next_id += 1
        record = {
            "entity_id": entity_id,
            "name": name,
            "entity_type": "Application",
            "create_time": CREATE_TIME,
            "created_by": "admin@local",
            "last_modified_time": 0,
            "last_modified_by": "",
            "last_modified_by_service": "",
        }
        self.applications.append(record)
        return record

    def names(self):
        with self.lock:
            return [record["name"] for record in self.applications]

    def count_named(self, name):
        with self.lock:
            return sum(1 for record in self.applications if record["name"] == name)

    def entity_id_for(self, name):
        with self.lock:
            for record in self.applications:
                if record["name"] == name:
                    return record["entity_id"]
        return None

    def log(self):
        with self.lock:
            return list(self.requests)

    def requests_for(self, operation_id):
        return [entry for entry in self.log() if entry["operation_id"] == operation_id]

    # -- fault scripting -------------------------------------------------

    def _take_fault(self, operation_id):
        seen = self._counts.get(operation_id, 0) + 1
        self._counts[operation_id] = seen
        for candidate in self.faults:
            if candidate.operation_id == operation_id and candidate.occurrence == seen:
                return candidate.mode
        return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vofn-contract-mock/1.0"

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 - silence stderr chatter
        return

    @property
    def state(self):
        return self.server.state

    @property
    def contract(self):
        return self.server.contract

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    # -- dispatch --------------------------------------------------------

    def _dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        raw_query = parsed.query
        query_pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        headers = {}
        for key, value in self.headers.items():
            headers.setdefault(key.lower(), []).append(value)

        route = self.server.routes.get((method, parsed.path))
        operation_id = route["operationId"] if route else None

        entry = {
            "seq": None,
            "operation_id": operation_id,
            "method": method,
            "path": parsed.path,
            "raw_query": raw_query,
            "query_pairs": query_pairs,
            "query_keys": [key for key, _ in query_pairs],
            "headers": headers,
            "body": body.decode("utf-8", "replace"),
            "status": None,
        }

        if route is None:
            known_path = parsed.path in self.server.paths
            status = 405 if known_path else 404
            self._record(entry, status)
            self._send_error(status, status, "no contract operation for %s %s" % (method, parsed.path))
            return

        with self.state.lock:
            mode = self.state._take_fault(operation_id)

        if mode == "reset_before":
            self._record(entry, 0)
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return

        handler = {
            "create": self._op_create_token,
            "delete": self._op_delete_token,
            "getSavedApplicationsSummaries": self._op_summaries,
            "addApplication": self._op_add_application,
        }[operation_id]
        handler(entry, query_pairs, body, mode)

    # -- operations ------------------------------------------------------

    def _op_create_token(self, entry, query_pairs, body, mode):
        if query_pairs:
            return self._fail(entry, 400, "create accepts no query parameters")
        payload, problem = self._json_object(body)
        if problem:
            return self._fail(entry, 400, problem)
        allowed = {"username", "password", "domain"}
        extra = sorted(set(payload) - allowed)
        if extra:
            return self._fail(entry, 400, "unknown UserCredential properties: %s" % (", ".join(extra),))
        for key in ("username", "password"):
            value = payload.get(key)
            if not isinstance(value, str) or value == "":
                return self._fail(entry, 400, "%s must be a non-empty string" % (key,))
        domain = payload.get("domain")
        domain_type = None
        domain_value = None
        if domain is not None:
            if not isinstance(domain, dict):
                return self._fail(entry, 400, "domain must be an object")
            domain_extra = sorted(set(domain) - {"domain_type", "value"})
            if domain_extra:
                return self._fail(entry, 400, "unknown Domain properties: %s" % (", ".join(domain_extra),))
            domain_type = domain.get("domain_type")
            if domain_type not in ("LDAP", "LOCAL"):
                return self._fail(entry, 400, "domain_type must be LDAP or LOCAL")
            if "value" in domain:
                domain_value = domain["value"]
                if not isinstance(domain_value, str) or domain_value == "":
                    return self._fail(entry, 400, "domain value must be a non-empty string")

        if mode == "unauthorized":
            return self._fail(entry, 401, "invalid credentials")
        state = self.state
        if (
            payload["username"] != state.username
            or payload["password"] != state.password
            or domain_type != state.domain_type
            or domain_value != state.domain_value
        ):
            return self._fail(entry, 401, "invalid credentials")

        token = ISSUED_TOKEN
        with state.lock:
            state.live_tokens.add(token)
        self._send_json(entry, 200, {"token": token, "expiry": TOKEN_EXPIRY})

    def _op_delete_token(self, entry, query_pairs, body, mode):
        token = self._require_token(entry)
        if token is None:
            return None
        if body:
            return self._fail(entry, 400, "delete accepts no request body")
        if mode == "server_error":
            return self._fail(entry, 500, "token store unavailable")
        with self.state.lock:
            self.state.live_tokens.discard(token)
        self._record(entry, 204)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _op_summaries(self, entry, query_pairs, body, mode):
        if self._require_token(entry) is None:
            return None
        if body:
            return self._fail(entry, 400, "getSavedApplicationsSummaries accepts no request body")
        if mode == "server_error":
            return self._fail(entry, 500, "application summaries are unavailable")
        allowed = self.server.query_params["getSavedApplicationsSummaries"]
        seen = set()
        params = {}
        for key, value in query_pairs:
            if key not in allowed:
                return self._fail(entry, 400, "unknown query parameter %r" % (key,))
            if key in seen:
                return self._fail(entry, 400, "repeated query parameter %r" % (key,))
            if value == "":
                return self._fail(entry, 400, "query parameter %r was sent empty; omit it instead" % (key,))
            seen.add(key)
            params[key] = value

        size = 10
        if "size" in params:
            try:
                size = int(params["size"])
            except ValueError:
                return self._fail(entry, 400, "size must be an integer")
            if size < 1 or size > 1000:
                return self._fail(entry, 400, "size out of range")

        offset = 0
        if "cursor" in params:
            offset = _decode_cursor(params["cursor"])
            if offset is None or offset < 0:
                return self._fail(entry, 400, "unusable cursor")

        with self.state.lock:
            sweep = self.state._summary_sweeps
            visible = [
                record
                for record in self.state.applications
                if not (record["name"] in self.state.hidden_names and sweep == 0)
            ]
            overrun = offset > len(visible)
            page = [] if overrun else visible[offset : offset + size]
            end = offset + len(page)
            payload = {
                "results": [dict(record) for record in page],
                "total_count": len(visible),
            }
            if end < len(visible):
                payload["cursor"] = _encode_cursor(end)
            elif not overrun:
                # A completed sweep retires the read-after-write lag.
                self.state._summary_sweeps = sweep + 1
        if mode == "repeat_cursor" and "cursor" in params:
            payload["cursor"] = params["cursor"]
        elif mode == "numeric_cursor":
            payload["cursor"] = end
        elif mode == "null_cursor":
            payload["cursor"] = None
        elif mode == "empty_cursor":
            payload["cursor"] = ""
        elif mode == "empty_results":
            payload["results"] = []
        elif mode == "malformed_results":
            payload["results"] = {}
        if overrun:
            return self._fail(entry, 400, "unusable cursor")
        self._send_json(entry, 200, payload)

    def _op_add_application(self, entry, query_pairs, body, mode):
        if self._require_token(entry) is None:
            return None
        if query_pairs:
            return self._fail(entry, 400, "addApplication accepts no query parameters")
        payload, problem = self._json_object(body)
        if problem:
            return self._fail(entry, 400, problem)
        extra = sorted(set(payload) - {"name"})
        if extra:
            return self._fail(entry, 400, "unknown ApplicationRequest properties: %s" % (", ".join(extra),))
        if "name" not in payload:
            return self._fail(entry, 400, "name is required")
        name = payload["name"]
        if not isinstance(name, str) or name == "":
            return self._fail(entry, 400, "name must be a non-empty string")

        if mode == "reject":
            return self._fail(entry, 400, "application name is not acceptable")
        if mode == "server_error":
            return self._fail(entry, 503, "application service unavailable")
        if mode in (500, 502, 503, 504):
            return self._fail(entry, mode, "application service unavailable")

        with self.state.lock:
            clash = any(record["name"] == name for record in self.state.applications)
            clash = clash or name in self.state.hidden_names
            if clash:
                created = None
            else:
                created = self._commit_locked(name)

        if created is None:
            return self._fail(
                entry, 400, "An application with name '%s' already exists" % (name,)
            )
        if mode == "commit_then_error":
            return self._fail(entry, 503, "application service unavailable")
        if mode == "commit_then_reset":
            self._record(entry, 0)
            self.close_connection = True
            try:
                self.connection.close()
            except OSError:
                pass
            return None
        self._send_json(entry, 201, dict(created))

    def _commit_locked(self, name):
        record = self.state._insert(name)
        self.state.hidden_names.discard(name)
        return record

    # -- helpers ---------------------------------------------------------

    def _require_token(self, entry):
        header = self.headers.get("Authorization")
        prefix = "NetworkInsight "
        if not header or not header.startswith(prefix):
            self._fail(entry, 401, "missing NetworkInsight authorization")
            return None
        token = header[len(prefix) :]
        with self.state.lock:
            live = token in self.state.live_tokens
        if not live:
            self._fail(entry, 401, "token is invalid or expired")
            return None
        return token

    def _json_object(self, body):
        if not body:
            return None, "a JSON request body is required"
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None, "Content-Type must be application/json"
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None, "request body is not valid JSON"
        if not isinstance(payload, dict):
            return None, "request body must be a JSON object"
        return payload, None

    def _fail(self, entry, status, message):
        self._record(entry, status)
        self._send_error(status, status, message)
        return None

    def _send_error(self, status, code, message):
        self._write(status, json.dumps({"code": code, "message": message}).encode("utf-8"))

    def _send_json(self, entry, status, payload):
        self._record(entry, status)
        self._write(status, json.dumps(payload).encode("utf-8"))

    def _write(self, status, blob):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _record(self, entry, status):
        entry["status"] = status
        with self.state.lock:
            entry["seq"] = len(self.state.requests)
            self.state.requests.append(entry)


def _encode_cursor(offset):
    return base64.b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_cursor(cursor):
    try:
        return int(base64.b64decode(cursor.encode("ascii"), validate=True).decode("ascii"))
    except Exception:  # noqa: BLE001 - any malformed cursor is a 400
        return None


class ContractMock:
    """Runs the contract-pinned appliance on an ephemeral loopback port."""

    def __init__(self, state):
        contract = load_contract()
        routes = {}
        paths = set()
        query_params = {}
        for operation in contract["operations"]:
            routes[(operation["method"], operation["fullPath"])] = operation
            paths.add(operation["fullPath"])
            query_params[operation["operationId"]] = {
                parameter["name"]
                for parameter in operation["parameters"]
                if parameter["in"] == "query"
            }
        self.state = state
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.state = state
        self._server.contract = contract
        self._server.routes = routes
        self._server.paths = paths
        self._server.query_params = query_params
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False
