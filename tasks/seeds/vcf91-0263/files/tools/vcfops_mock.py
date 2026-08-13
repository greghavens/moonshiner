#!/usr/bin/env python3
"""Loopback stand-in for a VCF Operations 9.1 appliance (suite-api).

This is a local fake service used for offline development and testing. It is
pinned to the contract in ``docs/contract.json``: it refuses to start unless the
route projection of that contract matches the digest this file was built
against, and it serves *only* the operations the contract names. Everything else
answers 404.

Every request it receives is appended to an NDJSON request log so tests can
assert the exact wire shape a client produced.

Usage:
    python3 tools/vcfops_mock.py --contract docs/contract.json --log requests.ndjson [--port 0]

On startup it prints a single JSON line to stdout, ``{"port": <bound port>}``,
then serves until terminated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# ---------------------------------------------------------------------------
# Contract pin
# ---------------------------------------------------------------------------

# sha256 over the canonical route projection of the contract:
#   rows = sorted([[operationId, METHOD, path], ...], key=first column)
#   sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()
CONTRACT_PIN = "fce6844001168b83cf4b7e0cb62c39dbe83c6346a9bbfe62218a19119a8e5ede"

BASE_PATH = "/suite-api"
AUTH_SCHEME = "OpsToken"

# Operations this appliance build knows how to serve. The contract decides which
# of them are actually routed; anything the contract does not name stays dark.
IMPLEMENTED = {
    "acquireToken",
    "createCustomGroup",
    "createMaintenanceSchedules",
    "assignPolicy",
}

# ---------------------------------------------------------------------------
# Appliance state
# ---------------------------------------------------------------------------

_UUID_NS = uuid.UUID("6f1a5c7e-3b42-4d18-9a06-0c2e7d5b8419")

LOCAL_USER = "svc-runbook"
LOCAL_PASSWORD = "Runb00k!Ops"

# Policies configured on this appliance.
POLICIES = {
    "b1c9f0e2-4a76-4d1e-9f3c-0d5a8e2b6741": "VCF 9.1 Patch Wave Policy",
}


def _issue_token(username: str, password: str) -> str:
    digest = hashlib.sha256(f"{username}:{password}".encode("utf-8")).hexdigest()
    return "OpsTkn-" + digest[:32]


class Appliance:
    """Mutable state of the fake appliance."""

    def __init__(self) -> None:
        self.tokens: set[str] = set()
        self.custom_groups: dict[str, dict] = {}
        self.group_names: dict[str, str] = {}
        # A maintenance schedule left over from the previous patch wave. Schedule
        # keys are unique appliance-wide, so re-using this key is rejected.
        self.schedule_keys: dict[str, str] = {
            "vcf91-patch-wave-1": "0f6c1d55-9d2b-4f28-a3e7-71b4c0d9e5a2",
        }


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


def route_projection(contract: dict) -> list:
    rows = []
    for op in contract.get("operations", []):
        rows.append([op["operationId"], str(op["method"]).upper(), op["path"]])
    rows.sort(key=lambda row: row[0])
    return rows


def projection_digest(rows: list) -> str:
    canonical = json.dumps(rows, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_routes(contract_path: str) -> dict:
    try:
        with open(contract_path, "r", encoding="utf-8") as handle:
            contract = json.load(handle)
    except FileNotFoundError:
        _fail(f"contract not found: {contract_path}")
    except json.JSONDecodeError as exc:
        _fail(f"contract is not valid JSON: {exc}")

    if not isinstance(contract, dict) or not isinstance(contract.get("operations"), list):
        _fail("contract must be an object with an 'operations' array")

    for index, op in enumerate(contract["operations"]):
        if not isinstance(op, dict):
            _fail(f"operations[{index}] is not an object")
        for field in ("operationId", "method", "path"):
            if field not in op:
                _fail(f"operations[{index}] is missing '{field}'")

    rows = route_projection(contract)
    digest = projection_digest(rows)
    if digest != CONTRACT_PIN:
        sys.stderr.write(
            "contract pin mismatch: this appliance build serves the operation set with\n"
            f"  route digest {CONTRACT_PIN}\n"
            f"but docs/contract.json projects to\n"
            f"  route digest {digest}\n"
            "The digest is sha256 over "
            'json.dumps(sorted([[operationId, METHOD, path], ...]), separators=(",", ":")).\n'
            f"Loaded routes: {json.dumps(rows)}\n"
        )
        raise SystemExit(3)

    unknown = sorted({row[0] for row in rows} - IMPLEMENTED)
    if unknown:
        _fail(f"contract names operations this appliance does not implement: {unknown}")

    routes = {}
    for operation_id, method, path in rows:
        routes[(method, path)] = operation_id
    return routes


def _fail(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    raise SystemExit(3)


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------


def match_route(routes: dict, method: str, path: str):
    """Return (operationId, path_params) or (None, None)."""
    if not path.startswith(BASE_PATH + "/"):
        return None, None
    tail = path[len(BASE_PATH):]
    tail_parts = [segment for segment in tail.split("/") if segment != ""]

    for (route_method, template), operation_id in routes.items():
        if route_method != method:
            continue
        template_parts = [segment for segment in template.split("/") if segment != ""]
        if len(template_parts) != len(tail_parts):
            continue
        params = {}
        for expected, actual in zip(template_parts, tail_parts):
            if expected.startswith("{") and expected.endswith("}"):
                params[expected[1:-1]] = actual
            elif expected != actual:
                break
        else:
            return operation_id, params
    return None, None


def error_body(status: int, message: str, api_error_code: int = 0) -> dict:
    """The spec's 'error' schema (only 'message' is required)."""
    body = {"httpStatusCode": status, "message": message}
    if api_error_code:
        body["apiErrorCode"] = api_error_code
    return body


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

