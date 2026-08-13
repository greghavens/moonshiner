#!/usr/bin/env python3
"""Loopback mock pinned to docs/contract.json for the VCF 9.1 SDDC LCM service.

It serves exactly the two operations the contract names -- getComponents and
getComponentNodes -- and nothing else. Every request is appended to a JSON Lines
request log so the acceptance test can assert the wire shape that was actually
put on the socket.

Contract violations (unknown route, wrong method, unknown query parameter,
empty query value, repeated query key, out-of-range pageSize) are answered with
a 4xx and flagged in the log with a "violation" field. The test fails if any
violation is recorded.

The listener binds 127.0.0.1 only. No VMware endpoint is contacted.
"""

import json
import os
import re
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- pinned request surface -------------------------------------------------

COMPONENTS_PATH = re.compile(r"^/v1/components$")
COMPONENT_NODES_PATH = re.compile(
    r"^/v1/components/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/nodes$"
)

ALLOWED_QUERY = {
    "getComponents": ("scope",),
    "getComponentNodes": ("pageNumber", "pageSize", "nodeTypes"),
}

SCOPES = ("FLEET", "INSTANCE")

# The specification documents "Page Size. Maximum allowed is 50." but declares no
# server default. The fixture picks 3 so that omitting pageSize still paginates.
MAX_PAGE_SIZE = 50
DEFAULT_PAGE_SIZE = 3

# --- fixture state ----------------------------------------------------------

FLEET_OPS_ID = "11111111-1111-4111-8111-111111111111"
INSTANCE_VC_ID = "22222222-2222-4222-8222-222222222222"
INSTANCE_OPS_ID = "33333333-3333-4333-8333-333333333333"
BAD_METADATA_ID = "44444444-4444-4444-8444-444444444444"
LATE_FAILURE_ID = "55555555-5555-4555-8555-555555555555"


# Stamped into every node so the acceptance test can prove the emitted objects
# came out of the responses rather than out of a hard-coded table.
RUN_ID = os.environ.get("VCF_FIXTURE_RUN_ID", "0")
NODE_VERSION = "9.1.0.0-%s" % RUN_ID


def _node(node_id, name, node_type, ip_last):
    return {
        "id": node_id,
        "name": name,
        "nodeType": node_type,
        "version": NODE_VERSION,
        "fqdn": "%s.vcf.sddc.lab" % name.lower(),
        "ipAddress": "10.20.30.%d" % ip_last,
        "status": "ACTIVE",
        "size": "medium",
    }


# Deliberately jumbled storage order: page-arrival order is not the required
# emission order, and neither a case-insensitive sort nor an id sort reproduces
# the required order.
FLEET_OPS_NODES = [
    _node("a3c1e2d4-0001-4a1b-9c2d-0000000000a3", "esx-04", "control-plane", 14),
    _node("b1d2e3f4-0002-4a1b-9c2d-0000000000b1", "ESX-02", "control-plane", 12),
    _node("c7e3f405-0003-4a1b-9c2d-0000000000c7", "worker", "worker", 23),
    _node("d2f40516-0004-4a1b-9c2d-0000000000d2", "esx-01", "control-plane", 11),
    _node("e9051627-0005-4a1b-9c2d-0000000000e9", "Esx-03", "control-plane", 13),
    _node("f4162738-0006-4a1b-9c2d-0000000000f4", "worker", "worker", 24),
    _node("0a273849-0007-4a1b-9c2d-0000000000a0", "vcf-node", "vsphere-supervisor", 31),
    _node("1b38495a-0008-4a1b-9c2d-0000000000b1", "Worker", "worker", 22),
]

INSTANCE_VC_NODES = [
    _node("cc000001-0009-4a1b-9c2d-0000000000c1", "vc-01", "vcenter", 41),
]

INSTANCE_OPS_NODES = [
    _node("dd000001-0010-4a1b-9c2d-0000000000d1", "ops-01", "control-plane", 51),
    _node("dd000002-0011-4a1b-9c2d-0000000000d2", "ops-02", "worker", 52),
]

NODES_BY_COMPONENT = {
    FLEET_OPS_ID: FLEET_OPS_NODES,
    INSTANCE_VC_ID: INSTANCE_VC_NODES,
    INSTANCE_OPS_ID: INSTANCE_OPS_NODES,
    # These direct-lookup-only fixture IDs exercise two failure responses that
    # the public contract requires clients to handle. They are intentionally
    # absent from COMPONENTS so they cannot affect component-type resolution.
    BAD_METADATA_ID: INSTANCE_VC_NODES,
    LATE_FAILURE_ID: INSTANCE_OPS_NODES,
}


