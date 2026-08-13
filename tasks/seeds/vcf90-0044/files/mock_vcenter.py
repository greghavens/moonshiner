#!/usr/bin/env python3
"""Contract-pinned loopback stand-in for a VCF 9.0 vCenter Automation endpoint.

Routing is built from docs/contract.json, which is a projection of
specifications/vsphere/openapi/automation/vcenter.yaml in vmware/vcf-api-specs
at tag 9.0.0.0.  Only the operations the contract names are served; every other
target answers 404.  Request bodies are validated against the projected schemas,
so a misspelled or null-valued property is rejected the way the real endpoint
rejects it.

Every request is appended to a JSON Lines log so a test can assert the exact
wire shape that was produced.

This file is protected.  Do not modify it.
"""

import argparse
import base64
import binascii
import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

IMPLEMENTED = (
    "Cis.Session_create",
    "Cis.Session_delete",
    "Vcenter.Vm.Power_get",
    "Vcenter.Vm.Hardware.Memory_update",
    "Vcenter.Vm.Hardware.Disk_create",
    "Vcenter.Vm.Hardware.Ethernet_create",
    "Vcenter.Vm.Power_start",
)


class ApiError(Exception):
    def __init__(self, status, error_type, message, message_id="com.vmware.vapi.std.errors.error"):
        super().__init__(message)
        self.status = status
        self.payload = {
            "error_type": error_type,
            "messages": [{"args": [], "default_message": message, "id": message_id}],
        }


# --------------------------------------------------------------------------
# contract
# --------------------------------------------------------------------------

