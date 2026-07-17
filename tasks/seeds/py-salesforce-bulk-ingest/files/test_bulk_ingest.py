"""Acceptance tests for the sf_bulk package.

Runs a loopback fake of the Salesforce Bulk API 2.0 ingest resources
(/services/data/v67.0/jobs/ingest) and drives sf_bulk against it. No network
beyond 127.0.0.1, no real org, no real credentials. The contract the fake
enforces is pinned in docs/contract.json with provenance in
docs/official_sources.json.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

TOKEN = "00Dxx-dummy-access-token-3fb81c"  # dummy; must never leak anywhere
API_VERSION = CONTRACT["api_version"]
BASE = CONTRACT["base_path"]  # /services/data/v67.0/jobs/ingest

CHECKS = 0


def check(cond, msg):
    global CHECKS
    assert cond, msg
    CHECKS += 1


def error_body(error_code, message):
    return json.dumps([{"errorCode": error_code, "message": message}]).encode()


class FakeOrg:
    """In-memory Bulk API 2.0 ingest subset with request recording."""

    def __init__(self):
        self.requests = []       # (method, path, headers dict, body bytes)
        self.jobs = {}           # job id -> job info dict
        self.uploads = {}        # job id -> list of (content_type, body bytes)
        self.state_script = {}   # job id -> list of dict overrides per info GET
        self.close_mode = None   # None | "drop_after_apply" | "drop_before_apply"
        self.fail_once = None    # (method, path substring, status, body bytes)
        self.results = {}        # job id -> {"failed": bytes, "unprocessed": bytes}
        self.redirect_failed_to = None  # absolute URL for failedResults 302
        self._n = 0
        self.server = None
        self.base_url = None

    def new_job(self, body):
        self._n += 1
        jid = "750KB" + str(self._n).rjust(10, "0") + "QA2"
        job = {
            "id": jid,
            "object": body["object"],
            "operation": body["operation"],
            "state": "Open",
            "contentType": "CSV",
            "lineEnding": body.get("lineEnding", "LF"),
            "apiVersion": float(API_VERSION[1:]),
            "contentUrl": "services/data/%s/jobs/ingest/%s/batches" % (API_VERSION, jid),
            "numberRecordsProcessed": 0,
            "numberRecordsFailed": 0,
        }
        self.jobs[jid] = job
        return job

    def count(self, method, suffix):
        return sum(1 for m, p, _, _ in self.requests
                   if m == method and p.endswith(suffix))

    def start(self):
        org = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _send(self, status, payload, ctype="application/json", extra=None):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(payload)

            def _record(self, body):
                org.requests.append(
                    (self.command, self.path, dict(self.headers), body))

            def _maybe_fail(self):
                if org.fail_once and org.fail_once[0] == self.command \
                        and org.fail_once[1] in self.path:
                    _, _, status, payload = org.fail_once
                    org.fail_once = None
                    self._send(status, payload)
                    return True
                return False

            def _job_for(self, path):
                parts = path[len(BASE):].strip("/").split("/")
                jid = parts[0] if parts and parts[0] else None
                return org.jobs.get(jid)

            def do_POST(self):
                body = self._body()
                self._record(body)
                if self._maybe_fail():
                    return
                if self.path != BASE:
                    self._send(404, error_body("NOT_FOUND",
                               "The requested resource does not exist"))
                    return
                job = org.new_job(json.loads(body.decode("utf-8")))
                self._send(200, json.dumps(job).encode())

            def do_PUT(self):
                body = self._body()
                self._record(body)
                if self._maybe_fail():
                    return
                job = self._job_for(self.path)
                if job is None or not self.path.endswith("/batches"):
                    self._send(404, error_body("NOT_FOUND", "no such job data path"))
                    return
                if job["state"] != "Open":
                    self._send(400, error_body("INVALIDJOBSTATE",
                               "Data cannot be uploaded unless the job is Open"))
                    return
                org.uploads.setdefault(job["id"], []).append(
                    (self.headers.get("Content-Type"), body))
                self._send(201, b"")

            def do_PATCH(self):
                body = self._body()
                self._record(body)
                if self._maybe_fail():
                    return
                job = self._job_for(self.path)
                if job is None:
                    self._send(404, error_body("NOT_FOUND", "no such job"))
                    return
                mode, org.close_mode = org.close_mode, None
                if mode == "drop_before_apply":
                    self.close_connection = True
                    return
                new_state = json.loads(body.decode("utf-8")).get("state")
                if job["state"] != "Open":
                    self._send(400, error_body("INVALIDJOBSTATE",
                               "Closing already closed job: " + job["id"]))
                    return
                if new_state not in ("UploadComplete", "Aborted"):
                    self._send(400, error_body("INVALIDJOBSTATE",
                               "Invalid state " + repr(new_state)))
                    return
                job["state"] = new_state
                if mode == "drop_after_apply":
                    self.close_connection = True
                    return
                self._send(200, json.dumps(job).encode())

            def do_GET(self):
                self._record(b"")
                if self._maybe_fail():
                    return
                job = self._job_for(self.path)
                if job is None:
                    self._send(404, error_body("NOT_FOUND", "no such job"))
                    return
                jid = job["id"]
                if self.path.endswith("/failedResults/"):
                    if org.redirect_failed_to:
                        self._send(302, b"", extra={
                            "Location": org.redirect_failed_to})
                        return
                    self._send(200, org.results[jid]["failed"], ctype="text/csv")
                    return
                if self.path.endswith("/unprocessedrecords/"):
                    self._send(200, org.results[jid]["unprocessed"],
                               ctype="text/csv")
                    return
                if self.path == BASE + "/" + jid:
                    script = org.state_script.get(jid) or []
                    info = dict(job)
                    if script:
                        info.update(script.pop(0))
                        job["state"] = info["state"]
                    self._send(200, json.dumps(info).encode())
                    return
                self._send(404, error_body("NOT_FOUND", "unknown ingest path"))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


class EvilHost:
    """A second origin that must never see the Bearer token."""

    def __init__(self):
        self.auth_seen = None
        self.hits = 0
        ev = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                ev.hits += 1
                if "Authorization" in self.headers:
                    ev.auth_seen = self.headers["Authorization"]
                payload = b"sf__Id,sf__Error\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def make_client(org):
    from sf_bulk.client import BulkClient
    return BulkClient(org.base_url, TOKEN)


def assert_common_headers(org):
    for method, path, headers, _ in org.requests:
        check(headers.get("Authorization") == "Bearer " + TOKEN,
              "%s %s must send Authorization: Bearer <token>" % (method, path))
        if not (method == "PUT" or "Results" in path or "records" in path):
            check((headers.get("Accept") or "").startswith("application/json"),
                  "%s %s must send Accept: application/json" % (method, path))


FIELDS = ["Name", "Industry", "AnnualRevenue"]
ROWS = [
    {"Name": "Acme West", "Industry": "Energy", "AnnualRevenue": "1200000"},
    {"Name": "Blue, Harbor Ltd", "Industry": None, "AnnualRevenue": "98000"},
    {"Name": 'Quote "Q" Co', "Industry": "Retail", "AnnualRevenue": None},
]
EXPECTED_LF_CSV = (
    "Name,Industry,AnnualRevenue\n"
    "Acme West,Energy,1200000\n"
    '"Blue, Harbor Ltd",#N/A,98000\n'
    '"Quote ""Q"" Co",Retail,#N/A\n'
).encode("utf-8")


def test_happy_path_insert():
    from sf_bulk.ingest import run_ingest
    org = FakeOrg().start()
    try:
        client = make_client(org)
        sleeps = []
        real_create = client.create_ingest_job

        def create_and_script(*a, **kw):
            # scripted after close: UploadComplete -> InProgress -> JobComplete
            job = real_create(*a, **kw)
            org.state_script[job["id"]] = [
                {"state": "UploadComplete"},
                {"state": "InProgress", "numberRecordsProcessed": 1},
                {"state": "JobComplete", "numberRecordsProcessed": 3,
                 "numberRecordsFailed": 0},
            ]
            return job

        client.create_ingest_job = create_and_script
        report = run_ingest(
            client, "Account", FIELDS, ROWS,
            operation="insert", line_ending="LF",
            poll_interval=1.5, max_polls=10,
            sleep=sleeps.append,
        )
        jid = report.job_id

        create = [r for r in org.requests if r[0] == "POST"]
        check(len(create) == 1, "exactly one ingest job must be created")
        check(create[0][1] == BASE,
              "job creation must POST %s, got %s" % (BASE, create[0][1]))
        sent = json.loads(create[0][3].decode("utf-8"))
        check(sent == {"object": "Account", "operation": "insert",
                       "contentType": "CSV", "lineEnding": "LF"},
              "create body must pin object/operation/contentType/lineEnding, got %r" % sent)
        check((create[0][2].get("Content-Type") or "").startswith("application/json"),
              "create must send Content-Type: application/json")

        puts = [r for r in org.requests if r[0] == "PUT"]
        check(len(puts) == 1, "job data must be uploaded exactly once")
        job = org.jobs[jid]
        check(puts[0][1] == "/" + job["contentUrl"],
              "upload must PUT the contentUrl verbatim (got %s, want /%s)"
              % (puts[0][1], job["contentUrl"]))
        ct, uploaded = org.uploads[jid][0]
        check((ct or "").startswith("text/csv"),
              "upload Content-Type must be text/csv, got %r" % ct)
        check(uploaded == EXPECTED_LF_CSV,
              "uploaded CSV bytes differ from the pinned LF contract:\n%r\n!=\n%r"
              % (uploaded, EXPECTED_LF_CSV))

        patches = [r for r in org.requests if r[0] == "PATCH"]
        check(len(patches) == 1, "job must be closed with exactly one PATCH")
        check(patches[0][1] == BASE + "/" + jid,
              "close must PATCH the job resource")
        check(json.loads(patches[0][3].decode("utf-8")) == {"state": "UploadComplete"},
              "close body must be exactly {\"state\": \"UploadComplete\"}")

        info_gets = [r for r in org.requests
                     if r[0] == "GET" and r[1] == BASE + "/" + jid]
        check(len(info_gets) == 3,
              "polling must GET job info once per poll until terminal (got %d)"
              % len(info_gets))
        check(sleeps == [1.5, 1.5],
              "sleep(poll_interval) must run before each poll after the first, got %r"
              % sleeps)

        check(report.job_id == jid, "report.job_id must be the created job id")
        check(report.state == "JobComplete", "report.state must be JobComplete")
        check(report.records_processed == 3, "records_processed must be 3")
        check(report.records_failed == 0, "records_failed must be 0")
        check(report.failed == [], "no failedResults download on a clean job")
        check(report.unprocessed == [], "no unprocessedrecords on a clean job")
        check(org.count("GET", "/failedResults/") == 0,
              "clean job must not fetch failedResults/")
        check(org.count("GET", "/unprocessedrecords/") == 0,
              "clean job must not fetch unprocessedrecords/")
        assert_common_headers(org)
    finally:
        org.stop()


def test_crlf_and_unicode_csv():
    org = FakeOrg().start()
    try:
        client = make_client(org)
        job = client.create_ingest_job("Account", "insert", line_ending="CRLF")
        check(job["lineEnding"] == "CRLF",
              "create_ingest_job must send lineEnding CRLF when asked")
        client.upload_job_data(job, ["Name", "City"], [
            {"Name": "Two\nLines", "City": "Bonn"},
            {"Name": "Grün Söhne GmbH", "City": "Köln"},
        ])
        _, uploaded = org.uploads[job["id"]][0]
        expected = ("Name,City\r\n"
                    '"Two\nLines",Bonn\r\n'
                    "Grün Söhne GmbH,Köln\r\n").encode("utf-8")
        check(uploaded == expected,
              "CRLF job upload must terminate records with \\r\\n and stay UTF-8:"
              "\n%r\n!=\n%r" % (uploaded, expected))
    finally:
        org.stop()


def test_failed_job_downloads_both_result_csvs():
    org = FakeOrg().start()
    try:
        client = make_client(org)
        job = client.create_ingest_job("Account", "insert")
        jid = job["id"]
        org.state_script[jid] = [
            {"state": "InProgress"},
            {"state": "Failed", "numberRecordsProcessed": 2,
             "numberRecordsFailed": 2,
             "errorMessage": "InvalidBatch : Field name not found : Industryy"},
        ]
        org.results[jid] = {
            "failed": (
                "sf__Id,sf__Error,Name,Industry\n"
                ',"REQUIRED_FIELD_MISSING:Required fields are missing: [Industry]:--",Kite Labs,\n'
                '003KB000004xQzKYAU,"DUPLICATES_DETECTED:Use one of these records?:--",Kite West,Energy\n'
            ).encode("utf-8"),
            "unprocessed": "Name,Industry\nHarbor Ltd,Marine\n".encode("utf-8"),
        }
        info = client.get_job_info(jid)
        check(info["state"] == "InProgress",
              "job info polls must surface intermediate states")
        info = client.get_job_info(jid)
        check(info["state"] == "Failed", "scripted job must land on Failed")
        check(info["errorMessage"].startswith("InvalidBatch"),
              "errorMessage must surface from job info")
        failed = client.get_failed_results(jid)
        check(org.count("GET", "/failedResults/") == 1,
              "failedResults/ must be fetched with its trailing slash")
        check(failed == [
            {"sf__Id": "", "sf__Error":
             "REQUIRED_FIELD_MISSING:Required fields are missing: [Industry]:--",
             "Name": "Kite Labs", "Industry": ""},
            {"sf__Id": "003KB000004xQzKYAU", "sf__Error":
             "DUPLICATES_DETECTED:Use one of these records?:--",
             "Name": "Kite West", "Industry": "Energy"},
        ], "failed results must parse sf__Id/sf__Error plus original columns, got %r" % failed)
        unproc = client.get_unprocessed_records(jid)
        check(org.count("GET", "/unprocessedrecords/") == 1,
              "unprocessedrecords/ must be fetched with its trailing slash")
        check(unproc == [{"Name": "Harbor Ltd", "Industry": "Marine"}],
              "unprocessed records must parse the original columns, got %r" % unproc)
    finally:
        org.stop()


def test_run_ingest_failed_state_fetches_results():
    from sf_bulk.ingest import run_ingest
    org = FakeOrg().start()
    try:
        client = make_client(org)
        seen = {}

        def sleep(_):
            if not seen:
                jid = next(iter(org.jobs))
                seen["jid"] = jid

        # Pre-script: the first (only) job created by run_ingest fails.
        # Install the script lazily via a tiny shim around create.
        real_create = client.create_ingest_job

        def create_and_script(*a, **kw):
            job = real_create(*a, **kw)
            jid = job["id"]
            org.state_script[jid] = [
                {"state": "Failed", "numberRecordsProcessed": 0,
                 "numberRecordsFailed": 1, "errorMessage": "boom"},
            ]
            org.results[jid] = {
                "failed": "sf__Id,sf__Error,Name\n,\"X:err:--\",A\n".encode(),
                "unprocessed": "Name\nB\n".encode(),
            }
            return job

        client.create_ingest_job = create_and_script
        report = run_ingest(client, "Account", ["Name"], [{"Name": "A"}],
                            poll_interval=0.1, max_polls=5, sleep=sleep)
        check(report.state == "Failed", "terminal Failed must be reported")
        check(report.error_message == "boom", "errorMessage must be reported")
        check(len(report.failed) == 1 and report.failed[0]["Name"] == "A",
              "Failed job must download and parse failedResults/")
        check(report.unprocessed == [{"Name": "B"}],
              "Failed job must download and parse unprocessedrecords/")
    finally:
        org.stop()


def test_complete_with_failures_fetches_failed_only():
    from sf_bulk.ingest import run_ingest
    org = FakeOrg().start()
    try:
        client = make_client(org)
        real_create = client.create_ingest_job

        def create_and_script(*a, **kw):
            job = real_create(*a, **kw)
            jid = job["id"]
            org.state_script[jid] = [
                {"state": "JobComplete", "numberRecordsProcessed": 2,
                 "numberRecordsFailed": 1},
            ]
            org.results[jid] = {
                "failed": "sf__Id,sf__Error,Name\n,\"Y:err:--\",C\n".encode(),
                "unprocessed": "Name\nD\n".encode(),
            }
            return job

        client.create_ingest_job = create_and_script
        report = run_ingest(client, "Account", ["Name"],
                            [{"Name": "C"}, {"Name": "E"}],
                            poll_interval=0.1, max_polls=5, sleep=lambda s: None)
        check(report.state == "JobComplete", "state must be JobComplete")
        check(report.records_failed == 1, "records_failed must be surfaced")
        check(len(report.failed) == 1 and report.failed[0]["sf__Error"] == "Y:err:--",
              "JobComplete with failures must fetch failedResults/")
        check(org.count("GET", "/unprocessedrecords/") == 0,
              "JobComplete must NOT fetch unprocessedrecords/")
    finally:
        org.stop()


def test_ambiguous_close_already_applied():
    from sf_bulk.ingest import run_ingest
    org = FakeOrg().start()
    try:
        client = make_client(org)
        org.close_mode = "drop_after_apply"
        real_create = client.create_ingest_job

        def create_and_script(*a, **kw):
            job = real_create(*a, **kw)
            org.state_script[job["id"]] = [
                {"state": "UploadComplete"},
                {"state": "JobComplete", "numberRecordsProcessed": 1,
                 "numberRecordsFailed": 0},
            ]
            return job

        client.create_ingest_job = create_and_script
        report = run_ingest(client, "Account", ["Name"], [{"Name": "A"}],
                            poll_interval=0.1, max_polls=8, sleep=lambda s: None)
        check(report.state == "JobComplete",
              "recovery after an applied-but-dropped close must finish the run")
        check(len([r for r in org.requests if r[0] == "POST"]) == 1,
              "ambiguous close recovery must NOT create a second job")
        check(len([r for r in org.requests if r[0] == "PATCH"]) == 1,
              "close already applied server-side: the PATCH must not be repeated"
              " (a repeat would fail with INVALIDJOBSTATE)")
    finally:
        org.stop()


def test_ambiguous_close_not_applied():
    from sf_bulk.ingest import run_ingest
    org = FakeOrg().start()
    try:
        client = make_client(org)
        org.close_mode = "drop_before_apply"
        real_create = client.create_ingest_job

        def create_and_script(*a, **kw):
            job = real_create(*a, **kw)
            org.state_script[job["id"]] = [
                {"state": "Open"},  # the recovery info read sees a still-Open job
                {"state": "JobComplete", "numberRecordsProcessed": 1,
                 "numberRecordsFailed": 0},
            ]
            return job

        client.create_ingest_job = create_and_script
        report = run_ingest(client, "Account", ["Name"], [{"Name": "A"}],
                            poll_interval=0.1, max_polls=8, sleep=lambda s: None)
        check(report.state == "JobComplete",
              "recovery after a dropped, unapplied close must finish the run")
        check(len([r for r in org.requests if r[0] == "POST"]) == 1,
              "recovery must reuse the same job, never create a second one")
        check(len([r for r in org.requests if r[0] == "PATCH"]) == 2,
              "job still Open after the drop: the close PATCH must be re-sent")
        job = org.jobs[report.job_id]
        check(job["state"] in ("UploadComplete", "JobComplete"),
              "the retried close must actually close the job")
    finally:
        org.stop()


def test_api_error_envelope_and_token_hygiene():
    from sf_bulk.errors import BulkApiError
    org = FakeOrg().start()
    try:
        client = make_client(org)
        org.fail_once = ("POST", "/jobs/ingest", 400, error_body(
            "INVALIDENTITY", "Unable to find object: ObscureThing__c"))
        try:
            client.create_ingest_job("ObscureThing__c", "insert")
            check(False, "a 400 error envelope must raise BulkApiError")
        except BulkApiError as exc:
            check(exc.status_code == 400, "BulkApiError.status_code must be 400")
            check(exc.error_code == "INVALIDENTITY",
                  "BulkApiError.error_code must come from the envelope")
            check("Unable to find object" in exc.message,
                  "BulkApiError.message must come from the envelope")
            check(TOKEN not in str(exc) and TOKEN not in repr(exc),
                  "the access token must never appear in exception text")

        org.fail_once = ("GET", "/jobs/ingest/750", 401, error_body(
            "INVALID_SESSION_ID", "Session expired or invalid"))
        job = client.create_ingest_job("Account", "insert")
        try:
            client.get_job_info(job["id"])
            check(False, "a 401 must raise BulkApiError")
        except BulkApiError as exc:
            check(exc.status_code == 401 and exc.error_code == "INVALID_SESSION_ID",
                  "401 INVALID_SESSION_ID must map onto BulkApiError fields")
    finally:
        org.stop()


def test_poll_timeout():
    from sf_bulk.errors import PollTimeout
    from sf_bulk.ingest import run_ingest
    org = FakeOrg().start()
    try:
        client = make_client(org)
        sleeps = []
        real_create = client.create_ingest_job

        def create_and_script(*a, **kw):
            job = real_create(*a, **kw)
            org.state_script[job["id"]] = [{"state": "InProgress"}] * 10
            return job

        client.create_ingest_job = create_and_script
        try:
            run_ingest(client, "Account", ["Name"], [{"Name": "A"}],
                       poll_interval=0.2, max_polls=3, sleep=sleeps.append)
            check(False, "a never-terminal job must raise PollTimeout")
        except PollTimeout:
            pass
        jid = next(iter(org.jobs))
        info_gets = [r for r in org.requests
                     if r[0] == "GET" and r[1] == BASE + "/" + jid]
        check(len(info_gets) == 3, "PollTimeout must fire after max_polls polls")
        check(sleeps == [0.2, 0.2],
              "sleep runs between polls only, got %r" % sleeps)
    finally:
        org.stop()


def test_cross_origin_redirect_never_leaks_token():
    from sf_bulk.errors import BulkApiError, TransportError
    org = FakeOrg().start()
    evil = EvilHost()
    try:
        client = make_client(org)
        job = client.create_ingest_job("Account", "insert")
        org.jobs[job["id"]]["state"] = "Failed"
        org.redirect_failed_to = evil.base_url + "/dump.csv"
        raised = False
        result = None
        try:
            result = client.get_failed_results(job["id"])
        except (BulkApiError, TransportError):
            raised = True
        check(evil.auth_seen is None,
              "the Bearer token must never be forwarded to another origin")
        check(raised or isinstance(result, list),
              "cross-origin redirect: either refuse loudly or follow without credentials")
    finally:
        evil.stop()
        org.stop()


def test_protected_fixtures_are_wired():
    check(SOURCES["research"]["required"] is True,
          "official_sources.json must pin research.required")
    urls = [s["url"] for s in SOURCES["research"]["official_sources"]]
    check(len(urls) >= 2 and all(
        u.startswith("https://developer.salesforce.com/") for u in urls),
        "provenance must point at first-party Salesforce docs")
    check(CONTRACT["api_version"] == "v67.0",
          "the pinned stable API version is v67.0")
    check(CONTRACT["operations"]["close_job"]["body"] == {"state": "UploadComplete"},
          "contract pins the exact close body")
    check(CONTRACT["csv_rules"]["null_value"] == "#N/A",
          "contract pins the documented CSV null token")


def main():
    tests = [
        test_happy_path_insert,
        test_crlf_and_unicode_csv,
        test_failed_job_downloads_both_result_csvs,
        test_run_ingest_failed_state_fetches_results,
        test_complete_with_failures_fetches_failed_only,
        test_ambiguous_close_already_applied,
        test_ambiguous_close_not_applied,
        test_api_error_envelope_and_token_hygiene,
        test_poll_timeout,
        test_cross_origin_redirect_never_leaks_token,
        test_protected_fixtures_are_wired,
    ]
    for t in tests:
        t()
        print("ok  %s" % t.__name__)
    print("PASS  %d tests, %d assertions" % (len(tests), CHECKS))


if __name__ == "__main__":
    main()
