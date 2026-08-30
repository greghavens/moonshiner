#!/usr/bin/env python3
"""Contract-pinned loopback vCenter used by the protected verification.

The service answers only the four operations named in docs/contract.json, which
is a projection of the vSphere Automation API specification at tag 9.0.0.0 of
vmware/vcf-api-specs.  Anything else is refused and still recorded, so the
harness can prove the run stayed inside the contract.

Both tagging listings are marker driven exactly as the specification describes
them: `names` and `iterate` are form/explode query parameters, so the wire keys
are `names`, `marker` and `page_size` at the top level of the query string; a
filter may not travel together with a marker; and a response without a marker
means the collection has been returned in full.  The service also refuses any
query key it does not know and any key sent with an empty value, so an unset
optional field has to be omitted rather than serialised empty.
"""

from __future__ import annotations

import base64
import binascii
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
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent

PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
PINNED_API_VERSION = "9.0.0.0"
PINNED_TAG = "9.0.0.0"
EXPECTED_OPERATION_IDS = {
    "Cis.Session_create",
    "Cis.Session_delete",
    "Vcenter.Tagging.Categories_list",
    "Vcenter.Tagging.Tags_list",
}

USERNAME = "administrator@vsphere.local"
PASSWORD = "dummy-vcenter-pass-90"
SESSION_TOKEN_PREFIX = "dummy-vcenter-session-"

# The specification documents a default page size of 20 for both listings.  This
# fixture keeps that default and additionally caps every page, the way a real
# deployment caps a client request, so the collections always span more than one
# page and have to be followed to the end.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 4

CATEGORY_PREFIX = "urn:vmomi:InventoryServiceCategory:"
TAG_PREFIX = "urn:vmomi:InventoryServiceTag:"

