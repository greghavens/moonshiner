#!/usr/bin/env python3
"""Loopback vCenter fixture pinned to docs/contract.json.

The routing table is built from the contract, so the fixture answers exactly the
five operations the contract names and nothing else. Every request is appended to
a JSON Lines log that the acceptance harness reads back.

Not a VMware product and not a simulation of one beyond the wire contract: it
serves HTTP on 127.0.0.1 only and never reaches the network.
"""
import argparse
import base64
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

SESSION_TOKEN = "sess-9f2c1d8a4b7e"
BASIC_USER = "svc-catalog@vsphere.local"
BASIC_PASSWORD = "VMw@re1!Catalog"

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Library inventory. sfo-w01-cl01 is the target; the other two exist so that a
# find that forgets to filter cannot accidentally land on a single answer.
LIBRARIES = {
    "lib-sfo-w01-cl01": {"name": "sfo-w01-cl01", "type": "LOCAL"},
    "lib-sfo-m01-cl01": {"name": "sfo-m01-cl01", "type": "LOCAL"},
    "lib-sfo-w01-sub01": {"name": "sfo-w01-cl01", "type": "SUBSCRIBED"},
}

# One item is already registered from a previous run of the job.
SEED_ITEMS = {
    "item-0007": {
        "library_id": "lib-sfo-w01-cl01",
        "name": "esx-9.0-image-profile",
        "type": "ovf",
        "description": "",
        "content_version": "1",
        "metadata_version": "1",
        "version": "1",
        "creation_time": "2026-01-14T09:12:44.000Z",
        "last_modified_time": "2026-01-14T09:12:44.000Z",
        "cached": True,
        "size": 4194304,
        "security_compliance": True,
    }
}

# The first committed create of this item answers 503 after the write, the way a
# response lost between vCenter and the client looks to the client.
GLITCH_ITEM_NAMES = {"photon-5.0-ova"}


def localizable(msg_id, message):
    return {"id": msg_id, "default_message": message, "args": []}


def vapi_error(error_type, msg_id, message):
    return {
        "error_type": error_type,
        "messages": [localizable(msg_id, message)],
    }


