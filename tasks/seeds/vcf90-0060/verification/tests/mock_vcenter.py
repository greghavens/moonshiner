#!/usr/bin/env python3
"""Loopback vCenter mock pinned to docs/contract.json.

The route table is built from the contract, so the mock serves exactly the four
operations the contract names and nothing else. Every request is appended to a
JSON Lines request log that both the harness and the verifier read.

This process never contacts a VMware endpoint; it only binds 127.0.0.1.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# Deterministic fixture directory. The credential that is being rotated away from,
# the credential that is rotated to, and a credential whose session is accepted by
# Cis.Session_create but cannot be validated by Cis.Session_get.
PRINCIPAL = "administrator@vsphere.local"
CREDENTIALS = {
    (PRINCIPAL, "OldSecret!23"): {"verifiable": True, "hold_validation": False},
    (PRINCIPAL, "NewSecret!45"): {"verifiable": True, "hold_validation": True},
    (PRINCIPAL, "Quarantined!99"): {"verifiable": False, "hold_validation": False},
}

# Replacement validation opens a window for a separate reconfigure, and completing validation
# releases the held old-session request. These events prove the concurrency ordering without
# relying on scheduler speed or a fixed request duration.
HELD_VIRTUAL_MACHINES = {"vm-slow"}
VALIDATION_WINDOW_VM = "vm-validation-window"
REJECTED_VIRTUAL_MACHINES = {"vm-rejected": 400}

CREATED_TIME = "2026-02-11T09:14:52.331Z"


class ContractRoutes:
    """Method plus compiled path pattern for every operation the contract names."""

    def __init__(self, contract: dict) -> None:
        self.base_path = contract["serverBasePath"].rstrip("/")
        self.routes = []
        for operation_id in contract["operationIds"]:
            operation = contract["operations"][operation_id]
            full_path = self.base_path + operation["path"]
            pattern = "^" + re.sub(r"\{([A-Za-z0-9_]+)\}", r"(?P<\1>[^/]+)", full_path) + "$"
            self.routes.append((operation["method"], re.compile(pattern), operation_id))
        update_spec = contract["schemas"]["Vcenter.Vm.Hardware.Cpu.UpdateSpec"]
        self.update_spec_properties = dict(update_spec["properties"])
        self.token_header = contract["sessionLifecycle"]["tokenHeader"]

    def resolve(self, method: str, path: str):
        for route_method, pattern, operation_id in self.routes:
            match = pattern.match(path)
            if match and route_method == method:
                return operation_id, match.groupdict()
        return None, {}


class MockState:
    def __init__(self, routes: ContractRoutes, log_path: Path) -> None:
        self.routes = routes
        self.log_path = log_path
        self.lock = threading.RLock()
        self.sequence = 0
        self.tokens_issued = 0
        self.sessions: dict[str, dict] = {}
        self.open_requests: dict[int, str | None] = {}
        self.validation_window_work = threading.Event()
        self.replacement_validation_completed = threading.Event()
        self.log_path.write_text("", encoding="utf-8")

    def append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True)
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()

    def begin(self, record: dict, token: str | None) -> int:
        with self.lock:
            self.sequence += 1
            sequence = self.sequence
            in_flight = 0
            if token is not None:
                in_flight = sum(1 for value in self.open_requests.values() if value == token)
            self.open_requests[sequence] = token
            record.update({"event": "received", "seq": sequence, "inFlightForToken": in_flight})
            self.append(record)
        return sequence

    def finish(self, sequence: int, status: int) -> None:
        with self.lock:
            self.open_requests.pop(sequence, None)
            self.append(
                {"event": "completed", "seq": sequence, "status": status, "at": time.monotonic()}
            )

    def issue_token(self, user: str, fixture: dict) -> str:
        with self.lock:
            self.tokens_issued += 1
            token = f"cis-session-token-{self.tokens_issued}"
            self.sessions[token] = {
                "user": user,
                "verifiable": fixture["verifiable"],
                "hold_validation": fixture["hold_validation"],
                "active": True,
            }
        return token

    def session(self, token: str | None) -> dict | None:
        if token is None:
            return None
        with self.lock:
            session = self.sessions.get(token)
            if session is None or not session["active"]:
                return None
            return dict(session)

    def revoke(self, token: str) -> None:
        with self.lock:
            if token in self.sessions:
                self.sessions[token]["active"] = False


def error_body(error_type: str, message: str) -> bytes:
    payload = {
        "error_type": error_type,
        "messages": [
            {
                "id": "com.vmware.vapi.std.errors." + error_type.lower(),
                "default_message": message,
                "args": [],
            }
        ],
    }
    return json.dumps(payload).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state: MockState = None  # type: ignore[assignment]

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        return

    # -- plumbing ---------------------------------------------------------

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8")

    def _send(self, status: int, body: bytes | None = None, content_type: str | None = None) -> None:
        self.send_response(status)
        if status == 204:
            self.end_headers()
            return
        payload = body if body is not None else b""
        if body is not None:
            self.send_header("Content-Type", content_type or "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _dispatch(self, method: str) -> None:
        raw_path = self.path
        path, _, query = raw_path.partition("?")
        headers = {key.lower(): value for key, value in self.headers.items()}
        body = self._read_body()
        token = headers.get(self.state.routes.token_header)

        operation_id, path_parameters = self.state.routes.resolve(method, path)
        if operation_id is None:
            self.state.append(
                {
                    "event": "rejected",
                    "at": time.monotonic(),
                    "method": method,
                    "path": path,
                    "query": query,
                    "headers": headers,
                    "body": body,
                }
            )
            self._send(404, error_body("NOT_FOUND", "no such operation in the pinned contract"))
            return

        record = {
            "at": time.monotonic(),
            "operationId": operation_id,
            "method": method,
            "path": path,
            "query": query,
            "headers": headers,
            "body": body,
            "sessionToken": token,
        }
        sequence = self.state.begin(record, token)
        status, payload = self._handle(operation_id, path_parameters, headers, body, token)
        # The request stops being in flight before the response reaches the client, so a
        # client that reacts to the response can never race the bookkeeping.
        self.state.finish(sequence, status)
        self._send(status, payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("PATCH")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib signature
        self._dispatch("PUT")

    # -- operations -------------------------------------------------------

    def _handle(self, operation_id, path_parameters, headers, body, token):
        if operation_id == "Cis.Session_create":
            return self._session_create(headers)
        if operation_id == "Cis.Session_get":
            return self._session_get(token)
        if operation_id == "Cis.Session_delete":
            return self._session_delete(token)
        return self._cpu_update(path_parameters.get("vm", ""), body, token)

    def _session_create(self, headers):
        authorization = headers.get("authorization", "")
        if not authorization.lower().startswith("basic "):
            return 401, error_body("UNAUTHENTICATED", "basic_auth credentials are required")
        try:
            decoded = base64.b64decode(authorization.split(" ", 1)[1], validate=True).decode("utf-8")
            user, _, password = decoded.partition(":")
        except Exception:
            return 401, error_body("UNAUTHENTICATED", "malformed basic_auth credentials")
        fixture = CREDENTIALS.get((user, password))
        if fixture is None:
            return 401, error_body("UNAUTHENTICATED", "unknown principal or password")
        token = self.state.issue_token(user, fixture)
        return 201, json.dumps(token).encode("utf-8")

    def _session_get(self, token):
        session = self.state.session(token)
        if session is None:
            return 401, error_body("UNAUTHENTICATED", "session token is missing or not valid")
        if not session["verifiable"]:
            return 503, error_body(
                "SERVICE_UNAVAILABLE", "session retrieval failed due to server specific issues"
            )
        if session["hold_validation"]:
            # Give a concurrent reconfigure a deterministic validation window. A client that
            # serializes new work behind rotation is also valid, so lack of such a request merely
            # closes the window rather than failing validation.
            self.state.validation_window_work.wait(timeout=5)
            self.state.replacement_validation_completed.set()
        info = {
            "user": session["user"],
            "created_time": CREATED_TIME,
            "last_accessed_time": CREATED_TIME,
        }
        return 200, json.dumps(info).encode("utf-8")

    def _session_delete(self, token):
        session = self.state.session(token)
        if session is None:
            return 401, error_body("UNAUTHENTICATED", "session token is missing or not valid")
        self.state.revoke(token)
        return 204, None

    def _cpu_update(self, vm_id, body, token):
        if self.state.session(token) is None:
            return 401, error_body("UNAUTHENTICATED", "session token is missing or not valid")
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            return 400, error_body("INVALID_ARGUMENT", "request body is not JSON")
        if not isinstance(parsed, dict):
            return 400, error_body(
                "INVALID_ARGUMENT", "Vcenter.Vm.Hardware.Cpu.UpdateSpec must be a JSON object"
            )
        properties = self.state.routes.update_spec_properties
        for name, value in parsed.items():
            declared = properties.get(name)
            if declared is None:
                return 400, error_body(
                    "INVALID_ARGUMENT", f"'{name}' is not a property of Vcenter.Vm.Hardware.Cpu.UpdateSpec"
                )
            if declared["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                return 400, error_body("INVALID_ARGUMENT", f"'{name}' must be an integer")
            if declared["type"] == "boolean" and not isinstance(value, bool):
                return 400, error_body("INVALID_ARGUMENT", f"'{name}' must be a boolean")

        if vm_id == VALIDATION_WINDOW_VM:
            self.state.validation_window_work.set()

        rejected_status = REJECTED_VIRTUAL_MACHINES.get(vm_id)
        if rejected_status is not None:
            return rejected_status, error_body(
                "INVALID_ARGUMENT", "the mock rejected this CPU reconfiguration"
            )

        if vm_id in HELD_VIRTUAL_MACHINES:
            if not self.state.replacement_validation_completed.wait(timeout=10):
                return 503, error_body(
                    "SERVICE_UNAVAILABLE", "credential rotation never completed session validation"
                )
            # The reconfigure only lands if the session it was issued under survived
            # for the whole call; a session torn down mid-flight strands the request.
            if self.state.session(token) is None:
                return 401, error_body(
                    "UNAUTHENTICATED", "session was terminated while the request was in flight"
                )
        return 204, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--port-file", required=True)
    arguments = parser.parse_args()

    contract = json.loads(Path(arguments.contract).read_text(encoding="utf-8"))
    routes = ContractRoutes(contract)
    Handler.state = MockState(routes, Path(arguments.log))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    Path(arguments.port_file).write_text(str(server.server_address[1]), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
