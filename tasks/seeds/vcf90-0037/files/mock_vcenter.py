#!/usr/bin/env python3
"""Contract-pinned loopback vCenter used by the protected verification.

The route table is built from docs/contract.json, which is a projection of the
vSphere Automation API specification at tag 9.0.0.0 of vmware/vcf-api-specs.
The service answers only the four operations that contract names; every other
target is refused and still recorded, so the harness can prove the run stayed
inside the contract.

The rollout this fixture backs is a multi-step change: one listing resolves the
parent pool, then one Vcenter.ResourcePool_create per planned pool.  The service
enforces the rules a real vCenter enforces, so a plan can fail part way through
and the pools created before the failure genuinely exist afterwards:

  * a child name must be unique among the children of its parent, otherwise
    400 INVALID_ARGUMENT;
  * a parent that is not in the inventory gives 404 NOT_FOUND;
  * reservations claimed by the children of a pool may not exceed that pool's
    capacity, otherwise 500 UNABLE_TO_ALLOCATE_RESOURCE.

It also refuses anything that contradicts the wire shape the specification
describes: an unknown query key, a query key sent with an empty value, an
unknown body property, a body property carrying null, and an allocation object
with no members at all.  An optional field that was not set therefore has to be
omitted rather than serialised empty.
"""

from __future__ import annotations

import base64
import binascii
import json
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"

PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
PINNED_TAG = "9.0.0.0"
PINNED_API_VERSION = "9.0.0.0"
EXPECTED_OPERATION_IDS = {
    "Cis.Session_create",
    "Cis.Session_delete",
    "Vcenter.ResourcePool_list",
    "Vcenter.ResourcePool_create",
}

USERNAME = "administrator@vsphere.local"
PASSWORD = "dummy-vcenter-pass-90"
SESSION_HEADER = "vmware-api-session-id"

# Wire names taken from Vcenter.ResourcePool.CreateSpec and the two schemas it
# nests.  Anything outside these sets is refused.
CREATE_SPEC_REQUIRED = ("name", "parent")
CREATE_SPEC_OPTIONAL = ("cpu_allocation", "memory_allocation")
ALLOCATION_MEMBERS = ("reservation", "expandable_reservation", "limit", "shares")
SHARES_REQUIRED = ("level",)
SHARES_OPTIONAL = ("shares",)
SHARES_LEVELS = ("LOW", "NORMAL", "HIGH", "CUSTOM")


def error_body(error_type: str, message_id: str, message: str, args: list[str]) -> dict[str, Any]:
    """A Vapi.Std.Errors.Error body as the specification shapes it."""
    return {
        "error_type": error_type,
        "messages": [{"id": message_id, "default_message": message, "args": args}],
    }


class Inventory:
    """The resource pool tree the fixture starts from.

    Capacities are the ceiling a parent can hand out to its children, so an
    over-committed plan fails on the pool that crosses the line and not before.
    """

    def __init__(self) -> None:
        self.pools: dict[str, dict[str, Any]] = {
            "resgroup-1": {
                "name": "Resources",
                "parent": None,
                "cpu_capacity": 120000,
                "memory_capacity": 524288,
            },
            "resgroup-10": {
                "name": "Production",
                "parent": "resgroup-1",
                "cpu_capacity": 40000,
                "memory_capacity": 131072,
            },
            "resgroup-20": {
                "name": "Staging",
                "parent": "resgroup-1",
                "cpu_capacity": 10000,
                "memory_capacity": 16384,
            },
            # Already present under Production, so a plan that asks for this
            # name again collides the way a real rollout would.
            "resgroup-11": {
                "name": "platform-shared",
                "parent": "resgroup-10",
                "cpu_capacity": 0,
                "memory_capacity": 0,
                "cpu_reservation": 0,
                "memory_reservation": 0,
            },
        }
        self._next_id = 100

    def allocate_id(self) -> str:
        identifier = f"resgroup-{self._next_id}"
        self._next_id += 1
        return identifier

    def children(self, parent_id: str) -> list[str]:
        return [i for i, p in self.pools.items() if p.get("parent") == parent_id]

    def child_named(self, parent_id: str, name: str) -> str | None:
        for identifier in self.children(parent_id):
            if self.pools[identifier]["name"] == name:
                return identifier
        return None

    def reserved(self, parent_id: str, kind: str) -> int:
        return sum(
            int(self.pools[i].get(f"{kind}_reservation", 0) or 0)
            for i in self.children(parent_id)
        )

    def summaries(self) -> list[dict[str, str]]:
        return [
            {"resource_pool": identifier, "name": pool["name"]}
            for identifier, pool in self.pools.items()
        ]