class Contract:
    def __init__(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            self.doc = json.load(handle)
        self.base_path = self.doc["basePath"].rstrip("/")
        self.schemas = self.doc["schemas"]
        self.by_operation = {}
        self.routes = []
        for op in self.doc["operations"]:
            if op["operationId"] not in IMPLEMENTED:
                raise SystemExit(
                    "contract names operation %r, which this mock does not implement"
                    % op["operationId"]
                )
            self.by_operation[op["operationId"]] = op
            self.routes.append(op)

    def resolve(self, ref):
        return self.schemas[ref.split("/")[-1]]

    def match(self, method, path, query):
        """Return (operation, path_params) for a request, or (None, None)."""
        if not path.startswith(self.base_path + "/") and path != self.base_path:
            return None, None
        rest = path[len(self.base_path):] or "/"
        for op in self.routes:
            if op["method"] != method:
                continue
            params = _match_template(op["path"], rest)
            if params is None:
                continue
            pinned = op.get("query") or {}
            if any(query.get(k) != v for k, v in pinned.items()):
                continue
            if set(query) - set(pinned):
                continue
            return op, params
        return None, None


def _match_template(template, actual):
    want = [seg for seg in template.split("/") if seg != ""]
    have = [seg for seg in actual.split("/") if seg != ""]
    if len(want) != len(have):
        return None
    params = {}
    for w, h in zip(want, have):
        if w.startswith("{") and w.endswith("}"):
            if h == "":
                return None
            params[w[1:-1]] = unquote(h)
        elif w != h:
            return None
    return params


# --------------------------------------------------------------------------
# body validation against the projected schemas
# --------------------------------------------------------------------------

def _type_ok(value, declared):
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "string":
        return isinstance(value, str)
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    return True


def validate(contract, value, schema_name, where):
    schema = contract.schemas[schema_name]
    if not isinstance(value, dict):
        raise ApiError(400, "INVALID_ARGUMENT", "%s must be a JSON object" % where,
                       "com.vmware.vapi.std.errors.invalid_argument")
    properties = schema.get("properties", {})
    for name, sub in sorted(value.items()):
        if name not in properties:
            raise ApiError(
                400, "INVALID_ARGUMENT",
                "%s carries unexpected property '%s'; %s declares %s"
                % (where, name, schema_name, ", ".join(sorted(properties)) or "no properties"),
                "com.vmware.vapi.std.errors.invalid_argument")
        if sub is None:
            raise ApiError(
                400, "INVALID_ARGUMENT",
                "%s sets optional property '%s' to null; unset properties must be omitted "
                "from the request body" % (where, name),
                "com.vmware.vapi.std.errors.invalid_argument")
        declared = properties[name]
        if "$ref" in declared:
            validate(contract, sub, declared["$ref"].split("/")[-1], "%s.%s" % (where, name))
        elif not _type_ok(sub, declared.get("type")):
            raise ApiError(
                400, "INVALID_ARGUMENT",
                "%s.%s must be of type %s" % (where, name, declared.get("type")),
                "com.vmware.vapi.std.errors.invalid_argument")
        elif declared.get("enum") and sub not in declared["enum"]:
            raise ApiError(
                400, "INVALID_ARGUMENT",
                "%s.%s must be one of %s" % (where, name, ", ".join(declared["enum"])),
                "com.vmware.vapi.std.errors.invalid_argument")
    for name in schema.get("required", []):
        if name not in value:
            raise ApiError(
                400, "INVALID_ARGUMENT",
                "%s is missing required property '%s'" % (where, name),
                "com.vmware.vapi.std.errors.invalid_argument")


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------

class Inventory:
    def __init__(self, host_free_memory_mib, disk_serial=2000, nic_serial=4000,
                 power_state="POWERED_OFF"):
        self.lock = threading.Lock()
        self.host_free_memory_mib = host_free_memory_mib
        self.sessions = set()
        self.session_serial = 0
        self.disk_serial = disk_serial
        self.nic_serial = nic_serial
        self.networks = {"network-1105": "vlan-1105-app", "network-1106": "vlan-1106-db"}
        self.vms = {
            "vm-3041": {
                "name": "payments-db-01",
                "power_state": power_state,
                "clean_power_off": power_state == "POWERED_OFF",
                "memory_mib": 4096,
                "hot_add_enabled": False,
                "disks": {},
                "nics": {},
            }
        }

    def vm(self, moid):
        try:
            return self.vms[moid]
        except KeyError:
            raise ApiError(404, "NOT_FOUND",
                           "The virtual machine %r could not be found." % moid,
                           "com.vmware.vapi.std.errors.not_found")

    def new_session(self):
        self.session_serial += 1
        token = "sess-%08x" % (0x5EED0000 + self.session_serial)
        self.sessions.add(token)
        return token


# --------------------------------------------------------------------------
# operation handlers
# --------------------------------------------------------------------------

def op_session_create(ctx):
    header = ctx["headers"].get("authorization", "")
    scheme, _, blob = header.partition(" ")
    if scheme.lower() != "basic" or not blob.strip():
        raise ApiError(401, "UNAUTHENTICATED",
                       "Authentication required.",
                       "com.vmware.vapi.std.errors.unauthenticated")
    try:
        user, sep, password = base64.b64decode(blob.strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required.",
                       "com.vmware.vapi.std.errors.unauthenticated")
    if not sep or not user or not password:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication required.",
                       "com.vmware.vapi.std.errors.unauthenticated")
    return 201, ctx["inventory"].new_session()


def op_session_delete(ctx):
    ctx["inventory"].sessions.discard(ctx["token"])
    return 204, None


def op_power_get(ctx):
    vm = ctx["inventory"].vm(ctx["params"]["vm"])
    info = {"state": vm["power_state"]}
    if vm["power_state"] == "POWERED_OFF":
        info["clean_power_off"] = vm["clean_power_off"]
    return 200, info


def op_memory_update(ctx):
    vm = ctx["inventory"].vm(ctx["params"]["vm"])
    body = ctx["body"]
    if "hot_add_enabled" in body and vm["power_state"] != "POWERED_OFF":
        raise ApiError(400, "NOT_ALLOWED_IN_CURRENT_STATE",
                       "hot_add_enabled may only be changed while the virtual machine is powered off.",
                       "com.vmware.vapi.std.errors.not_allowed_in_current_state")
    if "size_mib" in body:
        if body["size_mib"] <= 0:
            raise ApiError(400, "INVALID_ARGUMENT",
                           "size_mib must be a positive number of mebibytes.",
                           "com.vmware.vapi.std.errors.invalid_argument")
        vm["memory_mib"] = body["size_mib"]
    if "hot_add_enabled" in body:
        vm["hot_add_enabled"] = body["hot_add_enabled"]
    return 204, None


