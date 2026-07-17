"""Acceptance tests for the ontapvol ONTAP volume provisioner.

Runs a loopback HTTP mock that speaks the pinned ONTAP REST contract
(docs/contract.json) and asserts the exact requests the client sends.
No network, no credentials, no sleeps (pacing is injected).
"""

import base64
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USER = "api-user"
PASS = "dummy-pass-000"
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

JOB1_HREF = "/api/cluster/jobs/job-0001?fields=state,code,message,error"
JOB2_HREF = "/api/cluster/jobs/job-0002?fields=state,code,message,error"
JOB3_HREF = "/api/cluster/jobs/job-0003?fields=state,code,message,error"


class MockState:
    def __init__(self):
        self.routes = {}
        self.log = []

    def reset(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.log = []


STATE = MockState()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _serve(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = None
        if length:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        STATE.log.append({
            "method": method,
            "path": parsed.path,
            "query": urllib.parse.parse_qs(parsed.query),
            "raw": self.path,
            "auth": self.headers.get("Authorization"),
            "body": body,
        })
        queue = STATE.routes.get((method, parsed.path))
        if queue:
            resp = queue.pop(0)
        else:
            resp = (599, {"error": {"code": "0", "message": "UNEXPECTED %s %s" % (method, self.path)}})
        status, doc = resp
        payload = json.dumps(doc).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/hal+json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")


PASSED = 0
FAILED = 0


def check(cond, label):
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        print("FAIL: %s" % label)


def collection(records):
    return (200, {"records": records, "num_records": len(records)})


SVM_HIT = collection([{"uuid": "svm-uuid-1111", "name": "svm_data"}])
AGGR1_HIT = collection([{"uuid": "aggr-uuid-0001", "name": "aggr_ssd_01"}])
AGGR2_HIT = collection([{"uuid": "aggr-uuid-0002", "name": "aggr_ssd_02"}])
VOL_RECORD = {"uuid": "vol-uuid-9f01", "name": "vol_reports", "size": 10737418240, "state": "online"}


def job_running(uuid):
    return (200, {"uuid": uuid, "state": "running", "message": "Creating volume", "code": 0})


def job_success(uuid):
    return (200, {"uuid": uuid, "state": "success", "message": "Complete: Successful", "code": 0})


def make_client_and_provisioner(base_url, max_polls=10):
    from ontapvol import OntapClient, VolumeProvisioner

    sleeps = []
    client = OntapClient(base_url, USER, PASS)
    prov = VolumeProvisioner(client, sleep=sleeps.append, max_polls=max_polls)
    return client, prov, sleeps


def scenario_create_success(base_url):
    from ontapvol import ProvisionSpec

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([]), collection([VOL_RECORD])],
        ("GET", "/api/svm/svms"): [SVM_HIT],
        ("GET", "/api/storage/aggregates"): [AGGR1_HIT, AGGR2_HIT],
        ("POST", "/api/storage/volumes"): [
            (202, {"job": {"uuid": "job-0001", "_links": {"self": {"href": JOB1_HREF}}}}),
        ],
        ("GET", "/api/cluster/jobs/job-0001"): [
            job_running("job-0001"), job_running("job-0001"), job_success("job-0001"),
        ],
    })
    _, prov, sleeps = make_client_and_provisioner(base_url)
    spec = ProvisionSpec(
        name="vol_reports", svm="svm_data",
        aggregates=["aggr_ssd_01", "aggr_ssd_02"],
        size=10737418240, junction_path="/vol_reports",
    )
    result = prov.provision(spec)

    log = STATE.log
    check(len(log) == 9, "create flow issues exactly 9 requests, got %d" % len(log))
    check(all(e["auth"] == AUTH for e in log), "every request carries the basic auth header")
    check(log[0]["method"] == "GET" and log[0]["path"] == "/api/storage/volumes",
          "flow starts with the idempotent volume lookup")
    check(log[0]["query"].get("name") == ["vol_reports"], "lookup filters name=vol_reports")
    check(log[0]["query"].get("svm.name") == ["svm_data"], "lookup filters svm.name=svm_data")
    check(log[0]["query"].get("fields") == ["uuid,name,size,state"],
          "lookup requests fields=uuid,name,size,state")
    check(log[1]["path"] == "/api/svm/svms" and log[1]["query"].get("name") == ["svm_data"],
          "SVM resolved by name via /api/svm/svms")
    check(log[1]["query"].get("fields") == ["uuid,name"], "SVM resolution requests fields=uuid,name")
    check(log[2]["path"] == "/api/storage/aggregates" and log[2]["query"].get("name") == ["aggr_ssd_01"],
          "first aggregate resolved by name")
    check(log[3]["path"] == "/api/storage/aggregates" and log[3]["query"].get("name") == ["aggr_ssd_02"],
          "second aggregate resolved by name, spec order preserved")
    check(log[2]["query"].get("fields") == ["uuid,name"] and log[3]["query"].get("fields") == ["uuid,name"],
          "aggregate resolution requests fields=uuid,name")
    check(log[4]["method"] == "POST" and log[4]["path"] == "/api/storage/volumes",
          "creation POSTs /api/storage/volumes")
    check(log[4]["query"].get("return_timeout") == ["0"], "POST uses return_timeout=0")
    expected_body = {
        "name": "vol_reports",
        "svm": {"uuid": "svm-uuid-1111"},
        "aggregates": [{"uuid": "aggr-uuid-0001"}, {"uuid": "aggr-uuid-0002"}],
        "size": 10737418240,
        "nas": {"path": "/vol_reports"},
    }
    check(log[4]["body"] == expected_body, "POST body matches the pinned contract exactly: %r" % (log[4]["body"],))
    check([e["raw"] for e in log[5:8]] == [JOB1_HREF, JOB1_HREF, JOB1_HREF],
          "job polled on the exact _links.self.href (query string preserved, nothing appended)")
    check(log[8]["method"] == "GET" and log[8]["path"] == "/api/storage/volumes"
          and log[8]["query"] == log[0]["query"],
          "after job success the volume is confirmed with the same lookup query")
    check(sleeps == [1.0, 1.0], "sleep(1.0) between polls only (3 polls -> 2 sleeps), got %r" % (sleeps,))
    check(result["created"] is True, "result reports created=True")
    check(result["uuid"] == "vol-uuid-9f01", "result carries the UUID from the confirming lookup")
    check(result["state"] == "online", "result carries the volume state")


