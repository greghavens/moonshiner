"""Acceptance tests for the snow_sync package.

Runs a loopback fake ServiceNow instance (Table API subset for the incident
table) and drives snow_sync against it. No network beyond 127.0.0.1, no real
credentials. The contract the fake enforces is pinned in docs/contract.json.
"""

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

USERNAME = "sync.bot"
PASSWORD = "dummy-cred-3f9c1a"  # never a real credential; must never leak
EXPECTED_AUTH = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()
).decode()

INCIDENT_PATH = CONTRACT["base_path"]
MAX_RETRIES = CONTRACT["rate_limit"]["max_retries"]
RL_STATUS = CONTRACT["rate_limit"]["status"]


def error_body(message, detail):
    return json.dumps(
        {"error": {"message": message, "detail": detail}, "status": "failure"}
    ).encode()


class FakeInstance:
    """In-memory incident table plus request recorder and fault injection."""

    def __init__(self):
        self.records = {}          # sys_id -> record dict (string values)
        self.requests = []         # every request the instance saw, in order
        self.write_count = 0       # POST + PATCH that mutated state
        self.fail_queue = []       # (status, body_bytes, extra_headers) one-shots
        self.always_fail = None    # same tuple, applied to every request
        self._counter = 0
        self._num = 0

    def seed(self, **fields):
        self._counter += 1
        self._num += 1
        sys_id = f"{self._counter:032x}"
        rec = {"sys_id": sys_id, "number": f"INC{self._num:07d}"}
        rec.update({k: str(v) for k, v in fields.items()})
        self.records[sys_id] = rec
        return sys_id

    def create(self, data):
        self._counter += 1
        self._num += 1
        sys_id = f"{self._counter:032x}"
        rec = {"sys_id": sys_id, "number": f"INC{self._num:07d}"}
        rec.update({k: str(v) for k, v in data.items()})
        self.records[sys_id] = rec
        self.write_count += 1
        return rec

    def evaluate(self, query):
        """Tiny encoded-query evaluator for the operators this seed pins."""
        order_field = "sys_id"
        conds = []
        for term in query.split("^"):
            if term.startswith("ORDERBY"):
                order_field = term[len("ORDERBY"):]
            elif "=" in term:
                f, v = term.split("=", 1)
                conds.append(("eq", f, v))
            elif "IN" in term:
                f, vals = term.split("IN", 1)
                conds.append(("in", f, vals.split(",")))
            else:
                raise ValueError(f"fake instance cannot evaluate term: {term!r}")
        out = []
        for rec in self.records.values():
            ok = True
            for op, f, v in conds:
                got = rec.get(f, "")
                if op == "eq" and got != v:
                    ok = False
                if op == "in" and got not in v:
                    ok = False
            if ok:
                out.append(rec)
        out.sort(key=lambda r: r.get(order_field, ""))
        return out


