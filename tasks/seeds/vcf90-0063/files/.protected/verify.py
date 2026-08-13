#!/usr/bin/env python3
"""Protected verifier for vcf90-0063.

For each scenario it starts .protected/mock_vcf_ops.py on an ephemeral 127.0.0.1 port,
runs .protected/invoke-inventory.ps1 against it once, then asserts the objects the module
emitted and the exact wire shape of every request the mock recorded. No VMware endpoint is
contacted. Every expectation is computed from .protected/fixtures/alerts.json and
docs/contract.json, never from the module under test.

Exit code 0 means every scenario passed.
"""

import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MOCK = os.path.join(HERE, "mock_vcf_ops.py")
DRIVER = os.path.join(HERE, "invoke-inventory.ps1")
FIXTURES = os.path.join(HERE, "fixtures", "alerts.json")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")

USER = "svc-vcfops-reader"
PASSWORD = "R3ad-0nly!pw"
TOKEN = "ops-session-4f2c1d8a"

DRIVER_TIMEOUT = 180

# Property names, in order, that each emitted alert object must carry.
EXPECTED_PROPERTIES = [
    "AlertId",
    "ResourceId",
    "AlertLevel",
    "Status",
    "StartTimeUTC",
    "UpdateTimeUTC",
    "AlertDefinitionName",
]


class Failure(Exception):
    pass


def check(condition, message):
    if not condition:
        raise Failure(message)


# --------------------------------------------------------------------------- setup

def load_json(path, ordered=False):
    with open(path) as fh:
        if ordered:
            return json.load(fh, object_pairs_hook=OrderedDict)
        return json.load(fh)


FIXTURE = load_json(FIXTURES)
CONTRACT_DOC = load_json(CONTRACT)
ALERTS = FIXTURE["alerts"]
RESOURCES = {r["name"]: r["id"] for r in FIXTURE["resources"]}

ALERTS_URL = CONTRACT_DOC["operations"]["getAlerts"]["url"]
ACQUIRE_URL = CONTRACT_DOC["operations"]["acquireToken"]["url"]
RELEASE_URL = CONTRACT_DOC["operations"]["releaseToken"]["url"]
VERSION_URL = CONTRACT_DOC["operations"]["getCurrentVersionOfServer"]["url"]
AUTH_HEADER = CONTRACT_DOC["security"]["header"].lower()
AUTH_VALUE = CONTRACT_DOC["security"]["valuePrefix"] + TOKEN
UA_PREFIX = CONTRACT_DOC["client"]["userAgentPrefix"]


def expected_order(resource_ids):
    """The one stable order the task specifies: startTimeUTC asc, then alertId asc ordinal."""
    pool = ALERTS
    if resource_ids:
        wanted = set(resource_ids)
        pool = [a for a in pool if a["resourceId"] in wanted]
    return sorted(pool, key=lambda a: (a["startTimeUTC"], a["alertId"]))


# --------------------------------------------------------------------------- runner

class Mock:
    def __init__(self, workdir, name, fail_page=None):
        self.log = os.path.join(workdir, "%s.requests.jsonl" % name)
        cmd = [sys.executable, MOCK, "--port", "0", "--log", self.log, "--token", TOKEN]
        if fail_page is not None:
            cmd += ["--fail-page", str(fail_page)]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, cwd=ROOT)
        line = self.proc.stdout.readline().strip()
        if not line.startswith("PORT "):
            err = self.proc.stderr.read()
            raise Failure("mock did not start: %r %s" % (line, err))
        self.port = int(line.split()[1])

    def requests(self):
        entries = []
        if os.path.exists(self.log):
            with open(self.log) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        return entries

    def close(self):
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def run_driver(port, workdir, name, page_size=None, auth_source=None, resource_ids=None):
    out = os.path.join(workdir, "%s.result.json" % name)
    cmd = ["pwsh", "-NoLogo", "-NoProfile", "-File", DRIVER,
           "-Port", str(port), "-OutFile", out,
           "-UserName", USER, "-Password", PASSWORD]
    if page_size is not None:
        cmd += ["-PageSize", str(page_size)]
    if auth_source is not None:
        cmd += ["-AuthSource", auth_source]
    if resource_ids:
        cmd += ["-ResourceIdCsv", ",".join(resource_ids)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT,
                          timeout=DRIVER_TIMEOUT)
    payload = None
    if os.path.exists(out):
        payload = load_json(out, ordered=True)
    return proc, payload


