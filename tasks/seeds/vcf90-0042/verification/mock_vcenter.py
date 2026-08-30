"""Contract-pinned loopback stand-in for a VCF 9.0 vCenter Automation endpoint.

The routing table, the accepted request-body members and the response shapes are
read out of docs/contract.json, which is itself a projection of
specifications/vsphere/openapi/automation/vcenter.yaml at tag 9.0.0.0 of
vmware/vcf-api-specs. Only the operations that contract names are served; every
other target answers 404.

Every request is appended to a JSONL log so a test can assert the exact wire
shape that a client produced.

This module is part of the protected acceptance harness. It binds 127.0.0.1 only
and never talks to a real VMware endpoint.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT_PATH = os.path.join(HERE, "docs", "contract.json")

# Credentials the fixture endpoint accepts for Cis.Session_create. Dummy values,
# local to this harness.
VALID_USER = "administrator@vsphere.local"
VALID_PASSWORD = "Fixture-Passw0rd!"

# Documented default of Vcenter.Authorization.Permissions.IterationSpec.page_size.
DEFAULT_PAGE_SIZE = 200


def _load_contract():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


CONTRACT = _load_contract()
BASE_PATH = CONTRACT["server"]["basePath"]

# (METHOD, path, frozenset of query pairs) -> operationId, taken from the contract.
ROUTES = {}
for _op in CONTRACT["operations"]:
    _query = frozenset(parse_qsl(_op["query"])) if _op.get("query") else frozenset()
    ROUTES[(_op["method"], BASE_PATH + _op["path"], _query)] = _op["operationId"]

# Property names the contract allows in the Permissions_list request body.
_LIST_OP = next(o for o in CONTRACT["operations"]
                if o["operationId"] == "Vcenter.Authorization.Permissions_list")
BODY_MEMBERS = frozenset(_LIST_OP["requestBody"]["schema"]["properties"])
FILTER_MEMBERS = frozenset(
    CONTRACT["schemas"]["Vcenter.Authorization.Permissions.FilterSpec"]["properties"])
ITERATE_MEMBERS = frozenset(
    CONTRACT["schemas"]["Vcenter.Authorization.Permissions.IterationSpec"]["properties"])
SESSION_HEADER = CONTRACT["securitySchemes"]["api_key_auth"]["name"]


# --- fixture data ---------------------------------------------------------
# Shaped as Vcenter.Authorization.Permissions.ListItem. Deliberately stored in
# the ascending permission-id order the specification says a page arrives in,
# which is not the order the collected report has to end up in. One principal
# carries no domain, which the specification says means 'localos'.
PERMISSIONS = [
    {
        "permission": "perm-0001",
        "info": {
            "object": {"type": "Datacenter", "id": "datacenter-21"},
            "principal": {"type": "GROUP", "name": "vsphere-admins", "domain": "vsphere.local"},
            "role": "Admin",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0002",
        "info": {
            "object": {"type": "VirtualMachine", "id": "vm-4021"},
            "principal": {"type": "USER", "name": "backup-svc", "domain": "vsphere.local"},
            "role": "VirtualMachinePowerUser",
            "propagating": False,
        },
    },
    {
        "permission": "perm-0003",
        "info": {
            "object": {"type": "Folder", "id": "group-d1"},
            "principal": {"type": "USER", "name": "root"},
            "role": "Admin",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0004",
        "info": {
            "object": {"type": "Datacenter", "id": "datacenter-21"},
            "principal": {"type": "USER", "name": "audit-svc", "domain": "corp.example"},
            "role": "ReadOnly",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0005",
        "info": {
            "object": {"type": "ClusterComputeResource", "id": "domain-c8"},
            "principal": {"type": "GROUP", "name": "noc-operators", "domain": "corp.example"},
            "role": "ReadOnly",
            "propagating": False,
        },
    },
    {
        "permission": "perm-0006",
        "info": {
            "object": {"type": "Datacenter", "id": "datacenter-9"},
            "principal": {"type": "USER", "name": "audit-svc", "domain": "corp.example"},
            "role": "ReadOnly",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0007",
        "info": {
            "object": {"type": "Datacenter", "id": "datacenter-21"},
            "principal": {"type": "USER", "name": "audit-svc", "domain": "corp.example"},
            "role": "NoAccess",
            "propagating": False,
        },
    },
    # The next five records share most of their sort fields. Together they make
    # the verifier distinguish principal name, principal type, role and finally
    # permission id instead of merely checking the leading sort fields.
    {
        "permission": "perm-0008",
        "info": {
            "object": {"type": "HostSystem", "id": "host-17"},
            "principal": {"type": "USER", "name": "beta", "domain": "sort.example"},
            "role": "AlphaRole",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0010",
        "info": {
            "object": {"type": "HostSystem", "id": "host-17"},
            "principal": {"type": "USER", "name": "alpha", "domain": "sort.example"},
            "role": "ZetaRole",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0011",
        "info": {
            "object": {"type": "HostSystem", "id": "host-17"},
            "principal": {"type": "GROUP", "name": "beta", "domain": "sort.example"},
            "role": "ZetaRole",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0012",
        "info": {
            "object": {"type": "HostSystem", "id": "host-17"},
            "principal": {"type": "USER", "name": "beta", "domain": "sort.example"},
            "role": "ZetaRole",
            "propagating": True,
        },
    },
    {
        "permission": "perm-0013",
        "info": {
            "object": {"type": "HostSystem", "id": "host-17"},
            "principal": {"type": "USER", "name": "beta", "domain": "sort.example"},
            "role": "AlphaRole",
            "propagating": True,
        },
    },
    # Explicit JSON null exercises the same documented localos default as an
    # absent Principal.domain (perm-0003 exercises the absent form).
    {
        "permission": "perm-0014",
        "info": {
            "object": {"type": "ResourcePool", "id": "resgroup-4"},
            "principal": {"type": "USER", "name": "local-null", "domain": None},
            "role": "AuditRole",
            "propagating": True,
        },
    },
]


def _matches(item, spec):
    """Apply a Vcenter.Authorization.Permissions.FilterSpec to one item."""
    info = item["info"]
    if "roles" in spec and info["role"] not in spec["roles"]:
        return False
    if "is_propagating" in spec and bool(info["propagating"]) is not bool(spec["is_propagating"]):
        return False
    if "principals" in spec:
        principal = info["principal"]
        actual = (principal["type"], principal["name"],
                  principal.get("domain") or "localos")
        wanted = {(p.get("type"), p.get("name"), p.get("domain") or "localos")
                  for p in spec["principals"]}
        if actual not in wanted:
            return False
    if "objects" in spec:
        obj = info["object"]
        wanted = {(o.get("type"), o.get("id")) for o in spec["objects"]}
        if (obj["type"], obj["id"]) not in wanted:
            return False
    return True


def _encode_marker(offset, spec):
    """Opaque cursor. It carries the filter, which is why the specification
    forbids re-sending a FilterSpec alongside a marker."""
    raw = json.dumps({"o": offset, "f": spec}, sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_marker(marker):
    padding = "=" * (-len(marker) % 4)
    raw = base64.urlsafe_b64decode(marker + padding)
    payload = json.loads(raw.decode("utf-8"))
    return payload["o"], payload["f"]


class _BadRequest(Exception):
    def __init__(self, status, error_type, message):
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message


def _vapi_error(error_type, message):
    return {
        "error_type": error_type,
        "messages": [
            {
                "id": "com.vmware.vapi.std.errors.%s" % error_type.lower(),
                "default_message": message,
                "args": [],
            }
        ],
    }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockVcenter/9.0.0.0"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        return self.rfile.read(int(length))

    def _respond(self, status, payload=None):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _fail(self, exc):
        self._respond(exc.status, _vapi_error(exc.error_type, exc.message))

    def _record(self, method, parsed, raw_body, operation_id):
        try:
            parsed_body = json.loads(raw_body.decode("utf-8")) if raw_body else None
            body_valid_json = True
        except (ValueError, UnicodeDecodeError):
            parsed_body = None
            body_valid_json = False
        entry = {
            "seq": self.server.next_seq(),
            "operationId": operation_id,
            "method": method,
            "target": self.path,
            "path": parsed.path,
            "query": parsed.query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "raw_body": raw_body.decode("utf-8", "replace"),
            "body": parsed_body,
            "body_is_json": body_valid_json,
        }
        self.server.append_log(entry)

    # -- dispatch ---------------------------------------------------------
    def _dispatch(self, method):
        parsed = urlparse(self.path)
        raw_body = self._read_body()
        key = (method, parsed.path, frozenset(parse_qsl(parsed.query)))
        operation_id = ROUTES.get(key)
        self._record(method, parsed, raw_body, operation_id)

        if operation_id is None:
            self._respond(404, _vapi_error(
                "NOT_FOUND",
                "No operation in the pinned contract serves %s %s." % (method, self.path)))
            return
        try:
            handler = {
                "Cis.Session_create": self._session_create,
                "Cis.Session_delete": self._session_delete,
                "Vcenter.Authorization.Permissions_list": self._permissions_list,
            }[operation_id]
            handler(raw_body)
        except _BadRequest as exc:
            self._fail(exc)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    # -- operations -------------------------------------------------------
    def _session_create(self, raw_body):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            raise _BadRequest(401, "UNAUTHENTICATED",
                              "Cis.Session_create requires the basic_auth scheme.")
        try:
            decoded = base64.b64decode(header[len("Basic "):], validate=True).decode("utf-8")
            user, _, password = decoded.partition(":")
        except (binascii.Error, UnicodeDecodeError):
            raise _BadRequest(401, "UNAUTHENTICATED", "Malformed basic credentials.")
        if user != VALID_USER or password != VALID_PASSWORD:
            message = "Invalid credentials."
            if self.server.behavior["echo_auth_failure"]:
                # A defensive client must not trust an error body to keep the
                # credential secret on its behalf.
                message += " Rejected password: %s" % password
            raise _BadRequest(401, "UNAUTHENTICATED", message)
        token = self.server.issue_session()
        # The 201 response schema is a bare JSON string.
        self._respond(201, token)

    def _require_session(self):
        token = self.headers.get(SESSION_HEADER)
        if not token or not self.server.session_is_live(token):
            raise _BadRequest(401, "UNAUTHENTICATED",
                              "A live %s token is required." % SESSION_HEADER)
        return token

    def _session_delete(self, raw_body):
        token = self._require_session()
        if self.server.behavior["fail_logout"]:
            raise _BadRequest(503, "SERVICE_UNAVAILABLE",
                              "The session service is temporarily unavailable.")
        self.server.revoke_session(token)
        self._respond(204)

    def _permissions_list(self, raw_body):
        token = self._require_session()

        if self.server.behavior["fail_list_with_token"]:
            # The client is responsible for redacting secrets even when a
            # remote diagnostic improperly repeats one.
            raise _BadRequest(500, "INTERNAL_SERVER_ERROR",
                              "List failed for session %s." % token)

        if (self.server.behavior["null_final_marker"]
                and self.server.null_final_was_delivered()):
            raise _BadRequest(500, "INTERNAL_SERVER_ERROR",
                              "The client requested another page after a null marker.")

        if raw_body:
            try:
                body = json.loads(raw_body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise _BadRequest(400, "INVALID_ARGUMENT", "Request body is not valid JSON.")
        else:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise _BadRequest(400, "INVALID_ARGUMENT", "Request body must be a JSON object.")

        unknown = sorted(set(body) - BODY_MEMBERS)
        if unknown:
            raise _BadRequest(400, "INVALID_ARGUMENT",
                              "Unknown request body member(s): %s." % ", ".join(unknown))
        for name in ("filter", "iterate"):
            if name in body and not isinstance(body[name], dict):
                raise _BadRequest(400, "INVALID_ARGUMENT",
                                  "Member '%s' must be omitted, not sent as %s."
                                  % (name, json.dumps(body[name])))

        spec = body.get("filter", {})
        unknown = sorted(set(spec) - FILTER_MEMBERS)
        if unknown:
            raise _BadRequest(400, "INVALID_ARGUMENT",
                              "Unknown FilterSpec member(s): %s." % ", ".join(unknown))
        iterate = body.get("iterate", {})
        unknown = sorted(set(iterate) - ITERATE_MEMBERS)
        if unknown:
            raise _BadRequest(400, "INVALID_ARGUMENT",
                              "Unknown IterationSpec member(s): %s." % ", ".join(unknown))

        for container, name in ((spec, "filter"), (iterate, "iterate")):
            for member, value in sorted(container.items()):
                if value is None:
                    raise _BadRequest(400, "INVALID_ARGUMENT",
                                      "%s.%s was sent as null; an unset optional property "
                                      "must be omitted." % (name, member))

        marker = iterate.get("marker")
        if marker is not None and not isinstance(marker, str):
            raise _BadRequest(400, "INVALID_ARGUMENT", "iterate.marker must be a string.")

        # Documented 400 for this operation: "if both filter and marker are passed".
        if "filter" in body and marker is not None:
            raise _BadRequest(400, "INVALID_ARGUMENT",
                              "A FilterSpec and an iterate.marker cannot be passed together; "
                              "the marker already carries the filter.")

        page_size = iterate.get("page_size", DEFAULT_PAGE_SIZE)
        if isinstance(page_size, bool) or not isinstance(page_size, int):
            raise _BadRequest(400, "INVALID_ARGUMENT", "iterate.page_size must be an integer.")
        if page_size < 1:
            raise _BadRequest(400, "INVALID_ARGUMENT", "iterate.page_size must be positive.")

        if marker is not None:
            if marker == "" and self.server.behavior["empty_first_marker"]:
                continuation = self.server.consume_empty_marker()
                if continuation is None:
                    raise _BadRequest(400, "INVALID_ARGUMENT",
                                      "The empty marker was not returned by an earlier call.")
                offset, spec = continuation
            else:
                try:
                    offset, spec = _decode_marker(marker)
                except Exception:
                    raise _BadRequest(400, "INVALID_ARGUMENT",
                                      "iterate.marker was not returned by an earlier call to "
                                      "Vcenter.Authorization.Permissions_list.")
        else:
            offset = 0

        matching = [item for item in PERMISSIONS if _matches(item, spec)]
        page = matching[offset:offset + page_size]
        result = {"items": json.loads(json.dumps(page))}
        end = offset + len(page)
        if end < len(matching):
            if (self.server.behavior["empty_first_marker"]
                    and marker is None and offset == 0):
                # The schema does not impose minLength. The prompt deliberately
                # defines completion as missing/null, so an issued empty string
                # still has to be carried to the next request.
                self.server.remember_empty_marker(end, spec)
                result["marker"] = ""
            else:
                result["marker"] = _encode_marker(end, spec)
        elif self.server.behavior["null_final_marker"]:
            result["marker"] = None
            self.server.mark_null_final_delivered()
        self._respond(200, result)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, log_path):
        super().__init__(address, handler)
        self.log_path = log_path
        self._lock = threading.Lock()
        self._seq = 0
        self._sessions = set()
        self._issued = 0
        self.behavior = {
            "echo_auth_failure": False,
            "fail_list_with_token": False,
            "fail_logout": False,
            "empty_first_marker": False,
            "null_final_marker": False,
        }
        self._empty_marker = None
        self._null_final_delivered = False

    def next_seq(self):
        with self._lock:
            self._seq += 1
            return self._seq

    def append_log(self, entry):
        line = json.dumps(entry, sort_keys=True)
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def issue_session(self):
        with self._lock:
            self._issued += 1
            token = "fixture-session-%04d" % self._issued
            self._sessions.add(token)
            return token

    def session_is_live(self, token):
        with self._lock:
            return token in self._sessions

    def revoke_session(self, token):
        with self._lock:
            self._sessions.discard(token)

    def reset_behavior(self):
        with self._lock:
            for name in self.behavior:
                self.behavior[name] = False
            self._empty_marker = None
            self._null_final_delivered = False

    def configure(self, changes):
        unknown = sorted(set(changes) - set(self.behavior))
        if unknown:
            raise ValueError("unknown fixture behavior: %s" % ", ".join(unknown))
        with self._lock:
            self.behavior.update(changes)

    def remember_empty_marker(self, offset, spec):
        with self._lock:
            self._empty_marker = (offset, json.loads(json.dumps(spec)))

    def consume_empty_marker(self):
        with self._lock:
            value = self._empty_marker
            self._empty_marker = None
            return value

    def mark_null_final_delivered(self):
        with self._lock:
            self._null_final_delivered = True

    def null_final_was_delivered(self):
        with self._lock:
            return self._null_final_delivered


class MockVcenter:
    """Loopback vCenter fixture. Use as a context manager."""

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
        """Matches the specification's server template with host=127.0.0.1:<port>."""
        return "http://127.0.0.1:%d%s" % (self.port, BASE_PATH)

    def requests(self):
        """Every request the fixture has seen, in arrival order."""
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        entries.sort(key=lambda e: e["seq"])
        return entries

    def truncate_log(self):
        open(self.log_path, "w", encoding="utf-8").close()

    def reset_behavior(self):
        self._server.reset_behavior()

    def configure(self, **changes):
        self._server.configure(changes)


if __name__ == "__main__":
    import tempfile
    import time

    with MockVcenter(os.path.join(tempfile.gettempdir(), "mock_vcenter.log")) as mock:
        print(mock.base_url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
