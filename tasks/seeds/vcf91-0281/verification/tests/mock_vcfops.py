#!/usr/bin/env python3
"""Loopback mock of the VCF Operations 9.1 credential/adapter surface.

The route table is built from docs/contract.json: the server answers only the
operations that contract names, and anything else gets a 404 that says so.

Every request is appended to a JSON Lines request log so the verifier can assert
the exact wire shape after the run. Nothing here talks to a VMware endpoint; the
listener is bound to 127.0.0.1 on an ephemeral port.

Usage:
    python3 mock_vcfops.py --contract docs/contract.json \
                           --log /tmp/requests.jsonl \
                           --port-file /tmp/port.txt
"""

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# Fixed world. Every identifier is a literal so runs are byte-for-byte stable.
# ---------------------------------------------------------------------------

USERNAME = "svc-rotation"
PASSWORD = "OldRotationPassw0rd!"
TOKEN = "ops-tok-3f2a91c7d4e85b60"
TOKEN_VALIDITY = 1778000000000
TOKEN_EXPIRES_AT = "2026-05-13T14:19:58.000Z"
AUTH_PREFIX = "OpsToken "

OLD_CREDENTIAL_ID = "7b3f9c14-2e5a-4d68-9a01-3c6d5e8f1a20"
NEW_CREDENTIAL_ID = "2d9e4a71-6c08-4f3b-8b52-90a7d1e4c6f5"

# How many drain polls an adapter instance keeps reporting against the outgoing
# credential after it has been repointed. This stands in for collection cycles
# that were already in flight on the old secret when the PATCH landed.
DRAIN_LAG_POLLS = 2


def _resource_key(name, vc_url):
    return {
        "name": name,
        "adapterKindKey": "VMWARE",
        "resourceKindKey": "VMwareAdapter Instance",
        "resourceIdentifiers": [
            {
                "identifierType": {
                    "name": "AUTODISCOVERY",
                    "dataType": "STRING",
                    "isPartOfUniqueness": True,
                },
                "value": "true",
            },
            {
                "identifierType": {
                    "name": "VCURL",
                    "dataType": "STRING",
                    "isPartOfUniqueness": True,
                },
                "value": vc_url,
            },
        ],
    }


def initial_adapters():
    return [
        {
            "id": "a1c5e930-4b71-4f2e-9d83-1e6f0b27c845",
            "resourceKey": _resource_key(
                "Adapter for VC@https://vc-lab01.example.com/sdk",
                "https://vc-lab01.example.com/sdk",
            ),
            "credentialInstanceId": OLD_CREDENTIAL_ID,
            # Read-side noise. A client that echoes the read back into the PATCH
            # will drag these along, which is exactly what the contract forbids.
            "description": None,
            "collectorId": 1,
            "collectorGroupId": "0f2e6f0b-27c8-45a1-9c5e-930b714f2e9d",
            "monitoringInterval": 5,
            "monitoringIntervalSeconds": 0,
            "numberOfMetricsCollected": 148213,
            "numberOfResourcesCollected": 2044,
            "lastCollected": 1778000041000,
            "lastHeartbeat": 1778000053000,
            "messageFromAdapterInstance": None,
            "links": [
                {"href": "/suite-api/api/adapters/a1c5e930-4b71-4f2e-9d83-1e6f0b27c845",
                 "rel": "SELF", "name": "linkToSelf"}
            ],
        },
        {
            "id": "b2d6fa41-5c82-4a3f-8e94-2f70c138d956",
            "resourceKey": _resource_key(
                "Adapter for VC@https://vc-lab02.example.com/sdk",
                "https://vc-lab02.example.com/sdk",
            ),
            "credentialInstanceId": OLD_CREDENTIAL_ID,
            "description": "Secondary lab vCenter",
            "collectorId": 1,
            "collectorGroupId": "0f2e6f0b-27c8-45a1-9c5e-930b714f2e9d",
            "monitoringInterval": 5,
            "monitoringIntervalSeconds": 0,
            "numberOfMetricsCollected": 90117,
            "numberOfResourcesCollected": 1318,
            "lastCollected": 1778000039000,
            "lastHeartbeat": 1778000052000,
            "messageFromAdapterInstance": None,
            "links": [
                {"href": "/suite-api/api/adapters/b2d6fa41-5c82-4a3f-8e94-2f70c138d956",
                 "rel": "SELF", "name": "linkToSelf"}
            ],
        },
        {
            "id": "c3e70b52-6d93-4b40-9fa5-3081d249ea67",
            "resourceKey": _resource_key(
                "Adapter for VC@https://vc-lab03.example.com/sdk",
                "https://vc-lab03.example.com/sdk",
            ),
            "credentialInstanceId": OLD_CREDENTIAL_ID,
            "description": None,
            "collectorId": 2,
            "collectorGroupId": "0f2e6f0b-27c8-45a1-9c5e-930b714f2e9d",
            "monitoringInterval": 5,
            "monitoringIntervalSeconds": 0,
            "numberOfMetricsCollected": 51002,
            "numberOfResourcesCollected": 806,
            "lastCollected": 1778000037000,
            "lastHeartbeat": 1778000050000,
            "messageFromAdapterInstance": None,
            "links": [
                {"href": "/suite-api/api/adapters/c3e70b52-6d93-4b40-9fa5-3081d249ea67",
                 "rel": "SELF", "name": "linkToSelf"}
            ],
        },
    ]