def scenario_idempotent(base_url):
    from ontapvol import ProvisionSpec

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([VOL_RECORD])],
    })
    _, prov, sleeps = make_client_and_provisioner(base_url)
    result = prov.provision(ProvisionSpec(
        name="vol_reports", svm="svm_data", aggregates=["aggr_ssd_01"], size=10737418240,
    ))
    check(len(STATE.log) == 1, "idempotent hit issues exactly one request, got %d" % len(STATE.log))
    check(result["created"] is False, "existing volume reports created=False")
    check(result["uuid"] == "vol-uuid-9f01", "existing volume UUID reused from lookup")
    check(sleeps == [], "no polling for an idempotent hit")


def scenario_job_failure(base_url):
    from ontapvol import ProvisionSpec, JobFailedError

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([])],
        ("GET", "/api/svm/svms"): [SVM_HIT],
        ("GET", "/api/storage/aggregates"): [AGGR1_HIT],
        ("POST", "/api/storage/volumes"): [
            (202, {"job": {"uuid": "job-0002", "_links": {"self": {"href": JOB2_HREF}}}}),
        ],
        ("GET", "/api/cluster/jobs/job-0002"): [
            job_running("job-0002"),
            (200, {"uuid": "job-0002", "state": "failure", "code": 918123,
                   "message": "Insufficient space",
                   "error": {"code": "918123",
                             "message": "Cannot provision volume 'vol_bad': insufficient space in aggregate 'aggr_ssd_01'"}}),
        ],
    })
    _, prov, sleeps = make_client_and_provisioner(base_url)
    spec = ProvisionSpec(name="vol_bad", svm="svm_data", aggregates=["aggr_ssd_01"], size=5368709120)
    err = None
    try:
        prov.provision(spec)
    except JobFailedError as e:
        err = e
    check(err is not None, "terminal job failure raises JobFailedError")
    check(getattr(err, "code", None) == "918123", "JobFailedError.code carries the job error code")
    check("insufficient space" in getattr(err, "message", ""), "JobFailedError.message carries the job error message")
    check(STATE.log[3]["method"] == "POST" and STATE.log[3]["body"] is not None
          and "nas" not in STATE.log[3]["body"],
          "POST body omits the nas key entirely when no junction path is given")
    job_polls = [e for e in STATE.log if e["path"] == "/api/cluster/jobs/job-0002"]
    check(len(job_polls) == 2, "job polled until the failure state, got %d polls" % len(job_polls))
    check(STATE.log[-1]["path"] == "/api/cluster/jobs/job-0002",
          "no volume requests after a failed job (last request is the job poll)")
    check(sleeps == [1.0], "one sleep between the two polls")


def scenario_api_error(base_url):
    from ontapvol import OntapApiError

    STATE.reset({
        ("GET", "/api/storage/volumes"): [
            (400, {"error": {"code": "4", "message": 'Invalid value for field "name"', "target": "name"}}),
        ],
    })
    client, _, _ = make_client_and_provisioner(base_url)
    err = None
    try:
        client.find_volume("svm_data", "vol reports")
    except OntapApiError as e:
        err = e
    check(err is not None, "HTTP 400 with an error body raises OntapApiError")
    check(getattr(err, "status", None) == 400, "OntapApiError.status is the HTTP status")
    check(getattr(err, "code", None) == "4", "OntapApiError.code is the ONTAP error code")
    check(getattr(err, "target", None) == "name", "OntapApiError.target names the offending field")
    check('Invalid value for field "name"' in getattr(err, "message", ""), "OntapApiError.message preserved")
    check(getattr(err, "message", "") in str(err), "str(OntapApiError) includes the ONTAP message")
    check(PASS not in str(err) and PASS not in repr(err), "credentials never leak into error text")


