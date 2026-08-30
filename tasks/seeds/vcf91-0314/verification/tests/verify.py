#!/usr/bin/env python3
"""Deterministic verifier for the VCF Automation deployment-triage task.

Runs entirely against a loopback mock started by this script. No VMware
endpoint, and no network of any kind, is contacted.

It checks three things:

  1. The mock is genuinely pinned to docs/contract.json (self-check).
  2. The triage report the module produces carries the facts that are only
     obtainable by reading the failed request's events and that event's logs.
  3. The exact wire shape of every request the module sent, including that
     optional parameters and body fields the caller did not supply were
     omitted rather than sent empty.
  4. Every operation wrapper, including supplied optional query/body values,
     works independently of the triage orchestrator.

Usage:  python3 tests/verify.py
Exit code 0 on success, 1 on failure.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qsl

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "mock"))

import fixtures  # noqa: E402
import vcfa_mock  # noqa: E402

TOKEN = "mock-access-token"
CONTRACT_PATH = os.path.join(ROOT, "docs", "contract.json")

DEPLOYMENTS_PATH = "/deployment/api/deployments"
REQUESTS_PATH = "/deployment/api/deployments/%s/requests" % fixtures.DEPLOYMENT_ID
EVENTS_PATH = "/deployment/api/requests/%s/events" % fixtures.REQ_FAILED
LOGS_PATH = "%s/%s/logs" % (EVENTS_PATH, fixtures.FAILURE_EVENT_ID)
REMEDIATION_PATH = "/deployment/api/requests/%s" % fixtures.REMEDIATION_REQUEST_ID
WRAPPER_SCRIPT = os.path.join(HERE, "run_wrappers.ps1")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class Results:
    def __init__(self):
        self.failures = []
        self.passes = 0

    def check(self, ok, label, detail=""):
        if ok:
            self.passes += 1
            print("  ok    %s" % label)
        else:
            self.failures.append((label, detail))
            print("  FAIL  %s" % label)
            if detail:
                for line in str(detail).splitlines():
                    print("          %s" % line)
        return ok

    def fatal(self, label, detail=""):
        self.check(False, label, detail)
        self.summary()
        sys.exit(1)

    def summary(self):
        print("")
        print("-" * 68)
        if self.failures:
            print("FAILED: %d check(s) failed, %d passed" % (len(self.failures), self.passes))
            for label, _ in self.failures:
                print("  - %s" % label)
        else:
            print("PASSED: %d checks" % self.passes)
        print("-" * 68)


R = Results()


# ---------------------------------------------------------------------------
# Mock lifecycle
# ---------------------------------------------------------------------------


class Mock:
    def __init__(self, log_path):
        self.server = vcfa_mock.build_server(
            port=0, contract_path=CONTRACT_PATH, request_log_path=log_path, token=TOKEN
        )
        self.port = self.server.server_address[1]
        self.base = "http://127.0.0.1:%d" % self.port
        self.log_path = log_path
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def entries(self):
        if not os.path.exists(self.log_path):
            return []
        out = []
        with open(self.log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return sorted(out, key=lambda e: e["seq"])


def raw_request(base, method, path, body=None, headers=None):
    """Issue a request to the mock and return (status, parsed_json_or_text)."""
    url = base + path
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer %s" % TOKEN)
    req.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            payload = json.loads(payload)
        except ValueError:
            pass
        return exc.code, payload


# ---------------------------------------------------------------------------
# 1. Mock self-check: is it actually pinned to the contract?
# ---------------------------------------------------------------------------


def check_mock_is_pinned():
    print("\n[1/5] mock is pinned to docs/contract.json")
    with tempfile.TemporaryDirectory() as tmp:
        with Mock(os.path.join(tmp, "selfcheck.jsonl")) as mock:
            status, _ = raw_request(mock.base, "GET", "/deployment/api/blueprints")
            R.check(status == 404, "undeclared path is refused", "got %s" % status)

            status, _ = raw_request(mock.base, "DELETE", DEPLOYMENTS_PATH)
            R.check(status == 405, "undeclared method on a declared path is refused",
                    "got %s" % status)

            status, _ = raw_request(mock.base, "GET", DEPLOYMENTS_PATH + "?nickname=x")
            R.check(status == 400, "undeclared query parameter is refused", "got %s" % status)

            status, _ = raw_request(mock.base, "GET", DEPLOYMENTS_PATH + "?name=")
            R.check(status == 400, "empty-valued query parameter is refused", "got %s" % status)

            status, _ = raw_request(
                mock.base, "POST", REQUESTS_PATH,
                body=json.dumps({"actionId": "Deployment.PowerOn", "inputs": {}}),
                headers={"Content-Type": "application/json"},
            )
            R.check(status == 400, "body field sent as an empty object is refused",
                    "got %s" % status)

            status, _ = raw_request(
                mock.base, "POST", REQUESTS_PATH,
                body=json.dumps({"actionId": "Deployment.PowerOn", "reason": "r", "inputs": None}),
                headers={"Content-Type": "application/json"},
            )
            R.check(status == 400, "body field sent as null is refused", "got %s" % status)

            status, _ = raw_request(
                mock.base, "POST", REQUESTS_PATH,
                body=json.dumps({"actionId": "Deployment.PowerOn", "note": "x"}),
                headers={"Content-Type": "application/json"},
            )
            R.check(status == 400, "undeclared body field is refused", "got %s" % status)

            # Auth and negotiation.
            req = urllib.request.Request(mock.base + DEPLOYMENTS_PATH, method="GET")
            req.add_header("Accept", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status = resp.status
            except urllib.error.HTTPError as exc:
                status = exc.code
            R.check(status == 401, "missing bearer token is refused", "got %s" % status)

            # The failure event's logs are reachable; a sibling event's are not.
            status, _ = raw_request(mock.base, "GET", LOGS_PATH)
            R.check(status == 200, "logs exist for the event whose hasLogs is true",
                    "got %s" % status)

            other = fixtures.FAILED_REQUEST_EVENTS[0]["id"]
            status, _ = raw_request(
                mock.base, "GET", "%s/%s/logs" % (EVENTS_PATH, other)
            )
            R.check(status == 404, "logs are absent for an event whose hasLogs is false",
                    "got %s" % status)


# ---------------------------------------------------------------------------
# 2. Module packaging
# ---------------------------------------------------------------------------


def check_packaging():
    print("\n[2/5] module packaging")
    manifest = os.path.join(ROOT, "src", "VcfAutomation.Triage", "VcfAutomation.Triage.psd1")
    R.check(os.path.exists(manifest), "module manifest exists", manifest)
    if not os.path.exists(manifest):
        return

    text = open(manifest, "r", encoding="utf-8").read()
    R.check(
        "ExternalModuleDependencies" in text and "VMware.Sdk.Vcf" in text,
        "manifest declares the VMware.Sdk.Vcf prerequisite as an external dependency",
        "the VCF PowerCLI SDK is installed by the environment; the manifest should "
        "record it without hard-requiring it at import time",
    )

    vendored = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        if ".git" in dirpath:
            continue
        for name in list(dirnames) + list(filenames):
            if name.startswith("VMware.Sdk.Vcf") or name == "VCF.PowerCLI":
                vendored.append(os.path.join(dirpath, name))
    R.check(not vendored, "the VMware SDK is not vendored into the tree", "\n".join(vendored))


# ---------------------------------------------------------------------------
# 3. Run the triage and check the report
# ---------------------------------------------------------------------------


EXPECTED_REPORT = {
    "DeploymentId": fixtures.DEPLOYMENT_ID,
    "DeploymentName": fixtures.DEPLOYMENT_NAME,
    "DeploymentStatus": "UPDATE_FAILED",
    "FailedRequestId": fixtures.REQ_FAILED,
    "FailedActionId": "Deployment.PowerOn",
    "FailureEventId": fixtures.FAILURE_EVENT_ID,
    "FailureEventResourceName": "payments-app-02",
    "CorrelationId": fixtures.CORRELATION_ID,
    "RemediationRequestId": fixtures.REMEDIATION_REQUEST_ID,
    "RemediationRequestStatus": "INPROGRESS",
}


def run_triage(mock, tmp):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        R.fatal("pwsh is available on PATH", "PowerShell 7 is required to run the module")

    out_file = os.path.join(tmp, "report.json")
    proc = subprocess.run(
        [
            pwsh, "-NoProfile", "-NonInteractive",
            "-File", os.path.join(HERE, "run_triage.ps1"),
            "-BaseUri", mock.base,
            "-AccessToken", TOKEN,
            "-DeploymentName", fixtures.DEPLOYMENT_NAME,
            "-OutFile", out_file,
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not os.path.exists(out_file):
        R.fatal(
            "triage run completes",
            "exit=%s\nstdout:\n%s\nstderr:\n%s" % (proc.returncode, proc.stdout, proc.stderr),
        )
    R.check(True, "triage run completes")
    with open(out_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_report(report):
    print("\n[3/5] triage report")
    if not isinstance(report, dict):
        R.fatal("report is a single object", "got %r" % type(report).__name__)

    for key, expected in EXPECTED_REPORT.items():
        actual = report.get(key)
        R.check(
            actual == expected,
            "report.%s" % key,
            "expected %r, got %r" % (expected, actual),
        )

    root_cause = report.get("RootCauseMessage") or ""
    expected_root_cause = next(
        row["message"]
        for row in fixtures.FAILURE_EVENT_LOGS
        if "vCenter task" in row["message"] and "failed:" in row["message"]
    )
    R.check(
        root_cause == expected_root_cause,
        "report.RootCauseMessage is the failing vCenter task log row",
        "expected %r, got %r" % (expected_root_cause, root_cause),
    )


# ---------------------------------------------------------------------------
# 4. Wire shape
# ---------------------------------------------------------------------------


def contract_query_names(op_id):
    with open(CONTRACT_PATH, "r", encoding="utf-8") as fh:
        contract = json.load(fh)
    for op in contract["operations"]:
        if op["id"] == op_id:
            return {p["name"] for p in op.get("queryParams", [])}
    raise KeyError(op_id)


def check_wire(entries):
    print("\n[4/5] triage request wire shape")

    if not entries:
        R.fatal("the module issued requests", "the mock recorded none")

    # -- nothing off-contract, nothing malformed ------------------------
    off_contract = [e for e in entries if e["operationId"] is None]
    R.check(not off_contract, "every request hit a contract operation",
            "\n".join("%s %s -> %s" % (e["method"], e["path"], e["status"]) for e in off_contract))

    bad_status = [e for e in entries if e["status"] in (400, 401, 405, 406, 415)]
    R.check(
        not bad_status,
        "no request was rejected for shape, auth or negotiation",
        "\n".join(
            "seq=%s %s %s?%s -> %s" % (e["seq"], e["method"], e["path"], e["rawQuery"], e["status"])
            for e in bad_status
        ),
    )

    bad_auth = [e for e in entries if e["headers"].get("authorization") != "Bearer %s" % TOKEN]
    R.check(not bad_auth, "every request carried the bearer token",
            "%d request(s) did not" % len(bad_auth))

    bad_accept = [
        e for e in entries
        if "application/json" not in (e["headers"].get("accept") or "")
    ]
    R.check(not bad_accept, "every request set Accept: application/json",
            "%d request(s) did not" % len(bad_accept))

    empty_params = []
    for e in entries:
        for key, value in parse_qsl(e["rawQuery"] or "", keep_blank_values=True):
            if value == "":
                empty_params.append("seq=%s %s?%s" % (e["seq"], e["path"], e["rawQuery"]))
    R.check(
        not empty_params,
        "no query parameter was sent with an empty value",
        "\n".join(empty_params),
    )

    def first(op_id, predicate=None):
        for e in entries:
            if e["operationId"] == op_id and (predicate is None or predicate(e)):
                return e
        return None

    # -- deployment lookup ----------------------------------------------
    lookup = first("getDeployments")
    if not R.check(lookup is not None, "Get Deployments was called"):
        return
    q = dict(parse_qsl(lookup["rawQuery"] or "", keep_blank_values=True))
    R.check(
        q.get("name") == fixtures.DEPLOYMENT_NAME,
        "Get Deployments filtered server-side on name",
        "expected name=%s, got query %r" % (fixtures.DEPLOYMENT_NAME, lookup["rawQuery"]),
    )
    undeclared = set(q) - contract_query_names("getDeployments")
    R.check(not undeclared, "Get Deployments sent only declared parameters",
            "undeclared: %s" % ", ".join(sorted(undeclared)))
    R.check(
        set(q) == {"name"},
        "Get Deployments omitted every unsupplied optional parameter",
        "expected only name, got query %r" % lookup["rawQuery"],
    )

    # -- requests on the deployment --------------------------------------
    reqs = first("getDeploymentRequests")
    if not R.check(reqs is not None, "Get Deployment Requests was called"):
        return
    R.check(
        reqs["path"] == REQUESTS_PATH,
        "Get Deployment Requests targeted the deployment found by name",
        "expected %s, got %s" % (REQUESTS_PATH, reqs["path"]),
    )
    request_calls = [e for e in entries if e["operationId"] == "getDeploymentRequests"]
    request_paging = {"page", "size", "sort", "$top", "$skip", "$orderby"}
    unexpected_request_query = []
    for entry in request_calls:
        keys = {key for key, _ in parse_qsl(entry["rawQuery"] or "", keep_blank_values=True)}
        if keys - request_paging:
            unexpected_request_query.append(entry["rawQuery"])
    R.check(
        not unexpected_request_query,
        "Get Deployment Requests omitted unsupplied service filters",
        "unexpected query strings: %r" % unexpected_request_query,
    )

    # -- events on the failed request -------------------------------------
    event_calls = [e for e in entries
                   if e["operationId"] == "getRequestEvents" and e["path"] == EVENTS_PATH]
    if not R.check(
        event_calls,
        "Get Request Events was called for the FAILED request",
        "the failed request is the one with status FAILED, not the newest one",
    ):
        return

    failure_index = fixtures.FAILED_REQUEST_EVENTS.index(fixtures.FAILURE_EVENT)
    covered = False
    for e in event_calls:
        q = dict(parse_qsl(e["rawQuery"] or "", keep_blank_values=True))
        page = int(q.get("page", 0))
        size = int(q.get("size", 20))
        if page * size <= failure_index < page * size + size:
            covered = True
            break
    R.check(
        covered,
        "the event pages fetched reach the failing event",
        "the failed request has %d events and the failing one is at index %d; with the "
        "documented default page size of 20 it is not on the first page"
        % (len(fixtures.FAILED_REQUEST_EVENTS), failure_index),
    )
    unexpected_event_query = []
    for entry in event_calls:
        keys = {key for key, _ in parse_qsl(entry["rawQuery"] or "", keep_blank_values=True)}
        if keys - {"page", "size", "sort"}:
            unexpected_event_query.append(entry["rawQuery"])
    R.check(
        not unexpected_event_query,
        "Get Request Events omitted unsupplied non-paging parameters",
        "unexpected query strings: %r" % unexpected_event_query,
    )

    # -- logs on the failing event ----------------------------------------
    logs = first("getEventLogs", lambda e: e["path"] == LOGS_PATH and e["status"] == 200)
    if not R.check(
        logs is not None,
        "Get Event Logs was called for the failing event",
        "the diagnosis is only in the log rows of the event whose hasLogs is true",
    ):
        return
    R.check(
        not logs["rawQuery"],
        "Get Event Logs omitted sinceRow when the caller did not supply it",
        "got query %r" % logs["rawQuery"],
    )

    # -- remediation POST --------------------------------------------------
    posts = [e for e in entries if e["operationId"] == "submitDeploymentActionRequest"]
    if not R.check(len(posts) == 1, "exactly one deployment action request was submitted",
                   "got %d" % len(posts)):
        return
    post = posts[0]

    R.check(
        post["path"] == REQUESTS_PATH,
        "the action was submitted against the failed deployment",
        "expected %s, got %s" % (REQUESTS_PATH, post["path"]),
    )
    R.check(
        (post["headers"].get("content-type") or "").split(";")[0].strip() == "application/json",
        "the action request set Content-Type: application/json",
        "got %r" % post["headers"].get("content-type"),
    )
    R.check(
        post["seq"] > logs["seq"],
        "the action was submitted after the logs were read",
        "logs seq=%s, post seq=%s" % (logs["seq"], post["seq"]),
    )

    try:
        body = json.loads(post["rawBody"] or "")
    except ValueError as exc:
        R.check(False, "the action request body is valid JSON", str(exc))
        return
    if not R.check(isinstance(body, dict), "the action request body is a JSON object"):
        return

    R.check(
        set(body.keys()) == {"actionId", "reason"},
        "the action request body carries exactly actionId and reason",
        "ResourceActionRequest also declares the optional 'inputs' field. This action "
        "takes no inputs, so per the contract's wire rules it must be omitted entirely "
        "rather than sent as an empty object or null.\nbody was: %s" % post["rawBody"],
    )
    R.check(
        body.get("actionId") == "Deployment.PowerOn",
        "the action request re-runs the action that failed",
        "expected Deployment.PowerOn, got %r" % body.get("actionId"),
    )
    reason = body.get("reason") or ""
    R.check(
        fixtures.CORRELATION_ID in reason,
        "the reason cites the vCenter correlation id from the failure log",
        "the correlation id appears nowhere but the event log rows\nreason was: %r" % reason,
    )
    R.check(
        fixtures.REQ_FAILED in reason,
        "the reason cites the failed request id",
        "reason was: %r" % reason,
    )

    # -- confirmation ------------------------------------------------------
    confirm = first("getRequest", lambda e: e["path"] == REMEDIATION_PATH)
    R.check(
        confirm is not None and confirm["seq"] > post["seq"],
        "the submitted request was read back with Get Request",
        "expected a GET %s after the POST" % REMEDIATION_PATH,
    )
    if confirm is not None:
        R.check(
            not confirm["rawQuery"],
            "Get Request sent no query string",
            "got query %r" % confirm["rawQuery"],
        )


# ---------------------------------------------------------------------------
# 5. Direct operation-wrapper probe
# ---------------------------------------------------------------------------


def run_wrapper_probe(mock, tmp):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        R.fatal("pwsh is available on PATH", "PowerShell 7 is required to run the module")

    out_file = os.path.join(tmp, "wrapper-report.json")
    proc = subprocess.run(
        [
            pwsh, "-NoProfile", "-NonInteractive",
            "-File", WRAPPER_SCRIPT,
            "-BaseUri", mock.base,
            "-AccessToken", TOKEN,
            "-DeploymentId", fixtures.DEPLOYMENT_ID,
            "-DeploymentName", fixtures.DEPLOYMENT_NAME,
            "-FailedRequestId", fixtures.REQ_FAILED,
            "-FailureEventId", fixtures.FAILURE_EVENT_ID,
            "-OutFile", out_file,
        ],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not os.path.exists(out_file):
        R.fatal(
            "operation-wrapper probe completes",
            "exit=%s\nstdout:\n%s\nstderr:\n%s"
            % (proc.returncode, proc.stdout, proc.stderr),
        )
    R.check(True, "operation-wrapper probe completes")
    with open(out_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_wrapper_probe(report, entries):
    print("\n[5/5] direct operation-wrapper behavior")

    expected_report = {
        "DeploymentIds": [fixtures.DEPLOYMENT_ID],
        "FailedRequestIds": [fixtures.REQ_FAILED],
        "RequestId": fixtures.REQ_FAILED,
        "EventCount": len(fixtures.FAILED_REQUEST_EVENTS),
        "HasFailureEvent": True,
        "FirstReturnedLogRow": 4,
        "SubmittedRequestId": fixtures.REMEDIATION_REQUEST_ID,
        "SubmittedActionId": "Deployment.PowerOff",
    }
    for key, expected in expected_report.items():
        actual = report.get(key)
        R.check(
            actual == expected,
            "wrapper report.%s" % key,
            "expected %r, got %r" % (expected, actual),
        )

    bad = [
        e for e in entries
        if e["operationId"] is None or e["status"] >= 400
        or e["headers"].get("authorization") != "Bearer %s" % TOKEN
        or "application/json" not in (e["headers"].get("accept") or "")
    ]
    R.check(
        not bad,
        "every direct wrapper request was accepted and authenticated",
        "\n".join(
            "seq=%s %s %s?%s -> %s"
            % (e["seq"], e["method"], e["path"], e["rawQuery"], e["status"])
            for e in bad
        ),
    )

    by_operation = {}
    for entry in entries:
        by_operation.setdefault(entry["operationId"], []).append(entry)

    expected_operations = {
        "getDeployments",
        "getDeploymentRequests",
        "getRequest",
        "getRequestEvents",
        "getEventLogs",
        "submitDeploymentActionRequest",
    }
    R.check(
        expected_operations <= set(by_operation),
        "all six operation wrappers issued their contract operation",
        "missing: %s" % ", ".join(sorted(expected_operations - set(by_operation))),
    )

    deployment_call = (by_operation.get("getDeployments") or [None])[0]
    if deployment_call is not None:
        deployment_query = dict(parse_qsl(deployment_call["rawQuery"], keep_blank_values=True))
        expected_query = {
            "name": fixtures.DEPLOYMENT_NAME,
            "status": "CREATE_FAILED,UPDATE_FAILED",
            "search": "payments-uat",
            "page": "0",
            "size": "7",
        }
        R.check(
            deployment_query == expected_query,
            "Get Deployments preserved every supplied optional parameter",
            "expected %r, got %r" % (expected_query, deployment_query),
        )

    request_calls = by_operation.get("getDeploymentRequests") or []
    unexpected_request_filters = []
    for entry in request_calls:
        keys = {key for key, _ in parse_qsl(entry["rawQuery"], keep_blank_values=True)}
        if keys - {"page", "size", "sort", "$top", "$skip", "$orderby"}:
            unexpected_request_filters.append(entry["rawQuery"])
    R.check(
        not unexpected_request_filters,
        "the client-side request Status filter was not sent off-contract",
        "unexpected query strings: %r" % unexpected_request_filters,
    )

    get_request = (by_operation.get("getRequest") or [None])[0]
    if get_request is not None:
        R.check(
            not get_request["rawQuery"],
            "Get Request sent no query parameters",
            "got %r" % get_request["rawQuery"],
        )

    log_call = (by_operation.get("getEventLogs") or [None])[0]
    if log_call is not None:
        R.check(
            dict(parse_qsl(log_call["rawQuery"], keep_blank_values=True)) == {"sinceRow": "4"},
            "Get Event Logs preserved a supplied sinceRow",
            "got %r" % log_call["rawQuery"],
        )

    posts = by_operation.get("submitDeploymentActionRequest") or []
    if R.check(
        len(posts) == 1,
        "the direct Submit wrapper issued exactly one request",
        "got %d" % len(posts),
    ):
        post = posts[0]
        try:
            body = json.loads(post["rawBody"] or "")
        except ValueError as exc:
            R.check(False, "the direct Submit wrapper sent valid JSON", str(exc))
        else:
            R.check(
                body == {
                    "actionId": "Deployment.PowerOff",
                    "reason": "wrapper probe",
                    "inputs": {"force": True},
                },
                "the direct Submit wrapper preserved supplied actionId, reason and inputs",
                "got %r" % body,
            )


# ---------------------------------------------------------------------------


def main():
    print("VCF Automation deployment-triage verification")
    print("=" * 68)

    check_mock_is_pinned()
    check_packaging()

    with tempfile.TemporaryDirectory() as tmp:
        with Mock(os.path.join(tmp, "requests.jsonl")) as mock:
            report = run_triage(mock, tmp)
            entries = mock.entries()
        check_report(report)
        check_wire(entries)

        with Mock(os.path.join(tmp, "wrapper-requests.jsonl")) as mock:
            wrapper_report = run_wrapper_probe(mock, tmp)
            wrapper_entries = mock.entries()
        check_wrapper_probe(wrapper_report, wrapper_entries)

    R.summary()
    return 1 if R.failures else 0


if __name__ == "__main__":
    sys.exit(main())
