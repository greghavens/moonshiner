#!/usr/bin/env python3
"""Protected verifier for the VcfVsanDp module.

Starts the loopback snapservice mock, runs tests/drive.ps1 against it, then
asserts both the module's output and the exact wire shape of every request the
module made. No live VMware endpoint is contacted.

    python3 tests/verify.py

Exits 0 when every check passes, 1 otherwise.
"""

import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MOCK = os.path.join(REPO, "mock")
FIXTURE = os.path.join(MOCK, "fixture.json")

PORT_FILE = os.path.join(MOCK, "port.txt")
LOG_FILE = os.path.join(MOCK, "requests.jsonl")
RESULT_FILE = os.path.join(HERE, "result.json")

BASE_PATH = "/api"
PG_PATH = BASE_PATH + "/snapservice/clusters/domain-c1001/protection-groups"
REPORT_PATH = BASE_PATH + "/snapservice/reports/clusters/domain-c1001/protection-groups/snapshots"
SESSION_PATH = BASE_PATH + "/snapservice/sessions"

REQUIRED_CMDLETS = {
    "Connect-VcfVsanDpServer",
    "Disconnect-VcfVsanDpServer",
    "Get-VcfVsanDpProtectionGroup",
    "Get-VcfVsanDpProtectionGroupSnapshotReport",
}

FILTER_START = "2026-05-02T00:00:00.000Z"
FILTER_END = "2026-05-04T12:00:00.000Z"
FILTER_PGS = ["pg-0001", "pg-0003"]

failures = []
checks = 0


def check(condition, message, detail=None):
    global checks
    checks += 1
    if not condition:
        entry = message
        if detail is not None:
            entry += "\n      got: %s" % (detail,)
        failures.append(entry)
    return bool(condition)


def canonical(rows):
    """Reference ordering: creation_time, then pg, then snapshot id."""
    return sorted(rows, key=lambda r: (r["creation_time"], r["pg"], r.get("snapshot") or ""))


# --------------------------------------------------------------------------
# mock lifecycle
# --------------------------------------------------------------------------

def start_mock():
    for stale in (PORT_FILE, LOG_FILE):
        if os.path.exists(stale):
            os.remove(stale)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(MOCK, "snapservice_mock.py"),
         "--fixture", FIXTURE, "--port-file", PORT_FILE, "--log", LOG_FILE],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().decode("utf-8", "replace")
            raise SystemExit("mock exited early:\n%s" % err)
        if os.path.exists(PORT_FILE):
            with open(PORT_FILE) as fh:
                text = fh.read().strip()
            if text:
                port = int(text)
                # Probe at the socket layer only. An HTTP probe would land in the
                # request log and be mistaken for a call the module made.
                for _ in range(100):
                    try:
                        socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    except OSError:
                        time.sleep(0.1)
                    else:
                        return proc, port
        time.sleep(0.05)
    raise SystemExit("mock did not become ready in time")


