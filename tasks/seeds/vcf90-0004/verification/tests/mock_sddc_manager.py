#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used only by the protected verifier.

The route table is built from docs/contract.json, so this service can only ever
answer the OpenAPI operations that contract names. Everything else is a 404 that
is still written to the request log, which lets the verifier prove the module
under test never reached outside the contract.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PINNED_TAG = "9.0.0.0"
PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATION_IDS = {"createToken", "getNetworkPool", "createNetworkPool"}

# Connect-VcfSddcManagerServer performs this unversioned appliance read while it
# establishes a session. It is PowerCLI handshake behaviour rather than one of
# the contract operations, so it is answered separately, is never routable, and
# is tagged in the log so the verifier can hold the module to the contract only.
CONNECTION_PROBE = ("GET", "/v1/sddc-manager")

USERNAME = "svc-vcf-netpool@vsphere.local"
PASSWORD = "dummy-vcf-login-pass-90"
ACCESS_TOKEN = "dummy-vcf-access-token-90"
REFRESH_TOKEN_ID = "dummy-vcf-refresh-token-90"

# Pool names the verifier drives. "flaky" loses the response of its first
# accepted create, which is the whole point of the exercise.
EXISTING_POOL = "np-mgmt-a"
CONFLICTING_POOL = "np-mgmt-b"
NEW_POOL = "np-vi-c"
FLAKY_POOL = "np-vi-d"
TRANSIENT_POOL = "np-vi-e"
EXHAUSTED_POOL = "np-vi-f"
NONRETRY_POOL = "np-vi-g"
DEFAULT_SLEEP_POOL = "np-vi-h"


def seed_pools() -> list[dict[str, Any]]:
    return [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": EXISTING_POOL,
            "hostsCount": 4,
            "networks": [
                {
                    "id": "aaaaaaaa-0001-4001-8001-aaaaaaaaaaaa",
                    "type": "VSAN",
                    "vlanId": 3001,
                    "mtu": 9000,
                    "subnet": "172.20.30.0",
                    "mask": "255.255.255.0",
                    "gateway": "172.20.30.1",
                    "ipPools": [{"start": "172.20.30.10", "end": "172.20.30.60"}],
                },
                {
                    "id": "aaaaaaaa-0002-4002-8002-aaaaaaaaaaaa",
                    "type": "VMOTION",
                    "vlanId": 3002,
                    "mtu": 9000,
                    "subnet": "172.20.31.0",
                    "mask": "255.255.255.0",
                    "gateway": "172.20.31.1",
                    "ipPools": [{"start": "172.20.31.10", "end": "172.20.31.60"}],
                },
            ],
        },
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "name": CONFLICTING_POOL,
            "hostsCount": 0,
            "networks": [
                {
                    "id": "bbbbbbbb-0001-4001-8001-bbbbbbbbbbbb",
                    "type": "VSAN",
                    "vlanId": 3101,
                    "mtu": 9000,
                    "subnet": "172.20.40.0",
                    "mask": "255.255.255.0",
                    "gateway": "172.20.40.1",
                    "ipPools": [],
                }
            ],
        },
    ]


class Route:
    __slots__ = ("operation_id", "method", "template", "pattern")

    def __init__(self, operation_id: str, method: str, template: str) -> None:
        self.operation_id = operation_id
        self.method = method.upper()
        self.template = template
        parts = []
        for segment in template.strip("/").split("/"):
            found = re.fullmatch(r"\{([^{}]+)\}", segment)
            parts.append(f"(?P<{found.group(1)}>[^/]+)" if found else re.escape(segment))
        self.pattern = re.compile("^/" + "/".join(parts) + "$")

    @classmethod
    def from_contract(cls, operation: dict[str, Any]) -> "Route":
        return cls(operation["operationId"], operation["method"], operation["path"])


