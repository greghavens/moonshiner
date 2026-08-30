#!/usr/bin/env python3
"""Loopback mock of the VCF Automation IaaS API, pinned to docs/contract.json.

Serves only the five operations the contract names. Every other method+path pair
is rejected as out of contract. Each request is appended to a JSON Lines log that
the verifier reads back.

All state transitions are driven by observation counts, never by wall-clock time,
so a run is reproducible regardless of how fast or slow the client polls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT_PATH = os.path.normpath(os.path.join(HERE, "..", "..", "docs", "contract.json"))

# --------------------------------------------------------------------------
# Contract pinning
# --------------------------------------------------------------------------

with open(CONTRACT_PATH, "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)

BASE_PATH = CONTRACT["base_path"]
PINNED_API_VERSION = CONTRACT["api_version"]["value"]

#: Templates the contract names, compiled to regexes. The mock refuses to serve
#: anything that is not in here, so it cannot drift away from docs/contract.json.
_PARAM = re.compile(r"\{[^}]+\}")


def _compile(path_template: str) -> re.Pattern:
    return re.compile("^" + _PARAM.sub(r"([^/]+)", re.escape(path_template).replace(r"\{", "{").replace(r"\}", "}")) + "$")


ROUTES = {}
for _op in CONTRACT["operations"]:
    ROUTES[(_op["method"], _op["path"])] = {
        "id": _op["id"],
        "regex": _compile(_op["path"]),
        "name": _op["name"],
        "query_parameters": {item["name"] for item in _op["query_parameters"]},
    }

EXCLUDED = set(CONTRACT["closed_operation_set"]["explicitly_excluded"])

FIXTURE_TOKEN = "eyJhbGciOiJSUzI1NiJ9.vcfa-fixture-access-token.sig"


# --------------------------------------------------------------------------
# Fixture state
# --------------------------------------------------------------------------

CLOUD_ACCOUNT_ID = "ca-9f41d7b0-5c2e-4a18-bd93-0e7c6a1f4a22"

INITIAL_PRIVATE_KEY_ID = "svc-vcfa-provisioning@vsphere.local"
INITIAL_PRIVATE_KEY = "OldSecret!Rotate-Me-2026Q2"


def _initial_state():
    return {
        "seq": 0,
        "account": {
            "id": CLOUD_ACCOUNT_ID,
            "name": "vcfa-vc-payments-prod",
            "description": "Payments platform production vCenter. Owned by platform-infra; do not rename.",
            "cloudAccountType": "vsphere",
            "cloudAccountProperties": {
                "hostName": "vc-payments-prod-01.corp.example.net",
                "dcId": "onprem",
                "acceptSelfSignedCertificate": "false",
                "certificate": "-----BEGIN CERTIFICATE-----MIIFakeFixtureCert-----END CERTIFICATE-----",
            },
            "customProperties": {
                "costCenter": "CC-40817",
                "changeWindow": "sat-0200-0400-utc",
            },
            "tags": [
                {"key": "tier", "value": "prod"},
                {"key": "owner", "value": "platform-infra"},
            ],
            "enabledRegions": [
                {
                    "id": "reg-4c1a",
                    "externalRegionId": "Datacenter:datacenter-3",
                    "name": "Frankfurt-DC1",
                    "cloudAccountId": CLOUD_ACCOUNT_ID,
                    "orgId": "org-9d2e11a4",
                    "owner": "platform-infra@corp.example.net",
                    "ownerType": "USER",
                    "createdAt": "2026-02-14T09:12:03.114Z",
                    "updatedAt": "2026-07-30T18:41:55.902Z",
                    "_links": {"self": {"href": "/iaas/api/regions/reg-4c1a"}},
                },
                {
                    "id": "reg-77be",
                    "externalRegionId": "Datacenter:datacenter-9",
                    "name": "Frankfurt-DC2",
                    "cloudAccountId": CLOUD_ACCOUNT_ID,
                    "orgId": "org-9d2e11a4",
                    "owner": "platform-infra@corp.example.net",
                    "ownerType": "USER",
                    "createdAt": "2026-02-14T09:12:03.114Z",
                    "updatedAt": "2026-07-30T18:41:55.902Z",
                    "_links": {"self": {"href": "/iaas/api/regions/reg-77be"}},
                },
            ],
            "healthy": True,
            "inMaintenanceMode": False,
            "orgId": "org-9d2e11a4",
            "owner": "platform-infra@corp.example.net",
            "ownerType": "USER",
            "createdAt": "2026-02-14T09:12:03.114Z",
            "updatedAt": "2026-07-30T18:41:55.902Z",
            "_links": {"self": {"href": "/iaas/api/cloud-accounts/%s" % CLOUD_ACCOUNT_ID}},
        },
        # Credentials are held outside the CloudAccount projection: the API never
        # echoes privateKey back, so the mock keeps them separate.
        "credentials": {
            "privateKeyId": INITIAL_PRIVATE_KEY_ID,
            "privateKey": INITIAL_PRIVATE_KEY,
        },
        "trackers": {
            # A provisioning request that was admitted under the OLD secret and is
            # still running. Rotating before this reaches a terminal status strands it.
            "req-a41c9e02": {
                "id": "req-a41c9e02",
                "name": "Provisioning: deploy vm-payments-etl-0148",
                "status": "INPROGRESS",
                "progress": 40,
                "message": "Reconfiguring virtual machine on Frankfurt-DC1.",
                "resources": ["/iaas/api/machines/m-0148"],
                "selfLink": "/iaas/api/request-tracker/req-a41c9e02",
                "_remaining_observations": 3,
                "_kind": "preexisting",
                "_terminal_status": "FAILED",
            },
            # Already terminal on arrival; must not be treated as blocking.
            "req-b77f1d55": {
                "id": "req-b77f1d55",
                "name": "Provisioning: resize vm-payments-api-0091",
                "status": "FINISHED",
                "progress": 100,
                "message": "Request completed.",
                "resources": ["/iaas/api/machines/m-0091"],
                "selfLink": "/iaas/api/request-tracker/req-b77f1d55",
                "_remaining_observations": 0,
                "_kind": "preexisting",
            },
        },
        "rotation_counter": 0,
        "healthcheck_counter": 0,
        "pending_credentials": {},
    }


STATE = _initial_state()
LOCK = threading.Lock()
LOG_PATH = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _observe(tracker: dict) -> dict:
    """Return the public projection of a tracker, advancing its lifecycle.

    Each observation burns one tick. When the last tick is burned the tracker
    moves to its terminal status and any side effect it owns is applied.
    """
    if tracker["status"] == "INPROGRESS":
        if tracker["_remaining_observations"] > 0:
            tracker["_remaining_observations"] -= 1
        if tracker["_remaining_observations"] <= 0:
            _resolve(tracker)
    return {
        k: v for k, v in tracker.items() if not k.startswith("_")
    }


def _resolve(tracker: dict) -> None:
    kind = tracker["_kind"]
    if kind == "rotation":
        pending = STATE["pending_credentials"].pop(tracker["id"], None)
        if pending:
            STATE["credentials"].update(pending)
        tracker["status"] = "FINISHED"
        tracker["progress"] = 100
        tracker["message"] = "Cloud account updated."
    elif kind == "healthcheck":
        rotation_pending = any(
            t["status"] == "INPROGRESS" and t["_kind"] == "rotation"
            for t in STATE["trackers"].values()
        )
        if rotation_pending:
            tracker["status"] = "FAILED"
            tracker["progress"] = 100
            tracker["message"] = (
                "Endpoint health check ran against a cloud account with an "
                "unfinished update in flight; credentials are indeterminate."
            )
        elif STATE["credentials"]["privateKey"] == INITIAL_PRIVATE_KEY:
            tracker["status"] = "FAILED"
            tracker["progress"] = 100
            tracker["message"] = (
                "Authentication to vc-payments-prod-01.corp.example.net failed: "
                "the stored secret was rejected by the provider."
            )
        else:
            tracker["status"] = "FINISHED"
            tracker["progress"] = 100
            tracker["message"] = "Endpoint health check succeeded."
    else:
        tracker["status"] = tracker.get("_terminal_status", "FINISHED")
        tracker["progress"] = 100
        tracker["message"] = (
            "Request failed before credential rotation."
            if tracker["status"] == "FAILED"
            else "Request completed."
        )


def _account_projection() -> dict:
    """CloudAccount as the API returns it. Never carries privateKey."""
    return json.loads(json.dumps(STATE["account"]))


def _error(status: int, message: str, message_id: str) -> dict:
    return {
        "message": message,
        "messageId": message_id,
        "statusCode": status,
        "documentKind": "com:vmware:provisioning:ServiceErrorResponse",
        "serverErrorId": "mock-%d" % STATE["seq"],
    }


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-iaas-mock/1.0"

    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    # -- plumbing ---------------------------------------------------------

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            return self.rfile.read(int(length))
        except (TypeError, ValueError):
            return b""

    def _respond(self, status: int, payload, record: dict) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        record["status_code"] = status
        record["response_json"] = payload
        self._write_log(record)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _write_log(self, record: dict) -> None:
        if not LOG_PATH:
            return
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # -- routing ----------------------------------------------------------

    def _match(self, method: str, path: str):
        for (m, template), route in ROUTES.items():
            if m != method:
                continue
            hit = route["regex"].match(path)
            if hit:
                return route, list(hit.groups())
        return None, []

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        raw_body = self._read_body()

        with LOCK:
            STATE["seq"] += 1
            record = {
                "seq": STATE["seq"],
                "method": method,
                "path": path,
                "raw_target": self.path,
                "query": query,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body_raw": raw_body.decode("utf-8", "replace"),
                "operation_id": None,
                "out_of_contract": False,
            }
            try:
                record["body_json"] = json.loads(raw_body) if raw_body else None
            except ValueError:
                record["body_json"] = None
                record["body_unparseable"] = True

            route, params = self._match(method, path)

            if route is None:
                record["out_of_contract"] = True
                probe = "%s %s" % (method, path)
                msg = (
                    "Operation is not part of docs/contract.json. "
                    "This mock serves only: "
                    + ", ".join(sorted("%s %s" % (m, p) for (m, p) in ROUTES))
                )
                if any(probe.startswith(e.split("{")[0].rstrip()) for e in EXCLUDED):
                    msg += " The contract explicitly excludes this operation."
                return self._respond(404, _error(404, msg, "vcfa.mock.out.of.contract"), record)

            record["operation_id"] = route["id"]

            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or auth[7:].strip() != FIXTURE_TOKEN:
                return self._respond(
                    403,
                    _error(403, "Missing or invalid bearer token.", "vcfa.auth.forbidden"),
                    record,
                )

            if query.get("apiVersion") != PINNED_API_VERSION:
                return self._respond(
                    400,
                    _error(
                        400,
                        "apiVersion query parameter must be %r for every operation in this contract; got %r."
                        % (PINNED_API_VERSION, query.get("apiVersion")),
                        "vcfa.apiversion.invalid",
                    ),
                    record,
                )

            unknown_query = sorted(set(query) - route["query_parameters"])
            if unknown_query:
                return self._respond(
                    400,
                    _error(
                        400,
                        "Query parameter(s) are not part of this operation's contract: %s."
                        % ", ".join(unknown_query),
                        "vcfa.query.out.of.contract",
                    ),
                    record,
                )

            return getattr(self, "_op_" + route["id"])(params, query, record)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    # -- operations -------------------------------------------------------

    def _op_getCloudAccount(self, params, query, record):
        if params[0] != CLOUD_ACCOUNT_ID:
            return self._respond(
                404, _error(404, "Cloud account %s not found." % params[0], "vcfa.ca.notfound"), record
            )
        return self._respond(200, _account_projection(), record)

    def _op_getRequestTrackers(self, params, query, record):
        content = [_observe(t) for t in STATE["trackers"].values()]
        return self._respond(
            200,
            {
                "content": content,
                "totalElements": len(content),
                "numberOfElements": len(content),
            },
            record,
        )

    def _op_getRequestTracker(self, params, query, record):
        tracker = STATE["trackers"].get(params[0])
        if tracker is None:
            return self._respond(
                404, _error(404, "Request %s not found." % params[0], "vcfa.req.notfound"), record
            )
        return self._respond(200, _observe(tracker), record)

    def _op_updateCloudAccountAsync(self, params, query, record):
        if params[0] != CLOUD_ACCOUNT_ID:
            return self._respond(
                404, _error(404, "Cloud account %s not found." % params[0], "vcfa.ca.notfound"), record
            )

        # Anything still INPROGRESS at this instant was admitted under the old
        # secret and is about to be stranded by this rotation. Recorded for the
        # verifier; the update is still processed so the failure is legible.
        record["stranded_in_flight"] = sorted(
            t["id"] for t in STATE["trackers"].values() if t["status"] == "INPROGRESS"
        )

        body = record.get("body_json")
        if not isinstance(body, dict):
            return self._respond(
                400,
                _error(400, "Request body must be a JSON object.", "vcfa.body.invalid"),
                record,
            )

        missing = [f for f in ("name", "cloudAccountProperties", "regions") if f not in body]
        if missing:
            return self._respond(
                400,
                _error(
                    400,
                    "UpdateCloudAccountSpecification is missing required field(s): %s."
                    % ", ".join(missing),
                    "vcfa.body.missing.required",
                ),
                record,
            )

        # Apply the non-credential fields the caller actually sent. PATCH replaces
        # what it is given, so a caller that sends regions:[] really does clear them.
        account = STATE["account"]
        account["name"] = body["name"]
        account["cloudAccountProperties"] = dict(body["cloudAccountProperties"])
        account["enabledRegions"] = [
            {
                "id": "reg-%s" % (r.get("externalRegionId", "")[-4:] or "0000"),
                "externalRegionId": r.get("externalRegionId"),
                "name": r.get("name"),
                "cloudAccountId": CLOUD_ACCOUNT_ID,
                "orgId": account["orgId"],
                "owner": account["owner"],
                "ownerType": account["ownerType"],
                "createdAt": account["createdAt"],
                "updatedAt": account["updatedAt"],
                "_links": {"self": {"href": "/iaas/api/regions/reg-x"}},
            }
            for r in (body.get("regions") or [])
            if isinstance(r, dict)
        ]
        for optional in ("description", "customProperties", "tags"):
            if optional in body:
                account[optional] = body[optional]

        STATE["rotation_counter"] += 1
        tid = "req-rot-%04d" % STATE["rotation_counter"]
        pending = {}
        if "privateKeyId" in body:
            pending["privateKeyId"] = body["privateKeyId"]
        if "privateKey" in body:
            pending["privateKey"] = body["privateKey"]
        STATE["pending_credentials"][tid] = pending

        tracker = {
            "id": tid,
            "name": "Update cloud account %s" % account["name"],
            "status": "INPROGRESS",
            "progress": 10,
            "message": "Applying cloud account update.",
            "resources": ["/iaas/api/cloud-accounts/%s" % CLOUD_ACCOUNT_ID],
            "selfLink": "/iaas/api/request-tracker/%s" % tid,
            "_remaining_observations": 2,
            "_kind": "rotation",
        }
        STATE["trackers"][tid] = tracker
        return self._respond(202, {k: v for k, v in tracker.items() if not k.startswith("_")}, record)

    def _op_runEndpointHealthCheck(self, params, query, record):
        if params[0] != CLOUD_ACCOUNT_ID:
            return self._respond(
                404, _error(404, "Cloud account %s not found." % params[0], "vcfa.ca.notfound"), record
            )
        STATE["healthcheck_counter"] += 1
        tid = "req-hc-%04d" % STATE["healthcheck_counter"]
        tracker = {
            "id": tid,
            "name": "Endpoint health check",
            "status": "INPROGRESS",
            "progress": 25,
            "message": "Contacting endpoint.",
            "resources": ["/iaas/api/cloud-accounts/%s" % CLOUD_ACCOUNT_ID],
            "selfLink": "/iaas/api/request-tracker/%s" % tid,
            "_remaining_observations": 1,
            "_kind": "healthcheck",
        }
        STATE["trackers"][tid] = tracker
        return self._respond(202, {k: v for k, v in tracker.items() if not k.startswith("_")}, record)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> int:
    global LOG_PATH

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="0 selects a free port")
    ap.add_argument("--port-file", required=True, help="the chosen port is written here once bound")
    ap.add_argument("--log", required=True, help="JSON Lines request log")
    args = ap.parse_args()

    LOG_PATH = os.path.abspath(args.log)
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8"):
        pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    port = httpd.server_address[1]

    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(port))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, args.port_file)

    sys.stderr.write("vcfa mock listening on http://%s:%d\n" % (args.host, port))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