# Deliberately not in the order the answer has to be emitted in, so a run that
# simply concatenates the pages it received cannot produce a stable ordering.
SEED_CATEGORIES: list[dict[str, Any]] = [
    {
        "category_id": CATEGORY_PREFIX + "5e6f7081-owner:GLOBAL",
        "name": "owner",
        "description": "Team accountable for the workload",
        "cardinality": "SINGLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
    {
        "category_id": CATEGORY_PREFIX + "1a2b3c4d-workload-tier:GLOBAL",
        "name": "workload-tier",
        "description": "Workload tier applied by platform automation",
        "cardinality": "MULTIPLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
    {
        "category_id": CATEGORY_PREFIX + "3c4d5e6f-Compliance:GLOBAL",
        "name": "Compliance",
        "description": "Compliance regime the workload is in scope for",
        "cardinality": "MULTIPLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
    {
        "category_id": CATEGORY_PREFIX + "4d5e6f70-os-family:GLOBAL",
        "name": "os-family",
        "description": "Guest operating system family",
        "cardinality": "SINGLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
    {
        "category_id": CATEGORY_PREFIX + "2b3c4d5e-backup-policy:GLOBAL",
        "name": "backup-policy",
        "description": "Backup policy assigned to the workload",
        "cardinality": "SINGLE",
        "associable_types": ["VirtualMachine"],
        "used_by": [],
    },
]

_CATEGORY_BY_NAME = {item["name"]: item["category_id"] for item in SEED_CATEGORIES}

SEED_TAGS: list[dict[str, Any]] = [
    {
        "tag": TAG_PREFIX + "a1b2c3d4-tier-gold:GLOBAL",
        "name": "tier-gold",
        "category": _CATEGORY_BY_NAME["workload-tier"],
        "description": "Gold tier workloads",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "b2c3d4e5-SOC2:GLOBAL",
        "name": "SOC2",
        "category": _CATEGORY_BY_NAME["Compliance"],
        "description": "In scope for SOC 2 reporting",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "c3d4e5f6-windows2022:GLOBAL",
        "name": "windows2022",
        "category": _CATEGORY_BY_NAME["os-family"],
        "description": "Windows Server 2022 guests",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "d4e5f607-platform-team:GLOBAL",
        "name": "platform-team",
        "category": _CATEGORY_BY_NAME["owner"],
        "description": "Owned by the platform team",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "e5f60718-hourly:GLOBAL",
        "name": "hourly",
        "category": _CATEGORY_BY_NAME["backup-policy"],
        "description": "Hourly backup policy",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "f6071829-Tier-Platinum:GLOBAL",
        "name": "Tier-Platinum",
        "category": _CATEGORY_BY_NAME["workload-tier"],
        "description": "Platinum tier workloads",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "0718293a-rhel9:GLOBAL",
        "name": "rhel9",
        "category": _CATEGORY_BY_NAME["os-family"],
        "description": "Red Hat Enterprise Linux 9 guests",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "18293a4b-pci-dss:GLOBAL",
        "name": "pci-dss",
        "category": _CATEGORY_BY_NAME["Compliance"],
        "description": "In scope for PCI DSS",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "293a4b5c-app-team:GLOBAL",
        "name": "app-team",
        "category": _CATEGORY_BY_NAME["owner"],
        "description": "Owned by the application team",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "3a4b5c6d-nightly:GLOBAL",
        "name": "nightly",
        "category": _CATEGORY_BY_NAME["backup-policy"],
        "description": "Nightly backup policy",
        "used_by": [],
    },
    {
        "tag": TAG_PREFIX + "4b5c6d7e-tier-silver:GLOBAL",
        "name": "tier-silver",
        "category": _CATEGORY_BY_NAME["workload-tier"],
        "description": "Silver tier workloads",
        "used_by": [],
    },
]


@dataclass(frozen=True)
class Route:
    operation_id: str
    method: str
    wire_path: str
    query_keys: frozenset[str]

    @staticmethod
    def from_contract(operation: dict[str, Any]) -> "Route":
        keys: set[str] = set()
        for parameter in operation.get("queryParameters", []):
            keys.update(parameter.get("wireKeys", []))
        return Route(
            operation_id=operation["operationId"],
            method=operation["method"].upper(),
            wire_path=operation["wirePath"],
            query_keys=frozenset(keys),
        )


def load_routes() -> dict[tuple[str, str], Route]:
    contract = json.loads(
        (ROOT / "docs" / "contract.json").read_text(encoding="utf-8")
    )
    source = contract.get("source", {})
    if source.get("commitSha") != PINNED_COMMIT:
        raise RuntimeError("contract is not pinned to the expected repository commit")
    if source.get("repositoryTag") != PINNED_TAG:
        raise RuntimeError("contract is not pinned to the 9.0.0.0 repository tag")
    if source.get("specPath") != PINNED_SPEC_PATH:
        raise RuntimeError("contract has an unexpected specification path")
    if source.get("apiVersion") != PINNED_API_VERSION:
        raise RuntimeError("contract is not the 9.0.0.0 revision of the specification")
    operations = contract.get("operations", [])
    if {item.get("operationId") for item in operations} != EXPECTED_OPERATION_IDS:
        raise RuntimeError("contract operation set does not match the loopback service")
    routes: dict[tuple[str, str], Route] = {}
    for operation in operations:
        route = Route.from_contract(operation)
        routes[(route.method, route.wire_path)] = route
    return routes


def encode_marker(collection: str, offset: int, names: list[str] | None) -> str:
    payload = json.dumps(
        {"c": collection, "o": offset, "n": names},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_marker(marker: str) -> dict[str, Any] | None:
    padded = marker + "=" * (-len(marker) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


class MockState:
    def __init__(self, routes: dict[tuple[str, str], Route], request_log: Path) -> None:
        self.routes = routes
        self.request_log = request_log
        self.lock = threading.Lock()
        self.sequence = 0
        self.tokens_issued = 0
        self.live_tokens: set[str] = set()
        self.issued_markers: set[str] = set()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def issue_token(self) -> str:
        with self.lock:
            self.tokens_issued += 1
            token = f"{SESSION_TOKEN_PREFIX}{self.tokens_issued}"
            self.live_tokens.add(token)
            return token

    def is_live(self, token: str | None) -> bool:
        with self.lock:
            return token is not None and token in self.live_tokens

    def revoke(self, token: str) -> None:
        with self.lock:
            self.live_tokens.discard(token)

    def remember_marker(self, marker: str) -> None:
        with self.lock:
            self.issued_markers.add(marker)

    def knows_marker(self, marker: str) -> bool:
        with self.lock:
            return marker in self.issued_markers

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

    def do_PUT(self) -> None:  # noqa: N802 - refuse operations outside the contract
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802 - refuse operations outside the contract
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def _dispatch(self) -> None:
        target = urlsplit(self.path)
        query = parse_qs(target.query, keep_blank_values=True)
        state = self.server.state
        route = state.routes.get((self.command, target.path))
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(length)
        session_id = self.headers.get("vmware-api-session-id")
        issued_token: str | None = None
        response_marker: str | None = None
        response_items: int | None = None

        if route is None:
            status, response = 404, self._error(
                "NOT_FOUND", f"off-contract request: {self.command} {self.path}"
            )
        elif route.operation_id == "Cis.Session_create":
            status, response = self._create_session(query, body)
            if status == 201:
                issued_token = response
        elif not state.is_live(session_id):
            status, response = 401, self._error(
                "UNAUTHENTICATED",
                "a valid vmware-api-session-id header is required",
            )
        elif route.operation_id == "Cis.Session_delete":
            status, response = self._delete_session(query, session_id)
        else:
            status, response = self._list(route, query)
            if status == 200:
                response_items = len(response["items"])
                response_marker = response.get("marker")

        state.append_log(
            {
                "operationId": route.operation_id if route else None,
                "method": self.command,
                "rawTarget": self.path,
                "path": target.path,
                "rawQuery": target.query,
                "queryKeys": sorted(query),
                "query": {key: list(values) for key, values in query.items()},
                "headers": {
                    name.lower(): value.strip()
                    for name, value in self.headers.items()
                },
                "authorization": self.headers.get("Authorization"),
                "sessionId": session_id,
                "issuedToken": issued_token,
                "contentType": self.headers.get("Content-Type"),
                "bodyLength": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "responseStatus": status,
                "responseMarker": response_marker,
                "responseItemCount": response_items,
            }
        )
        self._send_json(status, response)

    # -- operations -------------------------------------------------------

    def _create_session(
        self, query: dict[str, list[str]], body: bytes
    ) -> tuple[int, Any]:
        if query:
            return 400, self._error(
                "INVALID_ARGUMENT", "Cis.Session_create takes no query parameters"
            )
        if body.strip() not in (b"", b"null"):
            return 400, self._error(
                "INVALID_ARGUMENT", "Cis.Session_create takes no request body"
            )
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Basic "):
            return 401, self._error(
                "UNAUTHENTICATED", "basic_auth credentials are required"
            )
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return 401, self._error(
                "UNAUTHENTICATED", "malformed basic_auth credentials"
            )
        user, _, secret = decoded.partition(":")
        if user != USERNAME or secret != PASSWORD:
            return 401, self._error("UNAUTHENTICATED", "invalid credentials")
        return 201, self.server.state.issue_token()

    def _delete_session(
        self, query: dict[str, list[str]], session_id: str
    ) -> tuple[int, Any]:
        if query:
            return 400, self._error(
                "INVALID_ARGUMENT", "Cis.Session_delete takes no query parameters"
            )
        self.server.state.revoke(session_id)
        return 204, None

    def _list(self, route: Route, query: dict[str, list[str]]) -> tuple[int, Any]:
        collection = (
            "categories"
            if route.operation_id == "Vcenter.Tagging.Categories_list"
            else "tags"
        )

        unknown = sorted(set(query) - route.query_keys)
        if unknown:
            return 400, self._error(
                "INVALID_ARGUMENT",
                f"{route.operation_id} has no query parameter {unknown[0]!r}; "
                f"the specification defines {sorted(route.query_keys)}",
            )
        for key, values in query.items():
            for value in values:
                if value == "":
                    return 400, self._error(
                        "INVALID_ARGUMENT",
                        f"query parameter {key!r} was sent empty; an unset optional "
                        "field must be omitted from the request",
                    )

        names = query.get("names")
        markers = query.get("marker", [])
        page_sizes = query.get("page_size", [])

        if len(markers) > 1 or len(page_sizes) > 1:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "marker and page_size are single valued iteration properties",
            )
        if names is not None and len(names) != len(set(names)):
            return 400, self._error(
                "INVALID_ARGUMENT", "names is a set and may not repeat a value"
            )
        if names is not None and markers:
            # /paths/~1vcenter~1tagging~1tags/get "400": marker and filter together.
            return 400, self._error(
                "INVALID_ARGUMENT",
                "a filter may not be supplied together with a marker",
            )

        page_size = DEFAULT_PAGE_SIZE
        if page_sizes:
            raw = page_sizes[0]
            if not re.fullmatch(r"[0-9]+", raw) or int(raw) < 1:
                return 400, self._error(
                    "INVALID_ARGUMENT", "page_size must be a positive integer"
                )
            page_size = int(raw)
        page_size = min(page_size, MAX_PAGE_SIZE)

        offset = 0
        if markers:
            marker = markers[0]
            if not self.server.state.knows_marker(marker):
                return 404, self._error(
                    "NOT_FOUND",
                    "the supplied marker was not returned from a prior call",
                )
            payload = decode_marker(marker)
            if payload is None or payload.get("c") != collection:
                return 404, self._error(
                    "NOT_FOUND", "the supplied marker is not valid for this collection"
                )
            offset = int(payload["o"])
            names = payload.get("n")

        source = SEED_CATEGORIES if collection == "categories" else SEED_TAGS
        if names is not None:
            wanted = set(names)
            matching = [item for item in source if item["name"] in wanted]
        else:
            matching = list(source)

        page = matching[offset : offset + page_size]
        if collection == "categories":
            items = [
                {
                    "category_id": item["category_id"],
                    "info": {
                        "name": item["name"],
                        "description": item["description"],
                        "cardinality": item["cardinality"],
                        "associable_types": list(item["associable_types"]),
                        "used_by": list(item["used_by"]),
                    },
                }
                for item in page
            ]
        else:
            items = [
                {
                    "tag": item["tag"],
                    "info": {
                        "name": item["name"],
                        "category": item["category"],
                        "description": item["description"],
                        "used_by": list(item["used_by"]),
                    },
                }
                for item in page
            ]

        result: dict[str, Any] = {"items": items}
        next_offset = offset + len(page)
        if next_offset < len(matching):
            marker = encode_marker(collection, next_offset, names)
            self.server.state.remember_marker(marker)
            result["marker"] = marker
        return 200, result

    # -- plumbing ---------------------------------------------------------

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