LOGGED_HEADERS = ("authorization", "content-type", "accept")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "VCFOperations/9.1.0.0"
    sys_version = ""

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):  # silence stderr access logging
        pass

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            return self.rfile.read(int(length))
        except (TypeError, ValueError):
            return b""

    def _respond(self, status: int, payload):
        raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def _record(self, entry: dict) -> None:
        server = self.server
        with server.log_lock:
            server.seq += 1
            entry["seq"] = server.seq
            with open(server.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

    # -- dispatch ----------------------------------------------------------

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def _handle(self, method: str):
        split = urlsplit(self.path)
        raw_body = self._read_body()
        text = raw_body.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None

        entry = {
            "method": method,
            "path": split.path,
            "query": {k: v for k, v in parse_qs(split.query, keep_blank_values=True).items()},
            "headers": {
                name: self.headers.get(name)
                for name in LOGGED_HEADERS
                if self.headers.get(name) is not None
            },
            "body_raw": text,
            "body": parsed,
        }

        operation_id, path_params = match_route(self.server.routes, method, split.path)
        entry["operationId"] = operation_id

        status, payload = self._dispatch(operation_id, path_params, parsed, text)
        entry["status"] = status
        self._record(entry)
        self._respond(status, payload)

    def _dispatch(self, operation_id, path_params, body, raw_text):
        if operation_id is None:
            return 404, error_body(404, "No matching resource found for the requested URI.")

        if operation_id != "acquireToken":
            unauthorized = self._check_token()
            if unauthorized is not None:
                return unauthorized

        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if raw_text and content_type != "application/json":
            return 415, error_body(415, "Unsupported media type; expected application/json.")

        if body is None:
            return 400, error_body(400, "A JSON request body is required.")
        if not isinstance(body, dict):
            return 400, error_body(400, "The request body must be a JSON object.")

        handler = {
            "acquireToken": self._acquire_token,
            "createCustomGroup": self._create_custom_group,
            "createMaintenanceSchedules": self._create_maintenance_schedule,
            "assignPolicy": self._assign_policy,
        }[operation_id]
        return handler(body, path_params)

    def _check_token(self):
        header = self.headers.get("Authorization")
        if not header:
            return 401, error_body(401, "Authorization header is missing.")
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0] != AUTH_SCHEME:
            return 401, error_body(
                401, f"Authorization header must use the '{AUTH_SCHEME}' scheme."
            )
        if parts[1] not in self.server.state.tokens:
            return 401, error_body(401, "The supplied token is invalid or has expired.")
        return None

    # -- operations --------------------------------------------------------

    def _acquire_token(self, body, _params):
        missing = [f for f in ("username", "password") if f not in body]
        if missing:
            return 400, error_body(400, f"Missing required field(s): {', '.join(missing)}.")
        if body["username"] != LOCAL_USER or body["password"] != LOCAL_PASSWORD:
            return 401, error_body(401, "Invalid username or password.")
        token = _issue_token(body["username"], body["password"])
        self.server.state.tokens.add(token)
        return 200, {
            "token": token,
            "validity": 1789200000000,
            "expiresAt": "Wednesday, September 16, 2026 10:00:00 AM UTC",
            "roles": ["ContentAdmin"],
        }

    def _create_custom_group(self, body, _params):
        missing = [f for f in ("resourceKey", "membershipDefinition") if f not in body]
        if missing:
            return 400, error_body(400, f"Missing required field(s): {', '.join(missing)}.")
        key = body["resourceKey"]
        if not isinstance(key, dict):
            return 400, error_body(400, "resourceKey must be an object.")
        key_missing = [
            f for f in ("name", "adapterKindKey", "resourceKindKey") if not key.get(f)
        ]
        if key_missing:
            return 400, error_body(
                400, f"resourceKey is missing required field(s): {', '.join(key_missing)}."
            )
        name = key["name"]
        state = self.server.state
        if name in state.group_names:
            return 409, error_body(409, f"A custom group named '{name}' already exists.")

        group_id = str(uuid.uuid5(_UUID_NS, f"custom-group:{name}"))
        stored = dict(body)
        stored["id"] = group_id
        state.custom_groups[group_id] = stored
        state.group_names[name] = group_id
        return 201, stored

    def _create_maintenance_schedule(self, body, _params):
        missing = [f for f in ("key", "schedule") if f not in body]
        if missing:
            return 400, error_body(400, f"Missing required field(s): {', '.join(missing)}.")
        schedule = body["schedule"]
        if not isinstance(schedule, dict):
            return 400, error_body(400, "schedule must be an object.")
        sched_missing = [
            f
            for f in ("scheduleType", "hour", "minuteOfTheHour", "duration")
            if f not in schedule
        ]
        if sched_missing:
            return 400, error_body(
                400, f"schedule is missing required field(s): {', '.join(sched_missing)}."
            )

        key = body["key"]
        state = self.server.state
        if key in state.schedule_keys:
            return 422, error_body(
                422,
                f"A maintenance schedule with key '{key}' already exists on this cluster; "
                "schedule keys must be unique.",
                api_error_code=1503,
            )

        schedule_id = str(uuid.uuid5(_UUID_NS, f"maintenance-schedule:{key}"))
        state.schedule_keys[key] = schedule_id
        created = dict(body)
        created["id"] = schedule_id
        return 201, created

    def _assign_policy(self, body, params):
        policy_id = params.get("id")
        if policy_id not in POLICIES:
            return 404, error_body(404, f"No policy found with identifier '{policy_id}'.")
        group_ids = body.get("groupIds") or []
        if not isinstance(group_ids, list):
            return 400, error_body(400, "groupIds must be an array.")
        state = self.server.state
        unknown = [gid for gid in group_ids if gid not in state.custom_groups]
        if unknown:
            return 400, error_body(
                400, f"No custom group found with identifier '{unknown[0]}'."
            )
        for gid in group_ids:
            state.custom_groups[gid]["policy"] = policy_id
        return 200, {"policyId": policy_id, "groupIds": list(group_ids)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="docs/contract.json")
    parser.add_argument("--log", required=True, help="NDJSON request log path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    routes = load_routes(args.contract)

    open(args.log, "w", encoding="utf-8").close()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.routes = routes
    server.state = Appliance()
    server.log_path = args.log
    server.log_lock = threading.Lock()
    server.seq = 0

    sys.stdout.write(json.dumps({"port": server.server_address[1]}) + "\n")
    sys.stdout.flush()

    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
