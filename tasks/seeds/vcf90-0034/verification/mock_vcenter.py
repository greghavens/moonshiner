#!/usr/bin/env python3
"""Contract-pinned loopback vCenter used by protected verification.

The service answers only the operations named in docs/contract.json, which is a
projection of the vSphere Automation API specification at tag 9.0.0.0 of
vmware/vcf-api-specs.  Everything else is refused so the harness can prove the
solution stayed inside the contract.

The fixture session token issued first is deliberately short lived: it stops
being accepted once it has served SESSION_REQUEST_BUDGET authenticated
requests, so a run that does more work than that must obtain a new token and
resume without repeating what already succeeded.
"""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent
PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
PINNED_API_VERSION = "9.0.0.0"
EXPECTED_OPERATION_IDS = {
    "Cis.Session_create",
    "Cis.Session_delete",
    "Vcenter.VM_list",
    "Cis.Tagging.Category_list",
    "Cis.Tagging.Category_get",
    "Cis.Tagging.Tag_listTagsForCategory",
    "Cis.Tagging.Tag_get",
    "Cis.Tagging.Tag_create",
    "Cis.Tagging.TagAssociation_attachTagToMultipleObjects",
}

USERNAME = "administrator@vsphere.local"
PASSWORD = "dummy-vcenter-pass-90"
SESSION_TOKEN_PREFIX = "dummy-vcenter-session-"

# The first token stops working once it has served this many authenticated
# requests.  A correct run needs more requests than this, so it must refresh.
SESSION_REQUEST_BUDGET = 11

VM_INVENTORY = [
    {"vm": "vm-101", "name": "web-01", "power_state": "POWERED_ON",
     "cpu_count": 4, "memory_size_mib": 8192},
    {"vm": "vm-102", "name": "web-02", "power_state": "POWERED_ON",
     "cpu_count": 4, "memory_size_mib": 8192},
    {"vm": "vm-201", "name": "app-01", "power_state": "POWERED_ON",
     "cpu_count": 8, "memory_size_mib": 16384},
    {"vm": "vm-202", "name": "app-02", "power_state": "POWERED_ON",
     "cpu_count": 8, "memory_size_mib": 16384},
    {"vm": "vm-203", "name": "app-03", "power_state": "POWERED_OFF",
     "cpu_count": 8, "memory_size_mib": 16384},
    {"vm": "vm-301", "name": "batch-01", "power_state": "POWERED_OFF",
     "cpu_count": 2, "memory_size_mib": 4096},
    {"vm": "vm-302", "name": "batch-02", "power_state": "POWERED_OFF",
     "cpu_count": 2, "memory_size_mib": 4096},
    # Present in the inventory but absent from the plan: an unfiltered listing
    # would pick this up and a filtered one must not.
    {"vm": "vm-901", "name": "legacy-01", "power_state": "SUSPENDED",
     "cpu_count": 1, "memory_size_mib": 2048},
]

CATEGORY_PREFIX = "urn:vmomi:InventoryServiceCategory:"
TAG_PREFIX = "urn:vmomi:InventoryServiceTag:"

