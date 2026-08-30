#!/usr/bin/env python3
"""Loopback mock of the seven VCF Operations 9.1 suite-api operations named in docs/contract.json.

Binds 127.0.0.1 only. Serves nothing outside the contract: any other method/path
pair answers 404. Every request is appended to <logdir>/requests.jsonl and the
identifiers it minted for this run are written to <logdir>/state.json.

  python3 mock/vcf_ops_mock.py --logdir .work --port 0 --portfile .work/port

Identifiers (resource id, alert ids, symptom ids, task ids, auth tokens) are
regenerated on every start, so a client can only produce them by reading them
back out of earlier responses.
"""

import argparse
import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = "/suite-api"
SCHEME = "vRealizeOpsToken"

# ---------------------------------------------------------------------------
# Fixture world: workload domain wld01, VCF Operations 9.1
# ---------------------------------------------------------------------------

T_ALERT_START = 1773186734000  # 2026-03-10T23:52:14Z
T_SYM1_START = 1773186734000  # 2026-03-10T23:52:14Z
T_SYM2_START = 1773196361000  # 2026-03-11T02:32:41Z
T_SYM3_START = 1773196385000  # 2026-03-11T02:33:05Z
T_NOISE_ALERT = 1773081663000  # 2026-03-09T18:41:03Z
T_TASK_CREATED = 1773186500000
T_TASK_UPDATED = 1773198622000  # 2026-03-11T03:10:22Z
T_TASK2_CREATED = 1773081663000
T_TASK2_UPDATED = 1773208800000  # 2026-03-11T06:00:00Z

CREDS = {"username": "svc-diag", "password": "R3d-Herring!2026"}
VALID_AUTH_SOURCES = {"local", "LOCAL"}


class World:
    """Per-run identifiers. Regenerated on every process start."""

    def __init__(self):
        u = lambda: str(uuid.uuid4())
        self.adapter_id = u()
        self.ds_id = u()
        self.host_id = u()
        self.alert_capacity = u()
        self.alert_noise = u()
        self.sym_used_pct = u()
        self.sym_freespace = u()
        self.sym_noise = u()
        self.task_notify = u()
        self.task_noise = u()
        self.tokens = {}  # token -> {"released": bool, "seq": int}
        self.token_seq = 0
        self.lock = threading.Lock()

    def mint_token(self):
        with self.lock:
            self.token_seq += 1
            tok = "ops-%s-%d" % (uuid.uuid4().hex, self.token_seq)
            self.tokens[tok] = {"released": False, "seq": self.token_seq}
            return tok, self.token_seq

    def token_ok(self, tok):
        with self.lock:
            rec = self.tokens.get(tok)
            return bool(rec) and not rec["released"]

    def release(self, tok):
        with self.lock:
            if tok in self.tokens:
                self.tokens[tok]["released"] = True

    def as_state(self):
        return {
            "adapterInstanceId": self.adapter_id,
            "datastoreResourceId": self.ds_id,
            "hostResourceId": self.host_id,
            "capacityAlertId": self.alert_capacity,
            "noiseAlertId": self.alert_noise,
            "symptomUsedPctId": self.sym_used_pct,
            "symptomFreeSpaceId": self.sym_freespace,
            "symptomNoiseId": self.sym_noise,
            "notificationTaskId": self.task_notify,
            "noiseTaskId": self.task_noise,
            "tokensIssued": [
                {"token": t, "seq": r["seq"], "released": r["released"]}
                for t, r in sorted(self.tokens.items(), key=lambda kv: kv[1]["seq"])
            ],
        }


W = World()


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------