class MockState:
    def __init__(self, contract: dict[str, Any], request_log: Path, state_path: Path) -> None:
        derived = contract["derived_from"]
        if derived.get("repository_commit_sha") != PINNED_COMMIT:
            raise SystemExit("contract.json is not pinned to the expected commit")
        if derived.get("spec_path") != PINNED_SPEC_PATH:
            raise SystemExit("contract.json is not derived from the SDDC Manager specification")
        if derived.get("spec_version") != PINNED_TAG:
            raise SystemExit("contract.json is not the 9.0.0.0 revision of the specification")
        operation_ids = {op["operationId"] for op in contract["operations"]}
        if operation_ids != EXPECTED_OPERATION_IDS:
            raise SystemExit(f"contract.json names unexpected operations: {sorted(operation_ids)}")

        self.routes = [Route.from_contract(op) for op in contract["operations"]]
        self.network_members = set(contract["schemas"]["Network"]["properties"])
        self.request_log = request_log
        self.state_path = state_path
        self.lock = threading.Lock()
        self.sequence = 0
        self.pools = seed_pools()
        self.create_calls: dict[str, int] = {}
        self.next_pool = 0
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")
        self._write_state()

    def _write_state(self) -> None:
        """Publish the pool store after every change so the verifier can read
        the surviving state even if this process is killed outright."""
        self.state_path.write_text(json.dumps(self.pools, indent=2), encoding="utf-8")

    def match(self, method: str, path: str) -> Route | None:
        for route in self.routes:
            if route.method == method and route.pattern.fullmatch(path):
                return route
        return None

    def append_log(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            with self.request_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()

    def record_create(self, name: str) -> int:
        with self.lock:
            count = self.create_calls.get(name, 0) + 1
            self.create_calls[name] = count
            return count

    def add_pool(self, body: dict[str, Any]) -> dict[str, Any]:
        """Append unconditionally: a duplicate create really does duplicate."""
        with self.lock:
            self.next_pool += 1
            index = self.next_pool
            networks = []
            for position, network in enumerate(body.get("networks") or [], start=1):
                stored = dict(network)
                stored["id"] = f"cccccccc-{index:04d}-4{position:03d}-8001-cccccccccccc"
                stored.setdefault("ipPools", [])
                networks.append(stored)
            pool = {
                "id": f"33333333-{index:04d}-4333-8333-333333333333",
                "name": body.get("name"),
                "hostsCount": 0,
                "networks": networks,
            }
            self.pools.append(pool)
            self._write_state()
            return pool

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return json.loads(json.dumps(self.pools))


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type, state: MockState) -> None:
        super().__init__(address, handler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ContractServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802 - outside the contract, still logged
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    # ----------------------------------------------------------------- helpers

    def _send(self, status: int, payload: object | None) -> None:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"errorCode": code, "message": message, "referenceToken": "loopback"}

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ACCESS_TOKEN}"

    def _page(self, elements: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "elements": elements,
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": len(elements),
                "totalElements": len(elements),
                "totalPages": 1,
            },
        }

    # ---------------------------------------------------------------- dispatch

    def _dispatch(self) -> None:
        state = self.server.state
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        text = raw.decode("utf-8") if raw else ""
        try:
            parsed_json = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed_json = None

        handshake = (self.command, parsed.path) == CONNECTION_PROBE
        route = None if handshake else state.match(self.command, parsed.path)

        state.append_log(
            {
                "operationId": None if route is None else route.operation_id,
                "handshake": handshake,
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "headers": {
                    "authorization": self.headers.get("Authorization"),
                    "content-type": self.headers.get("Content-Type"),
                },
                "bodyText": text,
                "json": parsed_json,
            }
        )

        if handshake:
            self._send(
                200,
                {
                    "id": "44444444-4444-4444-8444-444444444444",
                    "fqdn": "sddc-manager.loopback.local",
                    "version": PINNED_TAG,
                    "ipAddress": "127.0.0.1",
                },
            )
            return

        if route is None:
            self._send(404, self._error("NOT_FOUND", f"{self.command} {parsed.path} is outside the contract"))
            return

        if route.operation_id == "createToken":
            self._create_token(parsed_json)
        elif route.operation_id == "getNetworkPool":
            self._get_network_pool(state)
        elif route.operation_id == "createNetworkPool":
            self._create_network_pool(state, parsed_json)

    def _create_token(self, body: object) -> None:
        if not isinstance(body, dict) or body.get("username") != USERNAME or body.get("password") != PASSWORD:
            self._send(401, self._error("UNAUTHORIZED", "Bad credentials."))
            return
        self._send(201, {"accessToken": ACCESS_TOKEN, "refreshToken": {"id": REFRESH_TOKEN_ID}})

    def _get_network_pool(self, state: MockState) -> None:
        if not self._authorized():
            self._send(401, self._error("UNAUTHENTICATED", "A bearer token is required."))
            return
        self._send(200, self._page(state.snapshot()))

    def _create_network_pool(self, state: MockState, body: object) -> None:
        if not self._authorized():
            self._send(401, self._error("UNAUTHENTICATED", "A bearer token is required."))
            return
        if not isinstance(body, dict) or not body.get("name") or not body.get("networks"):
            self._send(400, self._error("INVALID_SPEC", "name and networks are required."))
            return
        for network in body["networks"]:
            unknown = sorted(set(network) - state.network_members)
            if unknown:
                self._send(
                    400,
                    self._error("INVALID_SPEC", f"Network members outside the 9.0.0.0 schema: {unknown}"),
                )
                return

        name = body["name"]
        attempt = state.record_create(name)

        # Exercise every retryable status across one create that has not yet
        # reached the server. Each failed request leaves the store unchanged,
        # so a correct client re-reads and then retries the create.
        if name == TRANSIENT_POOL and attempt <= 3:
            status = (429, 503, 504)[attempt - 1]
            self._send(status, self._error("TRANSIENT", f"Transient create failure {status}."))
            return

        # This retryable failure never clears, allowing the verifier to prove
        # that MaxAttempts is a total-attempt limit rather than a retry count.
        if name == EXHAUSTED_POOL:
            self._send(503, self._error("UNAVAILABLE", "Create remains unavailable."))
            return

        # HTTP 500 is deliberately outside the ticket's retry allow-list.
        if name == NONRETRY_POOL:
            self._send(500, self._error("INTERNAL_ERROR", "Create failed without retry."))
            return

        if name == DEFAULT_SLEEP_POOL and attempt == 1:
            self._send(503, self._error("UNAVAILABLE", "Retry after the default sleep."))
            return

        pool = state.add_pool(body)

        # The pool is created, but the caller never learns that: the response is
        # lost behind a gateway error. A retry that simply repeats the create
        # ends up with two pools of the same name.
        if name == FLAKY_POOL and attempt == 1:
            self._send(502, self._error("GATEWAY_TIMEOUT", "The response was lost in transit."))
            return

        self._send(201, pool)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    state = MockState(contract, args.log, args.state)
    server = ContractServer(("127.0.0.1", 0), ContractHandler, state)
    args.ready.write_text(str(server.server_address[1]), encoding="utf-8")
    server.serve_forever()


if __name__ == "__main__":
    main()