class MockState:
    def __init__(
        self,
        routes: dict[tuple[str, str], str],
        request_log: Path,
        *,
        duplicate_parent: bool = False,
        fail_session_delete: bool = False,
    ) -> None:
        self.routes = routes
        self.request_log = request_log
        self.inventory = Inventory()
        if duplicate_parent:
            self.inventory.pools["resgroup-30"] = {
                "name": "Production",
                "parent": "resgroup-1",
                "cpu_capacity": 20000,
                "memory_capacity": 65536,
            }
        self.fail_session_delete = fail_session_delete
        self.active_tokens: set[str] = set()
        self.sequence = 0
        self.lock = threading.Lock()
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def append_log(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            record["seq"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")
                stream.flush()


def load_routes() -> dict[tuple[str, str], str]:
    """Build the route table straight out of the contract projection."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    source = contract["source"]
    if source["commitSha"] != PINNED_COMMIT:
        raise SystemExit(f"contract is not pinned to {PINNED_COMMIT}")
    if source["specPath"] != PINNED_SPEC_PATH:
        raise SystemExit(f"contract is not a projection of {PINNED_SPEC_PATH}")
    if source["repositoryTag"] != PINNED_TAG or source["apiVersion"] != PINNED_API_VERSION:
        raise SystemExit(f"contract is not pinned to tag {PINNED_TAG}")

    routes: dict[tuple[str, str], str] = {}
    for operation in contract["operations"]:
        routes[(operation["method"], operation["wirePath"])] = operation["operationId"]
    if set(routes.values()) != EXPECTED_OPERATION_IDS:
        raise SystemExit("contract does not name exactly the operations this fixture serves")
    return routes


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcf-mock-vcenter/9.0"
    state: MockState

    # ---- plumbing ---------------------------------------------------------

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _respond(self, status: int, payload: Any) -> None:
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

    def _auth_kind(self) -> str:
        """What the request presented, never what it presented it with."""
        if self.headers.get(SESSION_HEADER):
            return "session-id"
        authorization = self.headers.get("Authorization") or ""
        if authorization.lower().startswith("basic "):
            return "basic"
        if authorization:
            return "other"
        return "none"

    def _basic_credential(self) -> tuple[str, str] | None:
        authorization = self.headers.get("Authorization") or ""
        if not authorization.lower().startswith("basic "):
            return None
        try:
            decoded = base64.b64decode(authorization.split(" ", 1)[1], validate=True)
        except (binascii.Error, IndexError, ValueError):
            return None
        try:
            text = decoded.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if ":" not in text:
            return None
        user, password = text.split(":", 1)
        return user, password

    # ---- dispatch ---------------------------------------------------------

    def _handle(self, method: str) -> None:
        state = self.state
        split = urlsplit(self.path)
        path = split.path
        raw_body = self._read_body()
        query = parse_qs(split.query, keep_blank_values=True)

        parsed_body: Any = None
        if raw_body:
            try:
                parsed_body = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed_body = None

        record: dict[str, Any] = {
            "method": method,
            "path": path,
            "rawQuery": split.query,
            "query": {k: v for k, v in query.items()},
            "queryKeys": sorted(query.keys()),
            "auth": self._auth_kind(),
            "hasSessionHeader": bool(self.headers.get(SESSION_HEADER)),
            "contentType": self.headers.get("Content-Type") or "",
            "bodyRaw": raw_body.decode("utf-8", errors="replace"),
            "body": parsed_body,
            "bodyKeys": sorted(parsed_body.keys()) if isinstance(parsed_body, dict) else [],
        }

        operation_id = state.routes.get((method, path))
        record["operationId"] = operation_id
        record["offContract"] = operation_id is None

        if operation_id is None:
            record["status"] = 404
            state.append_log(record)
            self._respond(
                404,
                error_body(
                    "NOT_FOUND",
                    "com.vmware.vapi.rest.no_such_endpoint",
                    f"The endpoint {method} {path} is not part of the pinned contract.",
                    [method, path],
                ),
            )
            return

        handlers = {
            "Cis.Session_create": self._session_create,
            "Cis.Session_delete": self._session_delete,
            "Vcenter.ResourcePool_list": self._resource_pool_list,
            "Vcenter.ResourcePool_create": self._resource_pool_create,
        }
        status, payload = handlers[operation_id](query, parsed_body)
        record["status"] = status
        record["responseBody"] = payload
        state.append_log(record)
        self._respond(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    # ---- shared guards ----------------------------------------------------

    def _unauthenticated(self) -> tuple[int, Any]:
        return 401, error_body(
            "UNAUTHENTICATED",
            "com.vmware.vapi.endpoint.method.authentication.required",
            "This method requires authentication.",
            [],
        )

    def _require_session(self) -> tuple[int, Any] | None:
        token = self.headers.get(SESSION_HEADER)
        if not token or token not in self.state.active_tokens:
            return self._unauthenticated()
        return None

    def _invalid_argument(self, message: str, args: list[str]) -> tuple[int, Any]:
        return 400, error_body(
            "INVALID_ARGUMENT",
            "com.vmware.vapi.std.errors.invalid_argument",
            message,
            args,
        )

    # ---- operations -------------------------------------------------------

    def _session_create(self, query: dict[str, list[str]], _body: Any) -> tuple[int, Any]:
        if query:
            return self._invalid_argument(
                "Cis.Session_create takes no query parameters.",
                sorted(query.keys()),
            )
        credential = self._basic_credential()
        if credential is None or credential != (USERNAME, PASSWORD):
            return self._unauthenticated()
        # Bind the token to this fresh fixture instance.  A caller therefore
        # has to use the value returned by Cis.Session_create rather than a
        # seed-wide constant learned from another run.
        session_token = f"dummy-vcenter-session-{self.server.server_address[1]}"
        self.state.active_tokens.add(session_token)
        # The specification gives the token as the 201 response body, so that is
        # the only place this fixture puts it.
        return 201, session_token

    def _session_delete(self, query: dict[str, list[str]], _body: Any) -> tuple[int, Any]:
        if query:
            return self._invalid_argument(
                "Cis.Session_delete takes no query parameters.",
                sorted(query.keys()),
            )
        failure = self._require_session()
        if failure is not None:
            return failure
        if self.state.fail_session_delete:
            return 503, error_body(
                "SERVICE_UNAVAILABLE",
                "com.vmware.vapi.std.errors.service_unavailable",
                "The session service is temporarily unavailable.",
                [],
            )
        self.state.active_tokens.discard(self.headers.get(SESSION_HEADER) or "")
        return 204, None

    def _resource_pool_list(self, query: dict[str, list[str]], _body: Any) -> tuple[int, Any]:
        failure = self._require_session()
        if failure is not None:
            return failure

        allowed = {
            "resource_pools",
            "names",
            "parent_resource_pools",
            "datacenters",
            "hosts",
            "clusters",
        }
        unknown = sorted(set(query) - allowed)
        if unknown:
            return self._invalid_argument(
                "Unknown query parameter(s) for Vcenter.ResourcePool_list.",
                unknown,
            )
        for key, values in sorted(query.items()):
            if any(value == "" for value in values):
                return self._invalid_argument(
                    f"Query parameter {key} was sent with an empty value; an unset "
                    "optional filter has to be omitted instead.",
                    [key],
                )

        summaries = self.state.inventory.summaries()
        if "names" in query:
            wanted = set(query["names"])
            summaries = [s for s in summaries if s["name"] in wanted]
        if "resource_pools" in query:
            wanted = set(query["resource_pools"])
            summaries = [s for s in summaries if s["resource_pool"] in wanted]
        if "parent_resource_pools" in query:
            wanted = set(query["parent_resource_pools"])
            summaries = [
                s
                for s in summaries
                if self.state.inventory.pools[s["resource_pool"]].get("parent") in wanted
            ]
        # datacenters / hosts / clusters are accepted and match everything in
        # this fixture; the rollout never sends them.
        return 200, summaries

    def _resource_pool_create(self, query: dict[str, list[str]], body: Any) -> tuple[int, Any]:
        failure = self._require_session()
        if failure is not None:
            return failure
        if query:
            return self._invalid_argument(
                "Vcenter.ResourcePool_create takes no query parameters.",
                sorted(query.keys()),
            )
        if not isinstance(body, dict):
            return self._invalid_argument(
                "The request body must be a Vcenter.ResourcePool.CreateSpec object.",
                [],
            )

        problem = self._validate_create_spec(body)
        if problem is not None:
            return self._invalid_argument(problem[0], problem[1])

        inventory = self.state.inventory
        parent = body["parent"]
        if parent not in inventory.pools:
            return 404, error_body(
                "NOT_FOUND",
                "com.vmware.vapi.std.errors.not_found",
                f"The resource pool {parent} could not be found.",
                [parent],
            )

        name = body["name"]
        if inventory.child_named(parent, name) is not None:
            return self._invalid_argument(
                f"A resource pool named {name} already exists under {parent}.",
                [name, parent],
            )

        for kind, key in (("cpu", "cpu_allocation"), ("memory", "memory_allocation")):
            requested = int((body.get(key) or {}).get("reservation", 0) or 0)
            if requested == 0:
                continue
            capacity = int(inventory.pools[parent].get(f"{kind}_capacity", 0) or 0)
            if inventory.reserved(parent, kind) + requested > capacity:
                return 500, error_body(
                    "UNABLE_TO_ALLOCATE_RESOURCE",
                    "com.vmware.vapi.std.errors.unable_to_allocate_resource",
                    f"The {kind} reservation of {requested} exceeds what {parent} "
                    "has left to hand out.",
                    [kind, str(requested), parent],
                )

        identifier = inventory.allocate_id()
        inventory.pools[identifier] = {
            "name": name,
            "parent": parent,
            "cpu_capacity": int((body.get("cpu_allocation") or {}).get("limit", 0) or 0),
            "memory_capacity": int((body.get("memory_allocation") or {}).get("limit", 0) or 0),
            "cpu_reservation": int((body.get("cpu_allocation") or {}).get("reservation", 0) or 0),
            "memory_reservation": int(
                (body.get("memory_allocation") or {}).get("reservation", 0) or 0
            ),
        }
        return 201, identifier

    def _validate_create_spec(self, body: dict[str, Any]) -> tuple[str, list[str]] | None:
        known = set(CREATE_SPEC_REQUIRED) | set(CREATE_SPEC_OPTIONAL)
        unknown = sorted(set(body) - known)
        if unknown:
            return ("Unknown propert(ies) for Vcenter.ResourcePool.CreateSpec.", unknown)
        nulls = sorted(k for k, v in body.items() if v is None)
        if nulls:
            return (
                "Propert(ies) of Vcenter.ResourcePool.CreateSpec were sent as null; "
                "an unset optional property has to be omitted instead.",
                nulls,
            )
        for field in CREATE_SPEC_REQUIRED:
            if field not in body:
                return (f"Vcenter.ResourcePool.CreateSpec.{field} is required.", [field])
            if not isinstance(body[field], str) or not body[field]:
                return (
                    f"Vcenter.ResourcePool.CreateSpec.{field} must be a non-empty string.",
                    [field],
                )

        for key in CREATE_SPEC_OPTIONAL:
            if key not in body:
                continue
            problem = self._validate_allocation(key, body[key])
            if problem is not None:
                return problem
        return None

    def _validate_allocation(self, key: str, value: Any) -> tuple[str, list[str]] | None:
        if not isinstance(value, dict):
            return (
                f"Vcenter.ResourcePool.CreateSpec.{key} must be a "
                "Vcenter.ResourcePool.ResourceAllocationCreateSpec object.",
                [key],
            )
        unknown = sorted(set(value) - set(ALLOCATION_MEMBERS))
        if unknown:
            return (
                f"Unknown propert(ies) for Vcenter.ResourcePool"
                f".ResourceAllocationCreateSpec under {key}.",
                unknown,
            )
        nulls = sorted(k for k, v in value.items() if v is None)
        if nulls:
            return (
                f"Propert(ies) of {key} were sent as null; an unset optional "
                "property has to be omitted instead.",
                nulls,
            )
        if not value:
            return (
                f"{key} was sent with no members at all; an allocation that tunes "
                "nothing has to be omitted instead.",
                [key],
            )
        for member in ("reservation", "limit"):
            if member in value and not isinstance(value[member], int):
                return (f"{key}.{member} must be an integer.", [member])
        if "expandable_reservation" in value and not isinstance(
            value["expandable_reservation"], bool
        ):
            return (f"{key}.expandable_reservation must be a boolean.", ["expandable_reservation"])
        if "shares" in value:
            return self._validate_shares(key, value["shares"])
        return None

    def _validate_shares(self, key: str, value: Any) -> tuple[str, list[str]] | None:
        if not isinstance(value, dict):
            return (
                f"{key}.shares must be a Vcenter.ResourcePool.SharesInfo object.",
                ["shares"],
            )
        unknown = sorted(set(value) - set(SHARES_REQUIRED) - set(SHARES_OPTIONAL))
        if unknown:
            return (f"Unknown propert(ies) for Vcenter.ResourcePool.SharesInfo.", unknown)
        nulls = sorted(k for k, v in value.items() if v is None)
        if nulls:
            return (
                f"Propert(ies) of {key}.shares were sent as null; an unset optional "
                "property has to be omitted instead.",
                nulls,
            )
        if "level" not in value:
            return ("Vcenter.ResourcePool.SharesInfo.level is required.", ["level"])
        if value["level"] not in SHARES_LEVELS:
            return (
                "Vcenter.ResourcePool.SharesInfo.level must be one of "
                + ", ".join(SHARES_LEVELS)
                + ".",
                [str(value["level"])],
            )
        if "shares" in value and not isinstance(value["shares"], int):
            return ("Vcenter.ResourcePool.SharesInfo.shares must be an integer.", ["shares"])
        return None


def main() -> None:
    if len(sys.argv) < 5:
        raise SystemExit(
            "usage: mock_vcenter.py <cert> <key> <port-file> <request-log> "
            "[--duplicate-parent] [--fail-session-delete]"
        )

    options = set(sys.argv[5:])
    unknown_options = options - {"--duplicate-parent", "--fail-session-delete"}
    if unknown_options:
        raise SystemExit(f"unknown option(s): {', '.join(sorted(unknown_options))}")

    cert_file, key_file, port_file, request_log = (
        Path(sys.argv[1]).resolve(),
        Path(sys.argv[2]).resolve(),
        Path(sys.argv[3]).resolve(),
        Path(sys.argv[4]).resolve(),
    )

    state = MockState(
        load_routes(),
        request_log,
        duplicate_parent="--duplicate-parent" in options,
        fail_session_delete="--fail-session-delete" in options,
    )
    handler = type("BoundHandler", (Handler,), {"state": state})

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_file), str(key_file))

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    port_file.write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