def initial_credentials():
    return {
        OLD_CREDENTIAL_ID: {
            "id": OLD_CREDENTIAL_ID,
            "name": "vCenter Principal Credential",
            "adapterKindKey": "VMWARE",
            "credentialKindKey": "PRINCIPALCREDENTIAL",
            "editable": True,
            # The server never hands a secret back; only the field names survive
            # a read, which is why a rotation has to be told the new values.
            "fields": [{"name": "USER", "value": "svc-vcops@vsphere.local"}],
        }
    }


# ---------------------------------------------------------------------------
# Contract-pinned routing
# ---------------------------------------------------------------------------

class Route:
    def __init__(self, operation_id, method, path_template, authenticated):
        self.operation_id = operation_id
        self.method = method
        self.path_template = path_template
        self.authenticated = authenticated
        pattern = re.escape(path_template)
        for name in re.findall(r"\{(\w+)\}", path_template):
            pattern = pattern.replace(re.escape("{" + name + "}"),
                                      "(?P<%s>[^/]+)" % name)
        self.regex = re.compile("^" + pattern + "$")


def load_routes(contract_path):
    with open(contract_path, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    routes = []
    for op in contract["operations"]:
        routes.append(Route(op["operationId"], op["method"], op["path"],
                            bool(op.get("authenticated"))))
    known = {r.operation_id for r in routes}
    missing = known - set(HANDLERS)
    if missing:
        raise SystemExit(
            "contract names operations this mock cannot serve: %s"
            % ", ".join(sorted(missing)))
    return routes, contract


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.seq = 0
        self.credentials = initial_credentials()
        self.adapters = initial_adapters()
        # adapter id -> {"target": <new credential id>, "remaining": int}
        self.pending = {}

    def next_seq(self):
        self.seq += 1
        return self.seq

    def adapter(self, adapter_id):
        for adapter in self.adapters:
            if adapter["id"] == adapter_id:
                return adapter
        return None

    def adapters_reported_for(self, credential_id):
        """Adapters an operator would still consider bound to this credential.

        A repointed adapter keeps showing up here until its lag expires, which
        is the whole reason a rotation has to drain before it deletes.
        """
        out = []
        for adapter in self.adapters:
            if adapter["credentialInstanceId"] == credential_id:
                out.append(adapter)
        return out

    def tick_drain(self, credential_id):
        """One drain poll against `credential_id` advances in-flight cutovers."""
        for adapter_id, entry in list(self.pending.items()):
            adapter = self.adapter(adapter_id)
            if adapter is None or adapter["credentialInstanceId"] != credential_id:
                continue
            entry["remaining"] -= 1
            if entry["remaining"] <= 0:
                adapter["credentialInstanceId"] = entry["target"]
                del self.pending[adapter_id]


STATE = State()


# ---------------------------------------------------------------------------
# Handlers. Each returns (status, body_object_or_None).
# ---------------------------------------------------------------------------

def _error(message, **extra):
    body = {"message": message, "httpStatusCode": None, "apiErrorCode": None}
    body.update(extra)
    return body


def _reject_nulls(body, path="body"):
    """Nothing in this API is expressed as an explicit null.

    The contract says an optional property with no value is omitted. A null or
    an empty string on a property the request is not setting means the client
    serialized an absent value instead of skipping it.
    """
    if body is None:
        return None
    if isinstance(body, dict):
        for key, value in body.items():
            if value is None:
                return ("%s.%s is null; the contract requires unset optional "
                        "properties to be omitted, not sent as null" % (path, key))
            problem = _reject_nulls(value, "%s.%s" % (path, key))
            if problem:
                return problem
    elif isinstance(body, list):
        for index, value in enumerate(body):
            if value is None:
                return "%s[%d] is null" % (path, index)
            problem = _reject_nulls(value, "%s[%d]" % (path, index))
            if problem:
                return problem
    return None


def handle_acquire_token(ctx):
    body = ctx["body_json"]
    if not isinstance(body, dict):
        return 400, _error("request body must be a username-password object")
    for required in ("username", "password"):
        if not isinstance(body.get(required), str) or not body[required]:
            return 400, _error("username-password.%s is required" % required)
    extra = set(body) - {"username", "password", "authSource"}
    if extra:
        return 400, _error(
            "username-password does not define %s" % ", ".join(sorted(extra)))
    if "authSource" in body and not body["authSource"]:
        return 400, _error(
            "username-password.authSource was sent empty; omit the property "
            "when no auth source is configured")
    if body["username"] != USERNAME or body["password"] != PASSWORD:
        return 401, _error("authentication failed")
    return 200, {
        "token": TOKEN,
        "validity": TOKEN_VALIDITY,
        "expiresAt": TOKEN_EXPIRES_AT,
        "roles": ["Administrator"],
    }


def handle_get_credential(ctx):
    credential = STATE.credentials.get(ctx["params"]["id"])
    if credential is None:
        return 404, _error("no credential instance with id %s"
                           % ctx["params"]["id"])
    return 200, credential


def handle_create_credential(ctx):
    body = ctx["body_json"]
    if not isinstance(body, dict):
        return 400, _error("request body must be a credential object")
    if "id" in body:
        return 422, _error(
            "credential.id must be null for credential instance creation "
            "requests; omit the property")
    if "editable" in body:
        return 422, _error("credential.editable is server-maintained")
    extra = set(body) - {"name", "adapterKindKey", "credentialKindKey", "fields"}
    if extra:
        return 422, _error(
            "credential does not define %s" % ", ".join(sorted(extra)))
    for required in ("name", "adapterKindKey", "credentialKindKey"):
        if not isinstance(body.get(required), str) or not body[required]:
            return 422, _error("credential.%s is required" % required)
    fields = body.get("fields", [])
    if not isinstance(fields, list):
        return 422, _error("credential.fields must be an array of name-value")
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            return 422, _error("credential.fields[%d] must be a name-value" % index)
        if set(field) != {"name", "value"}:
            return 422, _error(
                "credential.fields[%d] must carry exactly name and value; got %s"
                % (index, ", ".join(sorted(field)) or "nothing"))
        if not isinstance(field["name"], str) or not field["name"]:
            return 422, _error("credential.fields[%d].name is required" % index)
        if not isinstance(field["value"], str) or not field["value"]:
            return 422, _error("credential.fields[%d].value is required" % index)
    if any(c["name"] == body["name"] for c in STATE.credentials.values()):
        return 422, _error("a credential instance named %r already exists"
                           % body["name"])
    created = {
        "id": NEW_CREDENTIAL_ID,
        "name": body["name"],
        "adapterKindKey": body["adapterKindKey"],
        "credentialKindKey": body["credentialKindKey"],
        "editable": True,
        # Secret values are accepted but never echoed.
        "fields": [{"name": f["name"], "value": ""} for f in fields],
    }
    STATE.credentials[created["id"]] = created
    return 201, created


def handle_get_adapters_using_credential(ctx):
    credential_id = ctx["params"]["id"]
    if credential_id not in STATE.credentials:
        return 404, _error("no credential instance with id %s" % credential_id)
    STATE.tick_drain(credential_id)
    bound = STATE.adapters_reported_for(credential_id)
    return 200, {"adapterInstancesInfoDto": bound}


def handle_patch_adapter_instance(ctx):
    body = ctx["body_json"]
    if not isinstance(body, dict):
        return 400, _error("request body must be an adapter-instance object")
    permitted = {"id", "resourceKey", "credentialInstanceId"}
    extra = set(body) - permitted
    if extra:
        return 400, _error(
            "PATCH /api/adapters is a partial update; %s must be omitted "
            "rather than echoed back from the read"
            % ", ".join(sorted(extra)))
    if "resourceKey" not in body:
        return 400, _error("adapter-instance.resourceKey is required")
    adapter_id = body.get("id")
    if not isinstance(adapter_id, str) or not adapter_id:
        return 400, _error(
            "adapter-instance.id is required to identify the instance to patch")
    adapter = STATE.adapter(adapter_id)
    if adapter is None:
        return 400, _error("no adapter instance with id %s" % adapter_id)
    if body["resourceKey"] != adapter["resourceKey"]:
        return 400, _error(
            "adapter-instance.resourceKey does not match the stored identity "
            "key for %s; send it back exactly as it was read" % adapter_id)
    target = body.get("credentialInstanceId")
    if not isinstance(target, str) or not target:
        return 400, _error("adapter-instance.credentialInstanceId is required")
    if target not in STATE.credentials:
        return 400, _error("no credential instance with id %s" % target)
    if target != adapter["credentialInstanceId"]:
        # The rebind is accepted immediately but does not take effect until the
        # collections already running on the previous secret have wound down.
        STATE.pending[adapter_id] = {"target": target,
                                     "remaining": DRAIN_LAG_POLLS}
    echo = {
        "id": adapter["id"],
        "resourceKey": adapter["resourceKey"],
        "credentialInstanceId": adapter["credentialInstanceId"],
    }
    return 200, echo


def handle_delete_credential(ctx):
    credential_id = ctx["params"]["id"]
    if credential_id not in STATE.credentials:
        return 400, _error("no credential instance with id %s" % credential_id)
    still_bound = [a["id"] for a in STATE.adapters_reported_for(credential_id)]
    if still_bound:
        return 400, _error(
            "credential instance %s is still in use by %d adapter instance(s): "
            "%s" % (credential_id, len(still_bound), ", ".join(still_bound)))
    if credential_id in {entry["target"] for entry in STATE.pending.values()}:
        return 400, _error(
            "credential instance %s is the target of a cutover that has not "
            "completed" % credential_id)
    del STATE.credentials[credential_id]
    return 204, None


HANDLERS = {
    "acquireToken": handle_acquire_token,
    "getCredential": handle_get_credential,
    "createCredential": handle_create_credential,
    "getAdapterInstancesUsingCredential": handle_get_adapters_using_credential,
    "patchAdapterInstance": handle_patch_adapter_instance,
    "deleteCredential": handle_delete_credential,
}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcf-ops-mock/1.0"

    routes = []
    log_path = None

    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    # -- request log -------------------------------------------------------

    def _record(self, entry):
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _respond(self, status, body_obj, entry, reject_reason=None):
        payload = b""
        if body_obj is not None:
            payload = json.dumps(body_obj).encode("utf-8")
        entry["status"] = status
        entry["response_json"] = body_obj
        if reject_reason:
            entry["reject_reason"] = reject_reason
        # Persist the completed request/result pair before releasing the
        # response. The verifier may stop the mock as soon as the client has
        # consumed the final response, so logging afterwards creates a race.
        self._record(entry)
        self.send_response(status)
        if payload:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    # -- dispatch ----------------------------------------------------------

    def _dispatch(self, method):
        raw_path = self.path
        path, _, query = raw_path.partition("?")
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        body_text = raw_body.decode("utf-8", "replace")

        with STATE.lock:
            seq = STATE.next_seq()

        headers = {k.lower(): v for k, v in self.headers.items()}
        entry = {
            "seq": seq,
            "method": method,
            "path": path,
            "query": query,
            "operationId": None,
            "headers": {
                key: headers.get(key)
                for key in ("authorization", "content-type", "accept")
            },
            "body_raw": body_text,
            "body_json": None,
            "body_parse_error": None,
        }
        if body_text:
            try:
                entry["body_json"] = json.loads(body_text)
            except ValueError as exc:
                entry["body_parse_error"] = str(exc)

        route = None
        params = {}
        path_matched_other_method = False
        for candidate in self.routes:
            match = candidate.regex.match(path)
            if not match:
                continue
            if candidate.method != method:
                path_matched_other_method = True
                continue
            route = candidate
            params = match.groupdict()
            break

        if route is None:
            reason = (
                "%s %s is not an operation named by docs/contract.json; this "
                "mock serves only %s" % (
                    method, path,
                    ", ".join(sorted({r.operation_id for r in self.routes})))
            )
            if path_matched_other_method:
                reason = (
                    "%s is served by this mock, but not with %s; docs/"
                    "contract.json does not name that operation" % (path, method)
                )
            with STATE.lock:
                self._respond(404, _error(reason), entry, reject_reason=reason)
            return

        entry["operationId"] = route.operation_id

        with STATE.lock:
            if route.authenticated:
                supplied = headers.get("authorization")
                if not supplied:
                    self._respond(401, _error(
                        "missing Authorization header"), entry)
                    return
                if supplied != AUTH_PREFIX + TOKEN:
                    self._respond(401, _error(
                        "Authorization header must be %r followed by the token "
                        "returned by acquireToken" % AUTH_PREFIX.strip()), entry)
                    return

            if raw_body:
                content_type = (headers.get("content-type") or "").split(";")[0].strip()
                if content_type != "application/json":
                    self._respond(415, _error(
                        "request bodies must be application/json, got %r"
                        % content_type), entry)
                    return
                if entry["body_parse_error"]:
                    self._respond(400, _error(
                        "request body is not valid JSON: %s"
                        % entry["body_parse_error"]), entry)
                    return
                problem = _reject_nulls(entry["body_json"])
                if problem:
                    self._respond(400, _error(problem), entry)
                    return
            elif method in ("POST", "PATCH", "PUT"):
                self._respond(400, _error("%s requires a request body" % method),
                              entry)
                return

            ctx = {"params": params, "body_json": entry["body_json"],
                   "query": query, "headers": headers}
            status, body_obj = HANDLERS[route.operation_id](ctx)
            self._respond(status, body_obj, entry)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    routes, _contract = load_routes(args.contract)
    Handler.routes = routes
    Handler.log_path = os.path.abspath(args.log)
    open(Handler.log_path, "w", encoding="utf-8").close()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = server.server_address[1]
    with open(args.port_file, "w", encoding="utf-8") as handle:
        handle.write(str(port))
    sys.stderr.write("mock listening on http://127.0.0.1:%d/suite-api\n" % port)
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
