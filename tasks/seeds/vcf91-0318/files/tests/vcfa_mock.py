"""Loopback mock of the VCF Automation Policies API, pinned to docs/contract.json.

The mock builds its routing table from the contract at start-up: it serves the
three operations the contract names and nothing else. Any other path or method
is answered 404/405 and still written to the request log, so a test can prove
the client under test never invented an operation.

Everything here is deterministic. There are no clocks, no randomness, and no
outbound sockets: the server binds 127.0.0.1 on an ephemeral port.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

LEASE_POLICY_TYPE_ID = "com.vmware.policy.deployment.lease"

# Fixture policy type, shaped per the PolicyType schema in the contract.
POLICY_TYPES = {
    LEASE_POLICY_TYPE_ID: {
        "id": LEASE_POLICY_TYPE_ID,
        "name": LEASE_POLICY_TYPE_ID,
        "displayName": "Lease Policy",
        "definitionSchema": {
            "leaseGrace": {"type": "integer", "label": "Grace period (days)"},
            "leaseTermMax": {"type": "integer", "label": "Maximum lease (days)"},
            "leaseTotalTermMax": {"type": "integer", "label": "Maximum total lease (days)"},
        },
        "targetSchema": {"projectId": {"type": "string"}},
        "config": {
            "enableDryRun": True,
            "enableEnforcementType": True,
            "enableOpaRegoCriteria": False,
            "enablePolicyValidation": True,
            "enableReconciliation": False,
            "enableSingleProjectScope": True,
            "enableUpdateNotification": False,
            "maxNumberOfPoliciesPerOrg": 100,
            "maxNumberOfPoliciesPerProject": 20,
        },
    }
}


def load_contract():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _path_regex(template):
    """Turn '/policy/api/policies/{id}' into an anchored regex."""
    parts = []
    for chunk in re.split(r"(\{[^{}]+\})", template):
        if chunk.startswith("{") and chunk.endswith("}"):
            parts.append("(?P<%s>[^/]+)" % chunk[1:-1])
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


class _Route:
    def __init__(self, operation):
        self.operation_id = operation["operation_id"]
        self.method = operation["method"]
        self.path_template = operation["path"]
        self.regex = _path_regex(operation["path"])


class MockVcfAutomation:
    """Contract-pinned loopback stand-in for a VCF Automation appliance.

    fail_first_posts is a list of injected outcomes applied, in order, to the
    first POSTs the server receives. Each entry is a dict:

        {"status": 503, "apply": True}

    ``apply`` True means the mutation is committed before the injected outcome.
    An entry may inject an HTTP status, a successful 202, or a dropped connection
    after the write. These are the cases a retry-safe client has to survive.
    """

    def __init__(self, token="unit-test-token", fail_first_posts=None, log_path=None):
        self.contract = load_contract()
        self.token = token
        self.routes = [_Route(op) for op in self.contract["operations"]]
        self.contract_operation_ids = {r.operation_id for r in self.routes}
        self.fail_first_posts = list(fail_first_posts or [])
        self.log_path = log_path
        self.policies = {}
        self.requests = []
        self._post_count = 0
        self._minted = 0
        self._tick = 0
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self.base_url = None
        if self.log_path and os.path.exists(self.log_path):
            os.remove(self.log_path)

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # keep the test output clean
                pass

            def do_GET(self):
                mock._handle(self, "GET")

            def do_POST(self):
                mock._handle(self, "POST")

            def do_PUT(self):
                mock._handle(self, "PUT")

            def do_PATCH(self):
                mock._handle(self, "PATCH")

            def do_DELETE(self):
                mock._handle(self, "DELETE")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        host, port = self._server.server_address[:2]
        self.base_url = "http://%s:%d" % (host, port)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
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

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- helpers -----------------------------------------------------------

    def _next_timestamp(self):
        self._tick += 1
        return "2026-01-01T00:00:%02dZ" % self._tick

    def _mint_policy_id(self):
        self._minted += 1
        return "11111111-1111-4111-8111-%012d" % self._minted

    def _match(self, method, path):
        by_path = [r for r in self.routes if r.regex.match(path)]
        if not by_path:
            return None, None, "no_path"
        for route in by_path:
            match = route.regex.match(path)
            if route.method == method:
                return route, match.groupdict(), None
        return None, None, "bad_method"

    def _record(self, entry):
        with self._lock:
            entry["seq"] = len(self.requests) + 1
            self.requests.append(entry)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, sort_keys=True) + "\n")

    # -- request handling --------------------------------------------------

    def _handle(self, handler, method):
        raw_path = handler.path
        if "?" in raw_path:
            path, query = raw_path.split("?", 1)
        else:
            path, query = raw_path, ""

        length = int(handler.headers.get("Content-Length") or 0)
        raw_body = handler.rfile.read(length) if length else b""

        entry = {
            "method": method,
            "raw_path": raw_path,
            "path": path,
            "query": query,
            "headers": [[name, value] for name, value in handler.headers.items()],
            "body": raw_body.decode("utf-8", "replace"),
            "body_bytes": len(raw_body),
        }

        route, params, failure = self._match(method, path)
        entry["operation_id"] = route.operation_id if route else None

        if route is None:
            status, payload = (
                (405, {"message": "method %s not documented for %s" % (method, path)})
                if failure == "bad_method"
                else (404, {"message": "operation not in contract: %s %s" % (method, path)})
            )
        elif handler.headers.get("Authorization") != "Bearer %s" % self.token:
            status, payload = 401, {"message": "unauthorized"}
        else:
            result = self._dispatch(route, params, query, raw_body)
            status, payload = result[:2]
            response_headers = result[2] if len(result) == 3 else {}
            disconnect = result[3] if len(result) == 4 else False

        if route is None or handler.headers.get("Authorization") != "Bearer %s" % self.token:
            response_headers = {}
            disconnect = False

        entry["status"] = "connection-dropped" if disconnect else status
        self._record(entry)
        if disconnect:
            handler.close_connection = True
            handler.connection.close()
            return
        self._respond(handler, status, payload, response_headers)

    def _dispatch(self, route, params, query, raw_body):
        if route.operation_id == "getPolicyType":
            policy_type = POLICY_TYPES.get(params["id"])
            if policy_type is None:
                return 404, {"message": "policy type not found: %s" % params["id"]}
            return 200, policy_type

        if route.operation_id == "getPolicy":
            with self._lock:
                policy = self.policies.get(params["id"])
            if policy is None:
                return 404, {"message": "policy not found: %s" % params["id"]}
            return 200, policy

        if route.operation_id == "createOrUpdatePolicy":
            return self._create_or_update(raw_body)

        return 404, {"message": "unroutable"}

    def _writable_fields(self):
        fields = self.contract["schemas"]["Policy"]["fields"]
        return {f["name"] for f in fields if f.get("writable")}

    def _create_or_update(self, raw_body):
        with self._lock:
            self._post_count += 1
            injected = (
                self.fail_first_posts[self._post_count - 1]
                if self._post_count <= len(self.fail_first_posts)
                else None
            )

        if injected is not None and not injected.get("apply", False):
            return (
                injected["status"],
                {"message": "injected failure (request rejected)"},
                dict(injected.get("headers", {})),
            )

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return 400, {"message": "request body is not valid utf-8 json"}
        if not isinstance(body, dict):
            return 400, {"message": "request body must be a json object"}

        writable = self._writable_fields()
        for key, value in body.items():
            if key not in writable:
                return 400, {"message": "field is not writable: %s" % key}
            if value is None:
                return 400, {
                    "message": "field %s was sent as null; omit unset optional fields" % key
                }
        if not body.get("typeId"):
            return 400, {"message": "typeId is required"}
        if body["typeId"] not in POLICY_TYPES:
            return 400, {"message": "unknown policy type: %s" % body["typeId"]}
        if len(body.get("name") or "") > 1024:
            return 400, {"message": "name exceeds 1024 characters"}
        if len(body.get("description") or "") > 2000:
            return 400, {"message": "description exceeds 2000 characters"}
        enforcement = body.get("enforcementType")
        if enforcement is not None and enforcement not in ("SOFT", "HARD"):
            return 400, {"message": "enforcementType must be SOFT or HARD"}

        with self._lock:
            policy_id = body.get("id") or self._mint_policy_id()
            existing = self.policies.get(policy_id)
            stored = dict(body)
            stored["id"] = policy_id
            stored.setdefault("enforcementType", "HARD")
            stored["orgId"] = body.get("orgId", "org-7f2c1a")
            if existing is None:
                stored["createdAt"] = self._next_timestamp()
                stored["createdBy"] = "svc-pipeline@vcf.local"
                created = True
            else:
                stored["createdAt"] = existing["createdAt"]
                stored["createdBy"] = existing["createdBy"]
                created = False
            stored["lastUpdatedAt"] = self._next_timestamp()
            stored["lastUpdatedBy"] = "svc-pipeline@vcf.local"
            self.policies[policy_id] = stored

        if injected is not None:
            if injected.get("success", False):
                return injected["status"], None, dict(injected.get("headers", {}))
            if injected.get("disconnect", False):
                return injected["status"], None, {}, True
            # Applied, then the response was lost. Exactly the retry hazard.
            return injected["status"], {"message": "injected failure (request applied)"}

        # Per the reference pages the success responses carry no data structure.
        return (201 if created else 200), None

    def _respond(self, handler, status, payload, response_headers=None):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
        handler.send_response(status)
        if body:
            handler.send_header("Content-Type", "application/json")
        for name, value in (response_headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if body:
            handler.wfile.write(body)

    # -- inspection --------------------------------------------------------

    def read_log(self):
        """Read the request log back off disk, the way the tests do."""
        if not self.log_path or not os.path.exists(self.log_path):
            return []
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return sorted(entries, key=lambda e: e["seq"])