# --------------------------------------------------------------------- wire assertions

def group_query(pairs):
    grouped = OrderedDict()
    for key, value in pairs:
        grouped.setdefault(key, []).append(value)
    return grouped


def assert_no_off_contract(requests):
    stray = [(r["method"], r["path"]) for r in requests if r["offContract"]]
    check(not stray, "requests were made to routes the contract does not name: %s" % stray)


def assert_operation_sequence(requests, expected_alert_calls_range):
    ops = [r["operationId"] for r in requests]
    check(len(ops) >= 3, "expected at least three requests, saw %s" % ops)
    check(ops[0] == "acquireToken",
          "the session must open with acquireToken, saw %r as the first request" % ops[0])
    check(ops[-1] == "releaseToken",
          "the session must be closed with releaseToken, saw %r as the last request" % ops[-1])
    check(ops.count("acquireToken") == 1,
          "acquireToken must be called exactly once, saw %d times" % ops.count("acquireToken"))
    check(ops.count("releaseToken") == 1,
          "releaseToken must be called exactly once, saw %d times" % ops.count("releaseToken"))

    # Connect-VcfOpsServer follows its token handshake with getCurrentVersionOfServer. It is
    # the only other operation allowed, it may run at most once, and it belongs to the
    # handshake, so it must come before the first page is read.
    handshakes = [i for i, o in enumerate(ops) if o == "getCurrentVersionOfServer"]
    check(len(handshakes) <= 1,
          "getCurrentVersionOfServer belongs to the connect handshake and must not be repeated, "
          "saw it %d times" % len(handshakes))

    middle = ops[1:-1]
    unexpected = sorted({o for o in middle} - {"getAlerts", "getCurrentVersionOfServer"})
    check(not unexpected,
          "only getAlerts and the connect handshake may sit between acquireToken and "
          "releaseToken, saw %s" % unexpected)

    alert_positions = [i for i, o in enumerate(ops) if o == "getAlerts"]
    check(alert_positions, "no getAlerts request was recorded")
    check(alert_positions == list(range(alert_positions[0], alert_positions[-1] + 1)),
          "the page requests must be contiguous, saw the sequence %s" % ops)
    if handshakes:
        check(handshakes[0] < alert_positions[0],
              "getCurrentVersionOfServer is part of the connect handshake and must precede the "
              "first page request, saw the sequence %s" % ops)

    low, high = expected_alert_calls_range
    check(low <= len(alert_positions) <= high,
          "expected between %d and %d getAlerts requests, saw %d" % (low, high,
                                                                     len(alert_positions)))
    return [r for r in requests if r["operationId"] == "getAlerts"]


def assert_all_succeeded(requests):
    bad = [(r["operationId"] or r["path"], r["status"]) for r in requests
           if r["status"] >= 400]
    check(not bad, "every request must be answered 2xx, but the mock rejected: %s" % bad)


def assert_acquire_token(request, auth_source):
    check(request["method"] == "POST", "acquireToken must be a POST")
    check(request["path"] == ACQUIRE_URL,
          "acquireToken path must be %s, saw %s" % (ACQUIRE_URL, request["path"]))
    check(request["rawQuery"] == "",
          "acquireToken takes no query parameters, saw %r" % request["rawQuery"])
    ctype = request["headers"].get("content-type", "")
    check(ctype.startswith("application/json"),
          "acquireToken must send application/json, saw %r" % ctype)
    check(AUTH_HEADER not in request["headers"],
          "acquireToken is declared unsecured, so it must not carry an %s header" % AUTH_HEADER)
    check(request["body"], "acquireToken must send a username-password body")
    body = json.loads(request["body"])
    check(body.get("username") == USER,
          "acquireToken username must be %r, saw %r" % (USER, body.get("username")))
    check(body.get("password") == PASSWORD,
          "acquireToken must send the supplied password verbatim, saw %r"
          % body.get("password"))
    if auth_source is None:
        check(set(body) <= {"username", "password", "authSource"},
              "acquireToken body carried members outside username-password: %s" % sorted(body))
    else:
        check(body.get("authSource") == auth_source,
              "acquireToken authSource must be %r when -AuthSource is supplied, saw %r"
              % (auth_source, body.get("authSource")))


