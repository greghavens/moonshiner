#!/usr/bin/env python3
"""Protected verifier for the VCF Operations maintenance window module.

For each scenario the verifier starts a fresh contract-pinned loopback mock,
runs tests/exercise.ps1 against it with the genuine VMware.Sdk.Vcf.Ops PowerCLI
module, then asserts the recorded request log. No live VMware endpoint is ever
contacted: the mock binds 127.0.0.1 and the exercise harness connects only to
that port.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
SOURCES = os.path.join(ROOT, "docs", "official_sources.json")
MOCK = os.path.join(TESTS, "mock_vcf_operations.py")
EXERCISE = os.path.join(TESTS, "exercise.ps1")
MODULE_DIR = os.path.join(ROOT, "src", "VcfOps.MaintenanceWindow")
MANIFEST = os.path.join(MODULE_DIR, "VcfOps.MaintenanceWindow.psd1")
IMPLEMENTATION = os.path.join(MODULE_DIR, "VcfOps.MaintenanceWindow.psm1")

SCENARIO_TIMEOUT = 240

MAINTENANCE_OPS = (
    "getMaintenanceSchedules",
    "createMaintenanceSchedules",
    "updateMaintenanceSchedules",
)

# Every optional field of the "schedule" projection that the module can bind,
# plus the fields it must never emit at all.
SCHEDULE_REQUIRED = {"scheduleType", "hour", "minuteOfTheHour", "duration"}
SCHEDULE_BINDABLE = {"recurrence", "daysOfTheWeek", "expirationDate", "expireRuns", "timeZone"}
SCHEDULE_UNSUPPORTED = {
    "dayOfTheMonth",
    "daysOfTheMonth",
    "month",
    "months",
    "startDate",
    "weeksOfTheMonth",
}

BANNED_HTTP_PATTERNS = [
    (r"\bInvoke-RestMethod\b", "Invoke-RestMethod"),
    (r"\bInvoke-WebRequest\b", "Invoke-WebRequest"),
    (r"\bcurl\b", "curl"),
    (r"\bwget\b", "wget"),
    (r"System\.Net\.WebClient", "System.Net.WebClient"),
    (r"System\.Net\.Http\.HttpClient", "System.Net.Http.HttpClient"),
    (r"\[System\.Net\.HttpWebRequest\]", "System.Net.HttpWebRequest"),
    (r"HttpWebRequest\]::Create", "HttpWebRequest::Create"),
    (r"\bNew-Object\s+Net\.WebClient\b", "Net.WebClient"),
]

FAILURES = []
CHECKS = [0]


def check(condition, message):
    CHECKS[0] += 1
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def report():
    print("%d check(s) run, %d failed:\n" % (CHECKS[0], len(FAILURES)))
    for index, failure in enumerate(FAILURES, 1):
        print("  %2d. %s" % (index, failure))


def fatal(message):
    print("FAIL: %s" % message)
    print("")
    if FAILURES:
        report()
        print("")
    print("%d check(s) run, verification aborted." % CHECKS[0])
    sys.exit(1)


def wait_for_ready(path, process, deadline):
    """Return the OS-assigned mock port once its ready file is complete."""
    while time.time() < deadline:
        if process.poll() is not None:
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                port = int(fh.read().strip())
            if 0 < port < 65536:
                return port
        except (FileNotFoundError, OSError, ValueError):
            pass
        time.sleep(0.05)
    return None


def read_log(path):
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


def run_scenario(scenario):
    """Start a fresh mock, run one scenario, return (results, log entries)."""
    workdir = tempfile.mkdtemp(prefix="vcfops-%s-" % scenario)
    log_path = os.path.join(workdir, "requests.jsonl")
    ready_path = os.path.join(workdir, "ready")
    result_path = os.path.join(workdir, "result.json")

    mock = subprocess.Popen(
        [
            sys.executable,
            "-B",
            MOCK,
            "--port",
            "0",
            "--contract",
            CONTRACT,
            "--log",
            log_path,
            "--ready",
            ready_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        port = wait_for_ready(ready_path, mock, time.time() + 30)
        if port is None:
            mock.kill()
            out, err = mock.communicate()
            fatal(
                "loopback mock did not start for scenario %s\nstdout: %s\nstderr: %s"
                % (scenario, out.decode(errors="replace"), err.decode(errors="replace"))
            )

        proc = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                EXERCISE,
                "-Scenario",
                scenario,
                "-Port",
                str(port),
                "-ResultPath",
                result_path,
                "-ModulePath",
                MANIFEST,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=SCENARIO_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        fatal("scenario %s exceeded %ds" % (scenario, SCENARIO_TIMEOUT))
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mock.kill()

    entries = read_log(log_path)
    results = None
    if os.path.exists(result_path):
        with open(result_path, encoding="utf-8") as fh:
            results = json.load(fh)

    if proc.returncode != 0 or results is None or results.get("error"):
        detail = (results or {}).get("error") or ""
        fatal(
            "scenario %s failed (exit %s)\n--- harness error ---\n%s\n"
            "--- stdout ---\n%s\n--- stderr ---\n%s"
            % (scenario, proc.returncode, detail.strip(), proc.stdout.strip(), proc.stderr.strip())
        )

    calls = {c["call"]: c for c in results["calls"]}
    return calls, entries


def writes(entries):
    return [e for e in entries if e["operationId"] in ("createMaintenanceSchedules",
                                                       "updateMaintenanceSchedules")]


def by_op(entries, op_id):
    return [e for e in entries if e["operationId"] == op_id]


def body_of(entry):
    return json.loads(entry["body"])


def assert_baseline(scenario, calls, entries):
    """Assertions that hold for every scenario."""
    expected_key = "fleet-patch-window-" + scenario
    for label, returned in calls.items():
        check(
            returned.get("type") == "System.Management.Automation.PSCustomObject",
            "%s: %s must return a PSCustomObject, got %r"
            % (scenario, label, returned.get("type")),
        )
        check(
            returned.get("key") == expected_key,
            "%s: %s returned Key must be %r, got %r"
            % (scenario, label, expected_key, returned.get("key")),
        )
        check(
            bool(returned.get("id")),
            "%s: %s must return the server-assigned schedule Id"
            % (scenario, label),
        )

    off = [e for e in entries if not e["contract"]]
    check(
        not off,
        "%s: requests reached endpoints docs/contract.json does not name: %s"
        % (scenario, ["%s %s" % (e["method"], e["path"]) for e in off]),
    )

    failures = [e for e in entries if e["status"] >= 400]
    check(
        not failures,
        "%s: the mock rejected %d request(s): %s"
        % (
            scenario,
            len(failures),
            [
                "%s %s -> %d body=%s" % (e["method"], e["path"], e["status"], e["body"][:200])
                for e in failures
            ],
        ),
    )

    maintenance = [e for e in entries if e["operationId"] in MAINTENANCE_OPS]
    agents = {e["headers"].get("user-agent", "") for e in maintenance}
    check(
        maintenance and all(a.startswith("VMware.Sdk.Vcf.Ops/") for a in agents),
        "%s: maintenance schedule requests must originate from the VMware.Sdk.Vcf.Ops "
        "PowerCLI module; observed User-Agent values %s" % (scenario, sorted(agents)),
    )
    check(
        all(e["headers"].get("authorization", "").startswith("OpsToken ") for e in maintenance),
        "%s: maintenance schedule requests must carry the session OpsToken header" % scenario,
    )

    for entry in writes(entries):
        assert_wire_shape(scenario, entry)

    lookups = by_op(entries, "getMaintenanceSchedules")
    check(
        len(lookups) >= len(calls),
        "%s: every function call must read the current holder of the key; "
        "saw %d lookup(s) for %d call(s)" % (scenario, len(lookups), len(calls)),
    )

    maintenance = [e for e in entries if e["operationId"] in MAINTENANCE_OPS]
    last_write = -1
    for index, entry in enumerate(maintenance):
        if entry["operationId"] not in (
            "createMaintenanceSchedules",
            "updateMaintenanceSchedules",
        ):
            continue
        check(
            any(
                prior["operationId"] == "getMaintenanceSchedules"
                for prior in maintenance[last_write + 1:index]
            ),
            "%s: %s was not preceded by a fresh key lookup"
            % (scenario, entry["operationId"]),
        )
        last_write = index


def assert_wire_shape(scenario, entry):
    """Exact request wire shape for createMaintenanceSchedules / updateMaintenanceSchedules."""
    op = entry["operationId"]
    label = "%s: %s body" % (scenario, op)

    check(
        entry["headers"].get("content-type", "").startswith("application/json"),
        "%s must be sent as application/json, got %r"
        % (label, entry["headers"].get("content-type")),
    )

    try:
        body = body_of(entry)
    except ValueError:
        check(False, "%s is not valid JSON: %r" % (label, entry["body"][:300]))
        return

    check(isinstance(body, dict), "%s must be a JSON object, got %r" % (label, type(body).__name__))
    if not isinstance(body, dict):
        return

    allowed_top = {"key", "schedule"} | ({"id"} if op == "updateMaintenanceSchedules" else set())
    extra = set(body) - allowed_top
    check(not extra, "%s carries unexpected top-level field(s) %s" % (label, sorted(extra)))

    if op == "createMaintenanceSchedules":
        check(
            "id" not in body,
            "%s must not carry an id: the server assigns the identifier on create" % label,
        )
    else:
        check("id" in body, "%s must carry the existing schedule id" % label)

    check("key" in body, "%s must carry the schedule key" % label)
    check("schedule" in body, "%s must carry the schedule object" % label)
    schedule = body.get("schedule")
    check(isinstance(schedule, dict), "%s schedule must be an object" % label)
    if not isinstance(schedule, dict):
        return

    missing = SCHEDULE_REQUIRED - set(schedule)
    check(not missing, "%s schedule is missing required field(s) %s" % (label, sorted(missing)))

    unsupported = SCHEDULE_UNSUPPORTED & set(schedule)
    check(
        not unsupported,
        "%s schedule carries field(s) %s that Set-VcfOpsMaintenanceWindow never binds; "
        "unbound optional fields must be omitted entirely" % (label, sorted(unsupported)),
    )

    stray = set(schedule) - SCHEDULE_REQUIRED - SCHEDULE_BINDABLE - SCHEDULE_UNSUPPORTED
    check(not stray, "%s schedule carries unknown field(s) %s" % (label, sorted(stray)))

    # No placeholder values anywhere: an unset optional is an absent key.
    for container, name in ((body, "request"), (schedule, "schedule")):
        for field, value in sorted(container.items()):
            if value is None:
                check(
                    False,
                    "%s %s field %r was sent as null; unset optional fields must be omitted"
                    % (label, name, field),
                )
            elif isinstance(value, str) and value == "":
                check(
                    False,
                    "%s %s field %r was sent as an empty string; unset optional fields "
                    "must be omitted" % (label, name, field),
                )
            elif isinstance(value, list) and not value:
                check(
                    False,
                    "%s %s field %r was sent as an empty array; unset optional fields "
                    "must be omitted" % (label, name, field),
                )


def scenario_create_then_retry():
    scenario = "create-then-retry"
    calls, entries = run_scenario(scenario)
    assert_baseline(scenario, calls, entries)

    creates = by_op(entries, "createMaintenanceSchedules")
    updates = by_op(entries, "updateMaintenanceSchedules")
    lookups = by_op(entries, "getMaintenanceSchedules")

    check(
        len(creates) == 1,
        "%s: the identical call was made twice and must have produced exactly one "
        "createMaintenanceSchedules request, saw %d" % (scenario, len(creates)),
    )
    check(
        len(updates) == 0,
        "%s: nothing drifted, so no updateMaintenanceSchedules request should have been "
        "sent, saw %d" % (scenario, len(updates)),
    )
    check(
        len(lookups) >= 2,
        "%s: each call must read the current schedule for the key before mutating, "
        "saw %d getMaintenanceSchedules request(s)" % (scenario, len(lookups)),
    )
    check(
        entries and entries[-1]["operationId"] == "getMaintenanceSchedules",
        "%s: the retry must end on a read; the last request was %s"
        % (scenario, entries[-1]["operationId"] if entries else "<none>"),
    )

    if creates:
        body = body_of(creates[0])
        check(
            body.get("key") == "fleet-patch-window-create-then-retry",
            "%s: created schedule key was %r" % (scenario, body.get("key")),
        )
        check(
            body.get("schedule") == {
                "scheduleType": "DAILY",
                "hour": 2,
                "minuteOfTheHour": 30,
                "duration": 120,
                "recurrence": 1,
            },
            "%s: createMaintenanceSchedules schedule body must contain exactly the four "
            "required fields plus the bound recurrence, got %s"
            % (scenario, json.dumps(body.get("schedule"), sort_keys=True)),
        )

    first, retry = calls.get("first"), calls.get("retry")
    check(first is not None and retry is not None,
          "%s: both invocations must return a result object" % scenario)
    if first and retry:
        check(
            first.get("action") == "Created",
            "%s: first call Action must be 'Created', got %r" % (scenario, first.get("action")),
        )
        check(
            retry.get("action") == "Unchanged",
            "%s: the identical retry Action must be 'Unchanged', got %r"
            % (scenario, retry.get("action")),
        )
        check(
            first.get("id") and first.get("id") == retry.get("id"),
            "%s: the retry must report the same schedule Id as the create (%r vs %r)"
            % (scenario, first.get("id"), retry.get("id")),
        )
        check(
            first.get("key") == "fleet-patch-window-create-then-retry",
            "%s: returned Key must echo the requested key, got %r" % (scenario, first.get("key")),
        )


def scenario_drift_update():
    scenario = "drift-update"
    calls, entries = run_scenario(scenario)
    assert_baseline(scenario, calls, entries)

    creates = by_op(entries, "createMaintenanceSchedules")
    updates = by_op(entries, "updateMaintenanceSchedules")

    check(
        len(creates) == 1,
        "%s: exactly one createMaintenanceSchedules request expected, saw %d"
        % (scenario, len(creates)),
    )
    check(
        len(updates) == 1,
        "%s: the key already existed when the definition drifted, so exactly one "
        "updateMaintenanceSchedules request was expected and the settle call must send "
        "none, saw %d" % (scenario, len(updates)),
    )

    created_id = None
    if creates:
        response_ids = [
            e for e in entries if e["operationId"] == "createMaintenanceSchedules"
        ]
        created_id = calls.get("create", {}).get("id")
        check(
            "id" not in body_of(response_ids[0]),
            "%s: create request must not preassign an id" % scenario,
        )

    if updates:
        body = body_of(updates[0])
        check(
            created_id and body.get("id") == created_id,
            "%s: updateMaintenanceSchedules must address the existing schedule id %r, "
            "sent %r" % (scenario, created_id, body.get("id")),
        )
        check(
            body.get("schedule") == {
                "scheduleType": "DAILY",
                "hour": 2,
                "minuteOfTheHour": 30,
                "duration": 240,
                "recurrence": 1,
            },
            "%s: updateMaintenanceSchedules schedule body must carry the drifted duration "
            "and nothing else, got %s"
            % (scenario, json.dumps(body.get("schedule"), sort_keys=True)),
        )

    expected = {"create": "Created", "drift": "Updated", "settle": "Unchanged"}
    for label, action in expected.items():
        got = calls.get(label, {}).get("action")
        check(
            got == action,
            "%s: %s call Action must be %r, got %r" % (scenario, label, action, got),
        )

    ids = {label: calls.get(label, {}).get("id") for label in expected}
    check(
        len(set(ids.values())) == 1 and all(ids.values()),
        "%s: every call must report the same schedule Id, got %s" % (scenario, ids),
    )


def scenario_weekly_omission():
    scenario = "weekly-omission"
    calls, entries = run_scenario(scenario)
    assert_baseline(scenario, calls, entries)

    creates = by_op(entries, "createMaintenanceSchedules")
    updates = by_op(entries, "updateMaintenanceSchedules")

    check(
        len(creates) == 1,
        "%s: exactly one createMaintenanceSchedules request expected, saw %d"
        % (scenario, len(creates)),
    )
    check(
        len(updates) == 0,
        "%s: reordering or duplicating DaysOfTheWeek does not change its set, so no "
        "updateMaintenanceSchedules request should have been sent, saw %d"
        % (scenario, len(updates)),
    )

    if creates:
        schedule = body_of(creates[0]).get("schedule", {})
        check(
            set(schedule) == {
                "scheduleType",
                "hour",
                "minuteOfTheHour",
                "duration",
                "daysOfTheWeek",
                "expirationDate",
            },
            "%s: the request must carry exactly the bound fields. Recurrence, ExpireRuns "
            "and TimeZone were not bound and must be absent, got keys %s"
            % (scenario, sorted(schedule)),
        )
        check(
            schedule.get("daysOfTheWeek") == ["SATURDAY", "SUNDAY"],
            "%s: daysOfTheWeek must be sent as the caller supplied them, got %r"
            % (scenario, schedule.get("daysOfTheWeek")),
        )
        check(
            schedule.get("expirationDate") == "11/30/2027",
            "%s: expirationDate must be sent verbatim, got %r"
            % (scenario, schedule.get("expirationDate")),
        )
        check(
            schedule.get("scheduleType") == "WEEKLY" and schedule.get("duration") == 90,
            "%s: scheduleType/duration mismatch in %s"
            % (scenario, json.dumps(schedule, sort_keys=True)),
        )

    check(
        calls.get("create", {}).get("action") == "Created",
        "%s: create call Action must be 'Created', got %r"
        % (scenario, calls.get("create", {}).get("action")),
    )
    check(
        calls.get("reordered", {}).get("action") == "Unchanged",
        "%s: DaysOfTheWeek is a set; reordering it must be recognised as no change, "
        "got Action %r" % (scenario, calls.get("reordered", {}).get("action")),
    )
    check(
        calls.get("duplicated", {}).get("action") == "Unchanged",
        "%s: DaysOfTheWeek is a set; duplicate entries must not cause a change, "
        "got Action %r" % (scenario, calls.get("duplicated", {}).get("action")),
    )


def scenario_full_convergence():
    scenario = "full-convergence"
    calls, entries = run_scenario(scenario)
    assert_baseline(scenario, calls, entries)

    creates = by_op(entries, "createMaintenanceSchedules")
    updates = by_op(entries, "updateMaintenanceSchedules")
    check(
        len(creates) == 1,
        "%s: exactly one createMaintenanceSchedules request expected, saw %d"
        % (scenario, len(creates)),
    )
    check(
        len(updates) == 14,
        "%s: each independently changed field and omitted optional must update "
        "in place, while the settle call must not write; saw %d update(s)"
        % (scenario, len(updates)),
    )

    desired = {
        "scheduleType": "DAILY",
        "hour": 1,
        "minuteOfTheHour": 5,
        "duration": 30,
        "recurrence": 2,
        "daysOfTheWeek": ["MONDAY", "WEDNESDAY"],
        "expirationDate": "12/31/2028",
        "expireRuns": 7,
        "timeZone": "UTC",
    }
    expected_updates = []
    for field, value in (
        ("scheduleType", "WEEKLY"),
        ("hour", 4),
        ("minuteOfTheHour", 45),
        ("duration", 75),
        ("recurrence", 3),
        ("daysOfTheWeek", ["THURSDAY", "FRIDAY"]),
        ("expirationDate", "01/31/2029"),
        ("expireRuns", 11),
        ("timeZone", "America/Chicago"),
    ):
        desired[field] = value
        expected_updates.append(dict(desired))
    for field in (
        "recurrence",
        "daysOfTheWeek",
        "expirationDate",
        "expireRuns",
        "timeZone",
    ):
        desired.pop(field)
        expected_updates.append(dict(desired))

    if creates:
        created_body = body_of(creates[0])
        expected_create = {
            "scheduleType": "DAILY",
            "hour": 1,
            "minuteOfTheHour": 5,
            "duration": 30,
            "recurrence": 2,
            "daysOfTheWeek": ["MONDAY", "WEDNESDAY"],
            "expirationDate": "12/31/2028",
            "expireRuns": 7,
            "timeZone": "UTC",
        }
        check(
            created_body.get("schedule") == expected_create,
            "%s: create must project every bound optional field exactly; got %s"
            % (scenario, json.dumps(created_body.get("schedule"), sort_keys=True)),
        )

    created_id = calls.get("create", {}).get("id")
    for index, (entry, expected_schedule) in enumerate(
        zip(updates, expected_updates), 1
    ):
        body = body_of(entry)
        check(
            body.get("id") == created_id,
            "%s: update %d must retain the server-assigned id %r, sent %r"
            % (scenario, index, created_id, body.get("id")),
        )
        check(
            body.get("schedule") == expected_schedule,
            "%s: update %d schedule mismatch; expected %s, got %s"
            % (
                scenario,
                index,
                json.dumps(expected_schedule, sort_keys=True),
                json.dumps(body.get("schedule"), sort_keys=True),
            ),
        )

    expected_actions = [
        ("create", "Created"),
        ("schedule-type", "Updated"),
        ("hour", "Updated"),
        ("minute", "Updated"),
        ("duration", "Updated"),
        ("recurrence", "Updated"),
        ("days", "Updated"),
        ("expiration", "Updated"),
        ("expire-runs", "Updated"),
        ("time-zone", "Updated"),
        ("remove-recurrence", "Updated"),
        ("remove-days", "Updated"),
        ("remove-expiration", "Updated"),
        ("remove-expire-runs", "Updated"),
        ("remove-time-zone", "Updated"),
        ("settle", "Unchanged"),
    ]
    for label, action in expected_actions:
        call = calls.get(label, {})
        check(
            call.get("action") == action,
            "%s: %s Action must be %r, got %r"
            % (scenario, label, action, call.get("action")),
        )
        check(
            created_id and call.get("id") == created_id,
            "%s: %s must report the server-assigned id %r, got %r"
            % (scenario, label, created_id, call.get("id")),
        )
        check(
            call.get("key") == "fleet-patch-window-full-convergence",
            "%s: %s returned Key mismatch: %r"
            % (scenario, label, call.get("key")),
        )


def check_static():
    if not os.path.exists(IMPLEMENTATION):
        fatal("missing %s" % os.path.relpath(IMPLEMENTATION, ROOT))

    with open(IMPLEMENTATION, encoding="utf-8") as fh:
        source = fh.read()

    # Comments may document prohibited alternatives without using them.
    stripped = re.sub(r"(?s)<#.*?#>", "", source)
    stripped = re.sub(r"(?m)^\s*#.*$", "", stripped)
    for pattern, label in BANNED_HTTP_PATTERNS:
        check(
            re.search(pattern, stripped) is None,
            "the module must drive VCF Operations through VMware.Sdk.Vcf.Ops cmdlets, "
            "but the implementation references %s" % label,
        )
    check(
        "NotImplementedException" not in stripped,
        "Set-VcfOpsMaintenanceWindow still throws NotImplementedException",
    )
    for cmdlet in (
        "Initialize-VcfOpsschedule",
        "Initialize-VcfOpsmaintenanceschedule",
        "Invoke-VcfOpsGetMaintenanceSchedules",
        "Invoke-VcfOpsCreateMaintenanceSchedules",
        "Invoke-VcfOpsUpdateMaintenanceSchedules",
    ):
        check(
            cmdlet in stripped,
            "the implementation must use the SDK cmdlet %s" % cmdlet,
        )

    with open(CONTRACT, encoding="utf-8") as fh:
        contract = json.load(fh)
    with open(SOURCES, encoding="utf-8") as fh:
        sources = json.load(fh)
    check(
        contract["source"]["commit"] == sources["specification"]["commit"],
        "docs/contract.json and docs/official_sources.json disagree on the spec commit",
    )
    check(
        set(contract["operations"]) == {op["operationId"] for op in sources["operations"]},
        "docs/official_sources.json must record every operationId the contract names",
    )


def check_signature():
    """Load the module and verify the public signature the task says to retain."""
    script = r"""