def _component(component_id, component_type, scope, deployment_type, fqdn, nodes):
    return {
        "id": component_id,
        "componentType": component_type,
        "deploymentType": deployment_type,
        "version": "9.1.0.0",
        "size": "medium",
        "fqdn": fqdn,
        "scope": scope,
        # The Component schema carries an inline `nodes` array. The fixture
        # populates it with a truncated slice: a client that reads it instead of
        # calling getComponentNodes returns an incomplete collection.
        "nodes": nodes[:2],
    }


COMPONENTS = [
    _component(FLEET_OPS_ID, "VCF_OPERATIONS", "FLEET", "OVA",
               "ops-fleet.vcf.sddc.lab", FLEET_OPS_NODES),
    _component(INSTANCE_VC_ID, "VCENTER", "INSTANCE", "VSP",
               "vc-01.vcf.sddc.lab", INSTANCE_VC_NODES),
    _component(INSTANCE_OPS_ID, "VCF_OPERATIONS", "INSTANCE", "OVA",
               "ops-instance.vcf.sddc.lab", INSTANCE_OPS_NODES),
]


# --- request log ------------------------------------------------------------

_log_lock = threading.Lock()
_seq = [0]


def record(entry, log_path):
    with _log_lock:
        _seq[0] += 1
        entry["seq"] = _seq[0]
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


# --- helpers ----------------------------------------------------------------

def split_query(raw):
    """Split a raw query string without decoding, preserving duplicates."""
    if raw == "":
        return []
    return [part for part in raw.split("&")]


def parse_pairs(raw):
    pairs = []
    for part in split_query(raw):
        if "=" in part:
            key, value = part.split("=", 1)
        else:
            key, value = part, None
        pairs.append((key, value))
    return pairs