class Contract:
    """Routing table derived from docs/contract.json."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        self.base_path = doc["basePath"]
        self.routes = []
        for op_id, op in doc["operations"].items():
            spec_path = op["path"]
            template = spec_path.split("?", 1)[0]
            query = op.get("query", {})
            pattern = "^" + re.escape(self.base_path + template) + "$"
            pattern = re.sub(r"\\\{([A-Za-z_][A-Za-z0-9_]*)\\\}", r"(?P<\1>[^/]+)", pattern)
            self.routes.append(
                {
                    "operationId": op_id,
                    "method": op["method"],
                    "regex": re.compile(pattern),
                    "query": query,
                }
            )

    def resolve(self, method, target):
        split = urlsplit(target)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        for route in self.routes:
            if route["method"] != method:
                continue
            match = route["regex"].match(split.path)
            if not match:
                continue
            if route["query"] != query:
                continue
            return route["operationId"], match.groupdict()
        return None, {}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {k: dict(v) for k, v in SEED_ITEMS.items()}
        self.token_to_item = {}
        self.create_attempts = {}
        self.glitched = set()
        self.next_id = 1
        self.seq = 0

    def allocate_id(self):
        item_id = "item-%04d" % self.next_id
        self.next_id += 1
        return item_id


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcenter-fixture/9.0.0.0"
    sys_version = ""

    # -- plumbing ---------------------------------------------------------
    def log_message(self, *args):  # silence stderr chatter
        pass

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _respond(self, status, payload, entry, note=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        entry["status"] = status
        if note:
            entry["mockNote"] = note
        self.server.record(entry)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _dispatch(self, method):
        raw_body = self._read_body()
        split = urlsplit(self.path)
        operation_id, params = self.server.contract.resolve(method, self.path)
        entry = {
            "operationId": operation_id,
            "method": method,
            "target": self.path,
            "path": split.path,
            "query": split.query,
            "pathParameters": params,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw_body.decode("utf-8", "replace"),
        }
        with self.server.state.lock:
            self.server.state.seq += 1
            entry["seq"] = self.server.state.seq

        if operation_id is None:
            return self._respond(
                404,
                vapi_error(
                    "NOT_FOUND",
                    "com.vmware.vapi.rest.no_such_endpoint",
                    "No operation in docs/contract.json matches %s %s." % (method, self.path),
                ),
                entry,
                note="off-contract",
            )

        handler = getattr(self, "op_" + operation_id.replace(".", "_").replace("$", "_"))
        return handler(entry, params, raw_body)

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

    # -- shared checks ----------------------------------------------------
    def _require_session(self, entry):
        token = entry["headers"].get("vmware-api-session-id")
        if token != SESSION_TOKEN:
            self._respond(
                401,
                vapi_error(
                    "UNAUTHENTICATED",
                    "com.vmware.vapi.endpoint.method.authentication.required",
                    "A valid vmware-api-session-id header is required.",
                ),
                entry,
                note="missing-or-bad-session",
            )
            return False
        return True

    def _json_object(self, entry, raw_body, allowed, schema_name):
        """Parse a JSON object body and reject members the schema does not declare."""
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.vapi.rest.invalid_json",
                    "Request body is not valid JSON.",
                ),
                entry,
                note="bad-json",
            )
            return None
        if not isinstance(parsed, dict):
            self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.vapi.rest.invalid_body",
                    "Request body must be a %s object." % schema_name,
                ),
                entry,
                note="body-not-object",
            )
            return None
        unknown = sorted(set(parsed) - allowed)
        if unknown:
            self._respond(
                400,
                vapi_error(
                    "UNEXPECTED_INPUT",
                    "com.vmware.vapi.bindings.unexpected_field",
                    "%s does not declare: %s." % (schema_name, ", ".join(unknown)),
                ),
                entry,
                note="unexpected-input",
            )
            return None
        return parsed

    # -- operations -------------------------------------------------------
    def op_Cis_Session_create(self, entry, params, raw_body):
        header = entry["headers"].get("authorization", "")
        expected = base64.b64encode(
            ("%s:%s" % (BASIC_USER, BASIC_PASSWORD)).encode("utf-8")
        ).decode("ascii")
        if header != "Basic " + expected:
            return self._respond(
                401,
                vapi_error(
                    "UNAUTHENTICATED",
                    "com.vmware.vapi.endpoint.method.authentication.required",
                    "HTTP Basic credentials are required to create a session.",
                ),
                entry,
                note="bad-basic-auth",
            )
        if raw_body:
            return self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.vapi.rest.invalid_body",
                    "Cis.Session_create declares no request body.",
                ),
                entry,
                note="unexpected-body",
            )
        return self._respond(201, SESSION_TOKEN, entry, note="session-created")

    def op_Content_Library_find(self, entry, params, raw_body):
        if not self._require_session(entry):
            return None
        spec = self._json_object(
            entry, raw_body, {"name", "type", "storage_backing"}, "Content.Library.FindSpec"
        )
        if spec is None:
            return None
        if not spec:
            return self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.content.library.find.empty_spec",
                    "No properties are specified in the spec.",
                ),
                entry,
                note="empty-spec",
            )
        matches = []
        for library_id, library in sorted(LIBRARIES.items()):
            name = spec.get("name")
            if name is not None and library["name"].lower() != str(name).lower():
                continue
            kind = spec.get("type")
            if kind is not None and library["type"] != kind:
                continue
            matches.append(library_id)
        return self._respond(200, matches, entry, note="matched-%d" % len(matches))

    def op_Content_Library_Item_find(self, entry, params, raw_body):
        if not self._require_session(entry):
            return None
        spec = self._json_object(
            entry,
            raw_body,
            {"name", "library_id", "source_id", "type", "cached"},
            "Content.Library.Item.FindSpec",
        )
        if spec is None:
            return None
        if not spec:
            return self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.content.library.item.find.empty_spec",
                    "No properties are specified in the spec.",
                ),
                entry,
                note="empty-spec",
            )
        with self.server.state.lock:
            snapshot = {k: dict(v) for k, v in self.server.state.items.items()}
        matches = []
        for item_id, item in sorted(snapshot.items()):
            name = spec.get("name")
            if name is not None and item["name"].lower() != str(name).lower():
                continue
            library_id = spec.get("library_id")
            if library_id is not None and item["library_id"] != library_id:
                continue
            kind = spec.get("type")
            if kind is not None and (item.get("type") or "").lower() != str(kind).lower():
                continue
            source_id = spec.get("source_id")
            if source_id is not None and item.get("source_id") != source_id:
                continue
            cached = spec.get("cached")
            if cached is not None and bool(item.get("cached")) != bool(cached):
                continue
            matches.append(item_id)
        return self._respond(200, matches, entry, note="matched-%d" % len(matches))

    def op_Content_Library_Item_create(self, entry, params, raw_body):
        if not self._require_session(entry):
            return None
        client_token = entry["headers"].get("client-token")
        if client_token is not None and not UUID_RE.match(client_token):
            return self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.content.library.item.create.invalid_client_token",
                    "The clientToken does not conform to the UUID format.",
                ),
                entry,
                note="bad-client-token",
            )
        model = self._json_object(
            entry,
            raw_body,
            {
                "id",
                "library_id",
                "content_version",
                "creation_time",
                "description",
                "last_modified_time",
                "last_sync_time",
                "metadata_version",
                "name",
                "cached",
                "size",
                "type",
                "version",
                "source_id",
                "security_compliance",
                "certificate_verification_info",
            },
            "Content.Library.ItemModel",
        )
        if model is None:
            return None

        library_id = model.get("library_id")
        name = model.get("name")
        if not library_id or not name:
            return self._respond(
                400,
                vapi_error(
                    "INVALID_ARGUMENT",
                    "com.vmware.content.library.item.create.missing_property",
                    "library_id and name must be provided for the create operation.",
                ),
                entry,
                note="missing-required",
            )
        if library_id not in LIBRARIES:
            return self._respond(
                404,
                vapi_error(
                    "NOT_FOUND",
                    "com.vmware.content.library.not_found",
                    "Library %s does not exist." % library_id,
                ),
                entry,
                note="library-not-found",
            )

        # Supplemental acceptance scenarios use these two server switches to
        # exercise the retry contract without replacing the real HTTP calls.
        # A terminal 400 models a create-time race after find; pre-commit 503s
        # model transient failures that make the attempt ceiling observable.
        if self.server.reject_creates_with_400:
            return self._respond(
                400,
                vapi_error(
                    "ALREADY_EXISTS",
                    "com.vmware.content.library.item.already_exists",
                    "A concurrent request registered this item after find.",
                ),
                entry,
                note="terminal-400",
            )

        with self.server.state.lock:
            state = self.server.state
            attempt_key = client_token or "<missing-client-token>"
            attempt = state.create_attempts.get(attempt_key, 0) + 1
            state.create_attempts[attempt_key] = attempt
            if attempt <= self.server.precommit_create_failures:
                return self._respond(
                    503,
                    vapi_error(
                        "SERVICE_UNAVAILABLE",
                        "com.vmware.vapi.endpoint.service.unavailable",
                        "The content library service is temporarily unavailable before commit.",
                    ),
                    entry,
                    note="precommit-503",
                )
            # A create replayed with the token that already produced an item is
            # answered with that item, which is what makes the call retryable.
            if client_token and client_token in state.token_to_item:
                existing = state.token_to_item[client_token]
                return self._respond(201, existing, entry, note="idempotent-replay")
            duplicate = next(
                (
                    item_id
                    for item_id, item in state.items.items()
                    if item["library_id"] == library_id and item["name"] == name
                ),
                None,
            )
            if duplicate is not None:
                return self._respond(
                    400,
                    vapi_error(
                        "ALREADY_EXISTS",
                        "com.vmware.content.library.item.already_exists",
                        "Library item %s already exists in library %s." % (name, library_id),
                    ),
                    entry,
                    note="already-exists",
                )
            item_id = state.allocate_id()
            state.items[item_id] = {
                "library_id": library_id,
                "name": name,
                "type": model.get("type", ""),
                "description": model.get("description") or "",
                "content_version": "1",
                "metadata_version": "1",
                "version": "1",
                "creation_time": "2026-02-02T11:00:00.000Z",
                "last_modified_time": "2026-02-02T11:00:00.000Z",
                "cached": False,
                "size": 0,
                "security_compliance": True,
            }
            if client_token:
                state.token_to_item[client_token] = item_id
            glitch = name in GLITCH_ITEM_NAMES and name not in state.glitched
            if glitch:
                state.glitched.add(name)

        if glitch:
            return self._respond(
                503,
                vapi_error(
                    "SERVICE_UNAVAILABLE",
                    "com.vmware.vapi.endpoint.service.unavailable",
                    "The content library service is temporarily unavailable.",
                ),
                entry,
                note="created-then-503",
            )
        return self._respond(201, item_id, entry, note="created")

    def op_Content_Library_Item_get(self, entry, params, raw_body):
        if not self._require_session(entry):
            return None
        item_id = params.get("libraryItemId")
        with self.server.state.lock:
            item = self.server.state.items.get(item_id)
            item = dict(item) if item else None
        if item is None:
            return self._respond(
                404,
                vapi_error(
                    "NOT_FOUND",
                    "com.vmware.content.library.item.not_found",
                    "No item with id %s exists." % item_id,
                ),
                entry,
                note="item-not-found",
            )
        return self._respond(200, item, entry, note="ok")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        contract,
        log_path,
        precommit_create_failures=0,
        reject_creates_with_400=False,
    ):
        super().__init__(address, Handler)
        self.contract = contract
        self.state = State()
        self.log_path = log_path
        self.log_lock = threading.Lock()
        self.precommit_create_failures = precommit_create_failures
        self.reject_creates_with_400 = reject_creates_with_400

    def record(self, entry):
        line = json.dumps(entry, sort_keys=True)
        with self.log_lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--precommit-create-failures", type=int, default=0)
    parser.add_argument("--reject-creates-with-400", action="store_true")
    args = parser.parse_args()
    if args.precommit_create_failures < 0:
        parser.error("--precommit-create-failures must be non-negative")

    contract = Contract(args.contract)
    with open(args.log, "w", encoding="utf-8"):
        pass

    server = Server(
        ("127.0.0.1", args.port),
        contract,
        args.log,
        precommit_create_failures=args.precommit_create_failures,
        reject_creates_with_400=args.reject_creates_with_400,
    )
    port = server.server_address[1]
    tmp = args.ready + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"port": port, "baseUrl": "http://127.0.0.1:%d" % port}, handle)
    os.replace(tmp, args.ready)
    sys.stderr.write("vcenter fixture listening on 127.0.0.1:%d\n" % port)
    sys.stderr.flush()
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
