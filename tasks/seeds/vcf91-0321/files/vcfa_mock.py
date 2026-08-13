"""Loopback mock of the VCF Automation Provider Infrastructure APIs.

This is a test fixture, not a client library and not a VMware product. It binds to
127.0.0.1 only and never talks to a real VCF deployment.

It serves exactly the operations named in ``docs/contract.json`` and returns 404 for
anything else, so a client that drifts off the contract fails loudly instead of
silently exercising an endpoint that was never derived from the reference.

What it enforces
----------------
* ``Accept`` must request the contract's API version (``version=9.1.0``) -> else 406.
* ``Authorization: Bearer <token>`` must be present -> else 401.
* Request bodies may only carry documented fields -> else 400.
* Documented required fields must be present -> else 400.
* Response-only fields must not be written back -> else 400.

What it deliberately does NOT enforce
-------------------------------------
The contract's ``wireRules.omitUnsetOptionalBodyFields`` rule. A caller that sends
``{"secure": null}`` or ``{"additionalCAIssuers": []}`` for an option it never set gets
a 200 here, exactly as a lenient real server might. The request is recorded verbatim in
the request log and the verifier is what holds you to the rule.

The in-flight hazard
--------------------
The reference states that deleting a named credential terminates the associated vCenter
sessions, and documents no dual-secret window for an in-place update. This mock models
the documented delete behavior and the contract's conservative update-safety assumption
*silently*: retiring or overwriting a secret that in-flight work is still running on does
not fail the call. It aborts that work and appends a
``stranded_in_flight_requests`` event to the request log. Nothing in the HTTP response
tells you it happened.

Request log
-----------
``server.state.log`` is a list of dicts, each with a ``kind`` of ``"request"`` or
``"event"``. If ``VCFA_MOCK_LOG`` is set, the same records are appended as JSON Lines to
that path, flushed per record, so a standalone run can be inspected after the fact.

Standalone use::

    python3 vcfa_mock.py --port 8443
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

API_VERSION = "9.1.0"
CONTENT_TYPE = "application/json;version=%s" % API_VERSION
BASE = "/cloudapi/1.0.0"

# Deterministic, call-count driven timeline. No wall-clock, no randomness: the Nth poll
# always sees the same state regardless of how fast the client runs.
RECONNECT_AFTER_VC_POLLS = 3
DRAIN_AFTER_AUDIT_POLLS = 2

VC_URN = "urn:vcloud:vimserver:9a1c4d2e-7b33-4f18-9c05-2ad6e1f7b840"
ORG = {"name": "System", "id": "urn:vcloud:org:00000000-0000-0000-0000-000000000000"}
SEED_CREDENTIAL_ID = "urn:vcloud:namedCredential:3c7f1b90-52a4-4e6d-8b21-0d94ac5f6e77"


class ApiError(Exception):
    def __init__(self, status, minor_error_code, message):
        super().__init__(message)
        self.status = status
        self.minor_error_code = minor_error_code
        self.message = message


# --------------------------------------------------------------------------------------
# Tiny FIQL subset: `a==b`, `a!=b`, conjoined with `;`. Dotted field paths supported.
# --------------------------------------------------------------------------------------


def _resolve(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def fiql_match(item, expression):
    if not expression:
        return True
    for clause in expression.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        if "!=" in clause:
            field, _, want = clause.partition("!=")
            negate = True
        elif "==" in clause:
            field, _, want = clause.partition("==")
            negate = False
        else:
            raise ApiError(400, "BAD_REQUEST", "Unsupported FIQL clause: %r" % clause)
        want = want.strip().strip('"').strip("'")
        got = _resolve(item, field.strip())
        equal = str(got) == want
        if equal is negate:
            return False
    return True


def paged(values, page, page_size):
    if page_size == 0:
        window = []
    else:
        start = (page - 1) * page_size
        window = values[start : start + page_size]
    page_count = 1 if page_size == 0 else (len(values) + page_size - 1) // page_size
    return {
        "resultTotal": len(values),
        "pageCount": page_count,
        "page": page,
        "pageSize": page_size,
        "associations": [],
        "values": window,
    }


# --------------------------------------------------------------------------------------
# Service state
# --------------------------------------------------------------------------------------

NAMED_CREDENTIAL_WRITABLE = {"name", "username", "password", "entity"}
NAMED_CREDENTIAL_READ_ONLY = {"id", "org", "behavior"}

VCENTER_READ_ONLY = {
    "isConnected",
    "listenerState",
    "clusterHealthStatus",
    "vcVersion",
    "buildNumber",
    "uuid",
    "licenseStatus",
}
VCENTER_WRITABLE = {
    "vcId",
    "name",
    "description",
    "username",
    "password",
    "url",
    "isEnabled",
    "vsphereWebClientServerUrl",
    "hasProxy",
    "rootFolder",
    "vcNoneNetwork",
    "vcNoneNetworkMoref",
    "tenantVisibleName",
    "mode",
    "nsxVManager",
    "proxyConfigurationUrn",
    "isDedicatedForClassicTenants",
    "sddcManager",
}

TEST_CONNECTION_REQUIRED = {"host", "port"}
TEST_CONNECTION_OPTIONAL = {
    "secure",
    "timeout",
    "hostnameVerificationAlgorithm",
    "additionalCAIssuers",
    "proxyConnection",
    "preConfiguredProxy",
}


class State:
    """Seeded service state plus the request log."""

    def __init__(self):
        self.lock = threading.RLock()
        self.log = []
        self.seq = 0
        self.log_path = os.environ.get("VCFA_MOCK_LOG")
        self._task_counter = 0

        self.vc_polls_since_repoint = 0
        self.audit_polls_since_reconnect = 0
        self.repointed = False

        self.credentials = {
            SEED_CREDENTIAL_ID: {
                "id": SEED_CREDENTIAL_ID,
                "name": "vc-prod-01-svc",
                "org": dict(ORG),
                "entity": {"name": "vc-prod-01", "id": VC_URN},
                "username": "svc-vcfa@vsphere.local",
                "password": "Rot@teMe-2025-Q3",
                "behavior": {
                    "name": "VimServerCredential",
                    "id": "urn:vcloud:behavior:vimserver-credential",
                },
            }
        }

        self.vcenter = {
            "vcId": VC_URN,
            "name": "vc-prod-01",
            "description": "Production vCenter registration",
            "username": "svc-vcfa@vsphere.local",
            "password": "Rot@teMe-2025-Q3",
            "url": "https://vc-prod-01.lab.example.com",
            "isEnabled": True,
            "vsphereWebClientServerUrl": "https://vc-prod-01.lab.example.com/ui",
            "hasProxy": False,
            "rootFolder": None,
            "vcNoneNetwork": None,
            "vcNoneNetworkMoref": None,
            "tenantVisibleName": "prod-01",
            "isConnected": True,
            "mode": "NORMAL",
            "listenerState": "CONNECTED",
            "clusterHealthStatus": "GRAY",
            "vcVersion": "9.1.0",
            "buildNumber": "24755229",
            "uuid": "9a1c4d2e-7b33-4f18-9c05-2ad6e1f7b840",
            "nsxVManager": None,
            "proxyConfigurationUrn": None,
            "isDedicatedForClassicTenants": False,
            "licenseStatus": "VALID",
            "sddcManager": {
                "name": "sddc-manager-01",
                "id": "urn:vcloud:sddcManager:0f4b8a12-6c5e-4a70-9d3f-8e21b7c05a94",
            },
        }

        entity_ref = {"name": "vc-prod-01", "id": VC_URN}
        self.audit_events = [
            self._event("e-1001", "Provision namespace ns-payments", "RUNNING", entity_ref),
            self._event("e-1002", "Reconfigure VM app app-ledger", "RUNNING", entity_ref),
            self._event("e-1003", "Attach storage class gold to vpc-07", "RUNNING", entity_ref),
            self._event("e-0994", "Provision namespace ns-search", "SUCCESS", entity_ref),
            self._event("e-0995", "Delete VM app app-scratch", "SUCCESS", entity_ref),
            self._event("e-0996", "Import content library item", "SUCCESS", entity_ref),
        ]

    @staticmethod
    def _event(event_id, description, status, entity_ref):
        return {
            "eventId": event_id,
            "description": description,
            "operatingOrg": dict(ORG),
            "user": {"name": "provider-admin", "id": "urn:vcloud:user:provider-admin"},
            "actor": {"name": "provider-admin", "id": "urn:vcloud:user:provider-admin"},
            "eventEntity": dict(entity_ref),
            "taskId": "urn:vcloud:task:%s" % event_id,
            "taskCellId": "cell-01",
            "cellId": "cell-01",
            "eventType": "com/vmware/vcf/automation/infrastructure/request",
            "serviceNamespace": "com.vmware.vcf.automation",
            "eventStatus": status,
            "timestamp": "2026-08-11T09:%s:00.000Z" % event_id[-2:],
            "external": False,
            "additionalProperties": {},
        }

    # -- logging ------------------------------------------------------------------

    def record(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            self.log.append(entry)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")
                    handle.flush()
            return entry

    def note(self, event, **details):
        payload = {"kind": "event", "event": event}
        payload.update(details)
        return self.record(payload)

    def requests(self):
        with self.lock:
            return [e for e in self.log if e["kind"] == "request"]

    def events(self, name=None):
        with self.lock:
            return [
                e
                for e in self.log
                if e["kind"] == "event" and (name is None or e["event"] == name)
            ]

    # -- helpers ------------------------------------------------------------------

    def next_task_uri(self, verb):
        self._task_counter += 1
        return "https://vcfa.lab.example.com/api/task/urn:vcloud:task:%s-%04d" % (
            verb,
            self._task_counter,
        )

    def running_events(self):
        return [e for e in self.audit_events if e["eventStatus"] == "RUNNING"]

    def strand(self, reason, credential_id):
        """Abort every in-flight request that was riding the credential being changed."""
        victims = [e["eventId"] for e in self.running_events()]
        if not victims:
            return
        for event in self.audit_events:
            if event["eventStatus"] == "RUNNING":
                event["eventStatus"] = "ABORTED"
        self.note(
            "stranded_in_flight_requests",
            reason=reason,
            credentialId=credential_id,
            abortedEventIds=victims,
        )


# --------------------------------------------------------------------------------------
# Request handling
# --------------------------------------------------------------------------------------


def require_fields(body, required, writable, read_only, what):
    if not isinstance(body, dict):
        raise ApiError(400, "BAD_REQUEST", "%s body must be a JSON object" % what)
    leaked = sorted(set(body) & read_only)
    if leaked:
        raise ApiError(
            400,
            "READ_ONLY_FIELD",
            "%s: response-only field(s) may not be written: %s" % (what, ", ".join(leaked)),
        )
    unknown = sorted(set(body) - writable)
    if unknown:
        raise ApiError(
            400,
            "UNKNOWN_FIELD",
            "%s: undocumented field(s): %s" % (what, ", ".join(unknown)),
        )
    missing = sorted(f for f in required if f not in body)
    if missing:
        raise ApiError(
            400,
            "MISSING_REQUIRED_FIELD",
            "%s: required field(s) absent: %s" % (what, ", ".join(missing)),
        )


def read_page_params(query):
    def as_int(name, default, low, high):
        if name not in query:
            raise ApiError(
                400,
                "MISSING_REQUIRED_PARAM",
                "query parameter %r is documented as required" % name,
            )
        try:
            value = int(query[name])
        except (TypeError, ValueError):
            raise ApiError(400, "BAD_REQUEST", "query parameter %r must be an integer" % name)
        if value < low or (high is not None and value > high):
            raise ApiError(400, "BAD_REQUEST", "query parameter %r out of range" % name)
        return value

    return as_int("page", 1, 1, None), as_int("pageSize", 25, 0, 128)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-mock/1.0"

    # -- plumbing -----------------------------------------------------------------

    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    @property
    def state(self):
        return self.server.state

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def _respond(self, status, payload=None, extra_headers=None):
        blob = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", CONTENT_TYPE)
        self.send_header("Content-Length", str(len(blob)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if blob:
            self.wfile.write(blob)

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        raw_query = parsed.query
        query = dict(parse_qsl(raw_query, keep_blank_values=True))
        raw_body = self._read_body()

        entry = {
            "kind": "request",
            "method": method,
            "path": parsed.path,
            "query_raw": raw_query,
            "query": query,
            "headers": {
                "accept": self.headers.get("Accept"),
                "authorization": self.headers.get("Authorization"),
                "content-type": self.headers.get("Content-Type"),
            },
            "body_raw": raw_body,
        }
        try:
            entry["body"] = json.loads(raw_body) if raw_body else None
        except ValueError:
            entry["body"] = None

        try:
            self._check_headers(method, raw_body)
            status, payload, headers = self._route(method, parsed.path, query, entry["body"])
        except ApiError as err:
            status, headers = err.status, {}
            payload = {
                "minorErrorCode": err.minor_error_code,
                "message": err.message,
                "stackTrace": None,
            }

        entry["status"] = status
        self.state.record(entry)
        self._respond(status, payload, headers)

    def _check_headers(self, method, raw_body):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or not auth[7:].strip():
            raise ApiError(401, "UNAUTHORIZED", "Authorization: Bearer <jwt> is required")
        accept = self.headers.get("Accept") or ""
        if "version=%s" % API_VERSION not in accept:
            raise ApiError(
                406,
                "NOT_ACCEPTABLE",
                "Accept must pin the API version, e.g. %s" % CONTENT_TYPE,
            )
        if raw_body:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/json")

    # -- routing ------------------------------------------------------------------

    def _route(self, method, path, query, body):
        with self.state.lock:
            if path.endswith("/") and path != "/":
                path = path.rstrip("/")

            if path == BASE + "/namedCredentials":
                if method == "GET":
                    return self.op_query_named_credentials(query)
                if method == "POST":
                    return self.op_create_named_credential(body)
            elif path.startswith(BASE + "/namedCredentials/"):
                cred_id = path[len(BASE + "/namedCredentials/") :]
                if "/" in cred_id:
                    raise ApiError(404, "NOT_FOUND", "no such operation: %s %s" % (method, path))
                if method == "GET":
                    return self.op_get_named_credential(cred_id)
                if method == "PUT":
                    return self.op_update_named_credential(cred_id, body)
                if method == "DELETE":
                    return self.op_delete_named_credential(cred_id)
            elif path == BASE + "/testConnection":
                if method == "POST":
                    return self.op_test_connection(body)
            elif path == BASE + "/virtualCenters/" + VC_URN:
                if method == "GET":
                    return self.op_get_virtual_center()
                if method == "PUT":
                    return self.op_update_virtual_center(body)
            elif path.startswith(BASE + "/virtualCenters/"):
                raise ApiError(404, "NOT_FOUND", "no such vCenter registration")
            elif path == BASE + "/auditTrail":
                if method == "GET":
                    return self.op_query_audit_trail(query)

            raise ApiError(
                404,
                "NOT_FOUND",
                "no such operation: %s %s (this mock serves only the operations named in "
                "docs/contract.json)" % (method, path),
            )

    # -- operations ---------------------------------------------------------------

    def op_query_named_credentials(self, query):
        page, page_size = read_page_params(query)
        values = [dict(c) for c in self.state.credentials.values()]
        values = [v for v in values if fiql_match(v, query.get("filter"))]
        values.sort(key=lambda v: v["name"])
        return 200, paged(values, page, page_size), {}

    def op_create_named_credential(self, body):
        require_fields(
            body,
            {"name", "username", "password"},
            NAMED_CREDENTIAL_WRITABLE,
            NAMED_CREDENTIAL_READ_ONLY,
            "createNamedCredential",
        )
        if any(c["name"] == body["name"] for c in self.state.credentials.values()):
            raise ApiError(400, "DUPLICATE_NAME", "a named credential with that name exists")
        new_id = "urn:vcloud:namedCredential:%08x-0000-4000-8000-%012x" % (
            len(self.state.credentials) + 1,
            len(self.state.credentials) + 1,
        )
        record = {
            "id": new_id,
            "name": body["name"],
            "org": dict(ORG),
            "entity": body.get("entity"),
            "username": body["username"],
            "password": body["password"],
            "behavior": {
                "name": "VimServerCredential",
                "id": "urn:vcloud:behavior:vimserver-credential",
            },
        }
        self.state.credentials[new_id] = record
        return 201, dict(record), {}

    def op_get_named_credential(self, cred_id):
        record = self.state.credentials.get(cred_id)
        if record is None:
            raise ApiError(404, "NOT_FOUND", "no such named credential: %s" % cred_id)
        return 200, dict(record), {}

    def op_update_named_credential(self, cred_id, body):
        record = self.state.credentials.get(cred_id)
        if record is None:
            raise ApiError(404, "NOT_FOUND", "no such named credential: %s" % cred_id)
        require_fields(
            body, set(), NAMED_CREDENTIAL_WRITABLE, NAMED_CREDENTIAL_READ_ONLY,
            "updateNamedCredential",
        )
        overwrites_secret = "password" in body and body["password"] != record["password"]
        in_use = self.state.vcenter["username"] == record["username"]
        record.update(body)
        # Conservative contract assumption: without a documented dual-secret window,
        # treat the overwritten secret as immediately gone.
        if overwrites_secret and in_use:
            self.state.strand("in-place secret overwrite via updateNamedCredential", cred_id)
        return 200, dict(record), {}

    def op_delete_named_credential(self, cred_id):
        record = self.state.credentials.get(cred_id)
        if record is None:
            raise ApiError(404, "NOT_FOUND", "no such named credential: %s" % cred_id)
        in_use = self.state.vcenter["username"] == record["username"]
        del self.state.credentials[cred_id]
        if in_use:
            # "The associated vCenter sessions will be terminated."
            self.state.vcenter["isConnected"] = False
            self.state.vcenter["listenerState"] = "DISCONNECTED"
            self.state.note("terminated_vcenter_sessions", credentialId=cred_id, vcId=VC_URN)
            self.state.strand("deleted a credential the vCenter was still using", cred_id)
        elif self.state.running_events():
            self.state.strand("retired a credential while requests were still in flight", cred_id)
        return 202, None, {"Location": self.state.next_task_uri("delete")}

    def op_test_connection(self, body):
        require_fields(
            body,
            TEST_CONNECTION_REQUIRED,
            TEST_CONNECTION_REQUIRED | TEST_CONNECTION_OPTIONAL,
            set(),
            "testConnection",
        )
        probe = {
            "result": "SUCCESS",
            "resolvedIp": "10.20.30.41",
            "canConnect": True,
            "sslHandshake": True,
            "connectionResult": "SUCCESS",
            "sslResult": "SUCCESS",
            "certificateChain": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
            "additionalCAIssuers": [],
        }
        return 200, {"targetProbe": probe, "proxyProbe": None}, {}

    def op_get_virtual_center(self):
        vc = self.state.vcenter
        if self.state.repointed and not vc["isConnected"] and vc["listenerState"] == "CONNECTING":
            self.state.vc_polls_since_repoint += 1
            if self.state.vc_polls_since_repoint >= RECONNECT_AFTER_VC_POLLS:
                vc["isConnected"] = True
                vc["listenerState"] = "CONNECTED"
                self.state.note("vcenter_reconnected_on_new_secret", vcId=VC_URN)
        return 200, dict(vc), {}

    def op_update_virtual_center(self, body):
        require_fields(
            body, {"name", "url", "username"}, VCENTER_WRITABLE, VCENTER_READ_ONLY,
            "updateVirtualCenter",
        )
        self.state.vcenter.update(body)
        # Re-registering does not kill work already running: the existing sessions keep
        # using the secret they authenticated with until they finish.
        self.state.vcenter["isConnected"] = False
        self.state.vcenter["listenerState"] = "CONNECTING"
        self.state.repointed = True
        self.state.vc_polls_since_repoint = 0
        self.state.audit_polls_since_reconnect = 0
        return 202, None, {"Location": self.state.next_task_uri("repoint")}

    def op_query_audit_trail(self, query):
        page, page_size = read_page_params(query)
        if self.state.vcenter["isConnected"] and self.state.repointed:
            self.state.audit_polls_since_reconnect += 1
            if self.state.audit_polls_since_reconnect >= DRAIN_AFTER_AUDIT_POLLS:
                for event in self.state.audit_events:
                    if event["eventStatus"] == "RUNNING":
                        event["eventStatus"] = "SUCCESS"
        values = [dict(e) for e in self.state.audit_events]
        values = [v for v in values if fiql_match(v, query.get("filter"))]
        values.sort(key=lambda v: v["eventId"], reverse=True)
        return 200, paged(values, page, page_size), {}

    # -- verbs --------------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port=0):
        super().__init__(("127.0.0.1", port), Handler)
        self.state = State()

    @property
    def base_url(self):
        host, port = self.server_address[:2]
        return "http://%s:%d" % (host, port)


def start(port=0):
    """Start the mock on 127.0.0.1 in a daemon thread and return it."""
    server = MockServer(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.thread = thread
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    server = MockServer(args.port)
    print("vcfa mock listening on %s" % server.base_url, flush=True)
    print("seeded vCenter: %s" % VC_URN, flush=True)
    if server.state.log_path:
        print("request log: %s" % server.state.log_path, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