def assert_release_token(request):
    check(request["method"] == "POST", "releaseToken must be a POST")
    check(request["path"] == RELEASE_URL,
          "releaseToken path must be %s, saw %s" % (RELEASE_URL, request["path"]))
    check(request["rawQuery"] == "",
          "releaseToken takes no query parameters, saw %r" % request["rawQuery"])
    check(request["headers"].get(AUTH_HEADER) == AUTH_VALUE,
          "releaseToken must carry %s: %s, saw %r"
          % (AUTH_HEADER, AUTH_VALUE, request["headers"].get(AUTH_HEADER)))


def assert_version_handshake(request):
    check(request["method"] == "GET", "getCurrentVersionOfServer must be a GET")
    check(request["path"] == VERSION_URL,
          "getCurrentVersionOfServer path must be %s, saw %s"
          % (VERSION_URL, request["path"]))


def assert_get_alerts(requests, page_size, resource_ids, total):
    check(requests, "no getAlerts request was recorded")
    expected_keys = ["page", "pageSize"]
    if resource_ids:
        expected_keys = ["resourceId"] + expected_keys

    pages = []
    for index, request in enumerate(requests):
        where = "getAlerts request %d" % (index + 1)
        check(request["method"] == "GET", "%s must be a GET" % where)
        check(request["path"] == ALERTS_URL,
              "%s path must be %s, saw %s" % (where, ALERTS_URL, request["path"]))
        check(not request["body"], "%s must be bodyless, saw body %r" % (where, request["body"]))
        check("content-type" not in request["headers"],
              "%s is a bodyless GET and must not send Content-Type" % where)
        check(request["headers"].get("accept") == "application/json",
              "%s must send Accept: application/json, saw %r"
              % (where, request["headers"].get("accept")))
        check(request["headers"].get(AUTH_HEADER) == AUTH_VALUE,
              "%s must carry %s: %s, saw %r"
              % (where, AUTH_HEADER, AUTH_VALUE, request["headers"].get(AUTH_HEADER)))
        ua = request["headers"].get("user-agent", "")
        check(ua.startswith(UA_PREFIX),
              "%s must be issued by %s, whose User-Agent starts with %r, saw %r"
              % (where, CONTRACT_DOC["client"]["module"], UA_PREFIX, ua))

        grouped = group_query(request["queryPairs"])
        check(list(grouped) == expected_keys,
              "%s query keys must be exactly %s in that order, saw %s (raw %r). An optional "
              "parameter the caller did not set must be absent, not sent empty."
              % (where, expected_keys, list(grouped), request["rawQuery"]))
        check("id" not in grouped,
              "%s must not send the optional id parameter: this operation is not filtering by "
              "alert identifier" % where)
        blank = [k for k, values in grouped.items() if any(v == "" for v in values)]
        check(not blank, "%s sent %s with an empty value; unset optional parameters are omitted, "
                         "not blanked" % (where, blank))
        check(len(grouped["page"]) == 1 and len(grouped["pageSize"]) == 1,
              "%s must send page and pageSize exactly once each, saw %r"
              % (where, request["rawQuery"]))
        check(grouped["pageSize"][0] == str(page_size),
              "%s must send pageSize=%d, saw %r" % (where, page_size, grouped["pageSize"][0]))
        if resource_ids:
            check(grouped["resourceId"] == list(resource_ids),
                  "%s must repeat resourceId once per supplied value in the supplied order "
                  "(%s), saw %s" % (where, list(resource_ids), grouped["resourceId"]))
        pages.append(int(grouped["page"][0]))

    full = math.ceil(total / page_size) if total else 0
    check(pages == list(range(len(pages))),
          "getAlerts must walk consecutive 0-based pages starting at 0, saw %s" % pages)
    check(full <= len(pages) <= full + 1,
          "%d records at pageSize %d needs %d page requests (or %d if the pager also reads one "
          "empty page to detect the end), saw %d" % (total, page_size, full, full + 1, len(pages)))