def op_disk_create(ctx):
    inv = ctx["inventory"]
    vm = inv.vm(ctx["params"]["vm"])
    body = ctx["body"]
    if ("backing" in body) == ("new_vmdk" in body):
        raise ApiError(400, "INVALID_ARGUMENT",
                       "Exactly one of backing or new_vmdk must be specified.",
                       "com.vmware.vapi.std.errors.invalid_argument")
    addresses = [key for key in ("ide", "scsi", "sata", "nvme") if key in body]
    if len(addresses) > 1:
        raise ApiError(400, "INVALID_ARGUMENT",
                       "At most one adapter address may be specified.",
                       "com.vmware.vapi.std.errors.invalid_argument")
    if addresses and "type" in body and body["type"] != addresses[0].upper():
        raise ApiError(400, "INVALID_ARGUMENT",
                       "type %s does not match the %s address that was supplied."
                       % (body["type"], addresses[0]),
                       "com.vmware.vapi.std.errors.invalid_argument")
    capacity = None
    if "new_vmdk" in body:
        capacity = body["new_vmdk"].get("capacity", 17179869184)
        if capacity <= 0:
            raise ApiError(400, "INVALID_ARGUMENT",
                           "new_vmdk.capacity must be a positive number of bytes.",
                           "com.vmware.vapi.std.errors.invalid_argument")
    inv.disk_serial += 1
    disk_id = str(inv.disk_serial)
    vm["disks"][disk_id] = {"capacity": capacity}
    return 201, disk_id


def op_ethernet_create(ctx):
    inv = ctx["inventory"]
    vm = inv.vm(ctx["params"]["vm"])
    body = ctx["body"]
    backing = body.get("backing")
    if backing is None:
        raise ApiError(400, "NOT_FOUND",
                       "No suitable network backing could be found for the virtual machine.",
                       "com.vmware.vapi.std.errors.not_found")
    network = backing.get("network")
    if backing["type"] == "STANDARD_PORTGROUP" and not network:
        raise ApiError(400, "INVALID_ARGUMENT",
                       "backing.network is required for a STANDARD_PORTGROUP backing.",
                       "com.vmware.vapi.std.errors.invalid_argument")
    if network is not None and network not in inv.networks:
        raise ApiError(404, "NOT_FOUND",
                       "The network %r could not be found." % network,
                       "com.vmware.vapi.std.errors.not_found")
    inv.nic_serial += 1
    nic_id = str(inv.nic_serial)
    vm["nics"][nic_id] = {"network": network}
    return 201, nic_id


def op_power_start(ctx):
    inv = ctx["inventory"]
    vm = inv.vm(ctx["params"]["vm"])
    if vm["power_state"] == "POWERED_ON":
        raise ApiError(400, "ALREADY_IN_DESIRED_STATE",
                       "The virtual machine is already powered on.",
                       "com.vmware.vapi.std.errors.already_in_desired_state")
    if vm["memory_mib"] > inv.host_free_memory_mib:
        raise ApiError(
            500, "UNABLE_TO_ALLOCATE_RESOURCE",
            "The host does not have sufficient memory resources to satisfy the reservation "
            "for virtual machine %s: %d MiB requested, %d MiB available."
            % (vm["name"], vm["memory_mib"], inv.host_free_memory_mib),
            "com.vmware.vapi.std.errors.unable_to_allocate_resource")
    vm["power_state"] = "POWERED_ON"
    vm["clean_power_off"] = False
    return 204, None