def error_body(code, message):
    return {
        "code": code,
        "message": {"id": code, "defaultMessage": message, "localizedMessage": message},
        "resolution": {
            "id": code + ".resolution",
            "defaultMessage": "Correct the request and retry.",
            "localizedMessage": "Correct the request and retry.",
        },
        "referenceId": "fixture-0000-0000",
        "timestamp": "2026-05-13T00:00:00.000Z",
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SddcLcmContractMock/1.0"

    log_path = None
    expected_authorization = None

    def log_message(self, *args):  # silence stderr access logging
        pass

    # -- plumbing ------------------------------------------------------------

    def _respond(self, status, payload, operation_id, violation=None):
        raw = self.path
        path, _, query = raw.partition("?")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        entry = {
            "method": self.command,
            "target": raw,
            "path": path,
            "rawQuery": query,
            "queryPairs": [list(pair) for pair in parse_pairs(query)],
            "operationId": operation_id,
            "status": status,
            "requestBodyBytes": len(body),
            "headers": {
                "accept": self.headers.get("Accept"),
                "authorization": self.headers.get("Authorization"),
                "contentType": self.headers.get("Content-Type"),
                "contentLength": self.headers.get("Content-Length"),
            },
            "clientAddress": self.client_address[0],
        }
        if violation:
            entry["violation"] = violation
        record(entry, self.log_path)

        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _bad_request(self, operation_id, violation, message):
        self._respond(400, error_body("SDDC_LCM_BAD_REQUEST", message),
                      operation_id, violation)

    # -- routing -------------------------------------------------------------

    def _route(self):
        path = self.path.partition("?")[0]
        if COMPONENTS_PATH.match(path):
            return "getComponents", None
        match = COMPONENT_NODES_PATH.match(path)
        if match:
            return "getComponentNodes", match.group(1)
        return None, None

    def _unsupported(self):
        operation_id, _ = self._route()
        if operation_id is None:
            self._respond(404, error_body("SDDC_LCM_NOT_FOUND",
                                          "The specified resource was not found"),
                          None, "unknown-route")
        else:
            self._respond(405, error_body("SDDC_LCM_METHOD_NOT_ALLOWED",
                                          "Method not allowed"),
                          operation_id, "method-not-allowed")

    do_POST = _unsupported
    do_PUT = _unsupported
    do_PATCH = _unsupported
    do_DELETE = _unsupported
    do_HEAD = _unsupported
    do_OPTIONS = _unsupported

    def do_GET(self):
        operation_id, component_id = self._route()
        if operation_id is None:
            self._respond(404, error_body("SDDC_LCM_NOT_FOUND",
                                          "The specified resource was not found"),
                          None, "unknown-route")
            return

        if self.headers.get("Authorization") != self.expected_authorization:
            self._respond(401, error_body("SDDC_LCM_UNAUTHORIZED", "Unauthorized"),
                          operation_id)
            return

        raw_query = self.path.partition("?")[2]
        pairs = parse_pairs(raw_query)
        allowed = ALLOWED_QUERY[operation_id]

        seen = set()
        values = {}
        for key, value in pairs:
            if key not in allowed:
                self._bad_request(
                    operation_id, "unknown-query-parameter",
                    "Unknown query parameter '%s' for %s" % (key, operation_id))
                return
            if value is None or value == "":
                self._bad_request(
                    operation_id, "empty-query-parameter",
                    "Query parameter '%s' was sent with no value; unset optional "
                    "parameters must be omitted" % key)
                return
            if key in seen:
                self._bad_request(operation_id, "repeated-query-parameter",
                                  "Query parameter '%s' was sent twice" % key)
                return
            seen.add(key)
            values[key] = value

        if operation_id == "getComponents":
            self._get_components(values)
        else:
            self._get_component_nodes(component_id, values)

    # -- operations ----------------------------------------------------------

    def _get_components(self, values):
        scope = values.get("scope")
        if scope is not None and scope not in SCOPES:
            self._bad_request("getComponents", "invalid-enum-value",
                              "scope must be one of %s" % (SCOPES,))
            return
        selected = [c for c in COMPONENTS if scope is None or c["scope"] == scope]
        self._respond(200, {"components": selected}, "getComponents")

    def _get_component_nodes(self, component_id, values):
        page_number = 0
        if "pageNumber" in values:
            raw = values["pageNumber"]
            if not raw.isdigit():
                self._bad_request("getComponentNodes", "invalid-integer",
                                  "pageNumber must be a non-negative integer")
                return
            page_number = int(raw)

        page_size = DEFAULT_PAGE_SIZE
        if "pageSize" in values:
            raw = values["pageSize"]
            if not raw.isdigit():
                self._bad_request("getComponentNodes", "invalid-integer",
                                  "pageSize must be a positive integer")
                return
            page_size = int(raw)
            if page_size < 1 or page_size > MAX_PAGE_SIZE:
                self._bad_request("getComponentNodes", "page-size-out-of-range",
                                  "Page Size. Maximum allowed is 50.")
                return

        node_types = None
        if "nodeTypes" in values:
            raw = values["nodeTypes"]
            parts = raw.split(",")
            if any(part == "" for part in parts):
                self._bad_request("getComponentNodes", "empty-node-type",
                                  "nodeTypes must not contain empty elements")
                return
            node_types = parts

        if component_id not in NODES_BY_COMPONENT:
            self._respond(404, error_body("SDDC_LCM_COMPONENT_NOT_FOUND",
                                          "Component %s was not found" % component_id),
                          "getComponentNodes")
            return

        # A page can fail after an earlier page succeeded. The client must
        # terminate without leaking the node collected from page zero.
        if component_id == LATE_FAILURE_ID and page_number == 1:
            self._respond(500, error_body("SDDC_LCM_INTERNAL_ERROR",
                                          "Injected failure on the second page"),
                          "getComponentNodes")
            return

        nodes = NODES_BY_COMPONENT[component_id]
        if node_types is not None:
            nodes = [n for n in nodes if n["nodeType"] in node_types]

        total = len(nodes)
        total_pages = (total + page_size - 1) // page_size
        start = page_number * page_size
        page = nodes[start:start + page_size]

        reported_page_number = page_number
        if component_id == BAD_METADATA_ID:
            reported_page_number += 1

        self._respond(200, {
            "nodes": page,
            "pageMetadata": {
                "pageNumber": reported_page_number,
                "pageSize": page_size,
                "totalElements": total,
                "totalPages": total_pages,
            },
        }, "getComponentNodes")


def main():
    if len(sys.argv) != 3:
        sys.stderr.write("usage: sddc_lcm_contract_mock.py <ready-file> <log-file>\n")
        return 2
    ready_file, log_path = sys.argv[1], sys.argv[2]

    token = os.environ.get("VCF_FIXTURE_SESSION_TOKEN")
    if not token:
        sys.stderr.write("VCF_FIXTURE_SESSION_TOKEN is not set\n")
        return 2
    if not os.environ.get("VCF_FIXTURE_RUN_ID"):
        sys.stderr.write("VCF_FIXTURE_RUN_ID is not set\n")
        return 2

    Handler.log_path = log_path
    Handler.expected_authorization = "Bearer " + token

    open(log_path, "w", encoding="utf-8").close()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = httpd.server_address[0], httpd.server_address[1]

    tmp = ready_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"host": host, "port": port,
                   "baseUri": "http://127.0.0.1:%d" % port}, handle)
    os.replace(tmp, ready_file)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