def stop_mock(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_no_vendored_sdk():
    """The SDK is an environment prerequisite; it must not be committed here."""
    offenders = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for d in list(dirs):
            if d.startswith("VMware.Sdk.Vcf") or d in ("VMware.OpenAPI", "VMware.VimAutomation.Sdk"):
                offenders.append(os.path.relpath(os.path.join(root, d), REPO))
        for f in files:
            if f.lower().endswith(".dll") or f.lower().endswith(".nupkg"):
                offenders.append(os.path.relpath(os.path.join(root, f), REPO))
    check(not offenders,
          "The VMware SDK must not be vendored into the repository.", offenders)


def check_wire(log, token, fixture):
    if not check(len(log) >= 3, "Expected at least three requests.", len(log)):
        return

    statuses = [(e["seq"], e["method"], e["path"], e["status"], e["outcome"]) for e in log]
    check(all(e["status"] in (200, 201, 204) for e in log),
          "Every request must have been accepted by the appliance. "
          "A 4xx means the request was malformed.",
          [s for s in statuses if s[3] not in (200, 201, 204)])

    # -- session create ---------------------------------------------------
    first = log[0]
    check(first["method"] == "POST" and first["path"] == SESSION_PATH,
          "The first request must be POST %s (Snapservice.Sessions_create)." % SESSION_PATH,
          "%s %s" % (first["method"], first["path"]))
    expect_basic = "Basic " + base64.b64encode(
        ("%s:%s" % (fixture["credentials"]["username"],
                    fixture["credentials"]["password"])).encode("utf-8")).decode("ascii")
    check(first["headers"].get("authorization") == expect_basic,
          "Sessions_create must authenticate with HTTP Basic credentials.",
          first["headers"].get("authorization"))
    check("vmware-api-session-id" not in first["headers"],
          "Sessions_create must not send a session token; it is what mints one.")
    check(first["raw_query"] == "",
          "Sessions_create takes no query parameters.", first["raw_query"])
    check(first["body"] == "",
          "Sessions_create takes no request body.", first["body"])

    # -- session delete ---------------------------------------------------
    last = log[-1]
    check(last["method"] == "DELETE" and last["path"] == SESSION_PATH,
          "The final request must be DELETE %s (Snapservice.Sessions_delete)." % SESSION_PATH,
          "%s %s" % (last["method"], last["path"]))
    check(last["status"] == 204, "Sessions_delete must succeed with 204.", last["status"])
    check(last["raw_query"] == "",
          "Sessions_delete takes no query parameters.", last["raw_query"])
    check(last["body"] == "",
          "Sessions_delete takes no request body.", last["body"])

    # -- token discipline on every later call -----------------------------
    later = log[1:]
    bad_token = [e["seq"] for e in later
                 if e["headers"].get("vmware-api-session-id") != token]
    check(not bad_token,
          "Every call after Sessions_create must carry the minted token in the "
          "vmware-api-session-id header.", bad_token)
    leaked_basic = [e["seq"] for e in later if "authorization" in e["headers"]]
    check(not leaked_basic,
          "Basic credentials must not be resent once a session token exists.", leaked_basic)

    # -- no stray operations ----------------------------------------------
    known = {SESSION_PATH, PG_PATH, REPORT_PATH}
    stray = [(e["seq"], e["method"], e["path"]) for e in log if e["path"] not in known]
    check(not stray, "Only the four contracted operations may be called.", stray)

    # -- exploded parameter names must never appear -----------------------
    exploded = [(e["seq"], e["query_keys"]) for e in log
                if "filter" in e["query_keys"] or "iterate" in e["query_keys"]]
    check(not exploded,
          "'filter' and 'iterate' are form/explode=true object parameters. Their "
          "property names go on the wire, never the parameter name itself.", exploded)

    # -- no empty-valued keys ---------------------------------------------
    empties = [(e["seq"], k) for e in log for k, v in e["query_pairs"] if v == ""]
    check(not empties,
          "Optional parameters that were not set must be omitted entirely, not "
          "sent as empty values.", empties)

    # -- protection group listing -----------------------------------------
    pg_reqs = [e for e in log if e["path"] == PG_PATH]
    check(len(pg_reqs) == 2,
          "Expected the unfiltered and filtered ProtectionGroups_list calls.",
          len(pg_reqs))
    for e in pg_reqs:
        check(e["method"] == "GET", "ProtectionGroups_list is a GET.", e["method"])
    pg_unfiltered = [e for e in pg_reqs if not e["query_pairs"]]
    pg_filtered = [e for e in pg_reqs if e["query_pairs"]]
    check(len(pg_unfiltered) == 1,
          "The unfiltered protection-group call must have a completely empty query string.",
          [(e["seq"], e["raw_query"]) for e in pg_unfiltered])
    check(len(pg_filtered) == 1,
          "The filtered protection-group call must send all five supplied filters.",
          [(e["seq"], e["raw_query"]) for e in pg_filtered])
    for e in pg_filtered:
        check(sorted(set(e["query_keys"]))
              == ["cluster_pairs", "names", "pgs", "states", "vms"],
              "Protection-group filters must use the five contract query keys.",
              e["query_keys"])
        expected_arrays = {
            "pgs": ["pg-0001", "pg-0003"],
            "names": ["finance-critical", "web-tier"],
            "states": ["ACTIVE"],
            "vms": ["vm-7100", "vm-7106"],
            "cluster_pairs": ["pair-0001", "pair-0002"],
        }
        for key, expected_values in expected_arrays.items():
            check(e["query_multi"].get(key) == expected_values,
                  "%s must be repeated once per supplied value, in order." % key,
                  e["query_multi"].get(key))

    # -- report calls ------------------------------------------------------
    reports = [e for e in log if e["path"] == REPORT_PATH]
    unfiltered = [e for e in reports
                  if set(e["query_keys"]) == {"page_size", "offset"}]
    filtered = [e for e in reports if "start_time" in e["query_multi"]]
    end_only = [e for e in reports
                if "end_time" in e["query_multi"]
                and "start_time" not in e["query_multi"]]

    check(all(e["method"] == "GET" for e in reports),
          "The snapshot report operation is a GET.")

    if unfiltered and filtered:
        check(max(e["seq"] for e in unfiltered) < min(e["seq"] for e in filtered),
              "The unfiltered report must be retrieved before the filtered one.")

    # Scenario B: 47 records, page size 10 -> offsets 0,10,20,30,40 and nothing more.
    check(len(unfiltered) == 5,
          "47 records at a page size of 10 needs exactly 5 requests: offsets "
          "0,10,20,30,40. Fewer means the collection was not retrieved "
          "completely; more means a page was fetched that could not exist.",
          [(e["seq"], e["raw_query"]) for e in unfiltered])
    for i, e in enumerate(unfiltered):
        keys = sorted(e["query_keys"])
        check(keys == ["offset", "page_size"],
              "Unfiltered report request %d must carry exactly page_size and "
              "offset. No filter properties and no pgs, because none were set." % (i + 1),
              keys)
        check(e["query_multi"].get("page_size") == ["10"],
              "Unfiltered report request %d must send page_size=10." % (i + 1),
              e["query_multi"].get("page_size"))
    got_offsets = [e["query_multi"].get("offset", [None])[0] for e in unfiltered]
    check(got_offsets == ["0", "10", "20", "30", "40"],
          "Offsets must advance by the page size, in order, with no repeats.",
          got_offsets)

    # Scenario C: 11 matching records, page size 5 -> offsets 0,5,10.
    check(len(filtered) == 3,
          "The filtered window matches 11 records; at a page size of 5 that is "
          "exactly 3 requests (offsets 0,5,10).",
          [(e["seq"], e["raw_query"]) for e in filtered])
    for i, e in enumerate(filtered):
        keys = sorted(set(e["query_keys"]))
        check(keys == ["end_time", "offset", "page_size", "pgs", "start_time"],
              "Filtered report request %d must carry start_time, end_time, pgs, "
              "page_size and offset as top-level query keys." % (i + 1), keys)
        check(e["query_multi"].get("start_time") == [FILTER_START],
              "start_time must be sent verbatim as a top-level key.",
              e["query_multi"].get("start_time"))
        check(e["query_multi"].get("end_time") == [FILTER_END],
              "end_time must be sent verbatim as a top-level key.",
              e["query_multi"].get("end_time"))
        check(e["query_multi"].get("pgs") == FILTER_PGS,
              "pgs is an array parameter with explode=true: one repeated 'pgs' "
              "key per identifier, in order.", e["query_multi"].get("pgs"))
        check(e["query_multi"].get("page_size") == ["5"],
              "Filtered report request %d must send page_size=5." % (i + 1),
              e["query_multi"].get("page_size"))
    got_offsets = [e["query_multi"].get("offset", [None])[0] for e in filtered]
    check(got_offsets == ["0", "5", "10"],
          "Filtered offsets must advance by the page size, in order.", got_offsets)

    # Scenario D: EndTime supplied independently; one page is sufficient.
    check(len(end_only) == 1,
          "The end-only report fits in one page and must make exactly one request.",
          [(e["seq"], e["raw_query"]) for e in end_only])
    for e in end_only:
        check(sorted(e["query_keys"]) == ["end_time", "offset", "page_size"],
              "The end-only request must omit start_time and pgs entirely.",
              e["query_keys"])
        check(e["query_multi"].get("end_time") == [FILTER_END],
              "The independently supplied end_time must be sent verbatim.",
              e["query_multi"].get("end_time"))
        check(e["query_multi"].get("page_size") == ["100"],
              "The end-only request must send page_size=100.",
              e["query_multi"].get("page_size"))
        check(e["query_multi"].get("offset") == ["0"],
              "The end-only request must begin at offset=0.",
              e["query_multi"].get("offset"))


def check_output(result, fixture):
    pg_by_id = {i["pg"]: i["info"]["name"] for i in fixture["protectionGroups"]["items"]}

    got_pgs = [(p["ProtectionGroupId"], p["ProtectionGroupName"])
               for p in result.get("protectionGroups") or []]
    check(len(got_pgs) == len(pg_by_id) and dict(got_pgs) == pg_by_id,
          "Get-VcfVsanDpProtectionGroup must return every protection group with "
          "its identifier and name exactly once.", got_pgs)

    got_filtered_pgs = [(p["ProtectionGroupId"], p["ProtectionGroupName"])
                        for p in result.get("protectionGroupsFiltered") or []]
    expected_filtered_pgs = {key: pg_by_id[key] for key in ("pg-0001", "pg-0003")}
    check(len(got_filtered_pgs) == len(expected_filtered_pgs)
          and dict(got_filtered_pgs) == expected_filtered_pgs,
          "The filtered protection-group call must return the matching groups.",
          got_filtered_pgs)

    # -- scenario B --------------------------------------------------------
    expected = canonical(fixture["snapshots"])
    got = result.get("reportAll") or []
    if check(len(got) == len(expected),
             "The complete report must contain all %d records." % len(expected),
             len(got)):
        got_order = [(r["CreationTime"], r["ProtectionGroupId"], r["SnapshotId"]) for r in got]
        exp_order = [(r["creation_time"], r["pg"], r["snapshot"]) for r in expected]
        if not check(got_order == exp_order,
                     "Records must be emitted in a stable order: creation_time "
                     "ascending, ties broken by protection group id then snapshot "
                     "id. The service does not order ties for you."):
            for i, (a, b) in enumerate(zip(got_order, exp_order)):
                if a != b:
                    failures.append("      first divergence at index %d: got %s, expected %s"
                                    % (i, a, b))
                    break
        for i, (g, e) in enumerate(zip(got, expected)):
            if g["SnapshotId"] != e["snapshot"]:
                continue
            problems = []
            if g["SnapshotName"] != e["name"]:
                problems.append("SnapshotName=%r want %r" % (g["SnapshotName"], e["name"]))
            if g["Status"] != e["status"]:
                problems.append("Status=%r want %r" % (g["Status"], e["status"]))
            if g["SnapshotType"] != (e.get("snapshot_type") or ""):
                problems.append("SnapshotType=%r want %r"
                                % (g["SnapshotType"], e.get("snapshot_type") or ""))
            if g["CreationTime"] != e["creation_time"]:
                problems.append("CreationTime=%r want %r"
                                % (g["CreationTime"], e["creation_time"]))
            if g["ExpirationTime"] != (e.get("expiration_time") or ""):
                problems.append("ExpirationTime=%r want %r"
                                % (g["ExpirationTime"], e.get("expiration_time") or ""))
            if bool(g["Deleted"]) != bool(e["deleted"]):
                problems.append("Deleted=%r want %r" % (g["Deleted"], e["deleted"]))
            if not check(not problems,
                         "Record %d (%s) has mismatched fields." % (i, e["snapshot"]),
                         "; ".join(problems)):
                break

    # -- scenario C --------------------------------------------------------
    want = canonical([r for r in fixture["snapshots"]
                      if FILTER_START <= r["creation_time"] <= FILTER_END
                      and r["pg"] in set(FILTER_PGS)])
    gotf = result.get("reportFiltered") or []
    check(len(gotf) == len(want),
          "The filtered report must contain the %d matching records." % len(want),
          len(gotf))
    check([(r["CreationTime"], r["ProtectionGroupId"], r["SnapshotId"]) for r in gotf]
          == [(r["creation_time"], r["pg"], r["snapshot"]) for r in want],
          "The filtered report must use the same stable ordering.",
          [(r["CreationTime"], r["ProtectionGroupId"], r["SnapshotId"]) for r in gotf])

    # -- scenario D --------------------------------------------------------
    want_end = canonical([r for r in fixture["snapshots"]
                          if r["creation_time"] <= FILTER_END])
    got_end = result.get("reportEndOnly") or []
    check([(r["CreationTime"], r["ProtectionGroupId"], r["SnapshotId"])
           for r in got_end]
          == [(r["creation_time"], r["pg"], r["snapshot"]) for r in want_end],
          "The end-only report must contain all matching records in stable order.",
          len(got_end))


def main():
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        print("FAIL: PowerShell (pwsh) is not on PATH.")
        return 1

    with open(FIXTURE) as fh:
        fixture = json.load(fh)

    check_no_vendored_sdk()

    proc, port = start_mock()
    try:
        base = "http://127.0.0.1:%d" % port
        if os.path.exists(RESULT_FILE):
            os.remove(RESULT_FILE)
        run = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", os.path.join(HERE, "drive.ps1"),
             "-BaseUrl", base,
             "-OutFile", RESULT_FILE,
             "-Username", fixture["credentials"]["username"],
             "-Password", fixture["credentials"]["password"],
             "-Cluster", fixture["cluster"]],
            capture_output=True, text=True, timeout=600,
        )
    finally:
        stop_mock(proc)

    if not os.path.exists(RESULT_FILE):
        print("FAIL: the driver produced no result file.")
        print("--- stdout ---\n%s\n--- stderr ---\n%s" % (run.stdout, run.stderr))
        return 1

    with open(RESULT_FILE) as fh:
        result = json.load(fh)

    if not result.get("ok"):
        print("FAIL: the driver did not complete. It stopped at stage %r."
              % result.get("stage"))
        print(result.get("error") or "")
        if run.stderr.strip():
            print("--- stderr ---\n%s" % run.stderr)
        return 1

    exported = set(result.get("exportedNames") or [])
    check(REQUIRED_CMDLETS <= exported,
          "The module must export the four required cmdlets.",
          sorted(REQUIRED_CMDLETS - exported))

    expected_mandatory = {
        "Connect-VcfVsanDpServer": {"Server", "Port", "Protocol", "Credential"},
        "Disconnect-VcfVsanDpServer": {"Session"},
        "Get-VcfVsanDpProtectionGroup": {"Session", "Cluster"},
        "Get-VcfVsanDpProtectionGroupSnapshotReport": {"Session", "Cluster", "PageSize"},
    }
    got_mandatory = {name: set(values or []) for name, values
                     in (result.get("mandatoryParameters") or {}).items()}
    check(got_mandatory == expected_mandatory,
          "The cmdlets' mandatory parameters must match the documented signatures.",
          {name: sorted(values) for name, values in got_mandatory.items()})

    check("VMware.Sdk.Vcf.SddcManager" in (result.get("sdkModules") or []),
          "Importing VcfVsanDp must load VMware.Sdk.Vcf.SddcManager, which means "
          "declaring it in the manifest's RequiredModules.",
          result.get("sdkModules"))

    check("VMware.Sdk.Vcf.SddcManager"
          in (result.get("manifestRequiredModules") or []),
          "The VcfVsanDp manifest must declare VMware.Sdk.Vcf.SddcManager in "
          "RequiredModules.",
          result.get("manifestRequiredModules"))

    check(result.get("tokenReturned") == fixture["sessionToken"],
          "Connect-VcfVsanDpServer must return the minted token in SessionId.",
          result.get("tokenReturned"))

    log = []
    with open(LOG_FILE) as fh:
        for line in fh:
            line = line.strip()
            if line:
                log.append(json.loads(line))
    log.sort(key=lambda e: e["seq"])

    check_wire(log, fixture["sessionToken"], fixture)
    check_output(result, fixture)

    if failures:
        print("FAIL: %d of %d checks failed.\n" % (len(failures), checks))
        for f in failures:
            print("  - %s" % f)
        return 1

    print("PASS: all %d checks passed." % checks)
    print("  %d requests observed, %d records retrieved."
          % (len(log), len(result.get("reportAll") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