def body_resources(names, kinds):
    catalog = [
        {
            "identifier": W.ds_id,
            "creationTime": 1739199600000,
            "resourceKey": {
                "name": "wld01-vsan-ds01",
                "adapterKindKey": "VMWARE",
                "resourceKindKey": "Datastore",
                "resourceIdentifiers": [
                    {
                        "identifierType": {"name": "VMEntityVCID", "dataType": "STRING", "isPartOfUniqueness": True},
                        "value": "vsan:52a1c9f0c4d84e11-9b7d3e0a6f2c48d5",
                    }
                ],
            },
            "resourceHealth": "RED",
            "resourceHealthValue": 8.0,
            "monitoringInterval": 5,
            "monitoringIntervalSeconds": 300,
            "dtEnabled": True,
            "resourceStatusStates": [
                {
                    "adapterInstanceId": W.adapter_id,
                    "resourceState": "STARTED",
                    "resourceStatus": "DATA_RECEIVING",
                    "statusMessage": "Collecting; last collection completed 2026-03-11T06:00:00Z",
                }
            ],
        },
        {
            "identifier": W.host_id,
            "creationTime": 1739199600000,
            "resourceKey": {
                "name": "esx04.wld01.example.com",
                "adapterKindKey": "VMWARE",
                "resourceKindKey": "HostSystem",
                "resourceIdentifiers": [],
            },
            "resourceHealth": "GREEN",
            "resourceHealthValue": 100.0,
            "monitoringInterval": 5,
            "monitoringIntervalSeconds": 300,
            "dtEnabled": True,
            "resourceStatusStates": [
                {
                    "adapterInstanceId": W.adapter_id,
                    "resourceState": "STARTED",
                    "resourceStatus": "DATA_RECEIVING",
                    "statusMessage": "Collecting; last collection completed 2026-03-11T06:00:00Z",
                }
            ],
        },
    ]
    out = catalog
    if names:
        out = [r for r in out if r["resourceKey"]["name"] in names]
    if kinds:
        out = [r for r in out if r["resourceKey"]["resourceKindKey"] in kinds]
    return {
        "resourceList": out,
        "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": len(out)},
    }


def body_alerts(spec):
    catalog = [
        {
            "alertId": W.alert_capacity,
            "resourceId": W.ds_id,
            "alertLevel": "CRITICAL",
            "status": "ACTIVE",
            "controlState": "OPEN",
            "type": "Storage",
            "subType": "Capacity",
            "alertImpact": "RISK",
            "alertDefinitionId": "AlertDefinition-VMWARE-Datastore-capacity-remaining",
            "alertDefinitionName": "Datastore is running out of disk space",
            "startTimeUTC": T_ALERT_START,
            "updateTimeUTC": T_TASK_UPDATED,
            "cancelTimeUTC": 0,
            "suspendUntilTimeUTC": 0,
            "ownerId": None,
            "ownerName": None,
            "statKey": None,
        },
        {
            "alertId": W.alert_noise,
            "resourceId": W.ds_id,
            "alertLevel": "IMMEDIATE",
            "status": "ACTIVE",
            "controlState": "SUSPENDED",
            "type": "Storage",
            "subType": "Performance",
            "alertImpact": "HEALTH",
            "alertDefinitionId": "AlertDefinition-VMWARE-Datastore-latency",
            "alertDefinitionName": "Datastore write latency is above tolerance",
            "startTimeUTC": T_NOISE_ALERT,
            "updateTimeUTC": T_NOISE_ALERT,
            "cancelTimeUTC": 0,
            "suspendUntilTimeUTC": 1773280800000,
            "ownerId": None,
            "ownerName": None,
            "statKey": None,
        },
    ]
    out = catalog
    rq = spec.get("resource-query") or {}
    rids = rq.get("resourceId")
    if rids:
        out = [a for a in out if a["resourceId"] in rids]
    if spec.get("activeOnly") is True:
        # activeOnly excludes suspended and cancelled alerts
        out = [a for a in out if a["controlState"] not in ("SUSPENDED",) and a["status"] != "CANCELED"]
    crits = spec.get("alertCriticality")
    if crits:
        out = [a for a in out if a["alertLevel"] in crits]
    return {"alerts": out, "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": len(out)}}


CONTRIBUTING = {}


def _contributing():
    if not CONTRIBUTING:
        CONTRIBUTING[W.alert_capacity] = [
            {
                "symptomId": W.sym_used_pct,
                "symptomSetId": "capacity-symptom-set",
                "symptomDefinitionsIds": ["SymptomDefinition-VMWARE-Datastore-used_pct-critical"],
            },
            {
                "symptomId": W.sym_freespace,
                "symptomSetId": "capacity-symptom-set",
                "symptomDefinitionsIds": ["SymptomDefinition-VMWARE-Datastore-freespace-immediate"],
            },
        ]
        CONTRIBUTING[W.alert_noise] = [
            {
                "symptomId": W.sym_noise,
                "symptomSetId": "latency-symptom-set",
                "symptomDefinitionsIds": ["SymptomDefinition-VMWARE-Datastore-write_latency-warning"],
            }
        ]
    return CONTRIBUTING


