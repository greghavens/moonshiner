#!/usr/bin/env python3
"""Contract-pinned loopback SDDC Manager used by the protected verifier.

The mock refuses to start unless docs/contract.json still names exactly the two
operationIds it implements, and it answers nothing beyond those operations plus the
SDK connection probe the contract declares under sdkConnectionHandshake.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ACCESS_TOKEN = "loopback-access-token"

EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getCredentials": ("GET", "/v1/credentials"),
}
HANDSHAKE = ("GET", "/v1/sddc-manager")

# Filter parameters getCredentials accepts, in the order the pinned specification
# declares them. The mock rejects any query key outside this set.
FILTER_PARAMETERS = ("resourceName", "resourceIp", "resourceType", "domainName", "accountType")
PAGE_PARAMETERS = ("pageNumber", "pageSize")


def _credential(
    identifier,
    credential_type,
    account_type,
    username,
    resource_id,
    resource_name,
    resource_type,
    domain_names,
):
    return {
        "id": identifier,
        "credentialType": credential_type,
        "accountType": account_type,
        "username": username,
        "password": "fixture-secret-" + identifier[:8],
        "creationTimestamp": "2026-01-14T08:12:44.000Z",
        "modificationTimestamp": "2026-05-02T17:39:06.000Z",
        "expiry": {
            "expiryDate": "2026-11-02T17:39:06.000Z",
            "lastCheckedDate": "2026-08-01T02:00:00.000Z",
            "connectivityStatus": "ACTIVE",
            "status": "ACTIVE",
        },
        "resource": {
            "resourceId": resource_id,
            "resourceName": resource_name,
            "resourceType": resource_type,
            "domainNames": list(domain_names),
        },
    }


ESX01 = "d1b0f4a6-5c27-4f9e-9a30-6c1e4b7d2a51"
ESX02 = "6f83c0d2-11ae-4b65-8d47-2e90a5c3f118"
NSX = "b47e2c19-8d05-4a3f-91c6-0f7d5e8a4b23"
VC_MGMT = "38a6e5d0-4c72-4b19-8f53-9d2a1c6b0e47"
VC_WLD = "9c05a71e-2f48-4d6b-b83a-71e4c0d95f62"
VC_CASE = "4a7d3910-82ce-46b5-93f1-d0e8c2a6754b"

# Server-side storage order. It is deliberately neither the required output order nor
# id order, so page boundaries cut across the required ordering and every page must be
# collected before the result can be ordered.
CREDENTIALS = [
    _credential(
        "5d19f82b-6c04-4e71-93a8-2f7b0d1c5e36",
        "SSO",
        "USER",
        "administrator@vsphere.local",
        VC_MGMT,
        "vcenter-mgmt.vrack.vsphere.local",
        "VCENTER",
        ["mgmt-domain"],
    ),
    _credential(
        "1f3b6d40-7a92-4c58-8e01-4b6d9f2a7c13",
        "SSH",
        "USER",
        "root",
        ESX01,
        "esx-01.vrack.vsphere.local",
        "ESXI",
        ["mgmt-domain"],
    ),
    _credential(
        "a258e3d9-0b46-4f12-a7d5-8c3e1b09f742",
        "API",
        "SERVICE",
        "Admin",
        NSX,
        "nsx-mgmt.vrack.vsphere.local",
        "NSXT_MANAGER",
        ["mgmt-domain"],
    ),
    _credential(
        "3b6e0d54-9f21-4a80-b6c7-5e2d8a413f09",
        "SSO",
        "USER",
        "administrator@vsphere.local",
        VC_WLD,
        "vcenter-wld01.vrack.vsphere.local",
        "VCENTER",
        ["wld01-domain"],
    ),
    _credential(
        "7c2a9f18-3d65-4b07-92ef-1a4c6d8b5023",
        "API",
        "SYSTEM",
        "vcf-admin",
        ESX01,
        "esx-01.vrack.vsphere.local",
        "ESXI",
        ["mgmt-domain"],
    ),
    _credential(
        "B0c34b7f-5a18-4d92-8b60-3f7e2c1a9d84",
        "API",
        "USER",
        "Admin",
        NSX,
        "nsx-mgmt.vrack.vsphere.local",
        "NSXT_MANAGER",
        ["mgmt-domain"],
    ),
    _credential(
        "42d81b6e-0c37-4f45-a91d-8b25e6c07a3f",
        "API",
        "SYSTEM",
        "vcf-admin",
        ESX02,
        "esx-02.vrack.vsphere.local",
        "ESXI",
        ["mgmt-domain"],
    ),
    _credential(
        "9e05c7a2-4b83-4106-97fd-2c6a0e5b1d48",
        "SSH",
        "SYSTEM",
        "svc-esx01",
        ESX01,
        "esx-01.vrack.vsphere.local",
        "ESXI",
        ["mgmt-domain"],
    ),
    _credential(
        "b6741a0c-8e52-4937-b1a4-0d3f9c26e785",
        "SSH",
        "USER",
        "root",
        ESX02,
        "esx-02.vrack.vsphere.local",
        "ESXI",
        ["mgmt-domain"],
    ),
    _credential(
        "10c34b7f-5a18-4d92-8b60-3f7e2c1a9d84",
        "API",
        "USER",
        "admin",
        NSX,
        "nsx-mgmt.vrack.vsphere.local",
        "NSXT_MANAGER",
        ["mgmt-domain"],
    ),
    _credential(
        "61e45c0a-7f32-4db9-a186-29c503e7b814",
        "SSO",
        "USER",
        "administrator@vsphere.local",
        VC_CASE,
        "Vcenter-zz.vrack.vsphere.local",
        "VCENTER",
        ["case-domain"],
    ),
]


def matches(credential, filters):
    resource = credential["resource"]
    if "resourceName" in filters and resource["resourceName"] != filters["resourceName"]:
        return False
    if "resourceType" in filters and resource["resourceType"] != filters["resourceType"]:
        return False
    if "domainName" in filters and filters["domainName"] not in resource["domainNames"]:
        return False
    if "accountType" in filters and credential["accountType"] != filters["accountType"]:
        return False
    if "resourceIp" in filters:
        return False
    return True


class State:
    def __init__(self, contract_path: Path, log_path: Path) -> None:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        actual = {
            operation_id: (definition["method"], definition["path"])
            for operation_id, definition in contract["operations"].items()
        }
        if actual != EXPECTED_OPERATIONS:
            raise ValueError("docs/contract.json does not name the pinned operations")
        if contract["operationIds"] != list(EXPECTED_OPERATIONS):
            raise ValueError("docs/contract.json operationId list changed")
        handshake = contract["sdkConnectionHandshake"]
        if (handshake["method"], handshake["path"]) != HANDSHAKE:
            raise ValueError("docs/contract.json declares a different SDK handshake route")
        declared = [row["name"] for row in contract["operations"]["getCredentials"]["queryParameters"]]
        if declared != list(FILTER_PARAMETERS[:4]) + list(PAGE_PARAMETERS) + [FILTER_PARAMETERS[4]]:
            raise ValueError("getCredentials query parameters drifted from the contract")
        self.log_path = log_path
        self.lock = threading.Lock()

    def append_request(self, record) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Handler(BaseHTTPRequestHandler):
    server_version = "VcfSddcManagerContractMock/9.0"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format, *_args):
        return

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _record(self, body: bytes):
        split = urlsplit(self.path)
        self.state.append_request(
            {
                "method": self.command,
                "path": split.path,
                "query": split.query,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body": body.decode("utf-8"),
            }
        )
        return split.path, split.query

    def _json(self, status: int, payload) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"errorCode": code, "message": message})

    def _off_contract(self) -> None:
        self._error(
            404,
            "VCF_ROUTE_NOT_IN_CONTRACT",
            "No operationId in docs/contract.json matches this request",
        )

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        path, query = self._record(body)
        if path != "/v1/tokens" or query:
            self._off_contract()
            return
        self._json(
            201,
            {
                "accessToken": ACCESS_TOKEN,
                "refreshToken": {"id": "loopback-refresh-token"},
            },
        )

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        body = self._read_body()
        path, query = self._record(body)

        if path == HANDSHAKE[1]:
            if query:
                self._off_contract()
                return
            self._json(
                200,
                {
                    "id": "0f1c4d7a-6b23-4e58-9a01-7d5e3c2b8f14",
                    "fqdn": "sddc-manager.vrack.vsphere.local",
                    "version": "9.0.0.0",
                },
            )
            return

        if path != "/v1/credentials":
            self._off_contract()
            return

        pairs = parse_qsl(query, keep_blank_values=True)
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            self._error(400, "VCF_DUPLICATE_QUERY_PARAMETER", "Repeated query parameter")
            return
        parameters = dict(pairs)
        unknown = sorted(set(keys) - set(FILTER_PARAMETERS) - set(PAGE_PARAMETERS))
        if unknown:
            self._error(
                400,
                "VCF_UNKNOWN_QUERY_PARAMETER",
                "Query parameters not declared by getCredentials: " + ", ".join(unknown),
            )
            return

        for name in PAGE_PARAMETERS:
            if name not in parameters:
                self._error(
                    400,
                    "VCF_MISSING_PAGE_PARAMETER",
                    f"{name} must be sent explicitly by this client",
                )
                return
            if not parameters[name].isdigit():
                self._error(
                    400,
                    "VCF_INVALID_PAGE_PARAMETER",
                    f"{name} must be a non-negative integer, received {parameters[name]!r}",
                )
                return

        page_number = int(parameters["pageNumber"])
        page_size = int(parameters["pageSize"])
        if page_size < 1:
            self._error(
                400,
                "VCF_INVALID_PAGE_SIZE",
                "This deployment requires an explicit positive pageSize",
            )
            return

        # An empty filter value narrows nothing, exactly as the appliance behaves. The
        # difference between omitting an optional filter and sending it empty is
        # therefore invisible in the response and only observable in the request log,
        # which is what tests/verify.py inspects.
        filters = {
            name: parameters[name]
            for name in FILTER_PARAMETERS
            if parameters.get(name, "") != ""
        }

        selected = [row for row in CREDENTIALS if matches(row, filters)]
        total_elements = len(selected)
        if not filters and page_size == 4 and total_elements == 11:
            # This appliance returns a short first page but still advertises two later
            # pages. A client that mistakes response pageMetadata.pageSize for the
            # requested capacity will stop early; totalPages remains authoritative.
            page_ranges = ((0, 3), (3, 7), (7, 11))
            total_pages = len(page_ranges)
            if page_number < total_pages:
                start, end = page_ranges[page_number]
                elements = selected[start:end]
            else:
                elements = []
        else:
            total_pages = (total_elements + page_size - 1) // page_size
            start = page_number * page_size
            elements = selected[start : start + page_size]
        self._json(
            200,
            {
                "elements": elements,
                "pageMetadata": {
                    "pageNumber": page_number,
                    "pageSize": len(elements),
                    "totalElements": total_elements,
                    "totalPages": total_pages,
                },
            },
        )

    def do_PUT(self):  # noqa: N802
        self._record(self._read_body())
        self._off_contract()

    def do_PATCH(self):  # noqa: N802
        self._record(self._read_body())
        self._off_contract()

    def do_DELETE(self):  # noqa: N802
        self._record(self._read_body())
        self._off_contract()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--port-file", type=Path, required=True)
    args = parser.parse_args()

    args.log.write_text("", encoding="utf-8")
    state = State(args.contract, args.log)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.state = state  # type: ignore[attr-defined]
    args.port_file.write_text(str(server.server_port), encoding="ascii")
    server.serve_forever(poll_interval=0.05)


if __name__ == "__main__":
    main()