# --------------------------------------------------------------------- output assertions

def assert_emitted(payload, resource_ids):
    check(payload is not None, "the driver produced no result file")
    check(payload.get("ok") is True,
          "Get-VcfOpsAlertInventory failed: %s" % payload.get("error"))
    emitted = payload.get("alerts") or []
    expected = expected_order(resource_ids)

    check(len(emitted) == len(expected),
          "the complete collection holds %d alerts, but %d were emitted; every page must be "
          "retrieved" % (len(expected), len(emitted)))

    for index, (got, want) in enumerate(zip(emitted, expected)):
        where = "emitted alert %d" % (index + 1)
        check(isinstance(got, dict), "%s must be an object, saw %r" % (where, got))
        check(list(got) == EXPECTED_PROPERTIES,
              "%s must carry exactly the properties %s in that order, saw %s"
              % (where, EXPECTED_PROPERTIES, list(got)))

    got_ids = [a["AlertId"] for a in emitted]
    want_ids = [a["alertId"] for a in expected]
    if got_ids != want_ids:
        first = next(i for i in range(len(want_ids)) if got_ids[i] != want_ids[i])
        raise Failure(
            "the emitted order is wrong. Alerts must be ordered by StartTimeUTC ascending, then "
            "by AlertId ascending, and the collection is served in no particular order. "
            "Position %d should be %s (StartTimeUTC %d) but was %s."
            % (first + 1, want_ids[first], expected[first]["startTimeUTC"], got_ids[first]))

    for index, (got, want) in enumerate(zip(emitted, expected)):
        where = "emitted alert %d (%s)" % (index + 1, want["alertId"])
        for prop, source in (("ResourceId", "resourceId"),
                             ("AlertLevel", "alertLevel"),
                             ("Status", "status"),
                             ("AlertDefinitionName", "alertDefinitionName")):
            check(got[prop] == want[source],
                  "%s: %s must be %r, saw %r" % (where, prop, want[source], got[prop]))
        for prop, source in (("StartTimeUTC", "startTimeUTC"), ("UpdateTimeUTC", "updateTimeUTC")):
            check(isinstance(got[prop], int) and not isinstance(got[prop], bool),
                  "%s: %s must be a JSON integer, saw %r" % (where, prop, got[prop]))
            check(got[prop] == want[source],
                  "%s: %s must be %d, saw %r" % (where, prop, want[source], got[prop]))

    if resource_ids:
        wanted = set(resource_ids)
        stray = sorted({a["ResourceId"] for a in emitted} - wanted)
        check(not stray,
              "-ResourceId was supplied, so no alert from another resource may be emitted; saw %s"
              % stray)


# --------------------------------------------------------------------------- scenarios

def scenario_collection(workdir, name, page_size, resource_names=None, auth_source=None):
    resource_ids = [RESOURCES[n] for n in (resource_names or [])]
    mock = Mock(workdir, name)
    try:
        proc, payload = run_driver(mock.port, workdir, name, page_size=page_size,
                                   auth_source=auth_source, resource_ids=resource_ids)
    finally:
        mock.close()
    requests = mock.requests()

    if proc.returncode != 0 and payload is None:
        raise Failure("the driver exited %d without a result file.\nstdout:\n%s\nstderr:\n%s"
                      % (proc.returncode, proc.stdout, proc.stderr))

    assert_emitted(payload, resource_ids)
    assert_no_off_contract(requests)
    assert_all_succeeded(requests)

    total = len(expected_order(resource_ids))
    full = math.ceil(total / page_size) if total else 0
    alert_requests = assert_operation_sequence(requests, (full, full + 1))
    assert_acquire_token(requests[0], auth_source)
    for request in requests:
        if request["operationId"] == "getCurrentVersionOfServer":
            assert_version_handshake(request)
    assert_release_token(requests[-1])
    assert_get_alerts(alert_requests, page_size, resource_ids, total)