# Listed in this order; the category the plan needs is deliberately last so a
# name lookup has to walk the whole listing.
SEED_CATEGORIES = [
    {
        "id": CATEGORY_PREFIX + "0a1e2c40-os-family:GLOBAL",
        "name": "os-family",
        "description": "Guest operating system family",
        "cardinality": "SINGLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
    {
        "id": CATEGORY_PREFIX + "1b2f3d51-backup-policy:GLOBAL",
        "name": "backup-policy",
        "description": "Backup policy assigned to the workload",
        "cardinality": "SINGLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
    {
        "id": CATEGORY_PREFIX + "2c3a4e62-workload-tier:GLOBAL",
        "name": "workload-tier",
        "description": "Workload tier applied by platform automation",
        "cardinality": "MULTIPLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
]
PLAN_CATEGORY_ID = SEED_CATEGORIES[2]["id"]

SEED_TAGS = [
    {
        "id": TAG_PREFIX + "3d4b5f73-tier-gold:GLOBAL",
        "category_id": PLAN_CATEGORY_ID,
        "name": "tier-gold",
        "description": "Gold tier workloads",
        "used_by": [],
    },
    {
        "id": TAG_PREFIX + "4e5c6a84-legacy-tier:GLOBAL",
        "category_id": PLAN_CATEGORY_ID,
        "name": "legacy-tier",
        "description": "Retired tier retained for reporting",
        "used_by": [],
    },
    {
        "id": TAG_PREFIX + "5f6d7b95-nightly:GLOBAL",
        "category_id": SEED_CATEGORIES[1]["id"],
        "name": "nightly",
        "description": "Nightly backup policy",
        "used_by": [],
    },
]

# Deterministic identifiers for tags the run is expected to create.
CREATED_TAG_IDS = {
    "tier-silver": TAG_PREFIX + "6a7e8c06-tier-silver:GLOBAL",
    "tier-bronze": TAG_PREFIX + "7b8f9d17-tier-bronze:GLOBAL",
}


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    path_template: str
    pattern: re.Pattern[str]
    required_query: dict[str, str]

    @staticmethod
    def from_contract(operation: dict[str, Any]) -> "Route":
        template = operation["wirePath"]
        pieces: list[str] = []
        cursor = 0
        for match in re.finditer(r"\{([A-Za-z][A-Za-z0-9]*)\}", template):
            pieces.append(re.escape(template[cursor : match.start()]))
            pieces.append(f"(?P<{match.group(1)}>[^/]+)")
            cursor = match.end()
        pieces.append(re.escape(template[cursor:]))
        return Route(
            operation_id=operation["operationId"],
            method=operation["method"].upper(),
            path_template=template,
            pattern=re.compile("^" + "".join(pieces) + "$"),
            required_query=dict(operation.get("requiredQuery") or {}),
        )


def load_routes() -> list[Route]:
    contract = json.loads(
        (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
    )
    source = contract.get("source", {})
    if source.get("commitSha") != PINNED_COMMIT:
        raise RuntimeError("contract is not pinned to the expected repository commit")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract has an unexpected specification path")
    if source.get("apiVersion") != PINNED_API_VERSION:
        raise RuntimeError("contract is not the 9.0.0.0 revision of the specification")
    operations = contract.get("operations", [])
    if {item.get("operationId") for item in operations} != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the loopback service")
    return [Route.from_contract(item) for item in operations]


class MockState:
    def __init__(self, routes: list[Route], request_log: Path) -> None:
        self.routes = routes
        self.request_log = request_log
        self.lock = threading.Lock()
        self.sequence = 0
        self.tokens_issued = 0
        self.token_usage: dict[str, int] = {}
        self.categories = [dict(item) for item in SEED_CATEGORIES]
        self.tags = [dict(item) for item in SEED_TAGS]
        self.attachments: list[dict[str, Any]] = []
        self.deleted_tokens: set[str] = set()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def match(self, method: str, path: str, query: dict[str, list[str]]):
        for route in self.routes:
            if route.method != method:
                continue
            matched = route.pattern.fullmatch(path)
            if not matched:
                continue
            if any(query.get(key, [None])[0] != value
                   for key, value in route.required_query.items()):
                continue
            # An operation without a required action must not absorb a request
            # that carries one; the specification separates them by query.
            if not route.required_query and "action" in query:
                continue
            return route, {
                key: unquote(value)
                for key, value in matched.groupdict().items()
            }
        return None, {}

    def issue_token(self) -> str:
        with self.lock:
            self.tokens_issued += 1
            token = f"{SESSION_TOKEN_PREFIX}{self.tokens_issued}"
            self.token_usage[token] = 0
            return token

    def spend_token(self, token: str) -> bool:
        """Consume one authenticated request; False once the token is spent."""
        with self.lock:
            if token not in self.token_usage or token in self.deleted_tokens:
                return False
            first_token = f"{SESSION_TOKEN_PREFIX}1"
            if token == first_token and self.token_usage[token] >= SESSION_REQUEST_BUDGET:
                return False
            self.token_usage[token] += 1
            return True

    def append_log(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            record["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, ContractHandler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - reject operations outside contract
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        query = parse_qs(target.query, keep_blank_values=True)
        state = self.server.state
        route, parameters = state.match(self.command, target.path, query)
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(length)
        session_id = self.headers.get("vmware-api-session-id")

        if route is None:
            status, response = 404, self._error(
                "NOT_FOUND",
                f"off-contract request: {self.command} {self.path}",
            )
        elif route.operation_id == "Cis.Session_create":
            status, response = self._create_session(body)
        elif session_id is None:
            status, response = 401, self._error(
                "UNAUTHENTICATED", "vmware-api-session-id header is required"
            )
        elif not state.spend_token(session_id):
            status, response = 401, self._error(
                "UNAUTHENTICATED",
                "the session token is no longer valid; create a new session",
            )
        else:
            status, response = self._handle(route.operation_id, parameters, query, body)

        state.append_log(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "pathParameters": dict(parameters),
                "rawQuery": target.query,
                "query": {key: list(values) for key, values in query.items()},
                "headers": {
                    name.lower(): value.strip()
                    for name, value in self.headers.items()
                },
                "authorization": self.headers.get("Authorization"),
                "sessionId": session_id,
                "contentType": self.headers.get("Content-Type"),
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
            }
        )
        self._send_json(status, response)

    # -- operations -------------------------------------------------------

    def _handle(
        self,
        operation_id: str,
        parameters: dict[str, str],
        query: dict[str, list[str]],
        body: bytes,
    ) -> tuple[int, Any]:
        if operation_id == "Cis.Session_delete":
            return self._delete_session()
        if operation_id == "Vcenter.VM_list":
            return self._list_vms(query)
        if operation_id == "Cis.Tagging.Category_list":
            return self._list_categories()
        if operation_id == "Cis.Tagging.Category_get":
            return self._get_category(parameters)
        if operation_id == "Cis.Tagging.Tag_listTagsForCategory":
            return self._list_tags_for_category(body)
        if operation_id == "Cis.Tagging.Tag_get":
            return self._get_tag(parameters)
        if operation_id == "Cis.Tagging.Tag_create":
            return self._create_tag(body)
        if operation_id == "Cis.Tagging.TagAssociation_attachTagToMultipleObjects":
            return self._attach_tag(parameters, body)
        return 500, self._error("HANDLER_MISSING", "contract handler is missing")

    def _create_session(self, body: bytes) -> tuple[int, Any]:
        if body:
            return 400, self._error(
                "INVALID_ARGUMENT", "session creation does not take a request body"
            )
        if self.headers.get("vmware-api-session-id") is not None:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "session creation authenticates with basic_auth, not an existing token",
            )
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Basic "):
            return 401, self._error(
                "UNAUTHENTICATED", "basic authentication is required"
            )
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return 401, self._error("UNAUTHENTICATED", "malformed basic credentials")
        user, _, secret = decoded.partition(":")
        if user != USERNAME or secret != PASSWORD:
            return 401, self._error("UNAUTHENTICATED", "invalid dummy credentials")
        return 201, self.server.state.issue_token()

    def _delete_session(self) -> tuple[int, Any]:
        token = self.headers.get("vmware-api-session-id")
        with self.server.state.lock:
            self.server.state.deleted_tokens.add(token)
        return 204, None

    def _list_vms(self, query: dict[str, list[str]]) -> tuple[int, Any]:
        allowed = {
            "vms", "names", "folders", "datacenters",
            "hosts", "clusters", "resource_pools", "power_states",
        }
        unknown = sorted(set(query) - allowed)
        if unknown:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "filter properties are not defined by the contract: "
                + ", ".join(unknown),
            )
        names = set(query.get("names", []))
        identifiers = set(query.get("vms", []))
        states = set(query.get("power_states", []))
        matches = [
            dict(vm) for vm in VM_INVENTORY
            if (not names or vm["name"] in names)
            and (not identifiers or vm["vm"] in identifiers)
            and (not states or vm["power_state"] in states)
        ]
        return 200, matches

    def _list_categories(self) -> tuple[int, Any]:
        return 200, [item["id"] for item in self.server.state.categories]

    def _get_category(self, parameters: dict[str, str]) -> tuple[int, Any]:
        category_id = parameters.get("categoryId", "")
        for item in self.server.state.categories:
            if item["id"] == category_id:
                return 200, dict(item)
        return 404, self._error("NOT_FOUND", f"no category {category_id}")

    def _list_tags_for_category(self, body: bytes) -> tuple[int, Any]:
        payload = self._json(body)
        if not isinstance(payload, dict) or set(payload) != {"category_id"}:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "request body must carry exactly the required category_id property",
            )
        category_id = payload["category_id"]
        if not any(item["id"] == category_id for item in self.server.state.categories):
            return 404, self._error("NOT_FOUND", f"no category {category_id}")
        return 200, [
            item["id"] for item in self.server.state.tags
            if item["category_id"] == category_id
        ]

    def _get_tag(self, parameters: dict[str, str]) -> tuple[int, Any]:
        tag_id = parameters.get("tagId", "")
        for item in self.server.state.tags:
            if item["id"] == tag_id:
                return 200, dict(item)
        return 404, self._error("NOT_FOUND", f"no tag {tag_id}")

    def _create_tag(self, body: bytes) -> tuple[int, Any]:
        payload = self._json(body)
        if not isinstance(payload, dict):
            return 400, self._error("INVALID_ARGUMENT", "request body must be an object")
        required = {"name", "description", "category_id"}
        allowed = required | {"tag_id"}
        if not required <= set(payload) or not set(payload) <= allowed:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "Cis.Tagging.Tag.CreateSpec requires name, description and "
                "category_id, and allows only tag_id besides them",
            )
        category_id = payload["category_id"]
        if not any(item["id"] == category_id for item in self.server.state.categories):
            return 404, self._error("NOT_FOUND", f"no category {category_id}")
        with self.server.state.lock:
            for item in self.server.state.tags:
                if (item["category_id"] == category_id
                        and item["name"] == payload["name"]):
                    return 400, self._error(
                        "ALREADY_EXISTS",
                        f"tag {payload['name']} already exists in the category",
                    )
            tag_id = payload.get("tag_id") or CREATED_TAG_IDS.get(
                payload["name"],
                f"{TAG_PREFIX}generated-{len(self.server.state.tags)}:GLOBAL",
            )
            self.server.state.tags.append(
                {
                    "id": tag_id,
                    "category_id": category_id,
                    "name": payload["name"],
                    "description": payload["description"],
                    "used_by": [],
                }
            )
        return 201, tag_id

    def _attach_tag(
        self, parameters: dict[str, str], body: bytes
    ) -> tuple[int, Any]:
        tag_id = parameters.get("tagId", "")
        if not any(item["id"] == tag_id for item in self.server.state.tags):
            return 404, self._error("NOT_FOUND", f"no tag {tag_id}")
        payload = self._json(body)
        if not isinstance(payload, dict) or set(payload) != {"object_ids"}:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "request body must carry exactly the required object_ids property",
            )
        object_ids = payload["object_ids"]
        if not isinstance(object_ids, list) or not object_ids:
            return 400, self._error(
                "INVALID_ARGUMENT", "object_ids must be a non-empty array"
            )
        known = {vm["vm"] for vm in VM_INVENTORY}
        for entry in object_ids:
            if not isinstance(entry, dict) or set(entry) != {"type", "id"}:
                return 400, self._error(
                    "INVALID_ARGUMENT",
                    "each object id must be a Vapi.Std.DynamicID with type and id",
                )
            if entry["type"] != "VirtualMachine" or entry["id"] not in known:
                return 400, self._error(
                    "INVALID_ARGUMENT",
                    f"unknown object {entry.get('type')}:{entry.get('id')}",
                )
        # Exercise the operation's contract-defined unsuccessful BatchResult
        # without turning it into an HTTP transport failure. The main plan does
        # not contain legacy-01; a protected negative test uses it explicitly.
        if any(entry["id"] == "vm-901" for entry in object_ids):
            return 200, {
                "success": False,
                "error_messages": [
                    {
                        "id": "com.vmware.vapi.fixture.attachment_refused",
                        "default_message": "fixture refused the attachment batch",
                        "args": [],
                    }
                ],
            }
        with self.server.state.lock:
            self.server.state.attachments.append(
                {"tag_id": tag_id, "object_ids": object_ids}
            )
        return 200, {"success": True, "error_messages": []}

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def _json(body: bytes) -> Any:
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _error(error_type: str, message: str) -> dict[str, Any]:
        return {
            "error_type": error_type,
            "messages": [
                {
                    "id": f"com.vmware.vapi.fixture.{error_type.lower()}",
                    "default_message": message,
                    "args": [],
                }
            ],
        }

    def _send_json(self, status: int, payload: Any) -> None:
        if status == 204 or payload is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)


def write_atomic(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: mock_vcenter.py CERT_FILE KEY_FILE PORT_FILE REQUEST_LOG"
        )
    cert_file, key_file, port_file, request_log = (
        Path(sys.argv[1]).resolve(),
        Path(sys.argv[2]).resolve(),
        Path(sys.argv[3]).resolve(),
        Path(sys.argv[4]).resolve(),
    )
    state = MockState(load_routes(), request_log)
    server = ContractServer(("127.0.0.1", 0), state)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    write_atomic(port_file, str(server.server_port))
    server.serve_forever()


if __name__ == "__main__":
    main()
