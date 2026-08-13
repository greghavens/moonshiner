#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 SDDC LCM fleet task inventory task.

Builds runtime-only fixtures (fresh task ids, names and timestamps on every run), compiles the
single-file client together with the harness, drives it through a contract-pinned loopback mock on
an ephemeral 127.0.0.1 port, then asserts both the emitted report and the exact wire shape of every
recorded request. No live VMware endpoint is contacted.
"""

import base64
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED = os.path.join(ROOT, ".protected")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
CLIENT = os.path.join(ROOT, "src", "FleetTaskInventory.java")

MAX_PAGE_SIZE = 50
UNRESERVED = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)

# Contract-declared getTasks query parameter order (docs/contract.json operations[0].parameters).
CONTRACT_QUERY_ORDER = [
    "status", "type", "createdBy", "name", "description",
    "startTimeGt", "startTimeLt", "updateTimeGt", "updateTimeLt",
    "endTimeGt", "endTimeLt", "resourceId", "resourceType",
    "includeSystemTasks", "pageNumber", "pageSize",
]

FAILURES = []


def fail(msg):
    FAILURES.append(msg)


def die(msg):
    print("FAIL: " + msg)
    sys.exit(1)


# --------------------------------------------------------------------- helpers

def pct(value):
    out = []
    for byte in value.encode("utf-8"):
        ch = chr(byte)
        if ch in UNRESERVED:
            out.append(ch)
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def iso(dt, style):
    """Render an instant in one of three equivalent, spec-legal ISO-8601 forms."""
    base = dt.astimezone(timezone.utc)
    if style == "Z":
        return base.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (base.microsecond // 1000)
    if style == "+00:00":
        return base.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d+00:00" % (base.microsecond // 1000)
    if style == "+05:30":
        shifted = base.astimezone(timezone(timedelta(hours=5, minutes=30)))
        return shifted.strftime("%Y-%m-%dT%H:%M:%S.") + "%03d+05:30" % (shifted.microsecond // 1000)
    raise ValueError(style)


def parse_instant(text):
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    return datetime.fromisoformat(t)


def sort_key(task):
    """The report ordering rule, implemented independently of the client."""
    inst = parse_instant(task.get("startTime"))
    if inst is None:
        return (1, 0.0, task["id"])
    # Descending by instant: negate the epoch seconds.
    return (0, -inst.timestamp(), task["id"])


# -------------------------------------------------------------- fixture design

MESSAGE_IDS = [
    "com.broadcom.lcm.ops.component.upgrade.failed",
    "com.broadcom.lcm.ops.fleet.precheck.failed",
    "com.broadcom.lcm.ops.node.reconfigure.failed",
    "com.broadcom.lcm.ops.depot.resolve.failed",
]
STAGE_NAMES = [
    "PRECHECK", "DOWNLOAD_BUNDLE", "STAGE_BINARIES",
    "APPLY_UPGRADE", "POST_VALIDATE", "FINALIZE",
]
NON_FAILED_STATUSES = ["SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "RUNNING", "PENDING", "CANCELED"]


def make_stages(rng, failed_index, count):
    stages = []
    for i in range(count):
        name = STAGE_NAMES[i % len(STAGE_NAMES)]
        if i < failed_index:
            status = "SUCCEEDED"
        elif i == failed_index:
            status = "FAILED"
        else:
            status = "SKIPPED"
        stage = {
            "id": "stage-%d-%s" % (i, uuid.UUID(int=rng.getrandbits(128)).hex[:8]),
            "name": name,
            "status": status,
            "description": {
                "id": rng.choice(MESSAGE_IDS) if status == "FAILED" else
                     "com.broadcom.lcm.ops.stage.%s" % name.lower(),
                "defaultMessage": "%s %s" % (name, status.lower()),
                "localizedMessage": "%s %s" % (name, status.lower()),
            },
        }
        stages.append(stage)
    return stages


def build_detail(rng, summary):
    """A full Task for a FAILED summary, including decoy failed stages inside subTasks."""
    stage_count = rng.randint(3, 6)
    failed_index = rng.randrange(stage_count)
    stages = make_stages(rng, failed_index, stage_count)
    detail = dict(summary)
    detail["stages"] = stages
    # Decoy: nested sub-tasks that also carry FAILED stages with different names/message ids.
    detail["subTasks"] = [
        {
            "id": str(uuid.UUID(int=rng.getrandbits(128))),
            "name": "%s_sub_%d" % (summary["name"], j),
            "status": "FAILED",
            "stages": make_stages(rng, 0, rng.randint(2, 4)),
        }
        for j in range(rng.randint(1, 2))
    ]
    detail["messages"] = [
        {
            "level": "ERROR",
            "stageId": stages[failed_index]["id"],
            "message": {"id": stages[failed_index]["description"]["id"],
                        "defaultMessage": "stage failed"},
        }
    ]
    return detail, stages[failed_index]


def build_case_a(rng):
    """127 tasks over three pages, six FAILED, one same-instant triple across three ISO forms."""
    total = 127
    base = datetime(2026, 6, 10, 0, 0, 0, tzinfo=timezone.utc)
    styles = ["Z", "+00:00", "+05:30"]
    tasks = []
    for i in range(total):
        when = base + timedelta(minutes=7 * i, seconds=rng.randrange(0, 50))
        tasks.append({
            "id": str(uuid.UUID(int=rng.getrandbits(128))),
            "name": "fleet_upgrade_%s" % uuid.UUID(int=rng.getrandbits(128)).hex[:10],
            "status": "SUCCEEDED",
            "type": "apply",
            "createdBy": "admin",
            "description": {
                "id": "com.broadcom.lcm.ops.fleet.audit",
                "defaultMessage": " Fleet / 東京 + 100% ",
                "localizedMessage": " Fleet / 東京 + 100% ",
            },
            "resourceId": str(uuid.UUID(int=rng.getrandbits(128))),
            "resourceType": "COMPONENT",
            "startTime": iso(when, styles[i % 3]),
            "createTime": iso(when - timedelta(minutes=1), "Z"),
        })

    rng.shuffle(tasks)

    # One instant rendered three legal ways: lexicographic ordering of the raw strings disagrees
    # with chronological ordering, so only an instant comparison plus the id tie-break is correct.
    tie_instant = base + timedelta(days=1, hours=8)
    for slot, style in zip((11, 57, 96), styles):
        tasks[slot]["startTime"] = iso(tie_instant, style)

    for slot in (3, 27, 61, 88, 104, 126):
        tasks[slot]["status"] = "FAILED"
    for i, t in enumerate(tasks):
        if t["status"] != "FAILED":
            t["status"] = NON_FAILED_STATUSES[i % len(NON_FAILED_STATUSES)]

    return tasks


def build_case_b(rng):
    """Exactly 50 tasks on one full page; one failure never started and one time is blank."""
    base = datetime(2026, 5, 2, 9, 0, 0, tzinfo=timezone.utc)
    styles = ["Z", "+00:00", "+05:30"]
    tasks = []
    for i in range(50):
        when = base + timedelta(minutes=13 * i)
        tasks.append({
            "id": str(uuid.UUID(int=rng.getrandbits(128))),
            "name": "sddc_lcm_%s" % uuid.UUID(int=rng.getrandbits(128)).hex[:10],
            "status": NON_FAILED_STATUSES[i % len(NON_FAILED_STATUSES)],
            "type": "validate",
            "createdBy": "svc-lcm",
            "resourceType": "COMPONENT",
            "startTime": iso(when, styles[i % 3]),
            "createTime": iso(when - timedelta(minutes=1), "Z"),
        })
    rng.shuffle(tasks)
    tasks[2]["status"] = "FAILED"
    del tasks[2]["startTime"]          # failed before it ever started: property absent
    tasks[5]["startTime"] = ""         # present but blank: also "unset"
    tasks[5]["status"] = "SUCCEEDED"
    return tasks


def build_case_empty(_rng):
    """An empty collection still requires one getTasks response to discover that it is empty."""
    return []


def finish_fixture(rng, tasks, token):
    details = {}
    failed_stage_by_id = {}
    for t in tasks:
        if t["status"] == "FAILED":
            detail, failed_stage = build_detail(rng, t)
            details[t["id"]] = detail
            failed_stage_by_id[t["id"]] = (detail, failed_stage)
        else:
            detail = dict(t)
            detail["stages"] = make_stages(rng, -1, 2)
            details[t["id"]] = detail
    return {"bearerToken": token, "tasks": tasks, "details": details}, failed_stage_by_id


# -------------------------------------------------------------- expected report

def expected_report(tasks, details, failed_stage_by_id, page_size):
    ordered = sorted(tasks, key=sort_key)
    total = len(tasks)
    pages = max(1, (total + page_size - 1) // page_size)

    def norm_time(t):
        raw = t.get("startTime")
        if raw is None or not raw.strip():
            return None
        return raw

    report_tasks = [
        {"id": t["id"], "name": t["name"], "status": t["status"], "startTime": norm_time(t)}
        for t in ordered
    ]
    failures = []
    for t in ordered:
        if t["status"] != "FAILED":
            continue
        detail, stage = failed_stage_by_id[t["id"]]
        failures.append({
            "id": t["id"],
            "name": t["name"],
            "startTime": norm_time(t),
            "stageCount": len(detail["stages"]),
            "failedStage": stage["name"],
            "failureMessageId": stage["description"]["id"],
        })
    return {
        "pagesRetrieved": pages,
        "totalElements": total,
        "taskCount": total,
        "failedCount": len(failures),
        "tasks": report_tasks,
        "failures": failures,
    }


# ---------------------------------------------------------------- mock plumbing

class Mock:
    def __init__(self, workdir, name, fixture):
        self.dir = workdir
        self.log = os.path.join(workdir, "%s-requests.jsonl" % name)
        self.port_file = os.path.join(workdir, "%s-port" % name)
        self.fixture_file = os.path.join(workdir, "%s-fixture.json" % name)
        with open(self.fixture_file, "w", encoding="utf-8") as fh:
            json.dump(fixture, fh)
        self.proc = None
        self.port = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-B", os.path.join(PROTECTED, "mock_sddc_lcm.py"),
             CONTRACT, self.fixture_file, self.log, self.port_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            if os.path.exists(self.port_file):
                text = open(self.port_file, encoding="utf-8").read().strip()
                if text:
                    self.port = int(text)
                    return self
            if self.proc.poll() is not None:
                err = self.proc.stderr.read().decode("utf-8", "replace")
                die("mock exited before binding a port: " + err)
            time.sleep(0.02)
        die("mock did not bind a port within 20s")

    def __exit__(self, *_exc):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return False

    @property
    def base_url(self):
        return "http://127.0.0.1:%d" % self.port

    def entries(self):
        out = []
        if not os.path.exists(self.log):
            return out
        with open(self.log, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        out.sort(key=lambda e: e["seq"])
        return out


# ------------------------------------------------------------------ assertions

def header_values(entry, name):
    return entry["headers"].get(name.lower(), [])


def check_common(case, entries, token):
    for e in entries:
        tag = "%s request #%d %s %s" % (case, e["seq"], e["method"], e["target"])
        if e["operationId"] is None:
            fail("%s: reached no contract operation (mock answered %d)" % (tag, e["status"]))
        if e["status"] != 200:
            fail("%s: mock answered %d, expected 200" % (tag, e["status"]))
        auth = header_values(e, "authorization")
        if auth != ["Bearer " + token]:
            fail("%s: Authorization header was %r, expected exactly one 'Bearer <token>'"
                 % (tag, auth))
        accept = header_values(e, "accept")
        if accept != ["application/json"]:
            fail("%s: Accept header was %r, expected exactly one 'application/json'" % (tag, accept))
        ctype = header_values(e, "content-type")
        if ctype:
            fail("%s: Content-Type was sent (%r) on a request with no body; it must be omitted"
                 % (tag, ctype))
        if e["bodyLength"] != 0 or base64.b64decode(e["bodyBase64"]):
            fail("%s: sent a %d-byte body; GET operations in this contract take none"
                 % (tag, e["bodyLength"]))
        if e["method"] != "GET":
            fail("%s: method must be GET" % tag)


def expected_query(filters, page_number, page_size):
    parts = []
    for name in CONTRACT_QUERY_ORDER:
        if name == "pageNumber":
            parts.append("pageNumber=%d" % page_number)
            continue
        if name == "pageSize":
            parts.append("pageSize=%d" % page_size)
            continue
        value = filters.get(name)
        if value is None or not str(value).strip():
            continue
        parts.append("%s=%s" % (name, pct(str(value))))
    return "&".join(parts)


def check_case(case, mock, filters, tasks, details, failed_stage_by_id, token, report_text):
    entries = mock.entries()
    if not entries:
        fail("%s: the mock recorded no requests at all" % case)
        return
    check_common(case, entries, token)

    lists = [e for e in entries if e["operationId"] == "getTasks"]
    gets = [e for e in entries if e["operationId"] == "getTask"]
    other = [e for e in entries if e["operationId"] not in ("getTasks", "getTask")]
    for e in other:
        fail("%s: request #%d hit %s, which the contract does not name"
             % (case, e["seq"], e["target"]))

    total = len(tasks)
    # Even an empty collection consumes page zero: that first response is how the client learns
    # from pageMetadata that totalElements and totalPages are both zero.
    pages = max(1, (total + MAX_PAGE_SIZE - 1) // MAX_PAGE_SIZE)

    if len(lists) != pages:
        fail("%s: made %d getTasks request(s); the collection spans exactly %d page(s) at "
             "pageSize=%d, so every page must be fetched and no page beyond the last"
             % (case, len(lists), pages, MAX_PAGE_SIZE))
    for i, e in enumerate(lists[:pages]):
        want = "/v1/tasks?" + expected_query(filters, i, MAX_PAGE_SIZE)
        if e["target"] != want:
            fail("%s: getTasks request #%d target was\n    %s\nexpected\n    %s"
                 % (case, i, e["target"], want))

    # Named check for the "unset means absent" rule, for a clearer diagnosis than a target diff.
    for e in lists:
        sent = set()
        for chunk in e["rawQuery"].split("&"):
            if chunk:
                sent.add(chunk.split("=", 1)[0])
        for name in CONTRACT_QUERY_ORDER:
            supplied = filters.get(name)
            unset = supplied is None or not str(supplied).strip()
            if name in ("pageNumber", "pageSize"):
                if name not in sent:
                    fail("%s: request #%d omitted %s; both pagination parameters must be sent "
                         "explicitly on every page request" % (case, e["seq"], name))
            elif unset and name in sent:
                fail("%s: request #%d sent unset optional filter %r; unset filters must be absent "
                     "from the query string, not sent empty" % (case, e["seq"], name))
            elif not unset and name not in sent:
                fail("%s: request #%d omitted filter %r, which the caller set to %r"
                     % (case, e["seq"], name, supplied))

    # Report content.
    want_report = expected_report(tasks, details, failed_stage_by_id, MAX_PAGE_SIZE)
    try:
        got = json.loads(report_text)
    except Exception as exc:  # noqa: BLE001
        fail("%s: report is not valid JSON (%s)" % (case, exc))
        return

    if list(got.keys()) != list(want_report.keys()):
        fail("%s: report keys were %r, expected exactly %r in that order"
             % (case, list(got.keys()), list(want_report.keys())))

    for scalar in ("pagesRetrieved", "totalElements", "taskCount", "failedCount"):
        if got.get(scalar) != want_report[scalar]:
            fail("%s: report %s was %r, expected %r"
                 % (case, scalar, got.get(scalar), want_report[scalar]))

    for key in ("tasks", "failures"):
        gv = got.get(key)
        wv = want_report[key]
        if not isinstance(gv, list):
            fail("%s: report %s must be a JSON array, got %r" % (case, key, type(gv).__name__))
            continue
        if len(gv) != len(wv):
            fail("%s: report %s had %d entries, expected %d" % (case, key, len(gv), len(wv)))
        for i, (g, w) in enumerate(zip(gv, wv)):
            if not isinstance(g, dict):
                fail("%s: %s[%d] must be a JSON object" % (case, key, i))
                continue
            if list(g.keys()) != list(w.keys()):
                fail("%s: %s[%d] keys were %r, expected exactly %r in that order"
                     % (case, key, i, list(g.keys()), list(w.keys())))
            if g != w:
                fail("%s: %s[%d] was\n    %s\nexpected\n    %s"
                     % (case, key, i, json.dumps(g, sort_keys=True), json.dumps(w, sort_keys=True)))
                break

    # Detail fetches: exactly one per failed task, in the report's failure order.
    want_ids = [f["id"] for f in want_report["failures"]]
    got_ids = [e["path"].rsplit("/", 1)[-1] for e in gets]
    if got_ids != want_ids:
        fail("%s: getTask was called for %r; expected exactly the failed tasks, once each, in the "
             "report's failure order %r" % (case, got_ids, want_ids))
    for e in gets:
        want_target = "/v1/tasks/" + pct(e["path"].rsplit("/", 1)[-1])
        if e["target"] != want_target:
            fail("%s: getTask target was %s, expected %s (no query string)"
                 % (case, e["target"], want_target))

    # Ordering must be established before the detail fetches: all listing calls come first.
    if lists and gets and max(e["seq"] for e in lists) > min(e["seq"] for e in gets):
        fail("%s: a getTasks request was issued after a getTask request; the whole collection must "
             "be retrieved and ordered before any detail fetch" % case)


def run_expected_failure(workdir, build, name, fixture, token, expected_targets):
    """Run one malformed-response case and require collect() to reject it at the right point."""
    filters_file = os.path.join(workdir, "%s-filters.json" % name)
    report_file = os.path.join(workdir, "%s-report.json" % name)
    with open(filters_file, "w", encoding="utf-8") as fh:
        json.dump({}, fh)

    with Mock(workdir, name, fixture) as mock:
        run = subprocess.run(
            [shutil.which("java"), "-cp", build, "TestMain", mock.base_url, token,
             filters_file, report_file],
            capture_output=True, text=True, timeout=180,
        )
        if run.returncode == 0:
            fail("%s: collect() accepted a malformed response and returned successfully" % name)
        entries = mock.entries()
        check_common(name, entries, token)
        got_targets = [e["target"] for e in entries]
        if got_targets != expected_targets:
            fail("%s: requests before rejection were %r, expected exactly %r"
                 % (name, got_targets, expected_targets))
        if os.path.exists(report_file):
            fail("%s: a report was written even though collect() was required to fail" % name)


# ------------------------------------------------------------------------ main

FORBIDDEN = [
    (".protected", "the client must not read harness internals under .protected/"),
    ("ProcessBuilder", "the client must not spawn subprocesses"),
    ("Runtime.getRuntime", "the client must not spawn subprocesses"),
    ("requests.jsonl", "the client must not read the mock's request log"),
]


def main():
    if not os.path.exists(CLIENT):
        die("src/FleetTaskInventory.java does not exist")
    source = open(CLIENT, encoding="utf-8").read()
    for token, why in FORBIDDEN:
        if token in source:
            die("src/FleetTaskInventory.java references %r: %s" % (token, why))
    if not re.search(r"\bclass\s+FleetTaskInventory\b", source):
        die("src/FleetTaskInventory.java does not declare class FleetTaskInventory")

    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        die("javac and java must be on PATH")

    workdir = tempfile.mkdtemp(prefix="vcf-lcm-verify-")
    build = os.path.join(workdir, "classes")
    os.makedirs(build)
    compile_cmd = [javac, "--release", "17", "-nowarn", "-d", build,
                   os.path.join(PROTECTED, "Json.java"),
                   os.path.join(PROTECTED, "TestMain.java"),
                   CLIENT]
    proc = subprocess.run(compile_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        die("javac failed:\n" + (proc.stdout + proc.stderr).strip())

    seed = int.from_bytes(os.urandom(16), "big")
    rng = random.Random(seed)
    token = "eyJhbGciOiJIUzI1NiJ9." + uuid.UUID(int=rng.getrandbits(128)).hex

    # Invalid constructor inputs must be rejected without touching even a reachable loopback API.
    validation_fixture = {"bearerToken": token, "tasks": [], "details": {}}
    with Mock(workdir, "invalid-inputs", validation_fixture) as mock:
        run = subprocess.run(
            [java, "-cp", build, "TestMain", "--validate-inputs", mock.base_url],
            capture_output=True, text=True, timeout=180,
        )
        if run.returncode != 0:
            fail("invalid-inputs: validation harness exited %d\n--- stdout ---\n%s\n"
                 "--- stderr ---\n%s"
                 % (run.returncode, run.stdout.strip(), run.stderr.strip()))
        if mock.entries():
            fail("invalid-inputs: invalid baseUrl or bearerToken caused an HTTP request; "
                 "both must be rejected before any request")

    cases = [
        # The caller's key order here is deliberately neither the contract's parameter order nor
        # alphabetical, so emitting filters in map order or sorted order is detectable.
        ("fleet-upgrade-audit", build_case_a(rng), {
            "includeSystemTasks": "true",
            "name": "",
            "resourceType": "COMPONENT",
            "status": None,
            "type": "apply",
            "createdBy": "   ",
            "startTimeGt": "2026-06-01T00:00:00.000Z",
            "description": " Fleet / 東京 + 100% ",
            "resourceId": None,
        }),
        ("unfiltered-single-page", build_case_b(rng), {}),
        ("empty-collection", build_case_empty(rng), {}),
    ]

    for name, tasks, filters in cases:
        fixture, failed_stage_by_id = finish_fixture(rng, tasks, token)
        filters_file = os.path.join(workdir, "%s-filters.json" % name)
        report_file = os.path.join(workdir, "%s-report.json" % name)
        with open(filters_file, "w", encoding="utf-8") as fh:
            json.dump(filters, fh)

        with Mock(workdir, name, fixture) as mock:
            run = subprocess.run(
                [java, "-cp", build, "TestMain", mock.base_url, token, filters_file, report_file],
                capture_output=True, text=True, timeout=180,
            )
            if run.returncode != 0:
                fail("%s: TestMain exited %d\n--- stdout ---\n%s\n--- stderr ---\n%s"
                     % (name, run.returncode, run.stdout.strip(), run.stderr.strip()))
                report_text = ""
            elif not os.path.exists(report_file):
                fail("%s: the client produced no report" % name)
                report_text = ""
            else:
                report_text = open(report_file, encoding="utf-8").read()

            check_case(name, mock, filters, tasks, fixture["details"],
                       failed_stage_by_id, token, report_text)

    list_target = "/v1/tasks?pageNumber=0&pageSize=50"

    # A response whose metadata count disagrees with its elements must be rejected.
    count_fixture = {
        "bearerToken": token,
        "tasks": [],
        "details": {},
        "reportedTotalElements": 1,
    }
    run_expected_failure(workdir, build, "mismatched-total-elements", count_fixture, token,
                         [list_target])

    # Each remaining malformed case has one FAILED summary, so rejection must occur only after
    # the required getTask call has returned the invalid detail representation.
    bad_tasks = build_case_b(rng)
    failed_summary = next(t for t in bad_tasks if t["status"] == "FAILED")
    detail_fixture, _unused = finish_fixture(rng, [failed_summary], token)
    failed_id = failed_summary["id"]
    detail_target = "/v1/tasks/" + pct(failed_id)
    expected_targets = [list_target, detail_target]

    wrong_id_fixture = json.loads(json.dumps(detail_fixture))
    wrong_id_fixture["details"][failed_id]["id"] = str(
        uuid.UUID(int=uuid.UUID(failed_id).int ^ 1)
    )
    run_expected_failure(workdir, build, "mismatched-detail-id", wrong_id_fixture, token,
                         expected_targets)

    no_failed_stage_fixture = json.loads(json.dumps(detail_fixture))
    no_failed_stage_fixture["details"][failed_id]["stages"] = []
    run_expected_failure(workdir, build, "no-failed-stage", no_failed_stage_fixture, token,
                         expected_targets)

    multiple_failed_stages_fixture = json.loads(json.dumps(detail_fixture))
    stages = multiple_failed_stages_fixture["details"][failed_id]["stages"]
    extra_failed_stage = json.loads(json.dumps(next(s for s in stages if s["status"] == "FAILED")))
    extra_failed_stage["id"] = "stage-extra-" + uuid.UUID(int=rng.getrandbits(128)).hex[:8]
    extra_failed_stage["name"] = "UNEXPECTED_SECOND_FAILURE"
    stages.append(extra_failed_stage)
    run_expected_failure(workdir, build, "multiple-failed-stages",
                         multiple_failed_stages_fixture, token, expected_targets)

    if FAILURES:
        print("FAIL: %d check(s) failed (fixture seed %d)\n" % (len(FAILURES), seed))
        for i, msg in enumerate(FAILURES, 1):
            print("%2d. %s" % (i, msg))
        shutil.rmtree(workdir, ignore_errors=True)
        return 1

    shutil.rmtree(workdir, ignore_errors=True)
    print("PASS: fleet task inventory matches the contract-pinned wire and report expectations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