def scenario_page_failure(workdir):
    """A mid-collection page failure must surface and must still close the session."""
    name = "page-failure"
    page_size = 10
    mock = Mock(workdir, name, fail_page=1)
    try:
        proc, payload = run_driver(mock.port, workdir, name, page_size=page_size)
    finally:
        mock.close()
    requests = mock.requests()

    check(proc.returncode != 0,
          "when a page request fails, Get-VcfOpsAlertInventory must throw rather than return a "
          "partial collection, but the driver exited 0")
    check(payload is not None and payload.get("ok") is False,
          "expected the driver to record a failure, saw %r" % payload)

    assert_no_off_contract(requests)
    check(requests, "no request reached the appliance at all")
    ops = [r["operationId"] for r in requests]
    check(ops[-1] == "releaseToken",
          "the session token must be released even when a page request fails, but the last "
          "request was %r (sequence %s)" % (ops[-1], ops))
    check(ops.count("releaseToken") == 1,
          "releaseToken must be called exactly once, saw %d times" % ops.count("releaseToken"))
    assert_release_token(requests[-1])

    failed = [i for i, r in enumerate(requests)
              if r["operationId"] == "getAlerts" and r["status"] == 500]
    check(failed, "the mock was configured to fail page %d but no getAlerts request was "
                  "answered 500" % 1)
    after = [r["operationId"] for r in requests[failed[0] + 1:]]
    check("getAlerts" not in after,
          "no further page may be requested after a page fails, saw %s afterwards" % after)

    alert_requests = [r for r in requests if r["operationId"] == "getAlerts"]
    check(len(alert_requests) == 2,
          "the pager must stop on the first failing page, so exactly two getAlerts requests are "
          "expected (page 0 then the failing page 1), saw %d" % len(alert_requests))
    check(alert_requests[-1]["status"] == 500,
          "the last getAlerts request should be the failing one")


SCENARIOS = [
    ("unfiltered pageSize 10 (27 alerts, 3 full pages)",
     lambda wd: scenario_collection(wd, "page10", 10)),
    ("unfiltered pageSize 9 (27 alerts, exact multiple)",
     lambda wd: scenario_collection(wd, "page9", 9)),
    ("unfiltered pageSize 4 (many short pages)",
     lambda wd: scenario_collection(wd, "page4", 4)),
    ("filtered to two resources, pageSize 4",
     lambda wd: scenario_collection(wd, "filtered", 4,
                                    resource_names=["esx-host-07", "nsx-edge-node-02"])),
    ("named auth source, single page",
     lambda wd: scenario_collection(wd, "authsource", 100, auth_source="vidm-prod")),
    ("page request fails mid-collection",
     scenario_page_failure),
]


def preflight():
    check(shutil.which("pwsh") is not None, "pwsh is not on PATH")
    probe = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command",
         "if (Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Ops) { 'yes' } else { 'no' }"],
        capture_output=True, text=True, timeout=300)
    check("yes" in probe.stdout,
          "the VMware.Sdk.Vcf.Ops module is not installed. The environment provides it as a "
          "prerequisite; it is not vendored by this task.\n%s%s" % (probe.stdout, probe.stderr))


def main():
    try:
        preflight()
    except Failure as exc:
        print("PREFLIGHT FAILED: %s" % exc)
        return 2

    workdir = tempfile.mkdtemp(prefix="vcf90-0063-")
    failures = []
    try:
        for title, run in SCENARIOS:
            try:
                run(workdir)
            except Failure as exc:
                failures.append((title, str(exc)))
                print("FAIL  %s\n        %s" % (title, str(exc).replace("\n", "\n        ")))
            except subprocess.TimeoutExpired:
                failures.append((title, "the driver did not finish within %ds; the pager is "
                                        "probably not terminating" % DRIVER_TIMEOUT))
                print("FAIL  %s\n        driver timed out after %ds" % (title, DRIVER_TIMEOUT))
            except Exception as exc:  # never let a scenario abort the whole suite
                detail = "%s: %s" % (type(exc).__name__, exc)
                failures.append((title, detail))
                print("FAIL  %s\n        %s" % (title, detail))
            else:
                print("ok    %s" % title)
    finally:
        if not failures:
            shutil.rmtree(workdir, ignore_errors=True)

    print("")
    if failures:
        print("%d of %d scenarios failed. Artifacts kept in %s"
              % (len(failures), len(SCENARIOS), workdir))
        return 1
    print("all %d scenarios passed" % len(SCENARIOS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