def body_contributing_symptoms(ids):
    table = _contributing()
    return {
        "contributingSymptoms": [
            {"alertId": aid, "contributingSymptoms": {"contributingSymptoms": table.get(aid, [])}}
            for aid in ids
        ]
    }


def body_symptoms(resource_ids, active_only):
    catalog = [
        {
            "id": W.sym_used_pct,
            "resourceId": W.ds_id,
            "symptomDefinitionId": "SymptomDefinition-VMWARE-Datastore-used_pct-critical",
            "symptomCriticality": "CRITICAL",
            "statKey": "diskspace|used_pct",
            "message": (
                "Datastore space used is 97.4% of 40.00 TiB usable "
                "(critical threshold 90%, warning threshold 80%)"
            ),
            "startTimeUTC": T_SYM1_START,
            "updateTimeUTC": T_TASK_UPDATED,
            "cancelTimeUTC": 0,
            "kpi": True,
            "_active": True,
        },
        {
            "id": W.sym_freespace,
            "resourceId": W.ds_id,
            "symptomDefinitionId": "SymptomDefinition-VMWARE-Datastore-freespace-immediate",
            "symptomCriticality": "IMMEDIATE",
            "statKey": "diskspace|freespace",
            "message": (
                "Allocatable free space on wld01-vsan-ds01 is 118 GB once the vSAN resync "
                "slack reserve is held back; new VM provisioning is being refused"
            ),
            "startTimeUTC": T_SYM2_START,
            "updateTimeUTC": T_TASK_UPDATED,
            "cancelTimeUTC": 0,
            "kpi": True,
            "_active": True,
        },
        {
            "id": W.sym_noise,
            "resourceId": W.ds_id,
            "symptomDefinitionId": "SymptomDefinition-VMWARE-Datastore-write_latency-warning",
            "symptomCriticality": "WARNING",
            "statKey": "datastore|totalWriteLatency_average",
            "message": "Datastore write latency 24 ms is above the 20 ms warning threshold",
            "startTimeUTC": T_SYM3_START,
            "updateTimeUTC": T_SYM3_START,
            "cancelTimeUTC": 1773199000000,
            "kpi": False,
            "_active": False,
        },
    ]
    out = catalog
    if resource_ids:
        out = [s for s in out if s["resourceId"] in resource_ids]
    if active_only is True:
        out = [s for s in out if s["_active"]]
    out = [{k: v for k, v in s.items() if k != "_active"} for s in out]
    return {"symptom": out, "pageInfo": {"page": 0, "pageSize": 1000, "totalCount": len(out)}}