def make_handler(inst):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _record(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            req = {
                "method": self.command,
                "path": parsed.path,
                "raw_query": parsed.query,
                "params": {k: v[0] for k, v in parse_qs(parsed.query).items()},
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
            inst.requests.append(req)
            return req

        def _send(self, status, body=b"", headers=None):
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if "Content-Type" not in (headers or {}):
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _fault(self):
            entry = None
            if inst.always_fail is not None:
                entry = inst.always_fail
            elif inst.fail_queue:
                entry = inst.fail_queue.pop(0)
            if entry is None:
                return False
            status, body, headers = entry
            self._send(status, body, headers)
            return True

        def _dispatch(self):
            req = self._record()
            if self._fault():
                return
            if req["headers"].get("authorization") != EXPECTED_AUTH:
                self._send(401, error_body("User Not Authenticated",
                                           "Required to provide Auth information"))
                return
            if not req["path"].startswith(INCIDENT_PATH):
                self._send(404, error_body("Invalid table", req["path"]))
                return
            suffix = req["path"][len(INCIDENT_PATH):]
            if self.command == "GET" and suffix == "":
                self._list(req)
            elif self.command == "POST" and suffix == "":
                data = json.loads(req["body"])
                rec = inst.create(data)
                self._send(201, json.dumps({"result": rec}).encode(),
                           {"Location": f"{INCIDENT_PATH}/{rec['sys_id']}"})
            elif self.command == "PATCH" and suffix.startswith("/"):
                sys_id = suffix[1:]
                rec = inst.records.get(sys_id)
                if rec is None:
                    self._send(404, error_body(
                        "No Record found",
                        "Record doesn't exist or ACL restricts the record retrieval"))
                    return
                data = json.loads(req["body"])
                rec.update({k: str(v) for k, v in data.items()})
                inst.write_count += 1
                self._send(200, json.dumps({"result": rec}).encode())
            else:
                self._send(400, error_body("Unsupported operation", self.command))

        def _list(self, req):
            params = req["params"]
            query = params.get("sysparm_query", "")
            try:
                matched = inst.evaluate(query) if query else sorted(
                    inst.records.values(), key=lambda r: r["sys_id"])
            except ValueError as exc:
                self._send(400, error_body("Invalid query", str(exc)))
                return
            total = len(matched)
            offset = int(params.get("sysparm_offset", "0"))
            limit = int(params.get("sysparm_limit", "10000"))
            page = matched[offset:offset + limit]
            fields = params.get("sysparm_fields")
            if fields:
                keep = fields.split(",")
                page = [{k: r.get(k, "") for k in keep} for r in page]
            headers = {
                "X-Total-Count": str(total),
                "Link": f"<{INCIDENT_PATH}?sysparm_offset=0>;rel=\"first\"",
            }
            self._send(200, json.dumps({"result": page}).encode(), headers)

        do_GET = _dispatch
        do_POST = _dispatch
        do_PATCH = _dispatch

    return Handler


class RedirectTrap(BaseHTTPRequestHandler):
    """A different origin. Records what auth material reaches it."""
    seen = []

    def log_message(self, *a):
        pass

    def _any(self):
        RedirectTrap.seen.append(self.headers.get("Authorization"))
        body = json.dumps({"result": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _any
    do_POST = _any
    do_PATCH = _any


def start_server(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def fresh(page_sleep=None):
    from snow_sync.client import TableClient

    inst = FakeInstance()
    srv = start_server(make_handler(inst))
    sleeps = []
    client = TableClient(
        f"http://127.0.0.1:{srv.server_address[1]}",
        USERNAME,
        PASSWORD,
        sleep=(page_sleep if page_sleep is not None else sleeps.append),
        max_retries=MAX_RETRIES,
    )
    return inst, srv, client, sleeps


def gets(inst):
    return [r for r in inst.requests if r["method"] == "GET"]


def test_pagination_and_encoding():
    inst, srv, client, _ = fresh()
    for i in range(7):
        inst.seed(state="1", short_description=f"open incident {i}",
                  correlation_id=f"OPS-{i:04d}", urgency="3")
    for i in range(2):
        inst.seed(state="6", short_description=f"resolved incident {i}",
                  correlation_id=f"OPS-9{i:03d}", urgency="3")

    fields = ["sys_id", "number", "short_description", "correlation_id"]
    recs = client.get_records("incident", query="state=1^ORDERBYsys_id",
                              fields=fields, page_size=3)
    assert len(recs) == 7, f"expected all 7 matching records, got {len(recs)}"
    assert [r["number"] for r in recs] == [f"INC{n:07d}" for n in range(1, 8)], \
        "records must come back in sys_id scan order across pages"
    for r in recs:
        assert sorted(r.keys()) == sorted(fields), \
            f"sysparm_fields projection not honored: {sorted(r.keys())}"

    reqs = gets(inst)
    assert len(reqs) == 3, f"7 records at page_size 3 must take exactly 3 GETs, saw {len(reqs)}"
    assert [r["params"].get("sysparm_offset") for r in reqs] == ["0", "3", "6"], \
        "sysparm_offset must advance by page_size each page"
    for r in reqs:
        assert r["path"] == INCIDENT_PATH, f"wrong collection path {r['path']}"
        assert r["params"].get("sysparm_limit") == "3", "sysparm_limit must equal page_size"
        assert r["params"].get("sysparm_query") == "state=1^ORDERBYsys_id", \
            "sysparm_query must round-trip exactly"
        assert r["params"].get("sysparm_fields") == ",".join(fields), \
            "sysparm_fields must be the comma-joined field list"
        assert "%5EORDERBY" in r["raw_query"], \
            "the caret in sysparm_query must be percent-encoded as %5E on the wire"
        assert "state%3D1" in r["raw_query"], \
            "the '=' inside the sysparm_query value must be percent-encoded as %3D"
        assert r["headers"].get("accept") == "application/json", \
            "every request must send Accept: application/json"
        assert r["headers"].get("authorization") == EXPECTED_AUTH, \
            "every request must carry Basic credentials"
    srv.shutdown()


def test_error_envelope():
    from snow_sync.errors import SnowApiError

    inst, srv, client, _ = fresh()
    inst.fail_queue.append((403, error_body(
        "Operation Failed", "ACL Exception Insert Failed due to security constraints"), {}))
    raised = None
    try:
        client.create_record("incident", {"short_description": "denied"})
    except SnowApiError as exc:
        raised = exc
    assert raised is not None, "a 403 error envelope must raise SnowApiError"
    assert raised.status_code == 403, f"status_code should be 403, got {raised.status_code}"
    assert raised.message == "Operation Failed", f"wrong message: {raised.message!r}"
    assert raised.detail == "ACL Exception Insert Failed due to security constraints", \
        f"wrong detail: {raised.detail!r}"
    assert "Operation Failed" in str(raised), "str(err) should surface ServiceNow's message"
    assert PASSWORD not in str(raised) + repr(raised), "credentials leaked into the exception"

    raised = None
    try:
        client.update_record("incident", "f" * 32, {"urgency": "1"})
    except SnowApiError as exc:
        raised = exc
    assert raised is not None, "PATCH of a missing sys_id must raise SnowApiError"
    assert raised.status_code == 404, f"expected 404, got {raised.status_code}"
    assert raised.message == "No Record found", f"wrong 404 message: {raised.message!r}"
    assert inst.write_count == 0, "failed writes must not mutate the instance"
    srv.shutdown()


def test_rate_limit_retry_then_success():
    inst, srv, client, sleeps = fresh()
    inst.seed(state="1", short_description="only one", correlation_id="OPS-1", urgency="2")
    inst.fail_queue.append((RL_STATUS, error_body("Too many requests",
                                                  "Rate limit quota exceeded"),
                            {"Retry-After": "7"}))
    recs = client.get_records("incident", query="state=1^ORDERBYsys_id", page_size=5)
    assert len(recs) == 1, "the retried request must still return the full result"
    assert sleeps == [7] or sleeps == [7.0], \
        f"client must sleep exactly the Retry-After seconds once, slept {sleeps}"
    assert len(gets(inst)) == 2, "one 429 then one successful retry — no extra requests"
    srv.shutdown()


def test_rate_limit_exhaustion():
    from snow_sync.errors import RateLimitError, SnowApiError

    inst, srv, client, sleeps = fresh()
    inst.always_fail = (RL_STATUS, error_body("Too many requests",
                                              "Rate limit quota exceeded"),
                        {"Retry-After": "2"})
    raised = None
    try:
        client.get_records("incident", query="state=1^ORDERBYsys_id", page_size=5)
    except RateLimitError as exc:
        raised = exc
    assert raised is not None, "persistent 429 must raise RateLimitError"
    assert isinstance(raised, SnowApiError), "RateLimitError must subclass SnowApiError"
    assert raised.status_code == RL_STATUS
    assert raised.retry_after == 2, f"retry_after should carry the header value, got {raised.retry_after}"
    assert len(sleeps) == MAX_RETRIES, \
        f"client must sleep {MAX_RETRIES} times before giving up, slept {len(sleeps)}"
    assert len(gets(inst)) == MAX_RETRIES + 1, \
        "original attempt plus max_retries retries, then stop"
    srv.shutdown()


DESIRED = [
    {"correlation_id": "OPS-1001", "short_description": "Disk usage high on db-3", "urgency": "2"},
    {"correlation_id": "OPS-1002", "short_description": "Payment webhook timeouts", "urgency": "1"},
    {"correlation_id": "OPS-1003", "short_description": "TLS cert expires in 14 days", "urgency": "3"},
    {"correlation_id": "OPS-1004", "short_description": "Nightly ETL job stuck", "urgency": "2"},
]


def test_upsert_creates_patches_and_preserves_sys_id():
    from snow_sync.sync import IncidentSyncer

    inst, srv, client, _ = fresh()
    sid_a = inst.seed(correlation_id="OPS-1001",
                      short_description="Disk usage high on db-3", urgency="2", state="1")
    sid_b = inst.seed(correlation_id="OPS-1002",
                      short_description="Payment webhook timeouts", urgency="3", state="1")

    report = IncidentSyncer(client).sync([dict(d) for d in DESIRED])
    assert report.created == 2, f"OPS-1003/OPS-1004 are new: created={report.created}"
    assert report.updated == 1, f"only OPS-1002 changed: updated={report.updated}"
    assert report.skipped == 1, f"OPS-1001 is identical: skipped={report.skipped}"
    assert report.sys_ids["OPS-1001"] == sid_a, "existing sys_id identity must be preserved"
    assert report.sys_ids["OPS-1002"] == sid_b, "existing sys_id identity must be preserved"
    assert len(report.sys_ids) == 4 and all(len(s) == 32 for s in report.sys_ids.values())

    lookups = gets(inst)
    assert len(lookups) == 1, f"exactly one lookup query per sync run, saw {len(lookups)}"
    assert lookups[0]["params"]["sysparm_query"] == \
        "correlation_idINOPS-1001,OPS-1002,OPS-1003,OPS-1004^ORDERBYsys_id", \
        "lookup must batch all keys with IN, in input order, ordered for a stable scan"
    assert lookups[0]["params"]["sysparm_fields"] == \
        "sys_id,correlation_id,short_description,urgency", \
        "lookup must project sys_id + key field + desired fields (sorted)"

    patches = [r for r in inst.requests if r["method"] == "PATCH"]
    assert len(patches) == 1, f"one changed record means one PATCH, saw {len(patches)}"
    assert patches[0]["path"] == f"{INCIDENT_PATH}/{sid_b}", \
        "PATCH must target the existing record's sys_id"
    assert json.loads(patches[0]["body"]) == {"urgency": "1"}, \
        "PATCH body must contain only the fields that actually changed"
    assert patches[0]["headers"].get("content-type") == "application/json"

    posts = [r for r in inst.requests if r["method"] == "POST"]
    assert len(posts) == 2, f"two new records means two POSTs, saw {len(posts)}"
    posted = sorted(json.loads(p["body"])["correlation_id"] for p in posts)
    assert posted == ["OPS-1003", "OPS-1004"], f"unexpected POST set {posted}"
    for p in posts:
        body = json.loads(p["body"])
        assert body.get("short_description") and body.get("urgency"), \
            "POST must carry the full desired field set"
        assert p["headers"].get("content-type") == "application/json"

    assert len(inst.records) == 4, "no duplicate incidents may be created"
    assert inst.records[sid_b]["urgency"] == "1", "the changed field must be updated in place"

    # second run with identical desired state must be a no-op
    before_writes = inst.write_count
    before_reqs = len(inst.requests)
    report2 = IncidentSyncer(client).sync([dict(d) for d in DESIRED])
    assert (report2.created, report2.updated, report2.skipped) == (0, 0, 4), \
        f"idempotent re-run must skip everything, got {report2.created}/{report2.updated}/{report2.skipped}"
    assert inst.write_count == before_writes, "idempotent re-run must issue zero writes"
    assert len(inst.requests) == before_reqs + 1, \
        "idempotent re-run is exactly one lookup GET and nothing else"
    assert len(inst.records) == 4, "idempotent re-run must not create duplicates"
    srv.shutdown()


def test_cross_origin_redirect_never_forwards_credentials():
    from snow_sync.errors import SnowApiError

    RedirectTrap.seen = []
    trap = start_server(RedirectTrap)
    inst, srv, client, _ = fresh()
    trap_url = f"http://127.0.0.1:{trap.server_address[1]}{INCIDENT_PATH}"
    inst.fail_queue.append((302, b"", {"Location": trap_url}))
    try:
        client.get_records("incident", query="state=1^ORDERBYsys_id", page_size=5)
    except SnowApiError:
        pass  # refusing the redirect is an accepted behavior
    for auth in RedirectTrap.seen:
        assert auth is None, "Authorization header was forwarded across origins"
    assert all(a is None for a in RedirectTrap.seen), \
        "Basic credentials must never reach a different origin"
    srv.shutdown()
    trap.shutdown()


def test_protected_docs_fixtures():
    research = SOURCES["research"]
    assert research["required"] is True, "wave-8 seeds must record research provenance"
    assert len(research["official_sources"]) >= 2, "at least two official sources required"
    for src in research["official_sources"]:
        assert src["url"].startswith("https://"), f"non-https source {src['url']}"
        assert "servicenow.com" in src["url"], "sources must be first-party ServiceNow pages"
        assert src.get("used_for"), "each source must say which facts it backed"
    assert len(SOURCES["verified_facts"]) >= 4, "contract facts must be summarized"
    assert CONTRACT["operations"]["create"]["success"]["status"] == 201
    assert CONTRACT["operations"]["update"]["method"] == "PATCH"
    assert CONTRACT["rate_limit"]["retry_after_header"] == "Retry-After"
    assert CONTRACT["error_envelope"]["shape"]["status"] == "failure"


def main():
    tests = [
        test_protected_docs_fixtures,
        test_pagination_and_encoding,
        test_error_envelope,
        test_rate_limit_retry_then_success,
        test_rate_limit_exhaustion,
        test_upsert_creates_patches_and_preserves_sys_id,
        test_cross_origin_redirect_never_forwards_credentials,
    ]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"PASS  {len(tests)} test groups")


if __name__ == "__main__":
    main()
