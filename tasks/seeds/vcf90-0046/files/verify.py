#!/usr/bin/env python3
"""Protected verification for the vcfrotate secret-rotation job.

Boots the contract-pinned loopback vCenter (mock_vcenter.py) on 127.0.0.1,
drives `python3 -m vcfrotate` through a clean rotation, three aborts and failed
work responses, then
reads the mock's request log and asserts the exact wire shape and ordering of
every request: which session identifier each one carried, which credential was
presented where, the form-encoded body of every token exchange, the configured
concurrency ceiling, both endpoint error shapes, and that unset optional
properties are omitted rather than sent empty.

No live VMware endpoint is contacted.  Credentials are fixture dummies.

This file is protected.  Do not modify it.
"""

import ast
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "_verification")
CONTRACT_PATH = os.path.join(ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(ROOT, "docs", "official_sources.json")
PACKAGE = os.path.join(ROOT, "vcfrotate")

SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
SPEC_TAG = "9.0.0.0"
SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXCLUDED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
OPERATION_IDS = {
    "Cis.Session_create",
    "Cis.Session_get",
    "Cis.Session_delete",
    "Vcenter.Authentication.Token_issue",
}

USERNAME = "svc-rotation@vsphere.local"
OTHER_USER = "svc-decommissioned@vsphere.local"
OLD_PASSWORD = "fixture-old-not-a-real-password"
NEW_PASSWORD = "fixture-new-not-a-real-password"
WRONG_PASSWORD = "fixture-wrong-not-a-real-password"

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
ACTOR_TOKEN = "ZmFrZS1hY3Rvci10b2tlbg=="

ALT_BASE_PATH = "/vsphere-automation"

WORK_ITEMS = {
    "in_flight": [
        {"id": "flight-a", "subject_token_type": ACCESS_TOKEN_TYPE},
        {"id": "flight-b", "subject_token_type": ACCESS_TOKEN_TYPE,
         "audience": "vcenter-inventory", "scope": "vcenter:read vcenter:write"},
        {"id": "flight-c", "subject_token_type": ACCESS_TOKEN_TYPE,
         "actor_token": ACTOR_TOKEN, "actor_token_type": JWT_TOKEN_TYPE},
        {"id": "flight-d", "subject_token_type": ACCESS_TOKEN_TYPE,
         "resource": "https://inventory.example.internal/api"},
    ],
    "post_rotation": [
        {"id": "after-a", "subject_token_type": ACCESS_TOKEN_TYPE,
         "resource": "https://vcenter.example.internal/api",
         "requested_token_type": JWT_TOKEN_TYPE},
        {"id": "after-b", "subject_token_type": ID_TOKEN_TYPE},
    ],
}

ERROR_WORK_ITEMS = {
    "in_flight": [
        # Omitting actor_token_type makes the endpoint return its documented
        # Oauth2.Errors.Error response after this exchange reaches the wire.
        {"id": "oauth-failure", "subject_token_type": ACCESS_TOKEN_TYPE,
         "actor_token": ACTOR_TOKEN},
    ],
    "post_rotation": [
        # In the error scenario the mock expires this request's session just
        # before authentication, producing a Vapi.Std.Errors.Error response.
        {"id": "vapi-failure", "subject_token_type": ACCESS_TOKEN_TYPE},
    ],
}

# The token exchange carries no work-item id on the wire, so each item is
# identified by the property set it produces plus its subject_token_type.
EXPECTED_FORM = {
    "flight-a": ({"grant_type", "subject_token", "subject_token_type"}, ACCESS_TOKEN_TYPE),
    "flight-b": ({"grant_type", "subject_token", "subject_token_type",
                  "audience", "scope"}, ACCESS_TOKEN_TYPE),
    "flight-c": ({"grant_type", "subject_token", "subject_token_type",
                  "actor_token", "actor_token_type"}, ACCESS_TOKEN_TYPE),
    "flight-d": ({"grant_type", "subject_token", "subject_token_type",
                  "resource"}, ACCESS_TOKEN_TYPE),
    "after-a": ({"grant_type", "subject_token", "subject_token_type",
                 "resource", "requested_token_type"}, ACCESS_TOKEN_TYPE),
    "after-b": ({"grant_type", "subject_token", "subject_token_type"}, ID_TOKEN_TYPE),
}
IN_FLIGHT_IDS = ["flight-a", "flight-b", "flight-c", "flight-d"]
POST_ROTATION_IDS = ["after-a", "after-b"]
WORK_BY_ID = {item["id"]: item
              for phase in ("in_flight", "post_rotation")
              for item in WORK_ITEMS[phase]}

BARRIER = len(IN_FLIGHT_IDS)
HOLD_MS = 1000

CHECKS = 0
FAILURES = []


def check(label, condition, detail=None):
    global CHECKS
    CHECKS += 1
    if condition:
        return True
    FAILURES.append(label)
    print("FAIL %s" % label)
    if detail:
        for line in str(detail).splitlines():
            print("     " + line)
    return False


def check_eq(label, expected, actual):
    if expected == actual:
        return check(label, True)
    return check(label, False, "expected: %s\nactual:   %s" % (_show(expected), _show(actual)))


def _show(value):
    if isinstance(value, (set, frozenset)):
        value = sorted(value)
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


def session_token(ordinal):
    """The mock mints session identifiers deterministically."""
    return "7b3f9c2e4d1a48f0b6c5%012x" % ordinal


def basic(username, password):
    return "Basic " + base64.b64encode(
        ("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# mock lifecycle
# ---------------------------------------------------------------------------

class Mock:
    def __init__(self, name, barrier=0, hold_ms=0, session_user=None,
                 contract=CONTRACT_PATH, extra=()):
        self.name = name
        self.log_path = os.path.join(WORK, name + ".requests.jsonl")
        self.port_path = os.path.join(WORK, name + ".port")
        for path in (self.log_path, self.port_path):
            if os.path.exists(path):
                os.remove(path)
        argv = [sys.executable, "-B", os.path.join(ROOT, "mock_vcenter.py"),
                "--contract", contract,
                "--log", self.log_path,
                "--port-file", self.port_path,
                "--username", USERNAME,
                "--password", OLD_PASSWORD,
                "--rotated-password", NEW_PASSWORD,
                "--barrier-count", str(barrier),
                "--hold-ms", str(hold_ms)]
        argv += list(extra)
        if session_user is not None:
            argv += ["--session-user", session_user]
        self.proc = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.port = self._await_port()
        self.base_url = "http://127.0.0.1:%d" % self.port

    def _await_port(self):
        deadline = time.time() + 20
        while time.time() < deadline:
            if os.path.exists(self.port_path):
                with open(self.port_path, "r", encoding="utf-8") as handle:
                    text = handle.read().strip()
                if text:
                    return int(text)
            if self.proc.poll() is not None:
                out, err = self.proc.communicate()
                raise SystemExit("mock_vcenter.py exited early (%s)\n%s\n%s"
                                 % (self.proc.returncode, out, err))
            time.sleep(0.05)
        raise SystemExit("mock_vcenter.py did not report a port within 20s")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)

    def log(self):
        entries = []
        if os.path.exists(self.log_path):
            with open(self.log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        entries.sort(key=lambda entry: entry["completed"])
        return entries


def run_job(mock, report_name, work_name, new_password, contract=None, extra=(),
            old_password=OLD_PASSWORD, work_items=WORK_ITEMS):
    report_path = os.path.join(WORK, report_name)
    work_path = os.path.join(WORK, work_name)
    if os.path.exists(report_path):
        os.remove(report_path)
    with open(work_path, "w", encoding="utf-8") as handle:
        json.dump(work_items, handle, indent=2)
    argv = [sys.executable, "-B", "-m", "vcfrotate",
            "--base-url", mock.base_url,
            "--username", USERNAME,
            "--old-password", old_password,
            "--new-password", new_password,
            "--work", work_path,
            "--report", report_path]
    if contract is not None:
        argv += ["--contract", contract]
    argv += list(extra)
    completed = subprocess.run(argv, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=180)
    report = None
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as handle:
            try:
                report = json.load(handle)
            except json.JSONDecodeError as err:
                report = {"__unparsable__": str(err)}
    return completed, report


# ---------------------------------------------------------------------------
# shared log helpers
# ---------------------------------------------------------------------------

def by_operation(entries, operation_id):
    return [e for e in entries if e["operationId"] == operation_id]


def creates(entries):
    return by_operation(entries, "Cis.Session_create")


def gets(entries):
    return by_operation(entries, "Cis.Session_get")


def deletes(entries):
    return by_operation(entries, "Cis.Session_delete")


def issues(entries):
    return by_operation(entries, "Vcenter.Authentication.Token_issue")


def form_of(entry):
    return entry.get("form") or {}


def identify(entry):
    """Map a token exchange back to the work item that produced it."""
    keys = set(form_of(entry))
    subject_type = form_of(entry).get("subject_token_type")
    for item_id, (expected_keys, expected_type) in EXPECTED_FORM.items():
        if keys == expected_keys and subject_type == expected_type:
            return item_id
    return None


def assert_no_secret_leak(prefix, entries):
    for entry in entries:
        where = "%s %s" % (entry["method"], entry["target"])
        for secret in (OLD_PASSWORD, NEW_PASSWORD, WRONG_PASSWORD):
            check("%s: %s does not carry a password in its body" % (prefix, where),
                  secret not in entry["body"], entry["body"])
            for name, value in entry["headers"].items():
                if name == "authorization":
                    continue
                check("%s: %s header %r does not carry a password"
                      % (prefix, where, name), secret not in value, value)


def assert_no_empty_form_values(prefix, entries):
    """Every token exchange omits what it did not set, rather than blanking it."""
    for entry in issues(entries):
        pairs = [chunk for chunk in entry["body"].split("&") if chunk != ""]
        empties = sorted(chunk.split("=", 1)[0] for chunk in pairs
                         if "=" not in chunk or chunk.split("=", 1)[1] == "")
        check_eq("%s: the exchange at %s omits unset optional properties instead of "
                 "sending them empty" % (prefix, entry["target"]), [], empties)
        names = [chunk.split("=", 1)[0] for chunk in pairs]
        check_eq("%s: the exchange at %s sends each property once"
                 % (prefix, entry["target"]), sorted(set(names)), sorted(names))


def assert_exchange_shape(prefix, entry, item_id, expected_session):
    where = "%s (%s)" % (item_id, entry["target"])
    headers = entry["headers"]
    form = form_of(entry)

    check_eq("%s: %s uses the form media type" % (prefix, where),
             "application/x-www-form-urlencoded",
             headers.get("content-type", "").split(";")[0].strip().lower())
    check("%s: %s carries no Authorization header" % (prefix, where),
          "authorization" not in headers, sorted(headers))
    check_eq("%s: %s is bound to the expected session" % (prefix, where),
             expected_session, entry["session"])
    check_eq("%s: %s sends exactly the properties it set" % (prefix, where),
             EXPECTED_FORM[item_id][0], set(form))
    check_eq("%s: %s sends the token-exchange grant type" % (prefix, where),
             TOKEN_EXCHANGE, form.get("grant_type"))
    check_eq("%s: %s sends the work item's subject token type" % (prefix, where),
             WORK_BY_ID[item_id]["subject_token_type"],
             form.get("subject_token_type"))
    for name in ("resource", "audience", "scope", "requested_token_type",
                 "actor_token", "actor_token_type"):
        if name in WORK_BY_ID[item_id]:
            check_eq("%s: %s preserves %s from the work item"
                     % (prefix, where, name), WORK_BY_ID[item_id][name],
                     form.get(name))

    raw = entry["body"]
    pairs = [chunk for chunk in raw.split("&") if chunk != ""]
    check_eq("%s: %s sends one pair per property" % (prefix, where),
             len(EXPECTED_FORM[item_id][0]), len(pairs))
    empties = sorted(chunk.split("=", 1)[0] for chunk in pairs
                     if "=" not in chunk or chunk.split("=", 1)[1] == "")
    check_eq("%s: %s omits unset optional properties instead of sending them empty"
             % (prefix, where), [], empties)

    try:
        decoded = base64.b64decode(form.get("subject_token", ""), validate=True).decode("utf-8")
    except Exception as err:  # noqa: BLE001 - report whatever went wrong
        decoded = "<not base64: %s>" % err
    check_eq("%s: %s base64-encodes the bound session as subject_token" % (prefix, where),
             expected_session, decoded)


def assert_session_calls(prefix, entries, expected_old, expected_new):
    """Credential discipline on the three body-less session operations."""
    created = creates(entries)
    for index, entry in enumerate(created):
        where = "Cis.Session_create #%d" % (index + 1)
        check("%s: %s carries no session header" % (prefix, where),
              entry["session"] is None, entry["session"])
        check("%s: %s carries no request body" % (prefix, where),
              entry["body"] == "", entry["body"])
        check("%s: %s declares no Content-Type" % (prefix, where),
              "content-type" not in entry["headers"], sorted(entry["headers"]))
    if len(created) >= 1:
        check_eq("%s: the first login presents the retiring secret" % prefix,
                 basic(USERNAME, expected_old), created[0]["headers"].get("authorization"))
    if len(created) >= 2:
        check_eq("%s: the second login presents the incoming secret" % prefix,
                 basic(USERNAME, expected_new), created[1]["headers"].get("authorization"))

    for entry in gets(entries) + deletes(entries):
        where = "%s %s" % (entry["method"], entry["target"])
        check("%s: %s carries no Authorization header" % (prefix, where),
              "authorization" not in entry["headers"], sorted(entry["headers"]))
        check("%s: %s carries no request body" % (prefix, where),
              entry["body"] == "", entry["body"])
        check("%s: %s declares no Content-Type" % (prefix, where),
              "content-type" not in entry["headers"], sorted(entry["headers"]))

    for entry in entries:
        if entry["operationId"] == "Cis.Session_create":
            continue
        check("%s: only Cis.Session_create presents an Authorization header (%s %s)"
              % (prefix, entry["method"], entry["target"]),
              "authorization" not in entry["headers"], sorted(entry["headers"]))


def report_entry(report, item_id):
    for row in report.get("requests", []):
        if isinstance(row, dict) and row.get("id") == item_id:
            return row
    return None


def assert_report_shape(prefix, report):
    check_eq("%s: the report has exactly the documented keys" % prefix,
             {"user", "outcome", "aborted_reason", "retired_session_deleted",
              "requests"}, set(report))


def max_concurrent(entries):
    """Maximum number of request intervals open at any recorded arrival."""
    if not entries:
        return 0
    return max(sum(other["arrived"] <= entry["arrived"] < other["completed"]
                   for other in entries)
               for entry in entries)


def assert_no_process_secret_leak(prefix, completed, report):
    rendered = completed.stdout + completed.stderr + json.dumps(report, sort_keys=True)
    for secret in (OLD_PASSWORD, NEW_PASSWORD, WRONG_PASSWORD):
        check("%s: process output and report do not expose a password" % prefix,
              secret not in rendered)


# ---------------------------------------------------------------------------
# scenario 1: the rotation completes
# ---------------------------------------------------------------------------

def scenario_rotated():
    prefix = "rotated"
    mock = Mock("rotated", barrier=2, hold_ms=HOLD_MS)
    try:
        completed, report = run_job(mock, "rotated.report.json", "rotated.work.json",
                                    NEW_PASSWORD, extra=("--workers", "2"))
    finally:
        mock.stop()
    entries = mock.log()

    check_eq("%s: the job exits 0" % prefix, 0, completed.returncode)
    if completed.returncode != 0:
        print("     stdout: %s" % completed.stdout.strip())
        print("     stderr: %s" % completed.stderr.strip())
    if not check("%s: a report was written" % prefix, isinstance(report, dict)
                 and "__unparsable__" not in report, report):
        return
    assert_report_shape(prefix, report)
    assert_no_process_secret_leak(prefix, completed, report)

    old_session = session_token(1)
    new_session = session_token(2)

    check_eq("%s: exactly two sessions are opened" % prefix, 2, len(creates(entries)))
    check_eq("%s: the incoming session is inspected exactly once" % prefix,
             1, len(gets(entries)))
    check_eq("%s: both sessions are terminated" % prefix, 2, len(deletes(entries)))
    check_eq("%s: every work item produced one token exchange" % prefix,
             len(IN_FLIGHT_IDS) + len(POST_ROTATION_IDS), len(issues(entries)))
    check_eq("%s: --workers is the in-flight concurrency ceiling" % prefix,
             2, max_concurrent([e for e in issues(entries)
                                if e["session"] == old_session]))
    check_eq("%s: nothing outside the contract is called" % prefix,
             [], [e["target"] for e in entries if e["operationId"] is None])
    check_eq("%s: no request is answered 401" % prefix,
             [], ["%s %s" % (e["method"], e["target"]) for e in entries
                  if e["status"] == 401])
    check_eq("%s: no in-flight request is stranded" % prefix,
             [], ["%s %s" % (e["method"], e["target"]) for e in entries
                  if e.get("stranded")])

    assert_session_calls(prefix, entries, OLD_PASSWORD, NEW_PASSWORD)
    assert_no_secret_leak(prefix, entries)
    assert_no_empty_form_values(prefix, entries)

    seen = {}
    for entry in issues(entries):
        item_id = identify(entry)
        if not check("%s: every token exchange matches a work item (%s)"
                     % (prefix, entry["body"]), item_id is not None):
            continue
        check("%s: %s is exchanged once" % (prefix, item_id), item_id not in seen)
        seen[item_id] = entry
    check_eq("%s: all work items reached the endpoint" % prefix,
             set(EXPECTED_FORM), set(seen))

    for item_id in IN_FLIGHT_IDS:
        if item_id in seen:
            assert_exchange_shape(prefix, seen[item_id], item_id, old_session)
            check_eq("%s: %s succeeds" % (prefix, item_id), 200, seen[item_id]["status"])
    for item_id in POST_ROTATION_IDS:
        if item_id in seen:
            assert_exchange_shape(prefix, seen[item_id], item_id, new_session)
            check_eq("%s: %s succeeds" % (prefix, item_id), 200, seen[item_id]["status"])

    if len(seen) != len(EXPECTED_FORM):
        return

    inspect = gets(entries)[0] if gets(entries) else None
    delete_old = [e for e in deletes(entries) if e["session"] == old_session]
    delete_new = [e for e in deletes(entries) if e["session"] == new_session]
    check_eq("%s: the retiring session is terminated once" % prefix, 1, len(delete_old))
    check_eq("%s: the incoming session is terminated once" % prefix, 1, len(delete_new))
    if not (delete_old and delete_new and inspect):
        return
    delete_old = delete_old[0]
    delete_new = delete_new[0]

    check_eq("%s: the incoming session is the one that was inspected" % prefix,
             new_session, inspect["session"])

    drain_done = max(seen[i]["completed"] for i in IN_FLIGHT_IDS)
    second_login = creates(entries)[1]

    check("%s: the rotation does not stall behind the in-flight work" % prefix,
          second_login["arrived"] < drain_done,
          "second login arrived at %d, the last in-flight exchange finished at %d"
          % (second_login["arrived"], drain_done))
    check("%s: the incoming session is inspected before anything is retired" % prefix,
          inspect["completed"] < delete_old["arrived"],
          "inspect completed at %d, retire arrived at %d"
          % (inspect["completed"], delete_old["arrived"]))
    check("%s: the retiring session is terminated only after its work has drained"
          % prefix, delete_old["arrived"] > drain_done,
          "retire arrived at %d, last in-flight exchange finished at %d"
          % (delete_old["arrived"], drain_done))
    check("%s: post-rotation work is issued after the retiring session is gone"
          % prefix,
          min(seen[i]["arrived"] for i in POST_ROTATION_IDS) > delete_old["completed"],
          "first post-rotation exchange arrived at %d, retire completed at %d"
          % (min(seen[i]["arrived"] for i in POST_ROTATION_IDS), delete_old["completed"]))
    check("%s: the incoming session is released last" % prefix,
          delete_new["completed"] == max(e["completed"] for e in entries),
          "incoming release completed at %d, last request completed at %d"
          % (delete_new["completed"], max(e["completed"] for e in entries)))

    # report
    check_eq("%s: the report names the rotated account" % prefix, USERNAME,
             report.get("user"))
    check_eq("%s: the outcome is rotated" % prefix, "rotated", report.get("outcome"))
    check_eq("%s: no abort reason is recorded" % prefix, None,
             report.get("aborted_reason"))
    check_eq("%s: the retiring session is reported as deleted" % prefix, True,
             report.get("retired_session_deleted"))
    check_eq("%s: the report lists the work items in file order" % prefix,
             IN_FLIGHT_IDS + POST_ROTATION_IDS,
             [row.get("id") for row in report.get("requests", [])
              if isinstance(row, dict)])
    for item_id in IN_FLIGHT_IDS + POST_ROTATION_IDS:
        row = report_entry(report, item_id)
        if not check("%s: the report covers %s" % (prefix, item_id), row is not None):
            continue
        phase = "in_flight" if item_id in IN_FLIGHT_IDS else "post_rotation"
        check_eq("%s: %s is reported in the %s phase" % (prefix, item_id, phase),
                 phase, row.get("phase"))
        check_eq("%s: %s is reported as succeeded" % (prefix, item_id),
                 "succeeded", row.get("status"))
        check_eq("%s: %s carries the keys of a succeeded request" % (prefix, item_id),
                 {"id", "phase", "status", "issued_token_type"}, set(row))
        expected_type = WORK_BY_ID[item_id].get(
            "requested_token_type", ACCESS_TOKEN_TYPE)
        check_eq("%s: %s reports the token type the endpoint issued"
                 % (prefix, item_id), expected_type, row.get("issued_token_type"))


# ---------------------------------------------------------------------------
# scenario 2: the incoming secret is rejected
# ---------------------------------------------------------------------------

def scenario_secret_rejected():
    prefix = "rejected"
    mock = Mock("rejected", barrier=BARRIER, hold_ms=HOLD_MS)
    try:
        completed, report = run_job(mock, "rejected.report.json", "rejected.work.json",
                                    WRONG_PASSWORD)
    finally:
        mock.stop()
    entries = mock.log()

    check_eq("%s: the job exits 1" % prefix, 1, completed.returncode)
    if not check("%s: a report was written" % prefix, isinstance(report, dict)
                 and "__unparsable__" not in report, report):
        return
    assert_report_shape(prefix, report)
    assert_no_process_secret_leak(prefix, completed, report)

    old_session = session_token(1)
    check_eq("%s: two logins are attempted" % prefix, 2, len(creates(entries)))
    check_eq("%s: the second login is refused" % prefix, 401,
             creates(entries)[1]["status"] if len(creates(entries)) > 1 else None)
    check_eq("%s: no session is inspected" % prefix, 0, len(gets(entries)))
    check_eq("%s: the retiring session is left alive for a retry" % prefix,
             [], ["%s %s" % (e["method"], e["target"]) for e in deletes(entries)])
    check_eq("%s: only the in-flight work is issued" % prefix,
             len(IN_FLIGHT_IDS), len(issues(entries)))
    check_eq("%s: the default worker count permits four concurrent exchanges"
             % prefix, 4, max_concurrent(issues(entries)))
    check_eq("%s: nothing outside the contract is called" % prefix,
             [], [e["target"] for e in entries if e["operationId"] is None])
    check_eq("%s: no in-flight request is stranded" % prefix,
             [], ["%s %s" % (e["method"], e["target"]) for e in entries
                  if e.get("stranded")])

    assert_session_calls(prefix, entries, OLD_PASSWORD, WRONG_PASSWORD)
    assert_no_secret_leak(prefix, entries)
    assert_no_empty_form_values(prefix, entries)

    seen = {}
    for entry in issues(entries):
        item_id = identify(entry)
        if item_id is not None:
            seen[item_id] = entry
    check_eq("%s: the in-flight work still ran" % prefix, set(IN_FLIGHT_IDS), set(seen))
    for item_id in seen:
        assert_exchange_shape(prefix, seen[item_id], item_id, old_session)
        check_eq("%s: %s succeeds despite the failed rotation" % (prefix, item_id),
                 200, seen[item_id]["status"])

    check_eq("%s: the outcome is aborted" % prefix, "aborted", report.get("outcome"))
    check_eq("%s: the abort reason names the refused login" % prefix,
             "new_session_rejected", report.get("aborted_reason"))
    check_eq("%s: the retiring session is reported as still alive" % prefix, False,
             report.get("retired_session_deleted"))
    check_eq("%s: no account was confirmed" % prefix, None, report.get("user"))
    for item_id in IN_FLIGHT_IDS:
        row = report_entry(report, item_id) or {}
        check_eq("%s: %s is reported as succeeded" % (prefix, item_id),
                 "succeeded", row.get("status"))
    for item_id in POST_ROTATION_IDS:
        row = report_entry(report, item_id) or {}
        check_eq("%s: %s was never attempted" % (prefix, item_id),
                 "not_attempted", row.get("status"))
        check_eq("%s: %s carries the keys of an unattempted request" % (prefix, item_id),
                 {"id", "phase", "status"}, set(row))


# ---------------------------------------------------------------------------
# scenario 3: the incoming session belongs to somebody else
# ---------------------------------------------------------------------------

def scenario_user_mismatch():
    prefix = "mismatch"
    alt_contract = os.path.join(WORK, "alt-contract.json")
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    document["basePath"] = ALT_BASE_PATH
    document["serverUrlTemplate"] = "https://{host}" + ALT_BASE_PATH
    with open(alt_contract, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)

    mock = Mock("mismatch", barrier=BARRIER, hold_ms=HOLD_MS,
                session_user=OTHER_USER, contract=alt_contract)
    try:
        completed, report = run_job(mock, "mismatch.report.json", "mismatch.work.json",
                                    NEW_PASSWORD, contract=alt_contract)
    finally:
        mock.stop()
    entries = mock.log()

    check_eq("%s: the job exits 1" % prefix, 1, completed.returncode)
    if not check("%s: a report was written" % prefix, isinstance(report, dict)
                 and "__unparsable__" not in report, report):
        return
    assert_report_shape(prefix, report)
    assert_no_process_secret_leak(prefix, completed, report)

    old_session = session_token(1)
    new_session = session_token(2)

    check_eq("%s: every request is resolved against the contract's base path" % prefix,
             [], [e["path"] for e in entries
                  if not e["path"].startswith(ALT_BASE_PATH + "/")])
    check_eq("%s: nothing outside the contract is called" % prefix,
             [], [e["target"] for e in entries if e["operationId"] is None])
    check_eq("%s: both logins succeed" % prefix, [201, 201],
             [e["status"] for e in creates(entries)])
    check_eq("%s: the incoming session is inspected once" % prefix, 1, len(gets(entries)))
    check_eq("%s: only the in-flight work is issued" % prefix,
             len(IN_FLIGHT_IDS), len(issues(entries)))
    check_eq("%s: no in-flight request is stranded" % prefix,
             [], ["%s %s" % (e["method"], e["target"]) for e in entries
                  if e.get("stranded")])

    assert_session_calls(prefix, entries, OLD_PASSWORD, NEW_PASSWORD)
    assert_no_secret_leak(prefix, entries)
    assert_no_empty_form_values(prefix, entries)

    released = deletes(entries)
    check_eq("%s: exactly one session is terminated" % prefix, 1, len(released))
    if released:
        check_eq("%s: the terminated session is the unusable incoming one" % prefix,
                 new_session, released[0]["session"])

    seen = {}
    for entry in issues(entries):
        item_id = identify(entry)
        if item_id is not None:
            seen[item_id] = entry
    check_eq("%s: the in-flight work still ran on the retiring session" % prefix,
             set(IN_FLIGHT_IDS), set(seen))
    for item_id in seen:
        assert_exchange_shape(prefix, seen[item_id], item_id, old_session)
        check_eq("%s: %s succeeds despite the failed rotation" % (prefix, item_id),
                 200, seen[item_id]["status"])

    if released and seen:
        drain_done = max(seen[i]["completed"] for i in seen)
        check("%s: the incoming session is released only after the drain" % prefix,
              released[0]["arrived"] > drain_done,
              "release arrived at %d, last in-flight exchange finished at %d"
              % (released[0]["arrived"], drain_done))

    check_eq("%s: the outcome is aborted" % prefix, "aborted", report.get("outcome"))
    check_eq("%s: the abort reason names the mismatched principal" % prefix,
             "user_mismatch", report.get("aborted_reason"))
    check_eq("%s: the retiring session is reported as still alive" % prefix, False,
             report.get("retired_session_deleted"))
    check_eq("%s: the report quotes the principal the endpoint returned" % prefix,
             OTHER_USER, report.get("user"))
    for item_id in POST_ROTATION_IDS:
        row = report_entry(report, item_id) or {}
        check_eq("%s: %s was never attempted" % (prefix, item_id),
                 "not_attempted", row.get("status"))


# ---------------------------------------------------------------------------
# scenario 4: the retiring secret is rejected before any work starts
# ---------------------------------------------------------------------------

def scenario_login_failed():
    prefix = "login-failed"
    mock = Mock("login-failed")
    try:
        completed, report = run_job(
            mock, "login-failed.report.json", "login-failed.work.json",
            NEW_PASSWORD, old_password=WRONG_PASSWORD)
    finally:
        mock.stop()
    entries = mock.log()

    check_eq("%s: the job exits 1" % prefix, 1, completed.returncode)
    if not check("%s: a report was written" % prefix, isinstance(report, dict)
                 and "__unparsable__" not in report, report):
        return
    assert_report_shape(prefix, report)
    assert_no_process_secret_leak(prefix, completed, report)

    check_eq("%s: only the retiring login is attempted" % prefix,
             1, len(creates(entries)))
    check_eq("%s: the retiring login is refused" % prefix, 401,
             creates(entries)[0]["status"] if creates(entries) else None)
    check_eq("%s: no session work is attempted" % prefix, 0,
             len(gets(entries)) + len(deletes(entries)) + len(issues(entries)))
    assert_session_calls(prefix, entries, WRONG_PASSWORD, NEW_PASSWORD)
    assert_no_secret_leak(prefix, entries)

    check_eq("%s: the outcome is aborted" % prefix, "aborted",
             report.get("outcome"))
    check_eq("%s: the abort reason names the retiring login" % prefix,
             "login_failed", report.get("aborted_reason"))
    check_eq("%s: no account was confirmed" % prefix, None, report.get("user"))
    check_eq("%s: no retiring session was deleted" % prefix, False,
             report.get("retired_session_deleted"))
    check_eq("%s: the report retains work-file order" % prefix,
             IN_FLIGHT_IDS + POST_ROTATION_IDS,
             [row.get("id") for row in report.get("requests", [])
              if isinstance(row, dict)])
    for item_id in IN_FLIGHT_IDS + POST_ROTATION_IDS:
        row = report_entry(report, item_id) or {}
        phase = "in_flight" if item_id in IN_FLIGHT_IDS else "post_rotation"
        check_eq("%s: %s was never attempted" % (prefix, item_id),
                 "not_attempted", row.get("status"))
        check_eq("%s: %s retains its phase" % (prefix, item_id),
                 phase, row.get("phase"))
        check_eq("%s: %s carries only unattempted-request keys" % (prefix, item_id),
                 {"id", "phase", "status"}, set(row))


# ---------------------------------------------------------------------------
# scenario 5: both documented work-error response shapes reach the report
# ---------------------------------------------------------------------------

def scenario_work_errors():
    prefix = "work-errors"
    mock = Mock("work-errors", extra=("--expire-session-on-issue", "2"))
    try:
        completed, report = run_job(
            mock, "work-errors.report.json", "work-errors.work.json",
            NEW_PASSWORD, work_items=ERROR_WORK_ITEMS)
    finally:
        mock.stop()
    entries = mock.log()

    check_eq("%s: failed work makes the job exit 1" % prefix,
             1, completed.returncode)
    if not check("%s: a report was written" % prefix, isinstance(report, dict)
                 and "__unparsable__" not in report, report):
        return
    assert_report_shape(prefix, report)
    assert_no_process_secret_leak(prefix, completed, report)

    old_session = session_token(1)
    new_session = session_token(2)
    old_issues = [e for e in issues(entries) if e["session"] == old_session]
    new_issues = [e for e in issues(entries) if e["session"] == new_session]
    old_deletes = [e for e in deletes(entries) if e["session"] == old_session]
    new_deletes = [e for e in deletes(entries) if e["session"] == new_session]

    check_eq("%s: both sessions are opened" % prefix, 2, len(creates(entries)))
    check_eq("%s: the incoming session is inspected once" % prefix, 1,
             len(gets(entries)))
    check_eq("%s: each failing work item reaches the endpoint" % prefix,
             [1, 1], [len(old_issues), len(new_issues)])
    check_eq("%s: the malformed exchange returns OAuth error status" % prefix,
             400, old_issues[0]["status"] if old_issues else None)
    check_eq("%s: the expired-session exchange returns vAPI error status" % prefix,
             401, new_issues[0]["status"] if new_issues else None)
    check_eq("%s: both session releases are attempted" % prefix,
             [1, 1], [len(old_deletes), len(new_deletes)])
    check_eq("%s: the retiring session is successfully terminated" % prefix,
             204, old_deletes[0]["status"] if old_deletes else None)
    check_eq("%s: the expired incoming session answers its final release" % prefix,
             401, new_deletes[0]["status"] if new_deletes else None)
    assert_session_calls(prefix, entries, OLD_PASSWORD, NEW_PASSWORD)
    assert_no_secret_leak(prefix, entries)

    if old_issues and old_deletes:
        check("%s: failed in-flight work settles before retirement" % prefix,
              old_deletes[0]["arrived"] > old_issues[0]["completed"])
    if old_deletes and new_issues:
        check("%s: failed post-rotation work starts after retirement" % prefix,
              new_issues[0]["arrived"] > old_deletes[0]["completed"])
    if new_deletes:
        check("%s: releasing the incoming session is the last call" % prefix,
              new_deletes[0]["completed"] == max(e["completed"] for e in entries))

    check_eq("%s: retirement still makes the outcome rotated" % prefix,
             "rotated", report.get("outcome"))
    check_eq("%s: a completed rotation has no abort reason" % prefix,
             None, report.get("aborted_reason"))
    check_eq("%s: the confirmed principal is reported" % prefix,
             USERNAME, report.get("user"))
    check_eq("%s: retirement is reported" % prefix, True,
             report.get("retired_session_deleted"))
    check_eq("%s: failed work retains file order" % prefix,
             ["oauth-failure", "vapi-failure"],
             [row.get("id") for row in report.get("requests", [])
              if isinstance(row, dict)])

    oauth_row = report_entry(report, "oauth-failure") or {}
    vapi_row = report_entry(report, "vapi-failure") or {}
    for item_id, row, phase in (
            ("oauth-failure", oauth_row, "in_flight"),
            ("vapi-failure", vapi_row, "post_rotation")):
        check_eq("%s: %s is failed" % (prefix, item_id),
                 "failed", row.get("status"))
        check_eq("%s: %s retains its phase" % (prefix, item_id),
                 phase, row.get("phase"))
        check_eq("%s: %s carries exactly the failed-request keys" % (prefix, item_id),
                 {"id", "phase", "status", "error"}, set(row))
        check_eq("%s: %s carries exactly the documented error keys" % (prefix, item_id),
                 {"http_status", "error", "error_description"},
                 set(row.get("error") or {}))

    check_eq("%s: OAuth error fields come from Oauth2.Errors.Error" % prefix,
             {"http_status": 400, "error": "invalid_request",
              "error_description":
                  "actor_token and actor_token_type are sent together or not at all"},
             oauth_row.get("error"))
    check_eq("%s: vAPI error fields come from the first localizable message" % prefix,
             {"http_status": 401, "error": "UNAUTHENTICATED",
              "error_description":
                  "the session identifier is missing or no longer valid"},
             vapi_row.get("error"))


# ---------------------------------------------------------------------------
# scenario 6: the endpoint serves only what the contract names
# ---------------------------------------------------------------------------

def probe(url, method):
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as err:
        err.read()
        return err.code


def scenario_contract_surface():
    prefix = "surface"
    mock = Mock("surface")
    try:
        probes = [
            ("GET", "/api/vcenter/vm"),
            ("PUT", "/api/session"),
            ("POST", "/api/session?spec=1"),
            ("GET", "/api/vcenter/authentication/token"),
            ("POST", "/api/vcenter/authorization/permissions"),
        ]
        for method, target in probes:
            status = probe(mock.base_url + target, method)
            check_eq("%s: %s %s is not served" % (prefix, method, target), 404, status)
    finally:
        mock.stop()
    entries = mock.log()
    check_eq("%s: every refused probe is logged as unrouted" % prefix,
             [None] * 5, [e["operationId"] for e in entries])


# ---------------------------------------------------------------------------
# provenance and dependency checks
# ---------------------------------------------------------------------------

def scenario_provenance():
    prefix = "provenance"
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    with open(SOURCES_PATH, "r", encoding="utf-8") as handle:
        sources = json.load(handle)

    spec = sources["specification"]
    check_eq("%s: the contract is pinned to the 9.0.0.0 tag" % prefix,
             SPEC_TAG, spec.get("repository_tag"))
    check_eq("%s: the pinned commit is the 9.0.0.0 tag's commit" % prefix,
             SPEC_COMMIT, (spec.get("repository_commit_sha") or "").lower())
    check("%s: the 9.1 revision is not the source" % prefix,
          (spec.get("repository_commit_sha") or "").lower() != EXCLUDED_COMMIT)
    check_eq("%s: the spec path is the vCenter automation OpenAPI document" % prefix,
             SPEC_PATH, spec.get("spec_path"))
    check_eq("%s: provenance names every operation in the contract" % prefix,
             OPERATION_IDS,
             {row.get("operationId") for row in sources.get("operations", [])})
    for row in sources.get("operations", []):
        check_eq("%s: %s points at the pinned commit" % (prefix, row.get("operationId")),
                 SPEC_COMMIT, (row.get("repository_commit_sha") or "").lower())
        check_eq("%s: %s points at the pinned spec file" % (prefix, row.get("operationId")),
                 SPEC_PATH, row.get("spec_path"))

    check_eq("%s: the contract names exactly the operations in scope" % prefix,
             OPERATION_IDS, {op["operationId"] for op in contract["operations"]})
    check_eq("%s: the contract records the same commit" % prefix,
             SPEC_COMMIT, (contract["source"].get("commitSha") or "").lower())
    check_eq("%s: the contract records the same spec path" % prefix,
             SPEC_PATH, contract["source"].get("specPath"))


def scenario_stdlib_only():
    prefix = "stdlib"
    if not check("%s: the vcfrotate package exists" % prefix, os.path.isdir(PACKAGE)):
        return
    if not check("%s: the package is runnable with python3 -m" % prefix,
                 os.path.exists(os.path.join(PACKAGE, "__main__.py"))):
        return
    allowed = set(sys.stdlib_module_names) | {"vcfrotate"}
    banned = {"requests", "httpx", "urllib3", "pyvmomi", "pyVmomi", "pyVim",
              "vmware", "com", "aiohttp", "yaml", "vsphere_automation_sdk"}
    for dirpath, _dirnames, filenames in os.walk(PACKAGE):
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            rel = os.path.relpath(path, ROOT)
            try:
                tree = ast.parse(source, filename=rel)
            except SyntaxError as err:
                check("%s: %s parses" % (prefix, rel), False, err)
                continue
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        imported.add(node.module.split(".")[0])
            check_eq("%s: %s imports nothing outside the standard library"
                     % (prefix, rel), set(), imported - allowed)
            check_eq("%s: %s takes no VMware or HTTP dependency" % (prefix, rel),
                     set(), imported & banned)
            check("%s: %s does not shell out to curl" % (prefix, rel),
                  "curl" not in source, rel)


def scenario_no_persisted_secrets():
    prefix = "secret-storage"
    for top in (PACKAGE, WORK):
        if not os.path.isdir(top):
            continue
        for dirpath, _dirnames, filenames in os.walk(top):
            for filename in sorted(filenames):
                path = os.path.join(dirpath, filename)
                try:
                    with open(path, "rb") as handle:
                        content = handle.read()
                except OSError as err:
                    check("%s: %s is readable" % (prefix, os.path.relpath(path, ROOT)),
                          False, err)
                    continue
                for secret in (OLD_PASSWORD, NEW_PASSWORD, WRONG_PASSWORD):
                    check("%s: %s does not persist a password"
                          % (prefix, os.path.relpath(path, ROOT)),
                          secret.encode("utf-8") not in content)


# ---------------------------------------------------------------------------

def main():
    os.makedirs(WORK, exist_ok=True)
    for name in os.listdir(WORK):
        target = os.path.join(WORK, name)
        if os.path.isfile(target):
            os.remove(target)

    scenario_provenance()
    scenario_stdlib_only()
    scenario_contract_surface()
    scenario_rotated()
    scenario_secret_rejected()
    scenario_user_mismatch()
    scenario_login_failed()
    scenario_work_errors()
    scenario_no_persisted_secrets()

    print("")
    if FAILURES:
        print("%d of %d checks failed" % (len(FAILURES), CHECKS))
        for label in FAILURES:
            print("  - %s" % label)
        return 1
    print("all %d checks passed" % CHECKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