def body_tasks(states, ids):
    catalog = [
        {
            "taskId": W.task_notify,
            "taskState": "ERROR",
            "description": "Outbound notification delivery for alert plugin 'ops-smtp-relay'",
            "statusMessage": "Delivery aborted after 5 retries",
            "createdTime": T_TASK_CREATED,
            "lastUpdateTime": T_TASK_UPDATED,
            "errorMessages": [
                "ops-smtp-relay: connect timed out to smtp.wld01.example.com:587 after 30000 ms",
                "ops-smtp-relay: 3 notification(s) dropped after 5 retries, alertId=%s" % W.alert_capacity,
                "ops-smtp-relay: outbound plugin marked unhealthy at 2026-03-11T03:10:22Z",
            ],
        },
        {
            "taskId": W.task_noise,
            "taskState": "ERROR",
            "description": "Management pack content synchronisation",
            "statusMessage": "Content repository unreachable",
            "createdTime": T_TASK2_CREATED,
            "lastUpdateTime": T_TASK2_UPDATED,
            "errorMessages": ["Content repository host packs.example.com returned HTTP 503"],
        },
        {
            "taskId": str(uuid.uuid5(uuid.NAMESPACE_DNS, "wld01-dt-run")),
            "taskState": "FINISHED",
            "description": "Dynamic threshold computation for wld01",
            "statusMessage": "Completed",
            "createdTime": T_TASK2_CREATED,
            "lastUpdateTime": T_TASK2_UPDATED,
            "errorMessages": [],
        },
    ]
    out = catalog
    if states:
        out = [t for t in out if t["taskState"] in states]
    if ids:
        out = [t for t in out if t["taskId"] in ids]
    return {"taskStatusList": out}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcf-ops-mock/1.0"
    logdir = None
    log_lock = threading.Lock()
    seq = [0]

    def log_message(self, fmt, *a):  # silence stderr access log
        pass

    # -- helpers ------------------------------------------------------------

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _record(self, method, parsed, raw, status, note=None):
        try:
            parsed_json = json.loads(raw.decode("utf-8")) if raw else None
            bad_json = False
        except Exception:
            parsed_json = None
            bad_json = bool(raw)
        hdrs = {k.lower(): v for k, v in self.headers.items()}
        with Handler.log_lock:
            i = Handler.seq[0]
            Handler.seq[0] += 1
            rec = {
                "seq": i,
                "method": method,
                "path": parsed.path,
                "rawQuery": parsed.query,
                "query": parse_qs(parsed.query, keep_blank_values=True),
                "headers": hdrs,
                "bodyRaw": raw.decode("utf-8", "replace"),
                "bodyBytes": len(raw),
                "bodyJson": parsed_json,
                "bodyUnparseable": bad_json,
                "status": status,
                "note": note,
            }
            with open(os.path.join(Handler.logdir, "requests.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            self._dump_state()

    def _dump_state(self):
        with open(os.path.join(Handler.logdir, "state.json"), "w", encoding="utf-8") as fh:
            json.dump(W.as_state(), fh, indent=2, sort_keys=True)

    def _send(self, code, payload):
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _auth_token(self):
        raw = self.headers.get("Authorization")
        if not raw:
            return None
        parts = raw.split()
        if len(parts) != 2 or parts[0] != SCHEME:
            return None
        return parts[1]

    # -- dispatch -----------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        raw = self._read_body()
        try:
            status, payload, note = self._route(method, parsed, raw)
        except Exception as exc:  # pragma: no cover - defensive
            status, payload, note = 500, {"message": "mock failure: %r" % (exc,)}, "mock-exception"
        self._record(method, parsed, raw, status, note)
        self._send(status, payload)

    def _route(self, method, parsed, raw):
        path = parsed.path.rstrip("/") or parsed.path
        q = parse_qs(parsed.query, keep_blank_values=True)

        if not path.startswith(BASE + "/"):
            return 404, {"message": "not in contract: %s %s" % (method, parsed.path)}, "out-of-contract"
        op_path = path[len(BASE) :]

        # ---- unauthenticated ------------------------------------------------
        if (method, op_path) == ("POST", "/api/auth/token/acquire"):
            ct = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ct != "application/json":
                return 415, {"message": "Content-Type must be application/json"}, "acquireToken"
            ok, body_or_err = self._json_body(raw, {"username", "password", "authSource"})
            if not ok:
                return 400, body_or_err, "acquireToken"
            body = body_or_err
            for req in ("username", "password"):
                if not isinstance(body.get(req), str) or not body[req]:
                    return 400, {"message": "missing required field '%s'" % req}, "acquireToken"
            if "authSource" in body and body["authSource"] not in VALID_AUTH_SOURCES:
                return 401, {"message": "unknown auth source"}, "acquireToken"
            if body["username"] != CREDS["username"] or body["password"] != CREDS["password"]:
                return 401, {"message": "invalid credentials"}, "acquireToken"
            tok, seq = W.mint_token()
            return (
                200,
                {
                    "token": tok,
                    "validity": 1773216000000 + seq,
                    "expiresAt": "2026-03-11T08:00:00.000Z",
                    "roles": ["ContentAdmin", "ReadOnly"],
                },
                "acquireToken",
            )

        # ---- everything else needs a live token -----------------------------
        known = {
            ("POST", "/api/resources/query"),
            ("POST", "/api/alerts/query"),
            ("GET", "/api/alerts/contributingsymptoms"),
            ("GET", "/api/symptoms"),
            ("GET", "/api/tasks"),
            ("POST", "/api/auth/token/release"),
        }
        if (method, op_path) not in known:
            return 404, {"message": "not in contract: %s %s" % (method, parsed.path)}, "out-of-contract"

        tok = self._auth_token()
        if tok is None or not W.token_ok(tok):
            return 401, {"message": "Authorization header must be '%s <token>' from a live session" % SCHEME}, "unauthorized"

        if (method, op_path) == ("POST", "/api/auth/token/release"):
            W.release(tok)
            return 200, None, "releaseToken"

        if (method, op_path) == ("POST", "/api/resources/query"):
            ct = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ct != "application/json":
                return 415, {"message": "Content-Type must be application/json"}, "getMatchingResources"
            ok, body = self._json_body(raw, {"name", "resourceKind"})
            if not ok:
                return 400, body, "getMatchingResources"
            return 200, body_resources(body.get("name"), body.get("resourceKind")), "getMatchingResources"

        if (method, op_path) == ("POST", "/api/alerts/query"):
            ct = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ct != "application/json":
                return 415, {"message": "Content-Type must be application/json"}, "queryAlert"
            ok, body = self._json_body(raw, {"activeOnly", "alertCriticality", "resource-query"})
            if not ok:
                return 400, body, "queryAlert"
            rq = body.get("resource-query")
            if rq is not None:
                if not isinstance(rq, dict) or set(rq) - {"resourceId"}:
                    return 400, {"message": "resource-query supports only 'resourceId' in this contract"}, "queryAlert"
            return 200, body_alerts(body), "queryAlert"

        if (method, op_path) == ("GET", "/api/alerts/contributingsymptoms"):
            ids = q.get("id")
            if not ids:
                return 400, {"message": "query parameter 'id' is required"}, "getAlertContributingSymptoms"
            if set(q) - {"id"}:
                return 400, {"message": "unexpected query parameters: %s" % sorted(set(q) - {"id"})}, "getAlertContributingSymptoms"
            for i in ids:
                if not _UUID_RE.match(i):
                    return 400, {"message": "'id' must be a uuid, got %r" % i}, "getAlertContributingSymptoms"
            return 200, body_contributing_symptoms(ids), "getAlertContributingSymptoms"

        if (method, op_path) == ("GET", "/api/symptoms"):
            allowed = {"resourceId", "activeOnly", "includeAlarmInfo", "page", "pageSize"}
            if set(q) - allowed:
                return 400, {"message": "unexpected query parameters: %s" % sorted(set(q) - allowed)}, "getSymptoms"
            ao = self._qs_bool(q, "activeOnly")
            if ao is _BAD:
                return 400, {"message": "activeOnly must be 'true' or 'false'"}, "getSymptoms"
            if self._qs_bool(q, "includeAlarmInfo") is _BAD:
                return 400, {"message": "includeAlarmInfo must be 'true' or 'false'"}, "getSymptoms"
            return 200, body_symptoms(q.get("resourceId"), ao), "getSymptoms"

        if (method, op_path) == ("GET", "/api/tasks"):
            allowed = {"taskState", "taskId"}
            if set(q) - allowed:
                return 400, {"message": "unexpected query parameters: %s" % sorted(set(q) - allowed)}, "getTasksStatus"
            states = q.get("taskState")
            valid = {"INITIATED", "STOPPED", "RUNNING", "FINISHED", "ERROR", "ABORTED", "UNKNOWN"}
            for s in states or []:
                if s not in valid:
                    return 400, {"message": "unknown taskState %r" % s}, "getTasksStatus"
            return 200, body_tasks(states, q.get("taskId")), "getTasksStatus"

        return 404, {"message": "not in contract"}, "out-of-contract"  # pragma: no cover

    @staticmethod
    def _json_body(raw, allowed):
        if not raw:
            return False, {"message": "request body is required"}
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return False, {"message": "request body is not valid JSON"}
        if not isinstance(body, dict):
            return False, {"message": "request body must be a JSON object"}
        extra = set(body) - allowed
        if extra:
            return False, {"message": "fields not in contract: %s" % sorted(extra)}
        nulls = sorted(k for k, v in body.items() if v is None)
        if nulls:
            return False, {"message": "null-valued fields must be omitted, not sent: %s" % nulls}
        return True, body

    @staticmethod
    def _qs_bool(q, key):
        if key not in q:
            return None
        v = q[key]
        if len(v) != 1 or v[0] not in ("true", "false"):
            return _BAD
        return v[0] == "true"


class _Bad:
    def __repr__(self):
        return "<bad>"


_BAD = _Bad()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logdir", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--portfile")
    args = ap.parse_args()

    os.makedirs(args.logdir, exist_ok=True)
    open(os.path.join(args.logdir, "requests.jsonl"), "w", encoding="utf-8").close()
    Handler.logdir = args.logdir

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    port = srv.server_address[1]
    with open(os.path.join(args.logdir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(W.as_state(), fh, indent=2, sort_keys=True)
    if args.portfile:
        tmp = args.portfile + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(str(port))
        os.replace(tmp, args.portfile)
    print("vcf-ops-mock listening on http://127.0.0.1:%d%s" % (port, BASE), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
