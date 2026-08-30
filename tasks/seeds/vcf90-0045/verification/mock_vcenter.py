#!/usr/bin/env python3
"""Contract-pinned loopback vCenter used by protected verification.

Routes are built from docs/contract.json. Nothing outside the three operations
that contract names is served, so a candidate cannot reach an operation the
pinned VMware Cloud Foundation 9.0 specification does not cover.

Every request is appended to a JSON Lines request log so verify.py can assert
the exact wire shape the candidate package produced, and every log line carries
a snapshot of the fixture's pending customizations so a precheck that refused a
mutation can be shown to have changed nothing.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parent

PINNED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_TAG = "9.0.0.0"
PINNED_SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
PINNED_API_VERSION = "9.0.0.0"
PINNED_BASE_PATH = "/api"
SESSION_HEADER = "vmware-api-session-id"

EXPECTED_OPERATION_IDS = {
    "Vcenter.VM_list",
    "Vcenter.Vm.Guest.Customization_check",
    "Vcenter.Vm.Guest.Customization_set",
}

SESSION_ID = "dummy-vcf90-session-id-0045"

# Filters Vcenter.VM_list declares, in the order the specification declares
# them. The fixture only needs to honour the three the scenarios exercise; the
# rest are accepted and matched so an over-eager client is still answered
# consistently rather than silently ignored.
LIST_FILTERS = (
    "vms",
    "names",
    "folders",
    "datacenters",
    "hosts",
    "clusters",
    "resource_pools",
    "power_states",
)

# vm identifier -> fixture record. "summary" is exactly a Vcenter.VM.Summary.
INVENTORY: dict[str, dict[str, Any]] = {
    "vm-1201": {
        "summary": {
            "vm": "vm-1201",
            "name": "vcf90-web-01",
            "power_state": "POWERED_OFF",
            "cpu_count": 2,
            "memory_size_mib": 4096,
        },
        "folder": "group-v22",
        "datacenter": "datacenter-3",
        "host": "host-31",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1202": {
        "summary": {
            "vm": "vm-1202",
            "name": "vcf90-db-02",
            "power_state": "POWERED_ON",
            "cpu_count": 8,
            "memory_size_mib": 32768,
        },
        "folder": "group-v22",
        "datacenter": "datacenter-3",
        "host": "host-31",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1203": {
        "summary": {
            "vm": "vm-1203",
            "name": "vcf90-legacy-03",
            "power_state": "POWERED_OFF",
            "cpu_count": 1,
            "memory_size_mib": 2048,
        },
        "folder": "group-v23",
        "datacenter": "datacenter-3",
        "host": "host-32",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1204": {
        "summary": {
            "vm": "vm-1204",
            "name": "vcf90-clear-04",
            "power_state": "POWERED_OFF",
            "cpu_count": 4,
            "memory_size_mib": 8192,
        },
        "folder": "group-v23",
        "datacenter": "datacenter-3",
        "host": "host-32",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1205": {
        "summary": {
            "vm": "vm-1205",
            "name": "vcf90-flaky-05",
            "power_state": "POWERED_OFF",
            "cpu_count": 2,
            "memory_size_mib": 4096,
        },
        "folder": "group-v23",
        "datacenter": "datacenter-3",
        "host": "host-32",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1206": {
        "summary": {
            "vm": "vm-1206",
            "name": "vcf90-nospec-06",
            "power_state": "POWERED_OFF",
            "cpu_count": 2,
            "memory_size_mib": 4096,
        },
        "folder": "group-v23",
        "datacenter": "datacenter-3",
        "host": "host-32",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    # Two virtual machines deliberately share one name.
    "vm-1207": {
        "summary": {
            "vm": "vm-1207",
            "name": "vcf90-twin-07",
            "power_state": "POWERED_OFF",
            "cpu_count": 2,
            "memory_size_mib": 4096,
        },
        "folder": "group-v22",
        "datacenter": "datacenter-3",
        "host": "host-31",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1208": {
        "summary": {
            "vm": "vm-1208",
            "name": "vcf90-twin-07",
            "power_state": "POWERED_ON",
            "cpu_count": 2,
            "memory_size_mib": 4096,
        },
        "folder": "group-v23",
        "datacenter": "datacenter-3",
        "host": "host-32",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
    "vm-1209": {
        "summary": {
            "vm": "vm-1209",
            "name": "vcf90-indeterminate-09",
            "power_state": "POWERED_OFF",
            "cpu_count": 2,
            "memory_size_mib": 4096,
        },
        "folder": "group-v23",
        "datacenter": "datacenter-3",
        "host": "host-32",
        "cluster": "domain-c11",
        "resource_pool": "resgroup-12",
    },
}

# vm identifier -> what Vcenter.Vm.Guest.Customization_check answers.
CHECK_RESULTS: dict[str, dict[str, Any]] = {
    "vm-1201": {
        "status": 200,
        "body": {
            "check_status": "SUPPORTED",
            "supported_guest_os": True,
            "supported_power_state": True,
        },
    },
    "vm-1202": {
        "status": 200,
        "body": {
            "check_status": "NOT_SUPPORTED",
            "supported_guest_os": True,
            "supported_power_state": False,
        },
    },
    # The check never reached the power state step, so that member is absent.
    "vm-1203": {
        "status": 200,
        "body": {
            "check_status": "NOT_SUPPORTED",
            "supported_guest_os": False,
        },
    },
    "vm-1204": {
        "status": 200,
        "body": {
            "check_status": "SUPPORTED",
            "supported_guest_os": True,
            "supported_power_state": True,
        },
    },
    "vm-1205": {
        "status": 503,
        "error_type": "SERVICE_UNAVAILABLE",
        "message_id": "com.vmware.api.vcenter.vm.guest.customization.check.unavailable",
        "message": "The guest customization service is temporarily unavailable.",
    },
    "vm-1206": {
        "status": 200,
        "body": {
            "check_status": "SUPPORTED",
            "supported_guest_os": True,
            "supported_power_state": True,
        },
    },
    "vm-1207": {
        "status": 200,
        "body": {
            "check_status": "SUPPORTED",
            "supported_guest_os": True,
            "supported_power_state": True,
        },
    },
    "vm-1208": {
        "status": 200,
        "body": {
            "check_status": "SUPPORTED",
            "supported_guest_os": True,
            "supported_power_state": True,
        },
    },
    # A malformed success response is used to prove clients fail closed when
    # the required check_status member is missing.
    "vm-1209": {
        "status": 200,
        "body": {},
    },
}

# Named customization specifications that exist in the fixture inventory.
KNOWN_SPECS = ("vcf90-linux-prep", "vcf90-windows-prep")

SET_SPEC_MEMBERS = ("name", "spec")


def load_contract() -> dict[str, Any]:
    """Load docs/contract.json and refuse to serve anything it does not pin."""
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    source = contract["source"]
    if source["commitSha"] != PINNED_COMMIT:
        raise SystemExit(f"contract commit sha is not {PINNED_COMMIT}")
    if source["tag"] != PINNED_TAG:
        raise SystemExit(f"contract tag is not {PINNED_TAG}")
    if source["specPath"] != PINNED_SPEC_PATH:
        raise SystemExit(f"contract spec path is not {PINNED_SPEC_PATH}")
    if source["apiVersion"] != PINNED_API_VERSION:
        raise SystemExit(f"contract api version is not {PINNED_API_VERSION}")
    if contract["server"]["basePath"] != PINNED_BASE_PATH:
        raise SystemExit(f"contract base path is not {PINNED_BASE_PATH}")
    if contract["authentication"]["name"] != SESSION_HEADER:
        raise SystemExit(f"contract session header is not {SESSION_HEADER}")
    found = {op["operationId"] for op in contract["operations"]}
    if found != EXPECTED_OPERATION_IDS:
        raise SystemExit(f"contract operations are {sorted(found)}")
    return contract


class Route:
    """One routable operation, compiled from the contract projection."""

    def __init__(self, operation: dict[str, Any], base_path: str) -> None:
        self.operation_id = operation["operationId"]
        self.method = operation["method"]
        self.query = dict(operation.get("query") or {})
        self.parameter_names: list[str] = []
        pattern = ""
        for literal, name in re.findall(
            r"([^{]*)(?:\{([^}]+)\})?", base_path + operation["path"]
        ):
            pattern += re.escape(literal)
            if name:
                self.parameter_names.append(name)
                pattern += r"(?P<" + name + r">[^/]+)"
        self.pattern = re.compile("^" + pattern + "$")

    def match(self, method: str, path: str, query: list[tuple[str, str]]):
        if method != self.method:
            return None
        found = self.pattern.match(path)
        if found is None:
            return None
        fixed = [(name, value) for name, value in query if name in self.query]
        if fixed != [(name, value) for name, value in self.query.items()]:
            return None
        return found.groupdict()


class MockState:
    def __init__(self, contract: dict[str, Any], request_log: Path) -> None:
        self.routes = [
            Route(op, contract["server"]["basePath"]) for op in contract["operations"]
        ]
        self.request_log = request_log
        self.lock = threading.Lock()
        self.sequence = 0
        # vm identifier -> name of the pending customization specification.
        self.pending: dict[str, str] = {}
        request_log.parent.mkdir(parents=True, exist_ok=True)
        request_log.write_text("", encoding="utf-8")

    def record(self, entry: dict[str, Any]) -> int:
        with self.lock:
            self.sequence += 1
            entry["sequence"] = self.sequence
            entry["pendingCustomizations"] = dict(sorted(self.pending.items()))
            with self.request_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
            return self.sequence


class ContractServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, state: MockState) -> None:
        super().__init__(address, handler)
        self.state = state


class ContractHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "contract-pinned-vcenter/9.0.0.0"

    def log_message(self, *args: Any) -> None:  # keep verification output clean
        return

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _error(error_type: str, message_id: str, message: str) -> dict[str, Any]:
        return {
            "error_type": error_type,
            "messages": [
                {"id": message_id, "default_message": message, "args": []},
            ],
        }

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _respond(self, status: int, payload: Any) -> None:
        if payload is None:
            body = b""
        else:
            body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if status == 302:
            # The redirect scenario proves a client surfaces the original
            # non-2xx response instead of following it to a successful list.
            self.send_header("Location", "/api/vcenter/vm?names=vcf90-web-01")
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.close_connection = False
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _handle(self, method: str) -> None:
        state: MockState = self.server.state
        split = urlsplit(self.path)
        query_pairs = parse_qsl(split.query, keep_blank_values=True)
        raw_body = self._read_body()

        operation_id = None
        path_parameters: dict[str, str] = {}
        for route in state.routes:
            matched = route.match(method, split.path, query_pairs)
            if matched is not None:
                operation_id = route.operation_id
                path_parameters = matched
                break

        try:
            decoded = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None

        status, payload = self._dispatch(
            operation_id, path_parameters, query_pairs, raw_body
        )

        entry = {
            "operationId": operation_id,
            "method": method,
            "target": self.path,
            "path": split.path,
            "query": split.query,
            "queryPairs": [list(pair) for pair in query_pairs],
            "pathParameters": path_parameters,
            "sessionHeader": self.headers.get(SESSION_HEADER),
            "accept": self.headers.get("Accept"),
            "contentType": self.headers.get("Content-Type"),
            "contentLength": self.headers.get("Content-Length"),
            "bodyRaw": raw_body.decode("utf-8", "replace"),
            "bodyJson": decoded,
            "bodyMembers": sorted(decoded) if isinstance(decoded, dict) else None,
            "status": status,
        }
        state.record(entry)
        self._respond(status, payload)

    # -- dispatch ----------------------------------------------------------

    def _dispatch(self, operation_id, path_parameters, query_pairs, raw_body):
        if operation_id is None:
            return 404, self._error(
                "OPERATION_NOT_FOUND",
                "com.vmware.vapi.std.errors.operation_not_found",
                "The loopback fixture serves only the operations docs/contract.json names.",
            )
        if self.headers.get(SESSION_HEADER) != SESSION_ID:
            return 401, self._error(
                "UNAUTHENTICATED",
                "com.vmware.vapi.endpoint.method.authentication.required",
                "A valid vmware-api-session-id header is required.",
            )
        if operation_id == "Vcenter.VM_list":
            return self._list_vms(query_pairs, raw_body)
        if operation_id == "Vcenter.Vm.Guest.Customization_check":
            return self._check(path_parameters["vm"], raw_body)
        if operation_id == "Vcenter.Vm.Guest.Customization_set":
            return self._set(path_parameters["vm"], raw_body)
        return 404, self._error(
            "OPERATION_NOT_FOUND",
            "com.vmware.vapi.std.errors.operation_not_found",
            "Unhandled operation.",
        )

    def _list_vms(self, query_pairs, raw_body):
        if raw_body:
            return 400, self._error(
                "INVALID_REQUEST",
                "com.vmware.vapi.std.errors.invalid_request",
                "Vcenter.VM_list declares no request body.",
            )
        wanted: dict[str, list[str]] = {}
        for name, value in query_pairs:
            if name not in LIST_FILTERS:
                return 400, self._error(
                    "INVALID_ARGUMENT",
                    "com.vmware.vapi.std.errors.invalid_argument",
                    f"Vcenter.VM_list declares no query parameter named {name}.",
                )
            if value == "":
                return 400, self._error(
                    "INVALID_ARGUMENT",
                    "com.vmware.vapi.std.errors.invalid_argument",
                    f"Filter {name} was sent empty; an unset filter is omitted instead.",
                )
            wanted.setdefault(name, []).append(value)

        if wanted.get("names") == ["vcf90-redirect-10"]:
            return 302, self._error(
                "ERROR",
                "com.vmware.api.vcenter.vm.list.redirected",
                "The inventory request was redirected.",
            )

        field_for = {
            "vms": lambda record: record["summary"]["vm"],
            "names": lambda record: record["summary"]["name"],
            "folders": lambda record: record["folder"],
            "datacenters": lambda record: record["datacenter"],
            "hosts": lambda record: record["host"],
            "clusters": lambda record: record["cluster"],
            "resource_pools": lambda record: record["resource_pool"],
            "power_states": lambda record: record["summary"]["power_state"],
        }
        matches = []
        for vm_id in sorted(INVENTORY):
            record = INVENTORY[vm_id]
            if all(field_for[f](record) in values for f, values in wanted.items()):
                matches.append(dict(record["summary"]))
        return 200, matches

    def _check(self, vm_id: str, raw_body: bytes):
        if raw_body:
            return 400, self._error(
                "INVALID_REQUEST",
                "com.vmware.vapi.std.errors.invalid_request",
                "Vcenter.Vm.Guest.Customization_check declares no request body.",
            )
        if vm_id not in INVENTORY:
            return 404, self._error(
                "NOT_FOUND",
                "com.vmware.api.vcenter.vm.not_found",
                f"No virtual machine with identifier {vm_id} was found.",
            )
        outcome = CHECK_RESULTS[vm_id]
        if outcome["status"] != 200:
            return outcome["status"], self._error(
                outcome["error_type"], outcome["message_id"], outcome["message"]
            )
        return 200, dict(outcome["body"])

    def _set(self, vm_id: str, raw_body: bytes):
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type.lower() != "application/json":
            return 400, self._error(
                "INVALID_REQUEST",
                "com.vmware.vapi.std.errors.invalid_request",
                "Vcenter.Vm.Guest.Customization_set requires an application/json body.",
            )
        try:
            spec = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400, self._error(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                "The SetSpec body must be JSON.",
            )
        if not isinstance(spec, dict):
            return 400, self._error(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                "The SetSpec body must be a JSON object.",
            )
        unknown = sorted(set(spec) - set(SET_SPEC_MEMBERS))
        if unknown:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                "SetSpec members outside the schema: " + ", ".join(unknown),
            )
        if "spec" in spec:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                "The inline spec member is outside the contract projection.",
            )
        if "name" in spec and spec["name"] is None:
            return 400, self._error(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                "An unset SetSpec member is omitted from the body, not sent as null.",
            )
        if "name" in spec and not isinstance(spec["name"], str):
            return 400, self._error(
                "INVALID_ARGUMENT",
                "com.vmware.vapi.std.errors.invalid_argument",
                "SetSpec.name must be a string.",
            )
        if vm_id not in INVENTORY:
            return 404, self._error(
                "NOT_FOUND",
                "com.vmware.api.vcenter.vm.not_found",
                f"No virtual machine with identifier {vm_id} was found.",
            )
        if INVENTORY[vm_id]["summary"]["power_state"] != "POWERED_OFF":
            return 400, self._error(
                "NOT_ALLOWED_IN_CURRENT_STATE",
                "com.vmware.api.vcenter.vm.guest.customization.not_powered_off",
                f"Virtual machine {vm_id} is not in a powered off state.",
            )
        name = spec.get("name")
        if name is None:
            with self.server.state.lock:
                self.server.state.pending.pop(vm_id, None)
            return 204, None
        if name not in KNOWN_SPECS:
            return 404, self._error(
                "NOT_FOUND",
                "com.vmware.api.vcenter.guest.customization_spec.not_found",
                f"No customization specification named {name} was found.",
            )
        with self.server.state.lock:
            self.server.state.pending[vm_id] = name
        return 204, None

    # -- verbs -------------------------------------------------------------

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: mock_vcenter.py <port-file> <request-log>", file=sys.stderr)
        return 2
    port_file = Path(sys.argv[1])
    request_log = Path(sys.argv[2])

    contract = load_contract()
    state = MockState(contract, request_log)
    server = ContractServer(("127.0.0.1", 0), ContractHandler, state)

    port = server.server_address[1]
    port_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = port_file.with_suffix(port_file.suffix + ".tmp")
    tmp.write_text(str(port), encoding="utf-8")
    tmp.replace(port_file)

    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
