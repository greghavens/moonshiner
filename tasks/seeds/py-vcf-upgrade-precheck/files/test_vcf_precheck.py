"""Acceptance tests for the vcf_precheck package.

Runs a loopback fake SDDC Manager (check-sets / upgradables / tasks subset)
and drives vcf_precheck against it. No network beyond 127.0.0.1, no real
SDDC Manager and no real credentials. The contract the fake enforces is
pinned in docs/contract.json.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "docs", "contract.json"), "r", encoding="utf-8") as fh:
    CONTRACT = json.load(fh)
with open(os.path.join(HERE, "docs", "official_sources.json"), "r", encoding="utf-8") as fh:
    SOURCES = json.load(fh)

TOKEN = "dummy-access-7f3e19c2"  # never a real credential

DOMAIN_ID = "d0a2c9f4-1b2e-4c5d-9a01-3f6b8e7c5a10"
ESX_ID = "e11d3b60-77aa-4c22-9d4e-51f0a9c2b7d3"
SDDC_ID = "a4c81f02-93bd-4e67-b210-6c9dd0f4e881"
QUERY_ID = "3fbc2d17-8a54-49d1-9e0b-77c1a2f6d430"
TASK_ID = "9c41d7aa-5b02-4f6e-8f13-2ad0c3e97b55"

PAGE_OF_UPGRADABLE = {
    "elements": [
        {
            "bundleId": "b7a91c2e-4f30-4b8a-a2d5-90cc11ea6f72",
            "bundleType": "VMWARE_SOFTWARE",
            "resource": {
                "resourceId": ESX_ID,
                "fqdn": "esx01.sfo.rainpole.io",
                "type": "ESXI",
                "name": "esx01",
            },
            "softwareComponents": [{"id": "esx_host-9.1.0", "type": "ESX_HOST"}],
            "status": "AVAILABLE",
            "errors": [],
        },
        {
            "bundleId": "0d2e6b19-5a4c-4f8e-bb37-1e90c2a7d654",
            "bundleType": "VMWARE_SOFTWARE",
            "resource": {
                "resourceId": SDDC_ID,
                "fqdn": "sddc-manager.sfo.rainpole.io",
                "type": "SDDC_MANAGER",
                "name": "sddc-manager",
            },
            "softwareComponents": [{"id": "sddc_manager-9.1.0", "type": "SDDC_MANAGER"}],
            "status": "PENDING",
            "errors": [],
        },
    ],
    "pageMetadata": {"pageNumber": 0, "pageSize": 2, "totalElements": 2, "totalPages": 1},
}

ERROR_INVENTORY = {
    "errorCode": "INVENTORY_CACHE_NOT_SEEDED",
    "message": "The inventory cache is not seeded",
    "remediationMessage": "Wait for inventory sync to finish and retry",
    "referenceToken": "H2M9CQ",
}

ERROR_401 = {
    "errorCode": "UNAUTHORIZED",
    "message": "Authentication required",
    "referenceToken": "Z1B4XA",
}

ERROR_500 = {
    "errorCode": "VCF_SYSTEM_ERROR",
    "message": "Internal server error while reading task",
    "remediationMessage": "Retry the operation. If it persists contact support with the reference token.",
    "referenceToken": "R5T0PQ",
}

QUERY_RESULT = {
    "queryId": QUERY_ID,
    "resources": [
        {
            "resourceName": "esx01",
            "resourceId": ESX_ID,
            "resourceType": "ESXI",
            "resourceVersion": "8.0.3-24022510",
            "domain": {
                "domainId": DOMAIN_ID,
                "domainName": "sfo-m01",
                "domainType": "MANAGEMENT",
            },
            "checkSets": [
                {
                    "checkSetId": "esx-upgrade-readiness",
                    "checkSetName": "ESXi upgrade readiness",
                    "checkSetType": "UPGRADE",
                }
            ],
        }
    ],
}


def make_task(status):
    return {
        "id": TASK_ID,
        "name": "Upgrade precheck of management domain sfo-m01",
        "type": "PRECHECK",
        "status": status,
        "creationTimestamp": "2026-07-16T17:12:03.000Z",
        "subTasks": [],
        "errors": [],
        "resources": [
            {"resourceId": DOMAIN_ID, "type": "SDDC_MANAGER", "name": "sfo-m01"}
        ],
        "isCancellable": True,
        "isRetryable": False,
    }


TASK_FAILED = {
    "id": TASK_ID,
    "name": "Upgrade precheck of management domain sfo-m01",
    "type": "PRECHECK",
    "status": "FAILED",
    "creationTimestamp": "2026-07-16T17:12:03.000Z",
    "completionTimestamp": "2026-07-16T17:19:44.000Z",
    "resolutionStatus": "UNRESOLVED",
    "isCancellable": False,
    "isRetryable": True,
    "resources": [
        {"resourceId": DOMAIN_ID, "type": "SDDC_MANAGER", "name": "sfo-m01"}
    ],
    "errors": [
        {
            "errorCode": "VCF_UPGRADE_PRECHECK_FAILED",
            "errorType": "PRECHECK",
            "message": "Upgrade precheck failed for domain sfo-m01",
            "remediationMessage": "Review the nested precheck failures and remediate each resource",
            "referenceToken": "K3S2AB",
            "causes": [
                {"type": "PrecheckAggregationException", "message": "1 of 12 checks failed"}
            ],
            "nestedErrors": [
                {
                    "errorCode": "ESX_VSAN_HEALTH",
                    "message": "vSAN health check reported warnings on esx01.sfo.rainpole.io",
                    "remediationMessage": "Run vSAN health retest and resolve reported issues",
                    "referenceToken": "N7Q4ZX",
                    "nestedErrors": [
                        {
                            "errorCode": "ESX_DISK_BALANCE",
                            "message": "Disk balance below threshold on disk group 1",
                        }
                    ],
                }
            ],
        }
    ],
    "subTasks": [
        {
            "name": "esx01.sfo.rainpole.io: upgrade readiness",
            "description": "Run check-set esx-upgrade-readiness",
            "status": "FAILED",
            "errors": [
                {
                    "errorCode": "ESX_ENTERED_MAINTENANCE_CHECK",
                    "message": "Host cannot enter maintenance mode with current DRS settings",
                    "remediationMessage": "Set DRS to fully automated or evacuate the host manually",
                    "referenceToken": "P8W1LM",
                }
            ],
        },
        {
            "name": "SDDC Manager: config drift",
            "description": "Run check-set sddc-config-drift",
            "status": "SUCCESSFUL",
            "errors": [],
        },
    ],
}

EXPECTED_FLAT = [
    {
        "source": "task",
        "errorCode": "VCF_UPGRADE_PRECHECK_FAILED",
        "message": "Upgrade precheck failed for domain sfo-m01",
        "remediationMessage": "Review the nested precheck failures and remediate each resource",
        "referenceToken": "K3S2AB",
    },
    {
        "source": "task",
        "errorCode": "ESX_VSAN_HEALTH",
        "message": "vSAN health check reported warnings on esx01.sfo.rainpole.io",
        "remediationMessage": "Run vSAN health retest and resolve reported issues",
        "referenceToken": "N7Q4ZX",
    },
    {
        "source": "task",
        "errorCode": "ESX_DISK_BALANCE",
        "message": "Disk balance below threshold on disk group 1",
        "remediationMessage": None,
        "referenceToken": None,
    },
    {
        "source": "esx01.sfo.rainpole.io: upgrade readiness",
        "errorCode": "ESX_ENTERED_MAINTENANCE_CHECK",
        "message": "Host cannot enter maintenance mode with current DRS settings",
        "remediationMessage": "Set DRS to fully automated or evacuate the host manually",
        "referenceToken": "P8W1LM",
    },
]

ASSESSMENT_FAILURE = {
    "status": "COMPLETED_WITH_FAILURE",
    "validationResult": {
        "errorCode": "CHECK_SET_RUN_FAILED",
        "message": "1 resource reported blocking findings",
        "referenceToken": "K3S2AB",
    },
    "presentedArtifactsMap": {"reportSummary": "1 error, 0 warnings"},
    "discoveryProgress": {"percentageComplete": 100, "progressMessages": []},
    "timestamp": "2026-07-16T17:12:03.000Z",
    "completionTimestamp": "2026-07-16T17:19:44.000Z",
}

ASSESSMENT_SUCCESS = {
    "status": "COMPLETED_WITH_SUCCESS",
    "validationResult": None,
    "presentedArtifactsMap": {"reportSummary": "0 errors, 0 warnings"},
    "discoveryProgress": {"percentageComplete": 100, "progressMessages": []},
    "timestamp": "2026-07-16T17:30:00.000Z",
    "completionTimestamp": "2026-07-16T17:31:10.000Z",
}


class FakeSddc:
    """Scripted loopback SDDC Manager: route table plus request recorder."""

    def __init__(self):
        self.scripts = {}      # (method, path) -> list of (status, json_body)
        self.requests = []     # every request seen, in order
        self.lock = threading.Lock()

    def script(self, method, path, responses):
        with self.lock:
            self.scripts[(method, path)] = list(responses)

    def next_response(self, method, path):
        with self.lock:
            queue = self.scripts.get((method, path))
            if not queue:
                return 404, {"errorCode": "NOT_FOUND", "message": f"no route {method} {path}"}
            if len(queue) > 1:
                return queue.pop(0)
            return queue[0]

    def seen(self, method=None, path=None):
        return [
            r for r in self.requests
            if (method is None or r["method"] == method)
            and (path is None or r["path"] == path)
        ]


def make_handler(fake):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _handle(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            req = {
                "method": self.command,
                "path": parsed.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
            with fake.lock:
                fake.requests.append(req)
            if req["headers"].get("authorization") != f"Bearer {TOKEN}":
                status, payload = 401, ERROR_401
            else:
                status, payload = fake.next_response(self.command, parsed.path)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _handle
        do_POST = _handle

    return Handler


CHECKS = {"n": 0}


def ok(cond, label):
    CHECKS["n"] += 1
    assert cond, f"FAIL: {label}"


def main():
    fake = FakeSddc()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        run_all(fake, base)
    finally:
        server.shutdown()
        server.server_close()

    print(f"all {CHECKS['n']} checks passed")


def run_all(fake, base):
    from vcf_precheck.client import SddcClient
    from vcf_precheck.errors import (
        InventoryNotReadyError,
        SpecError,
        TaskTimeoutError,
        VcfApiError,
    )
    from vcf_precheck.precheck import flatten_errors, run_upgrade_precheck

    ok(issubclass(InventoryNotReadyError, VcfApiError), "InventoryNotReadyError subclasses VcfApiError")
    ok(issubclass(TaskTimeoutError, VcfApiError), "TaskTimeoutError subclasses VcfApiError")

    # --- upgradables discovery -------------------------------------------
    sleeps = []
    client = SddcClient(base, TOKEN, sleep=sleeps.append, poll_interval=2.5, max_polls=10)

    fake.script("GET", "/v1/system/upgradables", [(200, PAGE_OF_UPGRADABLE)])
    ups = client.list_upgradables()
    ok(ups == PAGE_OF_UPGRADABLE["elements"], "list_upgradables returns the elements verbatim")
    ok(ups[0]["resource"]["fqdn"] == "esx01.sfo.rainpole.io", "resource fqdn preserved")
    ok(ups[0]["bundleId"] == PAGE_OF_UPGRADABLE["elements"][0]["bundleId"], "bundleId preserved")
    req = fake.seen("GET", "/v1/system/upgradables")[0]
    ok(req["headers"].get("authorization") == f"Bearer {TOKEN}", "bearer token sent on upgradables GET")
    ok("application/json" in req["headers"].get("accept", ""), "Accept: application/json sent")

    fake.script("GET", "/v1/system/upgradables", [(409, ERROR_INVENTORY)])
    try:
        client.list_upgradables()
        ok(False, "409 must raise InventoryNotReadyError")
    except InventoryNotReadyError as exc:
        ok(exc.status_code == 409, "InventoryNotReadyError carries status 409")
        ok(exc.error_code == "INVENTORY_CACHE_NOT_SEEDED", "409 errorCode preserved")
        ok(exc.reference_token == "H2M9CQ", "409 referenceToken preserved")

    # --- resource descriptor validation ----------------------------------
    good = {
        "resourceType": "ESXI",
        "resourceId": ESX_ID,
        "resourceTargetVersion": "9.1.0",
        "fqdn": "esx01.sfo.rainpole.io",
    }
    for bad_key in ("address", "ipAddress"):
        bad = {"resourceType": "ESXI", "resourceId": ESX_ID, bad_key: "10.0.0.5"}
        before = len(fake.requests)
        try:
            client.query_check_sets(DOMAIN_ID, [bad])
            ok(False, f"descriptor with {bad_key!r} must raise SpecError")
        except SpecError as exc:
            ok("fqdn" in str(exc).lower(), f"SpecError for {bad_key!r} mentions fqdn")
        ok(len(fake.requests) == before, f"{bad_key!r} refused before any HTTP request")

    before = len(fake.requests)
    try:
        client.query_check_sets(DOMAIN_ID, [{"resourceType": "ESXI"}])
        ok(False, "descriptor missing resourceId must raise SpecError")
    except SpecError:
        ok(True, "missing resourceId raises SpecError")
    ok(len(fake.requests) == before, "invalid descriptor never reaches the wire")

    # --- check-set query ---------------------------------------------------
    fake.script("POST", "/v1/system/check-sets/queries", [(200, QUERY_RESULT)])
    qr = client.query_check_sets(DOMAIN_ID, [good])
    ok(qr == QUERY_RESULT, "query result returned verbatim")
    req = fake.seen("POST", "/v1/system/check-sets/queries")[-1]
    ok("application/json" in req["headers"].get("content-type", ""), "query POST content-type json")
    sent = json.loads(req["body"].decode())
    ok(sent == {
        "checkSetType": "UPGRADE",
        "domains": [{
            "domainId": DOMAIN_ID,
            "resources": [{
                "resourceType": "ESXI",
                "resourceId": ESX_ID,
                "resourceTargetVersion": "9.1.0",
            }],
        }],
    }, "CheckSetQueryInput matches the documented shape exactly (fqdn stripped)")

    # --- start run ----------------------------------------------------------
    fake.script("POST", "/v1/system/check-sets", [(202, make_task("PENDING"))])
    task = client.start_precheck(qr, target_version="9.1.0.0")
    ok(task["id"] == TASK_ID, "start_precheck returns the accepted task")
    req = fake.seen("POST", "/v1/system/check-sets")[-1]
    sent = json.loads(req["body"].decode())
    ok(sent["queryId"] == QUERY_ID, "run input echoes queryId")
    ok(sent["resources"] == QUERY_RESULT["resources"], "run input passes query resources through, checkSets intact")
    ok(sent["metadata"] == {"targetVersion": "9.1.0.0"}, "run input metadata.targetVersion set")
    ok(set(sent.keys()) == {"queryId", "resources", "metadata"}, "run input has no stray keys")

    fake.script("POST", "/v1/system/check-sets", [(200, make_task("PENDING"))])
    try:
        client.start_precheck(qr, target_version="9.1.0.0")
        ok(False, "non-202 acceptance must raise VcfApiError")
    except VcfApiError as exc:
        ok(exc.status_code == 200, "unexpected 200 surfaces its status code")

    # --- task polling -------------------------------------------------------
    task_path = f"/v1/tasks/{TASK_ID}"
    fake.script("GET", task_path, [
        (200, make_task("PENDING")),
        (200, make_task("QUEUED")),
        (200, make_task("IN_PROGRESS")),
        (200, TASK_FAILED),
    ])
    sleeps.clear()
    done = client.wait_for_task(TASK_ID)
    ok(done["status"] == "FAILED", "polling stops at terminal FAILED status")
    ok(done == TASK_FAILED, "terminal task returned verbatim, errors and subTasks intact")
    ok(done["errors"][0]["causes"] == TASK_FAILED["errors"][0]["causes"], "causes preserved")
    ok(len(fake.seen("GET", task_path)) == 4, "polled the task endpoint exactly 4 times")
    ok(sleeps == [2.5, 2.5, 2.5], "injected sleep called with poll_interval between polls")

    fake.script("GET", task_path, [(200, make_task("IN_PROGRESS"))])
    fake.requests.clear()
    sleeps.clear()
    short = SddcClient(base, TOKEN, sleep=sleeps.append, poll_interval=1.5, max_polls=3)
    try:
        short.wait_for_task(TASK_ID)
        ok(False, "exhausted max_polls must raise TaskTimeoutError")
    except TaskTimeoutError:
        ok(True, "TaskTimeoutError raised after max_polls reads")
    ok(len(fake.seen("GET", task_path)) == 3, "timeout performed exactly max_polls reads")
    ok(sleeps == [1.5, 1.5], "timeout performed max_polls - 1 sleeps")

    fake.script("GET", task_path, [
        (200, make_task("IN_PROGRESS")),
        (500, ERROR_500),
    ])
    try:
        client.wait_for_task(TASK_ID)
        ok(False, "500 during poll must raise VcfApiError")
    except VcfApiError as exc:
        ok(exc.status_code == 500, "poll error carries status 500")
        ok(exc.error_code == "VCF_SYSTEM_ERROR", "poll error carries errorCode")
        ok(exc.reference_token == "R5T0PQ", "poll error carries referenceToken")
        ok(exc.remediation == ERROR_500["remediationMessage"], "poll error carries remediationMessage")

    # --- bad credentials ----------------------------------------------------
    bad = SddcClient(base, "wrong-token", sleep=sleeps.append, poll_interval=1.0, max_polls=3)
    try:
        bad.list_upgradables()
        ok(False, "401 must raise VcfApiError")
    except VcfApiError as exc:
        ok(exc.status_code == 401, "401 carries status code")
        ok(exc.error_code == "UNAUTHORIZED", "401 errorCode preserved")

    # --- flatten_errors unit contract ----------------------------------------
    ok(flatten_errors(TASK_FAILED) == EXPECTED_FLAT, "flatten_errors: depth-first, task errors then subTask errors")
    ok(flatten_errors(make_task("SUCCESSFUL")) == [], "flatten_errors of a clean task is empty")

    # --- end to end: failure ---------------------------------------------------
    fake.requests.clear()
    fake.script("POST", "/v1/system/check-sets/queries", [(200, QUERY_RESULT)])
    fake.script("POST", "/v1/system/check-sets", [(202, make_task("PENDING"))])
    fake.script("GET", task_path, [
        (200, make_task("IN_PROGRESS")),
        (200, TASK_FAILED),
    ])
    fake.script("GET", f"/v1/system/check-sets/{TASK_ID}", [(200, ASSESSMENT_FAILURE)])
    report = run_upgrade_precheck(client, DOMAIN_ID, [good], target_version="9.1.0.0")
    ok(report["run_id"] == TASK_ID, "report.run_id is the accepted task id")
    ok(report["task"] == TASK_FAILED, "report.task is the terminal task, untrimmed")
    ok(report["assessment"] == ASSESSMENT_FAILURE, "report.assessment fetched from /v1/system/check-sets/{runId}")
    ok(report["errors"] == EXPECTED_FLAT, "report.errors is the flattened error list")
    ok(len(fake.seen("GET", f"/v1/system/check-sets/{TASK_ID}")) == 1, "assessment fetched exactly once")
    order = [(r["method"], r["path"]) for r in fake.requests]
    ok(order[0] == ("POST", "/v1/system/check-sets/queries"), "flow starts with the check-set query")
    ok(order[1] == ("POST", "/v1/system/check-sets"), "then triggers the run")
    ok(order[-1] == ("GET", f"/v1/system/check-sets/{TASK_ID}"), "assessment is fetched last")

    # --- end to end: success ---------------------------------------------------
    fake.script("GET", task_path, [(200, make_task("SUCCESSFUL"))])
    fake.script("GET", f"/v1/system/check-sets/{TASK_ID}", [(200, ASSESSMENT_SUCCESS)])
    report = run_upgrade_precheck(client, DOMAIN_ID, [good], target_version="9.1.0.0")
    ok(report["task"]["status"] == "SUCCESSFUL", "successful run returns terminal SUCCESSFUL task")
    ok(report["assessment"]["status"] == "COMPLETED_WITH_SUCCESS", "assessment status preserved")
    ok(report["errors"] == [], "no errors flattened for a clean run")


if __name__ == "__main__":
    main()