def scenario_post_forbidden(base_url):
    from ontapvol import ProvisionSpec, OntapApiError

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([])],
        ("GET", "/api/svm/svms"): [SVM_HIT],
        ("GET", "/api/storage/aggregates"): [AGGR1_HIT],
        ("POST", "/api/storage/volumes"): [
            (403, {"error": {"code": "6", "message": "not authorized for that command"}}),
        ],
    })
    _, prov, _ = make_client_and_provisioner(base_url)
    err = None
    try:
        prov.provision(ProvisionSpec(name="vol_x", svm="svm_data", aggregates=["aggr_ssd_01"], size=20971520))
    except OntapApiError as e:
        err = e
    check(err is not None and getattr(err, "status", None) == 403, "403 on POST surfaces as OntapApiError")
    check(getattr(err, "code", None) == "6", "permission error code decoded from the HAL error body")
    check(len(STATE.log) == 4, "no job polls or lookups after a rejected POST")
    check(PASS not in str(err), "password absent from the permission error text")


def scenario_resolution_errors(base_url):
    from ontapvol import ProvisionSpec, ResolutionError

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([])],
        ("GET", "/api/svm/svms"): [collection([])],
    })
    _, prov, _ = make_client_and_provisioner(base_url)
    err = None
    try:
        prov.provision(ProvisionSpec(name="vol_y", svm="svm_missing", aggregates=["aggr_ssd_01"], size=20971520))
    except ResolutionError as e:
        err = e
    check(err is not None and "svm_missing" in str(err), "unknown SVM raises ResolutionError naming the SVM")
    check(not any(e["method"] == "POST" for e in STATE.log), "no volume created when SVM resolution fails")

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([])],
        ("GET", "/api/svm/svms"): [SVM_HIT],
        ("GET", "/api/storage/aggregates"): [
            collection([{"uuid": "aggr-uuid-0001", "name": "aggr_dup"},
                        {"uuid": "aggr-uuid-0009", "name": "aggr_dup"}]),
        ],
    })
    _, prov, _ = make_client_and_provisioner(base_url)
    err = None
    try:
        prov.provision(ProvisionSpec(name="vol_y", svm="svm_data", aggregates=["aggr_dup"], size=20971520))
    except ResolutionError as e:
        err = e
    check(err is not None and "aggr_dup" in str(err), "ambiguous aggregate raises ResolutionError naming it")
    check(not any(e["method"] == "POST" for e in STATE.log), "no volume created on ambiguous aggregate")

    STATE.reset({
        ("GET", "/api/storage/volumes"): [
            collection([VOL_RECORD, {"uuid": "vol-uuid-ffff", "name": "vol_reports", "size": 1, "state": "online"}]),
        ],
    })
    client, _, _ = make_client_and_provisioner(base_url)
    err = None
    try:
        client.find_volume("svm_data", "vol_reports")
    except ResolutionError as e:
        err = e
    check(err is not None and "vol_reports" in str(err), "duplicate volume lookup raises ResolutionError naming it")


def scenario_poll_budget(base_url):
    from ontapvol import ProvisionSpec, JobFailedError

    STATE.reset({
        ("GET", "/api/storage/volumes"): [collection([])],
        ("GET", "/api/svm/svms"): [SVM_HIT],
        ("GET", "/api/storage/aggregates"): [AGGR1_HIT],
        ("POST", "/api/storage/volumes"): [
            (202, {"job": {"uuid": "job-0003", "_links": {"self": {"href": JOB3_HREF}}}}),
        ],
        ("GET", "/api/cluster/jobs/job-0003"): [
            job_running("job-0003"), job_running("job-0003"), job_running("job-0003"),
        ],
    })
    _, prov, sleeps = make_client_and_provisioner(base_url, max_polls=3)
    err = None
    try:
        prov.provision(ProvisionSpec(name="vol_slow", svm="svm_data", aggregates=["aggr_ssd_01"], size=20971520))
    except JobFailedError as e:
        err = e
    check(err is not None, "exhausted poll budget raises JobFailedError")
    check(getattr(err, "code", "sentinel") is None, "budget exhaustion has no ONTAP error code")
    check("poll budget" in getattr(err, "message", "") and "3" in getattr(err, "message", ""),
          "budget error names the bound")
    polls = [e for e in STATE.log if e["path"] == "/api/cluster/jobs/job-0003"]
    check(len(polls) == 3, "exactly max_polls polls issued, got %d" % len(polls))
    check(sleeps == [1.0, 1.0], "sleeps only between polls under the budget")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base_url = "http://127.0.0.1:%d" % server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        scenario_create_success(base_url)
        scenario_idempotent(base_url)
        scenario_job_failure(base_url)
        scenario_api_error(base_url)
        scenario_post_forbidden(base_url)
        scenario_resolution_errors(base_url)
        scenario_poll_budget(base_url)
    finally:
        server.shutdown()
        server.server_close()
    print("passed=%d failed=%d" % (PASSED, FAILED))
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
