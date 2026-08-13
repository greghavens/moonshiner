"""Loopback mock of the VCF Operations for Networks 9.1 certificate-update surface.

The route table is built entirely from ``docs/contract.json``: the mock serves
exactly the four operations that contract names, at the method and path the
contract records, and answers anything else with 404. It never looks at
operationIds -- it dispatches on the ``role`` each contract entry declares -- so
the contract file is the single source of truth for what is reachable.

Every request is appended to a JSON Lines request log so a test can read back the
exact wire shape that was sent.

Run standalone for manual poking:

    python3 tests/mock_vcfonw_server.py --scenario success --log /tmp/reqs.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONTRACT = os.path.join(REPO_ROOT, "docs", "contract.json")

REQUIRED_ROLES = (
    "authenticate",
    "submit_certificate_update",
    "poll_update_status",
    "revoke_token",
)

# Status a poll reports on its 1st, 2nd, ... read. The final entry repeats.
SCENARIOS = {
    "success": ["IN_PROGRESS", "IN_PROGRESS", "SUCCESS"],
    "failure": ["IN_PROGRESS", "FAILED"],
    "stuck": ["IN_PROGRESS"],
}

PLATFORM_NODE = {
    "id": "10000:901:1146842716",
    "entity_type": "Node",
    "node_type": "PLATFORM_VM",
    "node_id": "vcfonw-platform-1",
    "ip_address": "10.24.16.11",
    "name": "vcfonw-platform-1",
}
PROXY_NODE = {
    "id": "10000:901:1146842717",
    "entity_type": "Node",
    "node_type": "PROXY_VM",
    "node_id": "vcfonw-proxy-1",
    "ip_address": "10.24.16.12",
    "name": "vcfonw-proxy-1",
}

FAILURE_MESSAGE = (
    "certificate update failed: private key does not match the supplied certificate"
)


class ContractError(RuntimeError):
    """The contract does not describe a servable surface."""


def load_contract(path=DEFAULT_CONTRACT):
    with open(path, "r", encoding="utf-8") as handle:
        contract = json.load(handle)

    base_path = contract.get("source", {}).get("server_base_path")
    if not isinstance(base_path, str) or not base_path.startswith("/"):
        raise ContractError("contract source.server_base_path must be an absolute path")

    operations = contract.get("operations")
    if not isinstance(operations, list):
        raise ContractError("contract operations must be a list")

    routes = {}
    for entry in operations:
        role = entry.get("role")
        if role not in REQUIRED_ROLES:
            raise ContractError("unknown contract role: %r" % (role,))
        if role in routes:
            raise ContractError("duplicate contract role: %s" % role)
        method = entry.get("method")
        path = entry.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            raise ContractError("role %s needs a string method and path" % role)
        status = entry.get("success", {}).get("status")
        if not isinstance(status, int):
            raise ContractError("role %s needs an integer success status" % role)
        full = base_path.rstrip("/") + path
        routes[role] = {
            "method": method.upper(),
            "segments": [seg for seg in full.split("/") if seg != ""],
            "success_status": status,
        }

    missing = [role for role in REQUIRED_ROLES if role not in routes]
    if missing:
        raise ContractError("contract is missing roles: %s" % ", ".join(missing))

    security = contract.get("security", {})
    prefix = security.get("value_prefix")
    header = security.get("name")
    if not isinstance(prefix, str) or not prefix:
        raise ContractError("contract security.value_prefix is required")
    if not isinstance(header, str) or not header:
        raise ContractError("contract security.name is required")

    return {
        "routes": routes,
        "auth_header": header.lower(),
        "auth_prefix": prefix,
        "base_path": base_path.rstrip("/"),
    }


class ApplianceState:
    """Everything the mock remembers for one case."""

    def __init__(self, contract, scenario, log_path, case_id, username, password,
                 certificate_id, fault=None):
        if scenario not in SCENARIOS:
            raise ValueError("unknown scenario: %s" % scenario)
        self.contract = contract
        self.scenario = scenario
        self.log_path = log_path
        self.case_id = case_id
        self.username = username
        self.password = password
        self.certificate_id = certificate_id
        self.fault = fault
        self.token = "NI-TOKEN-%s" % case_id
        self.token_valid = False
        self.updates = {}
        self.update_seq = 0
        self.request_seq = 0
        self.lock = threading.Lock()
        with open(self.log_path, "w", encoding="utf-8"):
            pass

    def next_update_id(self):
        self.update_seq += 1
        return "cert-update-%s-%04d" % (self.case_id, self.update_seq)

    def record(self, entry):
        with self.lock:
            self.request_seq += 1
            entry["seq"] = self.request_seq
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
                handle.flush()


def _match(route, method, segments):
    if route["method"] != method:
        return None
    template = route["segments"]
    if len(template) != len(segments):
        return None
    params = {}
    for want, got in zip(template, segments):
        if want.startswith("{") and want.endswith("}"):
            if not got:
                return None
            params[want[1:-1]] = got
        elif want != got:
            return None
    return params


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockVcfOpsNetworks/9.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # silence stderr noise
        pass

    @property
    def state(self):
        return self.server.state

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            size = int(length)
        except ValueError:
            return b""
        if size <= 0:
            return b""
        return self.rfile.read(size)

    def _respond(self, status, payload, headers=None):
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)
        return status

    def _api_error(self, status, message):
        return status, {"code": status, "message": message, "details": []}

    # -- dispatch ---------------------------------------------------------
    def _handle(self, method):
        raw_path = self.path
        if "?" in raw_path:
            path, query = raw_path.split("?", 1)
        else:
            path, query = raw_path, ""
        segments = [seg for seg in path.split("/") if seg != ""]
        body = self._read_body()
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError:
            body_text = None
        body_json = None
        if body_text:
            try:
                body_json = json.loads(body_text)
            except ValueError:
                body_json = None

        matched_role = None
        params = None
        for role, route in self.state.contract["routes"].items():
            found = _match(route, method, segments)
            if found is not None:
                matched_role = role
                params = found
                break

        if matched_role is None:
            status, payload = self._api_error(404, "no operation in contract for %s %s" % (method, path))
        else:
            status, payload = self._dispatch(matched_role, params, body_json)

        entry = {
            "case": self.state.case_id,
            "role": matched_role,
            "method": method,
            "path": path,
            "query": query,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body_text": body_text,
            "body_json": body_json,
            "status": status,
            "monotonic": time.monotonic(),
        }
        self.state.record(entry)
        response_headers = None
        if self.state.fault == "authenticate_redirect" and status == 302:
            response_headers = {"Location": path}
        self._respond(status, payload, response_headers)

    def _authorized(self):
        state = self.state
        expected = "%s %s" % (state.contract["auth_prefix"], state.token)
        supplied = self.headers.get(state.contract["auth_header"])
        return state.token_valid and supplied == expected

    def _dispatch(self, role, params, body_json):
        state = self.state
        if role == "authenticate":
            if state.fault == "authenticate_redirect":
                return self._api_error(302, "authentication was redirected")
            if not isinstance(body_json, dict):
                return self._api_error(400, "request body must be a JSON object")
            if (body_json.get("username") != state.username
                    or body_json.get("password") != state.password):
                return self._api_error(401, "invalid credentials")
            state.token_valid = True
            return 200, {"token": state.token, "expiry": 1778000000000}

        if not self._authorized():
            return self._api_error(401, "auth token is missing, invalid or expired")

        if role == "submit_certificate_update":
            if state.fault == "submit":
                return self._api_error(409, "a certificate update is already in progress")
            cert_id = params.get("id")
            if cert_id != state.certificate_id:
                return self._api_error(404, "no certificate with id %s" % cert_id)
            if not isinstance(body_json, dict):
                return self._api_error(400, "request body must be a JSON object")
            if not body_json.get("certificate") or not body_json.get("private_key"):
                return self._api_error(400, "certificate and private_key are required")
            update_id = state.next_update_id()
            state.updates[update_id] = {"polls": 0, "certificate_id": cert_id}
            return state.contract["routes"][role]["success_status"], {
                "id": update_id,
                "name": cert_id,
                "status": "SUBMITTED",
                "last_modified_by_user": state.username,
                "last_modified_time": 1778000123456,
            }

        if role == "poll_update_status":
            if state.fault == "poll":
                return self._api_error(500, "certificate update status is unavailable")
            update_id = params.get("id")
            update = state.updates.get(update_id)
            if update is None:
                return self._api_error(404, "no certificate update with id %s" % update_id)
            sequence = SCENARIOS[state.scenario]
            index = min(update["polls"], len(sequence) - 1)
            update["polls"] += 1
            status_value = sequence[index]
            payload = {
                "id": update_id,
                "name": update["certificate_id"],
                "status": status_value,
                "last_modified_by_user": state.username,
                "last_modified_time": 1778000123456 + update["polls"],
            }
            if status_value == "SUCCESS":
                payload["updated_nodes"] = [PLATFORM_NODE, PROXY_NODE]
            elif status_value == "FAILED":
                payload["error_message"] = FAILURE_MESSAGE
                payload["failed_nodes"] = [PROXY_NODE]
                payload["updated_nodes"] = [PLATFORM_NODE]
            return 200, payload

        if role == "revoke_token":
            if state.fault == "revoke":
                return self._api_error(500, "token deletion is unavailable")
            state.token_valid = False
            return state.contract["routes"][role]["success_status"], None

        return self._api_error(500, "unhandled role %s" % role)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(*, log_path, case_id, scenario="success", contract_path=DEFAULT_CONTRACT,
          username="admin@local", password="VMware1!VMware1!",
          certificate_id="platform-web-cert", fault=None):
    """Start the mock on an ephemeral loopback port. Returns (server, base_url, state)."""
    contract = load_contract(contract_path)
    state = ApplianceState(contract, scenario, log_path, case_id, username, password,
                           certificate_id, fault)
    server = MockServer(("127.0.0.1", 0), Handler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.daemon = True
    thread.start()
    server.thread = thread
    host, port = server.server_address[:2]
    return server, "http://%s:%d" % (host, port), state


def stop(server):
    server.shutdown()
    server.server_close()
    server.thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=DEFAULT_CONTRACT)
    parser.add_argument("--scenario", default="success", choices=sorted(SCENARIOS))
    parser.add_argument("--log", required=True)
    parser.add_argument("--case", default="manual")
    args = parser.parse_args()
    server, url, _ = start(log_path=args.log, case_id=args.case, scenario=args.scenario,
                           contract_path=args.contract)
    print("listening on %s (scenario=%s)" % (url, args.scenario), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop(server)


if __name__ == "__main__":
    main()
