"""Acceptance harness: loopback fake Fusion HCM pod exercising the assignment
updater against the wire contract pinned in docs/contract.json (If-Match
optimistic concurrency with 412 recovery, Effective-Of CORRECTION vs UPDATE
range modes, application/vnd.oracle.adf.error+json multi-message validation
errors). No real tenant, no real credentials, no sleeps.

Run with: python3 test_assignment_update.py
Protected — do not modify this file or anything under docs/.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hcm_transport import HcmSession
from assignment_reader import read_assignment

USER = "HCM_EMP_SVC"
PASS = "dummy-hcm-secret-41"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

WORKER_UID = "00020000000EACED0005DUMW01"
POS_ID = 300100555666777
ASG_UID = "00030000000AACED0005DUMA02"
PATH = (
    f"/hcmRestApi/resources/11.13.18.05/workers/{WORKER_UID}"
    f"/child/workRelationships/{POS_ID}/child/assignments/{ASG_UID}"
)

ETAG_V1 = '"ACED00057372000B6173672D657461672D7631"'
ETAG_V2 = '"ACED00057372000B6173672D657461672D7632"'

BASE_RECORD = {
    "AssignmentId": 300100555666778,
    "AssignmentNumber": "E900311-2",
    "AssignmentName": "Field Service Technician",
    "DepartmentName": "Field Service EU",
    "JobCode": "SVC_TECH_2",
    "NormalHours": 40,
    "Frequency": "W",
    "EffectiveStartDate": "2025-01-01",
    "EffectiveEndDate": "4712-12-31",
    "EffectiveSequence": 1,
    "EffectiveLatestChange": "Y",
}

VALIDATION_BODY = {
    "title": "Bad Request",
    "status": "400",
    "o:errorDetails": [
        {
            "detail": "The value -4 for NormalHours must be greater than zero.",
            "o:errorCode": "PER-1530021",
            "o:errorPath": "/NormalHours",
        },
        {
            "detail": "The assignment status SUSPENDED isn't valid for the assignment's business unit.",
            "o:errorCode": "PER-1530377",
            "o:errorPath": "/AssignmentStatusTypeCode",
        },
        {
            "detail": "An action code is required when the assignment status changes.",
            "o:errorCode": "27021",
            "o:errorPath": "",
        },
    ],
}

REQUESTS = []
STATE = {}


def reset(etag_sequence=None, accept_etag=ETAG_V1, validation_fail=False):
    REQUESTS.clear()
    STATE.clear()
    STATE["etag_sequence"] = list(etag_sequence or [ETAG_V1])
    STATE["accept_etag"] = accept_etag
    STATE["validation_fail"] = validation_fail
    STATE["record"] = dict(BASE_RECORD)


def parse_effective_of(header):
    parts = {}
    for chunk in (header or "").split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key] = value
    return parts


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _record(self, body):
        REQUESTS.append(
            {
                "method": self.command,
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "framework": self.headers.get("REST-Framework-Version"),
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "if_match": self.headers.get("If-Match"),
                "effective_of": self.headers.get("Effective-Of"),
                "body": body,
            }
        )

    def _send(self, status, payload, content_type="application/json", etag=None):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("REST-Framework-Version", "4")
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._record(None)
        if self.path != PATH:
            self._send(
                404,
                {"title": "Not Found", "status": "404", "o:errorDetails": []},
                content_type="application/vnd.oracle.adf.error+json",
            )
            return
        seq = STATE["etag_sequence"]
        etag = seq.pop(0) if len(seq) > 1 else seq[0]
        item = dict(STATE["record"])
        item["links"] = [
            {
                "rel": "self",
                "href": f"http://127.0.0.1:{self.server.server_address[1]}{PATH}",
                "name": "assignments",
                "kind": "item",
                "properties": {"changeIndicator": etag},
            }
        ]
        self._send(200, item, etag=etag)

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else None
        self._record(body)
        if self.path != PATH:
            self._send(
                404,
                {"title": "Not Found", "status": "404", "o:errorDetails": []},
                content_type="application/vnd.oracle.adf.error+json",
            )
            return
        if self.headers.get("If-Match") != STATE["accept_etag"]:
            self._send(
                412,
                {
                    "title": "Precondition Failed",
                    "status": "412",
                    "o:errorDetails": [
                        {
                            "detail": "The resource has been modified since it was read.",
                            "o:errorCode": "412",
                            "o:errorPath": "",
                        }
                    ],
                },
                content_type="application/vnd.oracle.adf.error+json",
            )
            return
        if STATE["validation_fail"]:
            self._send(
                400,
                VALIDATION_BODY,
                content_type="application/vnd.oracle.adf.error+json",
            )
            return
        effective = parse_effective_of(self.headers.get("Effective-Of"))
        item = dict(STATE["record"])
        item.update(body or {})
        if effective.get("RangeMode") == "UPDATE":
            item["EffectiveStartDate"] = effective["RangeStartDate"]
            item["EffectiveSequence"] = 1
            item["EffectiveLatestChange"] = "Y"
        STATE["record"] = dict(item)
        self._send(200, item, etag=ETAG_V2)


def check_common_headers(reqs):
    for r in reqs:
        assert r["auth"] == AUTH, f"Authorization header missing/wrong on {r['method']}"
        assert r["framework"] == "4", f"REST-Framework-Version must be 4 on {r['method']}"
        assert r["accept"] == "application/json", f"Accept header wrong on {r['method']}"


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        run(base)
    finally:
        server.shutdown()
    print("OK — all assignment-update checks passed")


def run(base):
    from assignment_updates import (
        apply_assignment_change,
        ConcurrencyError,
        ValidationError,
    )

    session = HcmSession(base, USER, PASS)

    # ------------------------------------------- T1: existing reader still works
    reset()
    snap = read_assignment(session, WORKER_UID, POS_ID, ASG_UID)
    assert len(REQUESTS) == 1 and REQUESTS[0]["method"] == "GET", "reader issues one GET"
    assert REQUESTS[0]["path"] == PATH, f"reader path: {REQUESTS[0]['path']}"
    check_common_headers(REQUESTS)
    assert snap.fields["DepartmentName"] == "Field Service EU"
    assert snap.fields["EffectiveStartDate"] == "2025-01-01"
    assert "links" not in snap.fields, "reader strips links from fields"
    assert snap.etag == ETAG_V1, "reader captures the ETag response header"
    assert snap.change_indicator == ETAG_V1, "changeIndicator mirrors the ETag"
    assert snap.self_path == PATH, "self link is normalized to a path"

    # ------------------------------------------- T2: correction mode, happy path
    reset()
    result = apply_assignment_change(
        session,
        WORKER_UID,
        POS_ID,
        ASG_UID,
        changes={"DepartmentName": "Field Service DACH"},
        mode="correction",
    )
    methods = [r["method"] for r in REQUESTS]
    assert methods == ["GET", "PATCH"], f"correction flow must GET then PATCH, got {methods}"
    check_common_headers(REQUESTS)
    patch = REQUESTS[1]
    assert patch["path"] == PATH
    assert patch["effective_of"] == "RangeMode=CORRECTION", (
        f"correction header: {patch['effective_of']}"
    )
    assert patch["if_match"] == ETAG_V1, "PATCH must carry If-Match with the ETag just read"
    assert patch["content_type"] == "application/vnd.oracle.adf.resourceitem+json", (
        f"PATCH content type: {patch['content_type']}"
    )
    assert patch["body"] == {"DepartmentName": "Field Service DACH"}, (
        "PATCH body must contain only the changed attributes"
    )
    assert result.mode == "correction"
    assert result.retried is False
    assert result.item["DepartmentName"] == "Field Service DACH"
    assert result.item["EffectiveStartDate"] == "2025-01-01", (
        "a correction rewrites the row in place — no new effective start date"
    )

    # ------------------------------------------- T3: correction with explicit range
    reset()
    apply_assignment_change(
        session,
        WORKER_UID,
        POS_ID,
        ASG_UID,
        changes={"NormalHours": 36},
        mode="correction",
        range_start="2025-03-01",
        range_end="2025-06-30",
    )
    assert REQUESTS[1]["effective_of"] == (
        "RangeMode=CORRECTION;RangeStartDate=2025-03-01;RangeEndDate=2025-06-30"
    ), f"ranged correction header: {REQUESTS[1]['effective_of']}"

    # ------------------------------------------- T4: update mode inserts a new row
    reset()
    result = apply_assignment_change(
        session,
        WORKER_UID,
        POS_ID,
        ASG_UID,
        changes={"JobCode": "SVC_TECH_3"},
        mode="update",
        range_start="2026-08-01",
    )
    assert REQUESTS[1]["effective_of"] == "RangeMode=UPDATE;RangeStartDate=2026-08-01", (
        f"update header: {REQUESTS[1]['effective_of']}"
    )
    assert REQUESTS[1]["body"] == {"JobCode": "SVC_TECH_3"}
    assert result.mode == "update"
    assert result.item["JobCode"] == "SVC_TECH_3"
    assert result.item["EffectiveStartDate"] == "2026-08-01", (
        "an update creates a new date-effective row starting at RangeStartDate"
    )

    # ------------------------------------------- T5: mode validation, no HTTP
    reset()
    for bad_call in (
        dict(changes={"JobCode": "X"}, mode="update"),  # update requires range_start
        dict(changes={"JobCode": "X"}, mode="replace"),  # unknown mode
    ):
        try:
            apply_assignment_change(session, WORKER_UID, POS_ID, ASG_UID, **bad_call)
            raise AssertionError(f"expected ValueError for {bad_call}")
        except ValueError:
            pass
    assert REQUESTS == [], "invalid modes must be rejected before any HTTP request"

    # ------------------------------------------- T6: 412 conflict, one retry
    reset(etag_sequence=[ETAG_V1, ETAG_V2], accept_etag=ETAG_V2)
    result = apply_assignment_change(
        session,
        WORKER_UID,
        POS_ID,
        ASG_UID,
        changes={"DepartmentName": "Field Service Nordics"},
        mode="correction",
    )
    methods = [r["method"] for r in REQUESTS]
    assert methods == ["GET", "PATCH", "GET", "PATCH"], (
        f"412 must trigger exactly one refresh-and-retry, got {methods}"
    )
    assert REQUESTS[1]["if_match"] == ETAG_V1
    assert REQUESTS[3]["if_match"] == ETAG_V2, "retry must carry the freshly read ETag"
    assert result.retried is True
    assert result.item["DepartmentName"] == "Field Service Nordics"

    # ------------------------------------------- T7: persistent conflict stops
    reset(etag_sequence=[ETAG_V1, ETAG_V1], accept_etag=ETAG_V2)
    try:
        apply_assignment_change(
            session,
            WORKER_UID,
            POS_ID,
            ASG_UID,
            changes={"DepartmentName": "Field Service Iberia"},
            mode="correction",
        )
        raise AssertionError("expected ConcurrencyError")
    except ConcurrencyError as err:
        assert err.attempts == 2, "exactly two PATCH attempts before giving up"
        conflict_text = str(err)
    methods = [r["method"] for r in REQUESTS]
    assert methods == ["GET", "PATCH", "GET", "PATCH"], (
        f"persistent conflict must not loop, got {methods}"
    )
    assert PASS not in conflict_text and AUTH not in conflict_text

    # ------------------------------------------- T8: multi-message validation error
    reset(validation_fail=True)
    try:
        apply_assignment_change(
            session,
            WORKER_UID,
            POS_ID,
            ASG_UID,
            changes={"AssignmentStatusTypeCode": "SUSPENDED", "NormalHours": -4},
            mode="correction",
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as err:
        assert err.title == "Bad Request"
        assert err.status == "400"
        assert len(err.messages) == 3, "every o:errorDetails entry must be preserved"
        codes = [m[0] for m in err.messages]
        assert codes == ["PER-1530021", "PER-1530377", "27021"], f"codes in order: {codes}"
        paths = [m[1] for m in err.messages]
        assert paths == ["/NormalHours", "/AssignmentStatusTypeCode", ""], (
            f"paths in order: {paths}"
        )
        text = str(err)
        for entry in VALIDATION_BODY["o:errorDetails"]:
            assert entry["detail"] in text, f"missing detail in message: {entry['detail']}"
        assert PASS not in text and AUTH not in text, "credentials leaked into error text"
    methods = [r["method"] for r in REQUESTS]
    assert methods == ["GET", "PATCH"], (
        f"validation failures must not be retried, got {methods}"
    )

    # ------------------------------------------- T9: reader unaffected afterwards
    reset()
    snap = read_assignment(session, WORKER_UID, POS_ID, ASG_UID)
    assert snap.fields["DepartmentName"] == "Field Service EU"


if __name__ == "__main__":
    main()