$ErrorActionPreference = 'Stop'
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
Import-Module $env:VCFOPS_SIGNATURE_MANIFEST -Force
$command = Get-Command Set-VcfOpsMaintenanceWindow
$command.Parameters.GetEnumerator() | ForEach-Object {
    $attribute = $_.Value.Attributes |
        Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] } |
        Select-Object -First 1
    [pscustomobject]@{
        name = $_.Key
        type = $_.Value.ParameterType.FullName
        mandatory = [bool] $attribute.Mandatory
    }
} | ConvertTo-Json -Compress
"""
    signature_env = os.environ.copy()
    signature_env["VCFOPS_SIGNATURE_MANIFEST"] = MANIFEST
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        capture_output=True,
        env=signature_env,
        text=True,
        timeout=180,
    )
    if proc.returncode != 0:
        check(
            False,
            "the module manifest and implementation must import successfully for "
            "signature inspection; stderr: %s" % proc.stderr.strip(),
        )
        return
    try:
        parameters = json.loads(proc.stdout)
    except (TypeError, ValueError):
        check(
            False,
            "could not inspect Set-VcfOpsMaintenanceWindow parameters; stdout=%r stderr=%r"
            % (proc.stdout.strip(), proc.stderr.strip()),
        )
        return
    if isinstance(parameters, dict):
        parameters = [parameters]

    common = {
        "Verbose",
        "Debug",
        "ErrorAction",
        "WarningAction",
        "InformationAction",
        "ProgressAction",
        "ErrorVariable",
        "WarningVariable",
        "InformationVariable",
        "OutVariable",
        "OutBuffer",
        "PipelineVariable",
    }
    actual = {item["name"]: item for item in parameters if item["name"] not in common}
    expected = {
        "Server": ("System.Object", True),
        "Key": ("System.String", True),
        "ScheduleType": ("System.String", True),
        "Hour": ("System.Int32", True),
        "MinuteOfTheHour": ("System.Int32", True),
        "DurationMinutes": ("System.Int32", True),
        "Recurrence": ("System.Int32", False),
        "DaysOfTheWeek": ("System.String[]", False),
        "ExpirationDate": ("System.String", False),
        "ExpireRuns": ("System.Int32", False),
        "TimeZone": ("System.String", False),
    }
    check(
        set(actual) == set(expected),
        "Set-VcfOpsMaintenanceWindow public parameters changed; expected %s, got %s"
        % (sorted(expected), sorted(actual)),
    )
    for name, (parameter_type, mandatory) in expected.items():
        if name not in actual:
            continue
        check(
            actual[name].get("type") == parameter_type,
            "%s must retain type %s, got %r"
            % (name, parameter_type, actual[name].get("type")),
        )
        check(
            actual[name].get("mandatory") is mandatory,
            "%s Mandatory must remain %s, got %r"
            % (name, mandatory, actual[name].get("mandatory")),
        )


def main():
    try:
        probe = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command",
             "if (Get-Module -ListAvailable -Name VMware.Sdk.Vcf.Ops) { 'yes' } else { 'no' }"],
            capture_output=True, text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal("PowerShell 7.4+ is required but could not be run: %s" % exc)
    if "yes" not in probe.stdout:
        fatal(
            "the VMware.Sdk.Vcf.Ops PowerCLI module is an environment prerequisite and "
            "was not found. stdout: %s stderr: %s" % (probe.stdout.strip(), probe.stderr.strip())
        )

    check_static()
    check_signature()
    if FAILURES:
        # Static problems make every scenario result misleading, so stop here.
        report()
        return 1

    scenario_create_then_retry()
    scenario_drift_update()
    scenario_weekly_omission()
    scenario_full_convergence()

    if FAILURES:
        report()
        return 1

    print("PASS: %d checks" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