HANDLERS = {
    "Cis.Session_create": op_session_create,
    "Cis.Session_delete": op_session_delete,
    "Vcenter.Vm.Power_get": op_power_get,
    "Vcenter.Vm.Hardware.Memory_update": op_memory_update,
    "Vcenter.Vm.Hardware.Disk_create": op_disk_create,
    "Vcenter.Vm.Hardware.Ethernet_create": op_ethernet_create,
    "Vcenter.Vm.Power_start": op_power_start,
}


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "vcenter-mock/1.0"
    sys_version = ""

    def log_message(self, fmt, *args):  # keep stderr quiet
        pass

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def do_PATCH(self):
        self._serve("PATCH")

    def do_PUT(self):
        self._serve("PUT")

    def do_DELETE(self):
        self._serve("DELETE")

    # -- plumbing ---------------------------------------------------------

    def _serve(self, method):
        parsed = urlparse(self.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        headers = {k.lower(): v for k, v in self.headers.items()}
        length = int(headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        record = {
            "method": method,
            "target": self.path,
            "path": parsed.path,
            "query": query,
            "headers": headers,
            "body_raw": raw.decode("utf-8", "replace") if raw else None,
            "body": None,
            "operation_id": None,
        }
        try:
            status, payload = self._dispatch(method, parsed.path, query, headers, raw, record)
        except ApiError as err:
            status, payload = err.status, err.payload
        record["status"] = status
        self.server.write_log(record)
        self._respond(status, payload)

    def _dispatch(self, method, path, query, headers, raw, record):
        contract = self.server.contract
        inventory = self.server.inventory
        operation, params = contract.match(method, path, query)
        if operation is None:
            raise ApiError(404, "NOT_FOUND",
                           "No operation is served at %s %s." % (method, self.path),
                           "com.vmware.vapi.std.errors.not_found")
        record["operation_id"] = operation["operationId"]

        if operation["security"] == "api_key_auth":
            token = headers.get("vmware-api-session-id")
            if not token or token not in inventory.sessions:
                raise ApiError(401, "UNAUTHENTICATED",
                               "The session id is missing or no longer valid.",
                               "com.vmware.vapi.std.errors.unauthenticated")
        else:
            token = None

        spec = operation.get("requestBody")
        body = None
        if raw:
            media = (headers.get("content-type") or "").split(";")[0].strip().lower()
            if media != "application/json":
                raise ApiError(400, "UNSUPPORTED",
                               "Only application/json request bodies are accepted.",
                               "com.vmware.vapi.std.errors.unsupported")
            if spec is None:
                raise ApiError(400, "INVALID_REQUEST",
                               "%s takes no request body." % operation["operationId"],
                               "com.vmware.vapi.std.errors.invalid_request")
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "INVALID_REQUEST", "The request body is not valid JSON.",
                               "com.vmware.vapi.std.errors.invalid_request")
            record["body"] = body
            validate(contract, body, spec["schema"].split("/")[-1], "request body")
        elif spec is not None and spec.get("required"):
            raise ApiError(400, "INVALID_ARGUMENT",
                           "%s requires a request body." % operation["operationId"],
                           "com.vmware.vapi.std.errors.invalid_argument")

        ctx = {
            "inventory": inventory,
            "headers": headers,
            "params": params,
            "body": body if body is not None else {},
            "token": token,
        }
        with inventory.lock:
            status, payload = HANDLERS[operation["operationId"]](ctx)
        if status != operation["successStatus"]:
            raise ApiError(500, "ERROR", "internal mock inconsistency")
        return status, payload

    def _respond(self, status, payload):
        if payload is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        blob = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, contract, inventory, log_path):
        super().__init__(address, Handler)
        self.contract = contract
        self.inventory = inventory
        self.log_path = log_path
        self._log_lock = threading.Lock()
        self._seq = 0

    def write_log(self, record):
        with self._log_lock:
            self._seq += 1
            record["seq"] = self._seq
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="docs/contract.json")
    parser.add_argument("--log", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--port-file")
    parser.add_argument("--host-free-memory-mib", type=int, default=6144)
    parser.add_argument("--disk-serial", type=int, default=2000)
    parser.add_argument("--nic-serial", type=int, default=4000)
    parser.add_argument("--power-state", choices=("POWERED_OFF", "POWERED_ON", "SUSPENDED"),
                        default="POWERED_OFF")
    args = parser.parse_args(argv)

    contract = Contract(args.contract)
    missing = sorted(set(IMPLEMENTED) - set(contract.by_operation))
    if missing:
        raise SystemExit("contract does not name: %s" % ", ".join(missing))

    open(args.log, "w", encoding="utf-8").close()
    inventory = Inventory(args.host_free_memory_mib, args.disk_serial, args.nic_serial,
                          args.power_state)
    server = Server(("127.0.0.1", args.port), contract, inventory, args.log)
    port = server.server_address[1]
    if args.port_file:
        tmp = args.port_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(str(port))
        os.replace(tmp, args.port_file)
    sys.stdout.write("PORT %d\n" % port)
    sys.stdout.flush()

    # shutdown() blocks until serve_forever() returns, so it cannot run in the
    # signal handler itself: that handler executes on the serving thread.
    signal.signal(signal.SIGTERM,
                  lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
