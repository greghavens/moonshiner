#!/usr/bin/env python3
"""Protected verifier for the VCF Operations 9.1 alert sweep.

Drives `python3 -m vcfops_alerts` against the loopback mock in tests/mock_vcfops.py and
judges the run from the mock's request log plus the report the tool wrote.  No live
VMware endpoint is contacted.

Five deterministic scenarios are checked:

  A  no auth source, two filters set, token dies mid-pagination and again mid-detail
  B  auth source set, no filters at all, token dies mid-detail
  C  direct client call with explicit false and empty schema-valid values
  D  direct client call without an initial token (no request and no automatic acquire)
  E  direct client call whose replacement token is also refused (one retry only)

For each scenario the log must match an exact request sequence, and every request body must
carry exactly the properties the caller asked for -- an optional field the caller left
unset has to be absent, not present as null, "" or [].
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
MOCK = os.path.join(ROOT, "tests", "mock_vcfops.py")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
SOURCES = os.path.join(ROOT, "docs", "official_sources.json")

sys.path.insert(0, SRC)
from vcfops_alerts.client import TokenExpired, VcfOperationsClient

USERNAME = "svc-ops"
PASSWORD = "0ps-Passw0rd!"
AUTH_SOURCE = "vIDM-Corp"

SPEC_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
OPERATION_IDS = ["acquireToken", "queryAlert", "getAlert"]

A1 = "31eeaeec-82d5-4037-a59b-efed2e7c8e3a"  # CRITICAL / ACTIVE
A2 = "6b3d5f21-9c4e-4b83-8f0a-1d2e3c4b5a60"  # WARNING / ACTIVE
A3 = "b7c8d9e0-1234-4a5b-9c6d-7e8f90a1b2c3"  # IMMEDIATE / ACTIVE
A4 = "c2d3e4f5-6a7b-4c8d-9e0f-1a2b3c4d5e6f"  # CRITICAL / UPDATED
A5 = "d4e5f6a7-b8c9-4d0e-8f1a-2b3c4d5e6f70"  # INFORMATION / ACTIVE
A6 = "e6f7a8b9-c0d1-4e2f-8a3b-4c5d6e7f8091"  # IMMEDIATE / NEW
A7 = "f8a9b0c1-d2e3-4f40-8516-273849a0b1c2"  # CRITICAL / CANCELED

DETAIL_ONLY_FIELDS = ("alertDefinitionId", "alertImpact", "statKey")

FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def fail(message):
    FAILURES.append(message)


# ---------------------------------------------------------------- harness


def free_wait(port_file, proc, deadline=20.0):
    end = time.time() + deadline
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("mock server exited early: %s" % proc.stderr.read().decode())
        if os.path.exists(port_file):
            text = open(port_file, encoding="utf-8").read().strip()
            if text:
                port = int(text)
                with socket.socket() as s:
                    s.settimeout(0.5)
                    try:
                        s.connect(("127.0.0.1", port))
                        return port
                    except OSError:
                        pass
        time.sleep(0.05)
    raise RuntimeError("mock server did not come up in time")


def run_scenario(name, expire_after, cli_args, workdir):
    """Start the mock, run the tool once, return (log_entries, report_or_None, proc)."""
    log_path = os.path.join(workdir, "requests-%s.jsonl" % name)
    port_file = os.path.join(workdir, "port-%s" % name)
    report_path = os.path.join(workdir, "report-%s.json" % name)

    mock = subprocess.Popen(
        [sys.executable, MOCK, "--log", log_path, "--port-file", port_file,
         "--expire-after", expire_after],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        port = free_wait(port_file, mock)
        base_url = "http://127.0.0.1:%d/suite-api" % port
        env = dict(os.environ)
        env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "vcfops_alerts",
             "--base-url", base_url,
             "--username", USERNAME,
             "--password", PASSWORD,
             "--output", report_path] + cli_args,
            cwd=workdir,
            env=env,
            capture_output=True,
            timeout=60,
        )
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mock.kill()

    entries = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
    entries.sort(key=lambda e: e["seq"])

    report = None
    if os.path.exists(report_path):
        try:
            report = json.load(open(report_path, encoding="utf-8"))
        except ValueError as exc:
            fail("[%s] report is not valid JSON: %s" % (name, exc))

    if proc.returncode != 0:
        fail("[%s] `python3 -m vcfops_alerts` exited %d\nstdout:\n%s\nstderr:\n%s"
             % (name, proc.returncode, proc.stdout.decode()[-3000:], proc.stderr.decode()[-3000:]))
    return entries, report


def run_client_scenario(name, expire_after, auth_source, exercise, workdir):
    """Run a direct public-client exercise and return (wire log, client, exception)."""
    log_path = os.path.join(workdir, "requests-%s.jsonl" % name)
    port_file = os.path.join(workdir, "port-%s" % name)
    mock = subprocess.Popen(
        [sys.executable, MOCK, "--log", log_path, "--port-file", port_file,
         "--expire-after", expire_after],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    client = None
    caught = None
    try:
        port = free_wait(port_file, mock)
        client = VcfOperationsClient(
            "http://127.0.0.1:%d/suite-api" % port,
            USERNAME,
            PASSWORD,
            auth_source=auth_source,
        )
        try:
            exercise(client)
        except Exception as exc:  # the scenario judges the exact exception below
            caught = exc
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mock.kill()

    entries = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
    entries.sort(key=lambda e: e["seq"])
    return entries, client, caught


# ---------------------------------------------------------------- helpers


def key_of(entry):
    """A stable identifier for 'which request is this'."""
    op = entry.get("operationId")
    if op == "queryAlert":
        page = entry.get("query", {}).get("page", ["<missing>"])[0]
        return "queryAlert(page=%s)" % page
    if op == "getAlert":
        return "getAlert(%s)" % entry.get("alert_id")
    if op == "acquireToken":
        return "acquireToken"
    return "UNKNOWN(%s %s)" % (entry.get("method"), entry.get("path"))


def empties_in(node, path="body"):
    """Every place an unset optional was encoded instead of omitted."""
    bad = []
    if node is None:
        return [path + " is null"]
    if isinstance(node, str) and node == "":
        return [path + ' is ""']
    if isinstance(node, list):
        if not node:
            return [path + " is []"]
        for i, item in enumerate(node):
            bad += empties_in(item, "%s[%d]" % (path, i))
        return bad
    if isinstance(node, dict):
        for k, v in sorted(node.items()):
            bad += empties_in(v, "%s.%s" % (path, k))
        return bad
    return bad


def assert_sequence(name, entries, expected):
    """entries must be exactly `expected`, a list of (key, status)."""
    actual = [(key_of(e), e.get("status")) for e in entries]
    if actual == expected:
        return True
    lines = ["[%s] the request sequence the client put on the wire is not the expected one."
             % name, "  expected:"]
    lines += ["    %2d. %-28s -> %s" % (i + 1, k, s) for i, (k, s) in enumerate(expected)]
    lines.append("  actual:")
    lines += ["    %2d. %-28s -> %s" % (i + 1, k, s) for i, (k, s) in enumerate(actual)]
    fail("\n".join(lines))
    return False


def check_common_wire_rules(name, entries):
    """Rules that hold for every request in every run."""
    for e in entries:
        tag = "[%s] request #%d %s" % (name, e["seq"], key_of(e))

        if e.get("unknown_operation"):
            fail("%s hit an operation this contract does not name (%s %s); the client must "
                 "use only acquireToken, queryAlert and getAlert"
                 % (tag, e.get("method"), e.get("path")))
            continue

        op = e.get("operationId")
        if op == "acquireToken":
            check(e.get("authorization") is None,
                  "%s sent an Authorization header, but acquireToken is unauthenticated in the "
                  "contract (security: []); it carried %r" % (tag, e.get("authorization")))
            check(e.get("raw_query") == "",
                  "%s sent query parameters; acquireToken declares none" % tag)
        else:
            auth = e.get("authorization") or ""
            check(auth.startswith("OpsToken "),
                  "%s must send Authorization: 'OpsToken <token>' as pinned by "
                  "security.value_format in docs/contract.json; it sent %r" % (tag, auth))

        if e.get("method") == "POST":
            ctype = (e.get("content_type") or "").split(";")[0].strip()
            check(ctype == "application/json",
                  "%s sent Content-Type %r; the contract fixes application/json" % (tag, ctype))
            check(e.get("body") is not None,
                  "%s sent a body that is not a JSON object: %r" % (tag, e.get("raw_body")))

        if op == "getAlert":
            check(e.get("raw_query") == "",
                  "%s sent query parameters; getAlert declares only the {id} path parameter" % tag)
            check(not (e.get("raw_body") or ""),
                  "%s sent a request body; getAlert declares none" % tag)

        if op == "queryAlert":
            params = set(e.get("query") or {})
            check(params == {"page", "pageSize"},
                  "%s sent query parameters %s; queryAlert declares exactly page and pageSize, "
                  "and a sweep must set both explicitly rather than lean on the spec defaults"
                  % (tag, sorted(params)))


def check_token_freshness(name, entries):
    """No request may carry a token older than the newest one acquired before it."""
    newest = None
    for e in entries:
        tag = "[%s] request #%d %s" % (name, e["seq"], key_of(e))
        if e.get("operationId") == "acquireToken":
            if e.get("status") == 200:
                newest = e.get("issued_token")
            continue
        if e.get("unknown_operation"):
            continue
        auth = e.get("authorization") or ""
        presented = auth[len("OpsToken "):].strip() if auth.startswith("OpsToken ") else None
        check(presented == newest,
              "%s presented token %r but the newest token acquired at that point was %r; after a "
              "refresh every later request must use the new token" % (tag, presented, newest))


def check_refresh_resumes(name, entries):
    """Every 401 must be answered by exactly one acquireToken and then the same request again."""
    seen_401 = 0
    for i, e in enumerate(entries):
        if e.get("status") != 401 or e.get("operationId") == "acquireToken":
            continue
        seen_401 += 1
        tag = "[%s] request #%d %s" % (name, e["seq"], key_of(e))
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        after = entries[i + 2] if i + 2 < len(entries) else None
        if not check(nxt is not None and nxt.get("operationId") == "acquireToken",
                     "%s was answered 401 but the client's next request was %s, not acquireToken"
                     % (tag, key_of(nxt) if nxt else "nothing")):
            continue
        check(after is not None and key_of(after) == key_of(e),
              "%s was answered 401; after refreshing, the client must retry that same request. "
              "It sent %s instead, which means the work already done was thrown away"
              % (tag, key_of(after) if after else "nothing"))
    check(seen_401 >= 1,
          "[%s] the mock never expired a token, so the refresh path was not exercised" % name)


def check_no_repeated_success(name, entries):
    """A page or an alert detail must not be fetched successfully more than once."""
    served = {}
    for e in entries:
        if e.get("status") != 200 or e.get("operationId") == "acquireToken":
            continue
        k = key_of(e)
        served[k] = served.get(k, 0) + 1
    for k, n in sorted(served.items()):
        check(n == 1,
              "[%s] %s was fetched successfully %d times; a token refresh must resume the sweep, "
              "not restart it" % (name, k, n))


def check_bodies(name, entries, acquire_body, query_body):
    for e in entries:
        op = e.get("operationId")
        tag = "[%s] request #%d %s" % (name, e["seq"], key_of(e))
        if op == "acquireToken":
            check(e.get("body") == acquire_body,
                  "%s body was %s; expected exactly %s (an unset optional property of "
                  "`username-password` must be absent, not null or \"\")"
                  % (tag, json.dumps(e.get("body"), sort_keys=True),
                     json.dumps(acquire_body, sort_keys=True)))
        elif op == "queryAlert":
            check(e.get("body") == query_body,
                  "%s body was %s; expected exactly %s (every property of `alert-query` is "
                  "optional, so the ones the caller did not set must be absent, not null, \"\" "
                  "or [])" % (tag, json.dumps(e.get("body"), sort_keys=True),
                              json.dumps(query_body, sort_keys=True)))
        else:
            continue
        body = e.get("body")
        if isinstance(body, dict) and body:
            for problem in empties_in(body):
                fail("%s encoded an unset optional field instead of omitting it: %s"
                     % (tag, problem))


def check_report(name, report, entries, total, pages, alert_ids, detail_ids):
    if report is None:
        fail("[%s] the tool wrote no report" % name)
        return
    check(report.get("totalCount") == total,
          "[%s] report totalCount was %r; the mock reported %d" % (name, report.get("totalCount"), total))
    check(report.get("pagesFetched") == pages,
          "[%s] report pagesFetched was %r; expected %d" % (name, report.get("pagesFetched"), pages))
    check(report.get("alertIds") == alert_ids,
          "[%s] report alertIds was %s; expected %s -- no alert collected before the token "
          "expired may go missing" % (name, report.get("alertIds"), alert_ids))
    check(report.get("detailOrder") == detail_ids,
          "[%s] report detailOrder was %s; expected %s"
          % (name, report.get("detailOrder"), detail_ids))

    details = report.get("details")
    if not check(isinstance(details, dict), "[%s] report details is not an object" % name):
        return
    check(sorted(details) == sorted(detail_ids),
          "[%s] report details covers %s; expected %s" % (name, sorted(details), sorted(detail_ids)))
    for alert_id in detail_ids:
        record = details.get(alert_id)
        if not check(isinstance(record, dict), "[%s] detail for %s is missing" % (name, alert_id)):
            continue
        missing = [f for f in DETAIL_ONLY_FIELDS if f not in record]
        check(not missing,
              "[%s] detail for %s lacks %s, so it was not taken from a getAlert response"
              % (name, alert_id, missing))

    acquires = sum(1 for e in entries
                   if e.get("operationId") == "acquireToken" and e.get("status") == 200)
    check(report.get("tokenAcquisitions") == acquires,
          "[%s] report tokenAcquisitions was %r but the mock issued %d tokens"
          % (name, report.get("tokenAcquisitions"), acquires))


# ---------------------------------------------------------------- sources


def check_provenance():
    try:
        contract = json.load(open(CONTRACT, encoding="utf-8"))
        sources = json.load(open(SOURCES, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("docs/contract.json and docs/official_sources.json must both be readable JSON: %s" % exc)
        return

    src = contract.get("source", {})
    check(src.get("commit_sha") == SPEC_SHA,
          "docs/contract.json source.commit_sha is %r, expected %r" % (src.get("commit_sha"), SPEC_SHA))
    check(src.get("spec_path") == SPEC_PATH,
          "docs/contract.json source.spec_path is %r, expected %r" % (src.get("spec_path"), SPEC_PATH))
    check(contract.get("base_path") == "/suite-api",
          "docs/contract.json base_path is %r, expected '/suite-api'" % contract.get("base_path"))
    check([op.get("operationId") for op in contract.get("operations", [])] == OPERATION_IDS,
          "docs/contract.json must name exactly %s" % OPERATION_IDS)

    check(sources.get("repository_commit_sha") == SPEC_SHA,
          "docs/official_sources.json repository_commit_sha is %r, expected %r"
          % (sources.get("repository_commit_sha"), SPEC_SHA))
    check(sources.get("spec_path") == SPEC_PATH,
          "docs/official_sources.json spec_path is %r, expected %r"
          % (sources.get("spec_path"), SPEC_PATH))
    check([op.get("operationId") for op in sources.get("operations", [])] == OPERATION_IDS,
          "docs/official_sources.json must record exactly %s" % OPERATION_IDS)


# ---------------------------------------------------------------- runs


def scenario_a(workdir):
    """No auth source; activeOnly + two criticalities; token dies twice."""
    name = "A"
    entries, report = run_scenario(
        name,
        "1,3",
        ["--page-size", "3", "--active-only",
         "--criticality", "CRITICAL", "--criticality", "IMMEDIATE"],
        workdir,
    )
    if not entries:
        fail("[A] the mock recorded no requests at all")
        return

    assert_sequence(name, entries, [
        ("acquireToken", 200),
        ("queryAlert(page=0)", 200),
        ("queryAlert(page=1)", 401),
        ("acquireToken", 200),
        ("queryAlert(page=1)", 200),
        ("getAlert(%s)" % A1, 200),
        ("getAlert(%s)" % A3, 200),
        ("getAlert(%s)" % A4, 401),
        ("acquireToken", 200),
        ("getAlert(%s)" % A4, 200),
        ("getAlert(%s)" % A6, 200),
    ])
    check_common_wire_rules(name, entries)
    check_token_freshness(name, entries)
    check_refresh_resumes(name, entries)
    check_no_repeated_success(name, entries)
    check_bodies(
        name, entries,
        acquire_body={"username": USERNAME, "password": PASSWORD},
        query_body={"activeOnly": True, "alertCriticality": ["CRITICAL", "IMMEDIATE"]},
    )
    for e in entries:
        if e.get("operationId") == "queryAlert":
            size = (e.get("query") or {}).get("pageSize", [None])[0]
            check(size == "3", "[A] queryAlert sent pageSize=%r; the caller asked for 3" % size)
    check_report(name, report, entries, total=4, pages=2,
                 alert_ids=[A1, A3, A4, A6], detail_ids=[A1, A3, A4, A6])


def scenario_b(workdir):
    """Auth source set; no filters at all, so the alert-query body is {}."""
    name = "B"
    entries, report = run_scenario(
        name,
        "5",
        ["--page-size", "4", "--auth-source", AUTH_SOURCE],
        workdir,
    )
    if not entries:
        fail("[B] the mock recorded no requests at all")
        return

    assert_sequence(name, entries, [
        ("acquireToken", 200),
        ("queryAlert(page=0)", 200),
        ("queryAlert(page=1)", 200),
        ("getAlert(%s)" % A1, 200),
        ("getAlert(%s)" % A3, 200),
        ("getAlert(%s)" % A4, 200),
        ("getAlert(%s)" % A6, 401),
        ("acquireToken", 200),
        ("getAlert(%s)" % A6, 200),
        ("getAlert(%s)" % A7, 200),
    ])
    check_common_wire_rules(name, entries)
    check_token_freshness(name, entries)
    check_refresh_resumes(name, entries)
    check_no_repeated_success(name, entries)
    check_bodies(
        name, entries,
        acquire_body={"username": USERNAME, "password": PASSWORD, "authSource": AUTH_SOURCE},
        query_body={},
    )
    check_report(name, report, entries, total=7, pages=2,
                 alert_ids=[A1, A2, A3, A4, A5, A6, A7], detail_ids=[A1, A3, A4, A6, A7])


def scenario_c(workdir):
    """Explicit false and empty schema-valid values are present, not mistaken for unset."""
    name = "C"

    def exercise(client):
        client.acquire_token()
        client.query_alerts(
            0,
            1,
            filters={"active_only": False, "criticality": [], "alert_name": ""},
        )

    entries, client, caught = run_client_scenario(
        name, "", auth_source="", exercise=exercise, workdir=workdir
    )
    check(caught is None, "[C] direct client exercise raised unexpectedly: %r" % caught)
    assert_sequence(name, entries, [
        ("acquireToken", 200),
        ("queryAlert(page=0)", 200),
    ])
    check_common_wire_rules(name, entries)
    check_token_freshness(name, entries)
    check(client is not None and client.token_acquisitions == 1,
          "[C] direct client should acquire exactly one token")
    expected = {
        "acquireToken": {
            "username": USERNAME,
            "password": PASSWORD,
            "authSource": "",
        },
        "queryAlert": {
            "activeOnly": False,
            "alertCriticality": [],
            "alertName": "",
        },
    }
    for e in entries:
        op = e.get("operationId")
        check(e.get("body") == expected.get(op),
              "[C] %s body was %s; an explicitly supplied value must be sent as given, "
              "including false, an empty list, or an empty string"
              % (op, json.dumps(e.get("body"), sort_keys=True)))


def scenario_d(workdir):
    """A missing initial token is not a 401 and therefore must not trigger authentication."""
    name = "D"

    def exercise(client):
        client.query_alerts(0, 1)

    entries, client, caught = run_client_scenario(
        name, "", auth_source=None, exercise=exercise, workdir=workdir
    )
    check(isinstance(caught, TokenExpired),
          "[D] queryAlert without an acquired token must raise TokenExpired, got %r" % caught)
    check(not entries,
          "[D] queryAlert without an acquired token made requests %s; automatic refresh is "
          "allowed only after an HTTP 401" % [key_of(e) for e in entries])
    check(client is not None and client.token_acquisitions == 0,
          "[D] queryAlert without an acquired token must not acquire one automatically")


def scenario_e(workdir):
    """If the retried request also gets 401, surface TokenExpired without another refresh."""
    name = "E"

    def exercise(client):
        client.acquire_token()
        client.query_alerts(0, 1)

    entries, client, caught = run_client_scenario(
        name, "0,0", auth_source=None, exercise=exercise, workdir=workdir
    )
    check(isinstance(caught, TokenExpired) and caught.status == 401,
          "[E] a replacement token rejected with 401 must surface TokenExpired, got %r" % caught)
    assert_sequence(name, entries, [
        ("acquireToken", 200),
        ("queryAlert(page=0)", 401),
        ("acquireToken", 200),
        ("queryAlert(page=0)", 401),
    ])
    check_common_wire_rules(name, entries)
    check_token_freshness(name, entries)
    check(client is not None and client.token_acquisitions == 2,
          "[E] the client must stop after one replacement token per request")
    check_bodies(
        name, entries,
        acquire_body={"username": USERNAME, "password": PASSWORD},
        query_body={},
    )


def main():
    check_provenance()
    workdir = tempfile.mkdtemp(prefix="vcfops-verify-")
    try:
        scenario_a(workdir)
        scenario_b(workdir)
        scenario_c(workdir)
        scenario_d(workdir)
        scenario_e(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if FAILURES:
        print("FAIL: %d problem(s)\n" % len(FAILURES))
        for i, message in enumerate(FAILURES, 1):
            print("%d. %s\n" % (i, message))
        return 1
    print("PASS: contract-exact wire shape, token refresh resumed the sweep, no work lost.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
