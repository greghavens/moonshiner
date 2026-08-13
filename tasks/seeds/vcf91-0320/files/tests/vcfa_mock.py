"""Loopback mock of the VCF Automation operations named in docs/contract.json.

The routing table is built from the contract, not written out by hand, so the
mock serves exactly the three operations the contract names and answers 501 to
anything else. Every request it receives is appended to a JSONL log that the
verifier reads back, which is how the tests prove that a failing precheck sent
no mutating request.

Binds 127.0.0.1 on an ephemeral port. No clock and no randomness: ids and
timestamps are counter-derived or fixed, so two runs produce identical output.

Standard library only.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")
FIXTURES_PATH = os.path.join(HERE, "fixtures.json")

#: Fixed stamp handed out to everything the mock mints.
MINTED_AT = "2026-01-01T00:00:00.000Z"


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _route_regex(path_template):
    """Turn ``/iaas/api/regions/{id}`` into an anchored regex."""
    pattern = re.escape(path_template)
    pattern = re.sub(r"\\\{([A-Za-z_][A-Za-z0-9_]*)\\\}", r"(?P<\1>[^/]+)", pattern)
    return re.compile("^" + pattern + "$")


def build_routes(contract):
    """Derive (method, regex, operation_name, spec) tuples from the contract."""
    routes = []
    for name, spec in contract["operations"].items():
        routes.append(
            {
                "operation": name,
                "method": spec["method"],
                "regex": _route_regex(spec["path_template"]),
                "query_parameters": list(spec.get("query_parameters") or []),
                "spec": spec,
            }
        )
    # Longest template first, so a literal collection path is never shadowed by
    # a templated one that happens to also match.
    routes.sort(key=lambda route: -len(route["spec"]["path_template"]))
    return routes


class _State:
    """Everything the server mutates, guarded by one lock."""

    def __init__(self, fixtures, log_path, contract_body_spec):
        self.fixtures = fixtures
        self.log_path = log_path
        self.contract_body_spec = contract_body_spec
        self.lock = threading.Lock()
        self.seq = 0
        self.minted = 0
        self.created_profiles = []

    def next_seq(self):
        self.seq += 1
        return self.seq

    def next_profile_id(self):
        self.minted += 1
        return "st-prof-%04d" % self.minted

    def append_log(self, entry):
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _error_body(status, message, message_id):
    return {
        "message": message,
        "messageId": message_id,
        "statusCode": status,
        "errorCode": status * 1000,
        "documentKind": "com:vmware:vcfa:iaas:ServiceErrorResponse",
    }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-mock/1.0"

    # -- plumbing ------------------------------------------------------------

    def log_message(self, fmt, *args):  # keep the test output clean
        return

    @property
    def state(self):
        return self.server.state

    @property
    def routes(self):
        return self.server.routes

    @staticmethod
    def _reply(status, payload, headers=None):
        """Build a response. Nothing reaches the socket until _write_reply."""
        return (status, payload, headers or {})

    def _write_reply(self, reply):
        status, payload, headers = reply
        body = b"" if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return None
        try:
            count = int(length)
        except (TypeError, ValueError):
            return None
        if count <= 0:
            return None
        return self.rfile.read(count).decode("utf-8")

    # -- dispatch ------------------------------------------------------------

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
        parsed = urlparse(self.path)
        raw_body = self._read_body()
        try:
            body_json = json.loads(raw_body) if raw_body else None
        except ValueError:
            body_json = None

        query = parse_qs(parsed.query, keep_blank_values=True)
        headers = {key.lower(): value for key, value in self.headers.items()}

        route = None
        params = {}
        for candidate in self.routes:
            if candidate["method"] != method:
                continue
            match = candidate["regex"].match(parsed.path)
            if match:
                route = candidate
                params = match.groupdict()
                break

        with self.state.lock:
            seq = self.state.next_seq()
            if route is None:
                reply = self._reply(
                    501,
                    _error_body(
                        501,
                        "Operation %s %s is not named in docs/contract.json, so this mock "
                        "does not serve it." % (method, parsed.path),
                        "vcfa.mock.unrouted",
                    ),
                )
            else:
                reply = self._handle(route, params, query, headers, body_json)

            # Logged before anything reaches the socket, so a caller that has
            # its response in hand is guaranteed to see this entry in the log.
            self.state.append_log(
                {
                    "seq": seq,
                    "operation": route["operation"] if route else None,
                    "routed": route is not None,
                    "method": method,
                    "path": parsed.path,
                    "raw_query": parsed.query,
                    "query": query,
                    "headers": headers,
                    "raw_body": raw_body,
                    "body": body_json,
                    "status": reply[0],
                }
            )

        self._write_reply(reply)

    # -- shared request validation ------------------------------------------

    def _check_common(self, route, query, headers, method):
        """Contract-wide checks. Returns a reply once something is wrong, else None."""
        auth = headers.get("authorization")
        if not auth or not auth.startswith("Bearer ") or not auth[len("Bearer "):].strip():
            return self._reply(
                403,
                _error_body(403, "Missing or malformed Authorization header.", "vcfa.auth.missing"),
            )

        allowed = set(route["query_parameters"])
        for key in query:
            if key not in allowed:
                return self._reply(
                    400,
                    _error_body(
                        400,
                        "Unsupported query parameter %r for %s." % (key, route["operation"]),
                        "vcfa.query.unsupported",
                    ),
                )

        for key, values in query.items():
            if any(value == "" for value in values):
                return self._reply(
                    400,
                    _error_body(
                        400,
                        "Query parameter %r was sent empty. Omit it instead." % key,
                        "vcfa.query.empty",
                    ),
                )

        if method == "GET" and "content-type" in headers:
            return self._reply(
                400,
                _error_body(
                    400,
                    "Content-Type must not be sent on a request with no body.",
                    "vcfa.header.spurious-content-type",
                ),
            )
        return None

    def _handle(self, route, params, query, headers, body_json):
        early = self._check_common(route, query, headers, self.command)
        if early is not None:
            return early

        operation = route["operation"]
        if operation == "getRegion":
            return self._get_entity("regions", params["id"])
        if operation == "getFabricVsphereDatastore":
            return self._get_entity("datastores", params["id"])
        if operation == "createVsphereStorageProfile":
            return self._create_profile(headers, body_json)
        return self._reply(
            501, _error_body(501, "Unhandled operation %s." % operation, "vcfa.mock.unhandled")
        )

    def _get_entity(self, collection, entity_id):
        override = self.state.fixtures["status_overrides"].get(collection, {}).get(entity_id)
        if override:
            if override == 302:
                return self._reply(
                    302,
                    _error_body(302, "Redirects are not API responses.", "vcfa.mock.redirect"),
                    {"Location": "/outside-the-contract"},
                )
            return self._reply(
                override,
                _error_body(override, "Access to %r is denied." % entity_id, "vcfa.rbac.denied"),
            )
        entity = self.state.fixtures[collection].get(entity_id)
        if entity is None:
            return self._reply(
                404,
                _error_body(404, "No such entity: %r." % entity_id, "vcfa.entity.not-found"),
            )
        return self._reply(200, entity)

    # -- the mutating operation ---------------------------------------------

    def _create_profile(self, headers, body_json):
        content_type = (headers.get("content-type") or "").split(";")[0].strip()
        if content_type != "application/json":
            return self._reply(
                400,
                _error_body(
                    400,
                    "Content-Type must be application/json, got %r." % content_type,
                    "vcfa.header.content-type",
                ),
            )
        if not isinstance(body_json, dict):
            return self._reply(
                400,
                _error_body(400, "Request body must be a JSON object.", "vcfa.body.malformed"),
            )

        body_spec = self.state.contract_body_spec
        required = body_spec["required_fields"]
        optional = body_spec["optional_fields"]

        unknown = sorted(set(body_json) - set(required) - set(optional))
        if unknown:
            return self._reply(
                400,
                _error_body(
                    400,
                    "StorageProfileVsphereSpecification has no field(s) %s."
                    % ", ".join(repr(name) for name in unknown),
                    "vcfa.body.unknown-field",
                ),
            )

        missing = sorted(name for name in required if name not in body_json)
        if missing:
            return self._reply(
                400,
                _error_body(
                    400,
                    "Required field(s) absent: %s." % ", ".join(repr(name) for name in missing),
                    "vcfa.body.required-field-absent",
                ),
            )

        for name, value in sorted(body_json.items()):
            if value is None:
                return self._reply(
                    400,
                    _error_body(
                        400,
                        "Field %r was sent as null. Omit an unset optional field instead of "
                        "clearing it." % name,
                        "vcfa.body.null-field",
                    ),
                )

        if not isinstance(body_json["name"], str) or not body_json["name"]:
            return self._reply(
                400,
                _error_body(400, "'name' must be a non-empty string.", "vcfa.body.invalid-name"),
            )
        if not isinstance(body_json["defaultItem"], bool):
            return self._reply(
                400,
                _error_body(
                    400, "'defaultItem' must be a boolean.", "vcfa.body.invalid-default-item"
                ),
            )
        if body_json["name"] in self.state.fixtures["rejected_profile_names"]:
            return self._reply(
                400,
                _error_body(
                    400,
                    "A storage class named %r already exists." % body_json["name"],
                    "vcfa.storage-profile.name-conflict",
                ),
            )

        region = self.state.fixtures["regions"].get(body_json["regionId"])
        if region is None:
            # The reference documents no 404 for this operation, so an unknown
            # region surfaces as an opaque 400. This is exactly the failure the
            # precheck exists to avoid reaching.
            return self._reply(
                400,
                _error_body(400, "Invalid Argument.", "vcfa.storage-profile.invalid-argument"),
            )

        profile_id = self.state.next_profile_id()
        profile = {
            "id": profile_id,
            "_links": {"self": {"href": "/iaas/api/storage-profiles-vsphere/" + profile_id}},
            "createdAt": MINTED_AT,
            "updatedAt": MINTED_AT,
            "orgId": region.get("orgId", "org-9d1"),
            "externalRegionId": region["externalRegionId"],
            "defaultItem": body_json["defaultItem"],
            "name": body_json["name"],
        }
        if region.get("cloudAccountId"):
            profile["cloudAccountId"] = region["cloudAccountId"]
        # Echo back only the optional fields that were actually sent, minus the
        # ones VsphereStorageProfile does not carry.
        not_returned = {"datastoreId", "storagePolicyId", "regionId"}
        for name, value in body_json.items():
            if name in optional and name not in not_returned:
                profile[name] = value

        self.state.created_profiles.append({"request": body_json, "profile": profile})
        return self._reply(201, profile)


class MockAppliance:
    """A VCF Automation stand-in on 127.0.0.1, pinned to docs/contract.json."""

    def __init__(self, log_path, contract_path=CONTRACT_PATH, fixtures_path=FIXTURES_PATH):
        self.contract = _load(contract_path)
        self.routes = build_routes(self.contract)
        self.state = _State(
            _load(fixtures_path),
            log_path,
            self.contract["operations"]["createVsphereStorageProfile"]["request_body"],
        )
        self._server = None
        self._thread = None

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.state = self.state
        self._server.routes = self.routes
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc_info):
        self.stop()
        return False

    @property
    def base_url(self):
        host, port = self._server.server_address[:2]
        return "http://%s:%d" % (host, port)

    # -- what the verifier reads --------------------------------------------

    def reset(self):
        """Return the appliance to its starting state.

        Clears the request log and the record of created storage classes, and
        rewinds both counters, so every test sees the same appliance and the
        ids it mints do not depend on which tests ran before it.
        """
        with open(self.state.log_path, "w", encoding="utf-8"):
            pass
        self.state.created_profiles = []
        self.state.seq = 0
        self.state.minted = 0

    def requests(self):
        """Every request received since the last reset, in arrival order."""
        try:
            with open(self.state.log_path, "r", encoding="utf-8") as handle:
                lines = [line for line in handle.read().splitlines() if line.strip()]
        except FileNotFoundError:
            return []
        entries = [json.loads(line) for line in lines]
        entries.sort(key=lambda entry: entry["seq"])
        return entries

    def requests_for(self, operation):
        return [entry for entry in self.requests() if entry["operation"] == operation]

    def unrouted_requests(self):
        return [entry for entry in self.requests() if not entry["routed"]]

    def created_profiles(self):
        return list(self.state.created_profiles)
