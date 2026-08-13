#!/usr/bin/env python3
"""Loopback mock of the three VCF Operations 9.1 operations named in docs/contract.json.

Serves nothing else: every other path or method is answered 404 and recorded in the
request log as an unknown operation.  Nothing here talks to a real VMware endpoint.

The mock is deliberately strict about the things the contract fixes (base path, method,
content type, query parameter names, alert-query property names) and deliberately
tolerant about the things the verifier is responsible for judging (whether an unset
optional field was omitted or sent as null/""/[]).  A real server accepts a body full
of nulls; the point of the exercise is not to send one.

Token expiry is driven by a call budget rather than the clock so that runs are
reproducible.  The advertised `validity` is far in the future, so a client cannot
learn that its token died by looking at the acquire response -- only by handling 401.

Usage:
    python3 tests/mock_vcfops.py --log LOG.jsonl --port-file PORT --expire-after 1,3
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BASE_PATH = "/suite-api"

# Advertised token lifetime: 2030-01-01T00:00:00Z.  Fixed, so runs are deterministic.
VALIDITY_MILLIS = 1893456000000
EXPIRES_AT = "Tuesday, January 1, 2030 12:00:00 AM UTC"

# Property names of the `alert-query` schema at commit c3f3b52c.  A body carrying any
# other key is rejected: the client is expected to speak the contract, not invent it.
ALERT_QUERY_PROPERTIES = {
    "activeOnly",
    "alertControlState",
    "alertCriticality",
    "alertDefinitionId",
    "alertId",
    "alertImpact",
    "alertName",
    "alertStatus",
    "alertTypeSubtype",
    "cancelTimeRange",
    "compositeOperator",
    "extractOwnerName",
    "groupId",
    "groupingCondition",
    "includeChildrenResources",
    "resource-query",
    "resourceKind",
    "startTimeRange",
    "updateTimeRange",
    "userId",
    "userName",
}

QUERY_ALERT_PARAMS = {"page", "pageSize"}

USERNAME = "svc-ops"
PASSWORD = "0ps-Passw0rd!"
AUTH_SOURCE = "vIDM-Corp"

# Seven alerts.  `summary_only` fields are withheld from the paged query response and
# appear only on GET /api/alerts/{id}, which is why the sweep fetches details at all.
ALERTS = [
    {
        "alertId": "31eeaeec-82d5-4037-a59b-efed2e7c8e3a",
        "resourceId": "c40271ed-5a59-4d8a-b98c-1aa3aa603a3f",
        "alertLevel": "CRITICAL",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Capacity",
        "resourceKind": "VirtualMachine",
        "alertDefinitionName": "Virtual machine has memory contention",
        "startTimeUTC": 1753368185,
        "updateTimeUTC": 1753378185,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-VirtualMachine-memory-contention",
            "alertImpact": "RISK",
            "ownerId": "5c0a0c5e-1f1a-4d6c-9a7a-2d1b0e6f4a11",
            "ownerName": "ops-oncall",
            "statKey": "mem|host_contentionPct",
        },
    },
    {
        "alertId": "6b3d5f21-9c4e-4b83-8f0a-1d2e3c4b5a60",
        "resourceId": "9a1c2d3e-4f50-4617-8b29-0c7d6e5f4a31",
        "alertLevel": "WARNING",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Performance",
        "resourceKind": "HostSystem",
        "alertDefinitionName": "Host is experiencing high CPU ready time",
        "startTimeUTC": 1753368400,
        "updateTimeUTC": 1753379400,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-HostSystem-cpu-ready",
            "alertImpact": "HEALTH",
            "ownerId": "",
            "statKey": "cpu|readyPct",
        },
    },
    {
        "alertId": "b7c8d9e0-1234-4a5b-9c6d-7e8f90a1b2c3",
        "resourceId": "0f1e2d3c-4b5a-4968-8778-695a4b3c2d1e",
        "alertLevel": "IMMEDIATE",
        "status": "ACTIVE",
        "controlState": "ASSIGNED",
        "type": "Storage",
        "subType": "Capacity",
        "resourceKind": "Datastore",
        "alertDefinitionName": "Datastore is running out of disk space",
        "startTimeUTC": 1753369000,
        "updateTimeUTC": 1753380000,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-Datastore-diskspace",
            "alertImpact": "RISK",
            "ownerId": "77d1b6a4-3e2f-4c1d-8a9b-0e5f6a7b8c90",
            "ownerName": "storage-team",
            "statKey": "capacity|used_space",
        },
    },
    {
        "alertId": "c2d3e4f5-6a7b-4c8d-9e0f-1a2b3c4d5e6f",
        "resourceId": "1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9",
        "alertLevel": "CRITICAL",
        "status": "UPDATED",
        "controlState": "OPEN",
        "type": "Virtualization/Hypervisor",
        "subType": "Availability",
        "resourceKind": "ClusterComputeResource",
        "alertDefinitionName": "Cluster has insufficient failover resources",
        "startTimeUTC": 1753369600,
        "updateTimeUTC": 1753381600,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-Cluster-ha-failover",
            "alertImpact": "RISK",
            "ownerId": "",
            "statKey": "summary|ha|failover_resources_violation",
        },
    },
    {
        "alertId": "d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f70",
        "resourceId": "2c3d4e5f-6071-4829-83a4-b5c6d7e8f901",
        "alertLevel": "INFORMATION",
        "status": "ACTIVE",
        "controlState": "OPEN",
        "type": "Application",
        "subType": "Configuration",
        "resourceKind": "VirtualMachine",
        "alertDefinitionName": "Virtual machine has an outdated VMware Tools build",
        "startTimeUTC": 1753370200,
        "updateTimeUTC": 1753382200,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-VirtualMachine-tools-outdated",
            "alertImpact": "HEALTH",
            "ownerId": "",
            "statKey": "config|tools|version_status",
        },
    },
    {
        "alertId": "e6f7a8b9-c0d1-4e2f-8a3b-4c5d6e7f8091",
        "resourceId": "3d4e5f60-7182-493a-84b5-c6d7e8f90123",
        "alertLevel": "IMMEDIATE",
        "status": "NEW",
        "controlState": "OPEN",
        "type": "Network",
        "subType": "Performance",
        "resourceKind": "DistributedVirtualSwitch",
        "alertDefinitionName": "Uplink is dropping packets",
        "startTimeUTC": 1753370800,
        "updateTimeUTC": 1753382800,
        "cancelTimeUTC": 0,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-DVS-uplink-drops",
            "alertImpact": "HEALTH",
            "ownerId": "",
            "statKey": "network|droppedPct",
        },
    },
    {
        "alertId": "f8a9b0c1-d2e3-4f40-8516-273849a0b1c2",
        "resourceId": "4e5f6071-8293-4a4b-85c6-d7e8f9012345",
        "alertLevel": "CRITICAL",
        "status": "CANCELED",
        "controlState": "OPEN",
        "type": "Storage",
        "subType": "Availability",
        "resourceKind": "Datastore",
        "alertDefinitionName": "Datastore is unreachable from one or more hosts",
        "startTimeUTC": 1753371400,
        "updateTimeUTC": 1753383400,
        "cancelTimeUTC": 1753384000,
        "suspendUntilTimeUTC": 0,
        "detail": {
            "alertDefinitionId": "AlertDefinition-Datastore-unreachable",
            "alertImpact": "HEALTH",
            "ownerId": "",
            "statKey": "summary|accessible",
        },
    },
]

SUMMARY_FIELDS = (
    "alertId",
    "resourceId",
    "alertLevel",
    "status",
    "controlState",
    "type",
    "subType",
    "alertDefinitionName",
    "startTimeUTC",
    "updateTimeUTC",
    "cancelTimeUTC",
    "suspendUntilTimeUTC",
)


def summary_of(alert):
    out = {k: alert[k] for k in SUMMARY_FIELDS}
    out["links"] = [
        {
            "href": "/suite-api/api/alerts/" + alert["alertId"],
            "rel": "SELF",
            "name": "details",
        }
    ]
    return out


def detail_of(alert):
    out = {k: alert[k] for k in SUMMARY_FIELDS}
    out.update(alert["detail"])
    out["links"] = [
        {
            "href": "/suite-api/api/alerts/" + alert["alertId"],
            "rel": "SELF",
            "name": "details",
        }
    ]
    return out


def contains(haystack, needle):
    return needle.lower() in (haystack or "").lower()


def matches(alert, query):
    """Apply the subset of alert-query filters this mock honours."""
    if query.get("activeOnly") is True:
        if alert["status"] == "CANCELED" or alert["controlState"] == "SUSPENDED":
            return False
    crit = query.get("alertCriticality")
    if isinstance(crit, list) and crit and alert["alertLevel"] not in crit:
        return False
    status = query.get("alertStatus")
    if isinstance(status, list) and status and alert["status"] not in status:
        return False
    name = query.get("alertName")
    if isinstance(name, str) and name and not contains(alert["alertDefinitionName"], name):
        return False
    kind = query.get("resourceKind")
    if isinstance(kind, str) and kind and not contains(alert["resourceKind"], kind):
        return False
    return True


class State:
    def __init__(self, log_path, budgets):
        self.lock = threading.Lock()
        self.log_path = log_path
        self.budgets = budgets
        self.issued = 0
        self.remaining = {}
        self.seq = 0

    def issue_token(self):
        self.issued += 1
        token = "ops-token-%d" % self.issued
        idx = self.issued - 1
        budget = self.budgets[idx] if idx < len(self.budgets) else 1_000_000
        self.remaining[token] = budget
        return token, budget

    def record(self, entry):
        self.seq += 1
        entry["seq"] = self.seq
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockVcfOperations/9.1"

    def log_message(self, fmt, *args):  # silence stderr chatter
        pass

    # -- plumbing ---------------------------------------------------------

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return ""
        return self.rfile.read(length).decode("utf-8", "replace")

    def _respond(self, status, payload, entry):
        entry["status"] = status
        self.state.record(entry)
        blob = json.dumps(payload).encode("utf-8") if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        if blob:
            self.wfile.write(blob)

    def _entry(self, method, raw_body):
        split = urlsplit(self.path)
        try:
            parsed = json.loads(raw_body) if raw_body else None
        except ValueError:
            parsed = None
        return {
            "method": method,
            "path": split.path,
            "raw_query": split.query,
            "query": {k: v for k, v in parse_qs(split.query, keep_blank_values=True).items()},
            "authorization": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "accept": self.headers.get("Accept"),
            "raw_body": raw_body,
            "body": parsed,
            "operationId": None,
        }

    def _authorize(self, entry):
        """Return (token, error_response_sent)."""
        header = entry["authorization"]
        if not header or not header.startswith("OpsToken "):
            self._respond(401, {"message": "missing or malformed Authorization header"}, entry)
            return None, True
        token = header[len("OpsToken "):].strip()
        with self.state.lock:
            remaining = self.state.remaining.get(token)
            if remaining is None:
                self._respond(401, {"message": "unknown token"}, entry)
                return None, True
            if remaining <= 0:
                entry["token_expired"] = True
                self._respond(401, {"message": "token has expired"}, entry)
                return None, True
            self.state.remaining[token] = remaining - 1
        entry["token"] = token
        return token, False

    # -- routing ----------------------------------------------------------

    def do_POST(self):
        raw = self._read_body()
        entry = self._entry("POST", raw)
        path = entry["path"]
        if path == BASE_PATH + "/api/auth/token/acquire":
            return self.op_acquire_token(entry)
        if path == BASE_PATH + "/api/alerts/query":
            return self.op_query_alert(entry)
        return self.unknown(entry)

    def do_GET(self):
        entry = self._entry("GET", "")
        path = entry["path"]
        prefix = BASE_PATH + "/api/alerts/"
        if path.startswith(prefix) and "/" not in path[len(prefix):] and path[len(prefix):]:
            return self.op_get_alert(entry, path[len(prefix):])
        return self.unknown(entry)

    def do_PUT(self):
        self._read_body()
        return self.unknown(self._entry("PUT", ""))

    def do_DELETE(self):
        return self.unknown(self._entry("DELETE", ""))

    def do_PATCH(self):
        self._read_body()
        return self.unknown(self._entry("PATCH", ""))

    def unknown(self, entry):
        entry["unknown_operation"] = True
        self._respond(404, {"message": "no such operation in this contract"}, entry)

    # -- operations -------------------------------------------------------

    def op_acquire_token(self, entry):
        entry["operationId"] = "acquireToken"
        if (entry["content_type"] or "").split(";")[0].strip() != "application/json":
            return self._respond(415, {"message": "expected application/json"}, entry)
        if entry["raw_query"]:
            return self._respond(400, {"message": "acquireToken takes no query parameters"}, entry)
        body = entry["body"]
        if not isinstance(body, dict):
            return self._respond(400, {"message": "body must be a JSON object"}, entry)
        extra = set(body) - {"username", "password", "authSource"}
        if extra:
            return self._respond(
                400, {"message": "unknown username-password properties: %s" % sorted(extra)}, entry
            )
        if body.get("username") != USERNAME or body.get("password") != PASSWORD:
            return self._respond(401, {"message": "authentication failed"}, entry)
        if "authSource" in body and body["authSource"] not in (AUTH_SOURCE, "", None):
            return self._respond(401, {"message": "unknown auth source"}, entry)
        with self.state.lock:
            token, budget = self.state.issue_token()
        entry["issued_token"] = token
        entry["issued_call_budget"] = budget
        return self._respond(
            200,
            {
                "token": token,
                "validity": VALIDITY_MILLIS,
                "expiresAt": EXPIRES_AT,
                "roles": ["ContentAdmin", "ReadOnly"],
            },
            entry,
        )

    def op_query_alert(self, entry):
        entry["operationId"] = "queryAlert"
        token, failed = self._authorize(entry)
        if failed:
            return
        if (entry["content_type"] or "").split(";")[0].strip() != "application/json":
            return self._respond(415, {"message": "expected application/json"}, entry)
        unknown_params = set(entry["query"]) - QUERY_ALERT_PARAMS
        if unknown_params:
            return self._respond(
                400, {"message": "unknown query parameters: %s" % sorted(unknown_params)}, entry
            )
        try:
            page = int(entry["query"].get("page", ["0"])[0])
            page_size = int(entry["query"].get("pageSize", ["1000"])[0])
        except ValueError:
            return self._respond(400, {"message": "page and pageSize must be integers"}, entry)
        if page < 0 or page_size < 1:
            return self._respond(400, {"message": "page must be >= 0 and pageSize >= 1"}, entry)
        body = entry["body"]
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return self._respond(400, {"message": "body must be a JSON object"}, entry)
        extra = set(body) - ALERT_QUERY_PROPERTIES
        if extra:
            return self._respond(
                400, {"message": "unknown alert-query properties: %s" % sorted(extra)}, entry
            )
        selected = [a for a in ALERTS if matches(a, body)]
        start = page * page_size
        window = selected[start:start + page_size]
        entry["returned_alert_ids"] = [a["alertId"] for a in window]
        return self._respond(
            200,
            {
                "alerts": [summary_of(a) for a in window],
                "pageInfo": {
                    "page": page,
                    "pageSize": page_size,
                    "totalCount": len(selected),
                },
                "links": [
                    {
                        "href": "/suite-api/api/alerts/query?page=%d&pageSize=%d" % (page, page_size),
                        "rel": "SELF",
                        "name": "current",
                    }
                ],
            },
            entry,
        )

    def op_get_alert(self, entry, alert_id):
        entry["operationId"] = "getAlert"
        entry["alert_id"] = alert_id
        token, failed = self._authorize(entry)
        if failed:
            return
        if entry["raw_query"]:
            return self._respond(400, {"message": "getAlert takes no query parameters"}, entry)
        for alert in ALERTS:
            if alert["alertId"] == alert_id:
                return self._respond(200, detail_of(alert), entry)
        return self._respond(404, {"message": "no alert with id %s" % alert_id}, entry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="path to the JSONL request log")
    ap.add_argument("--port-file", required=True, help="file to write the bound port into")
    ap.add_argument(
        "--expire-after",
        default="",
        help="comma separated authorized-call budget per issued token; later tokens are unlimited",
    )
    args = ap.parse_args()

    budgets = [int(x) for x in args.expire_after.split(",") if x.strip()]
    open(args.log, "w", encoding="utf-8").close()

    state = State(args.log, budgets)
    Handler.state = state
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    tmp = args.port_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(str(port))
    os.replace(tmp, args.port_file)
    sys.stderr.write("mock listening on 127.0.0.1:%d\n" % port)
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
