#!/usr/bin/env python3
"""
Protected verifier for SDDC-51782.

Reads two artefacts produced by a single run of ./run_tests.sh:

  * mock/runtime/requests.jsonl - every HTTP request the client made, as recorded
    by the loopback mock before it chose a response
  * out/diagnosis.json          - the document the client wrote

and asserts the exact wire shape of every request, including that optional
fields the client did not set were omitted rather than sent as null, as an empty
string, or as a default value.

Expected values are derived from mock/fixtures.json, not hard-coded, so the
verifier and the mock cannot drift apart.

Contacts nothing. Exits 0 on success, 1 on the first failing run.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

REQUEST_LOG = os.path.join(ROOT, "mock", "runtime", "requests.jsonl")
DIAGNOSIS = os.path.join(ROOT, "out", "diagnosis.json")
FIXTURES = json.load(open(os.path.join(ROOT, "mock", "fixtures.json")))
CONTRACT = json.load(open(os.path.join(ROOT, "docs", "contract.json")))

FAILURES = []
CHECKS = [0]


def check(condition, label, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s" % label)
        if detail:
            for line in str(detail).splitlines():
                print("       %s" % line)
        FAILURES.append(label)
    return bool(condition)


def section(title):
    print("\n== %s" % title)


def diff(expected, actual):
    return "expected: %s\nactual:   %s" % (
        json.dumps(expected, sort_keys=True), json.dumps(actual, sort_keys=True))


# ------------------------------------------------------ expected values -----

TASK = next(t for t in FIXTURES["tasks"]
            if t["status"] == "Failed" and t["type"] == "CREDENTIALS_ROTATE")
CRED_TASK = FIXTURES["credentialsTask"]
FAILED_SUBTASKS = [s for s in CRED_TASK["subTasks"] if s["status"] != "SUCCESSFUL"]
FAILED_SUBTASK_IDS = sorted(s["id"] for s in FAILED_SUBTASKS)
AFFECTED_HOSTS = sorted(s["resourceName"] for s in FAILED_SUBTASKS)
ACCESS_TOKEN = FIXTURES["accessToken"]
BUNDLE_ID = FIXTURES["supportBundle"]["id"]
MIN_STATUS_POLLS = FIXTURES["supportBundle"]["inProgressPolls"] + 1

# Root cause: the notification type that covers exactly the affected hosts.
_by_type = {}
for note in FIXTURES["notifications"]:
    hosts = {r.get("name") for r in note.get("resources", [])}
    _by_type.setdefault(note["type"], {"hosts": set(), "domains": set()})
    _by_type[note["type"]]["hosts"] |= hosts
    if note.get("domain"):
        _by_type[note["type"]]["domains"].add(note["domain"]["name"])

_candidates = [t for t, v in _by_type.items() if v["hosts"] >= set(AFFECTED_HOSTS)]
assert len(_candidates) == 1, "fixture design is ambiguous: %r" % _candidates
ROOT_CAUSE_TYPE = _candidates[0]

_domains = {n["domain"]["name"] for n in FIXTURES["notifications"]
            if n["type"] == ROOT_CAUSE_TYPE and n.get("domain")
            and {r.get("name") for r in n.get("resources", [])} & set(AFFECTED_HOSTS)}
assert len(_domains) == 1, "fixture design is ambiguous: %r" % _domains
DOMAIN_NAME = next(iter(_domains))

EXPECTED_BUNDLE_BODY = {
    "logs": {"esxLogs": True, "sddcManagerLogs": True},
    "scope": {"domains": [{"domainName": DOMAIN_NAME}]},
}

EXPECTED_DIAGNOSIS = {
    "taskId": TASK["id"],
    "taskType": TASK["type"],
    "credentialsTaskStatus": CRED_TASK["status"],
    "failedSubtaskIds": FAILED_SUBTASK_IDS,
    "affectedHosts": AFFECTED_HOSTS,
    "domainName": DOMAIN_NAME,
    "rootCauseNotificationType": ROOT_CAUSE_TYPE,
    "supportBundleId": BUNDLE_ID,
    "supportBundleStatus": "SUCCESSFUL",
}
DIAGNOSIS_KEYS = set(EXPECTED_DIAGNOSIS) | {"rootCauseSummary"}


# --------------------------------------------------------------- loading ----

def load_requests():
    if not os.path.isfile(REQUEST_LOG):
        print("FATAL: %s does not exist - did the mock run?" % REQUEST_LOG)
        sys.exit(1)
    out = []
    with open(REQUEST_LOG) as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                print("FATAL: %s line %d is not JSON: %s" % (REQUEST_LOG, line_no, exc))
                sys.exit(1)
    return out


def only(reqs, op):
    return [r for r in reqs if r.get("operationId") == op]


def first_index(reqs, op):
    for i, r in enumerate(reqs):
        if r.get("operationId") == op:
            return i
    return -1


def last_index(reqs, op):
    idx = -1
    for i, r in enumerate(reqs):
        if r.get("operationId") == op:
            idx = i
    return idx


# ----------------------------------------------------------------- checks ---

def verify_contract_provenance():
    section("contract provenance")
    src = CONTRACT["derivedFrom"]
    check(src["specInfoVersion"] == "9.0.0.0",
          "contract is derived from spec version 9.0.0.0", src.get("specInfoVersion"))
    check(src["commit"] == "85151f6b1bb58f13b6ac0304bfec53904bea085f",
          "contract records the 9.0.0.0 commit sha", src.get("commit"))
    logs = CONTRACT["requestSchemas"]["Logs"]["properties"]
    check(len(logs) == 16, "Logs declares the 16 properties present in 9.0", len(logs))
    check("hcxLogs" not in logs and "vmsLogs" not in logs,
          "Logs does not carry the properties 9.1 added", sorted(logs))


def verify_no_violations(reqs):
    section("contract conformance of every recorded request")
    bad = [r for r in reqs if r.get("violations")]
    check(not bad, "no request violated the contract",
          "\n".join("#%s %s %s -> %s" % (r["seq"], r["method"], r["path"], r["violations"])
                    for r in bad))
    non2xx = [r for r in reqs if not 200 <= int(r.get("responseStatus", 0)) < 300]
    check(not non2xx, "every request was answered 2xx",
          "\n".join("#%s %s %s -> HTTP %s" % (r["seq"], r["method"], r["path"],
                                              r.get("responseStatus")) for r in non2xx))
    unknown = [r for r in reqs if r.get("operationId") is None]
    check(not unknown, "no request went to an operation outside the contract",
          "\n".join("#%s %s %s" % (r["seq"], r["method"], r["path"]) for r in unknown))


def verify_call_counts(reqs):
    section("operation call counts")
    expected = {
        "createToken": 1,
        "getTasks": 1,
        "getCredentialsTask": 1,
        "getCredentialsSubTask": len(FAILED_SUBTASKS),
        "getNotifications": 1,
        "startSupportBundle": 1,
    }
    for op, want in expected.items():
        got = len(only(reqs, op))
        check(got == want, "%s called exactly %d time(s)" % (op, want), "actual: %d" % got)

    polls = len(only(reqs, "getSupportBundleStatus"))
    check(polls == MIN_STATUS_POLLS,
          "getSupportBundleStatus stopped at the first terminal response",
          "actual: %d, expected: %d (the bundle reports IN_PROGRESS for the first %d "
          "polls and SUCCESSFUL on the next one)"
          % (polls, MIN_STATUS_POLLS, FIXTURES["supportBundle"]["inProgressPolls"]))

    total = sum(expected.values()) + polls
    check(len(reqs) == total, "no requests beyond the ones the investigation needs",
          "recorded %d, accounted for %d" % (len(reqs), total))


def verify_create_token(reqs):
    section("createToken - optional fields omitted, not sent empty")
    calls = only(reqs, "createToken")
    if not check(len(calls) == 1, "exactly one createToken call"):
        return
    r = calls[0]
    check(reqs[0] is r, "createToken is the first request of the run",
          "first request was %s" % reqs[0].get("operationId"))
    check(r["method"] == "POST" and r["path"] == "/v1/tokens",
          "POST /v1/tokens", "%s %s" % (r["method"], r["path"]))
    check(r.get("contentType") == "application/json",
          "Content-Type: application/json", r.get("contentType"))
    check(not r.get("rawQuery"), "no query string", r.get("rawQuery"))
    check(r.get("authorization") is None,
          "no Authorization header on the token call", r.get("authorization"))

    body = r.get("body")
    if not check(isinstance(body, dict), "request body is a JSON object", body):
        return
    check(set(body) == {"username", "password"},
          "body carries exactly username and password - apiKey and idToken omitted",
          diff(["password", "username"], sorted(body)))
    check(body.get("username") == FIXTURES["credentials"]["username"],
          "username is the configured account", body.get("username"))
    check(body.get("password") == FIXTURES["credentials"]["password"],
          "password is the configured password")


def verify_bearer(reqs):
    section("bearer token")
    authed = [r for r in reqs if r.get("operationId") not in (None, "createToken")]
    expected = "Bearer " + ACCESS_TOKEN
    wrong = [r for r in authed if r.get("authorization") != expected]
    check(not wrong, "every authenticated call sends 'Bearer <accessToken>'",
          "\n".join("#%s %s -> %r" % (r["seq"], r["operationId"], r.get("authorization"))
                    for r in wrong))


def verify_get_tasks(reqs):
    section("getTasks - only the three filters that were asked for")
    calls = only(reqs, "getTasks")
    if not check(len(calls) == 1, "exactly one getTasks call"):
        return
    r = calls[0]
    check(r["method"] == "GET" and r["path"] == "/v1/tasks",
          "GET /v1/tasks", "%s %s" % (r["method"], r["path"]))
    query = r.get("query") or {}
    check(set(query) == {"taskStatus", "taskType", "limit"},
          "query carries exactly taskStatus, taskType and limit - the other nine "
          "declared parameters are omitted",
          diff(["limit", "taskStatus", "taskType"], sorted(query)))
    check(query.get("taskStatus") == "Failed", "taskStatus=Failed", query.get("taskStatus"))
    check(query.get("taskType") == "CREDENTIALS_ROTATE",
          "taskType=CREDENTIALS_ROTATE", query.get("taskType"))
    check(query.get("limit") == "50", "limit=50", query.get("limit"))
    check("=&" not in (r.get("rawQuery") or "") and not (r.get("rawQuery") or "").endswith("="),
          "no parameter was sent with an empty value", r.get("rawQuery"))
    check(first_index(reqs, "getTasks") > first_index(reqs, "createToken"),
          "getTasks happens after authentication")


def verify_credentials_task(reqs):
    section("getCredentialsTask")
    calls = only(reqs, "getCredentialsTask")
    if not check(len(calls) == 1, "exactly one getCredentialsTask call"):
        return
    r = calls[0]
    check(r["path"] == "/v1/credentials/tasks/%s" % TASK["id"],
          "path addresses the failed rotation task id found through getTasks",
          "%s (expected id %s)" % (r["path"], TASK["id"]))
    check(not r.get("rawQuery"), "no query string", r.get("rawQuery"))
    check(first_index(reqs, "getCredentialsTask") > first_index(reqs, "getTasks"),
          "getCredentialsTask happens after getTasks")


def verify_sub_tasks(reqs):
    section("getCredentialsSubTask - only the subtasks that failed")
    calls = only(reqs, "getCredentialsSubTask")
    prefix = "/v1/credentials/tasks/%s/subtasks/" % CRED_TASK["id"]
    shapes = [r["path"] for r in calls if not r["path"].startswith(prefix)]
    check(not shapes, "every subtask call is nested under the rotation task id", shapes)

    got = sorted(r["path"].rsplit("/", 1)[-1] for r in calls)
    check(got == FAILED_SUBTASK_IDS,
          "fetched detail for exactly the %d subtask(s) that did not succeed"
          % len(FAILED_SUBTASK_IDS), diff(FAILED_SUBTASK_IDS, got))
    check(all(not r.get("rawQuery") for r in calls), "no query strings")
    if calls:
        check(first_index(reqs, "getCredentialsSubTask") > first_index(reqs, "getCredentialsTask"),
              "subtask detail is fetched after the rotation task roster")


def verify_notifications(reqs):
    section("getNotifications")
    calls = only(reqs, "getNotifications")
    if not check(len(calls) == 1, "exactly one getNotifications call"):
        return
    r = calls[0]
    check(r["method"] == "GET" and r["path"] == "/v1/notifications",
          "GET /v1/notifications", "%s %s" % (r["method"], r["path"]))
    check(not r.get("rawQuery"),
          "no query string - the operation declares no parameters", r.get("rawQuery"))


def verify_support_bundle(reqs):
    section("startSupportBundle - narrow spec, everything unset omitted")
    calls = only(reqs, "startSupportBundle")
    if not check(len(calls) == 1, "exactly one startSupportBundle call"):
        return
    r = calls[0]
    check(r["method"] == "POST" and r["path"] == "/v1/system/support-bundles",
          "POST /v1/system/support-bundles", "%s %s" % (r["method"], r["path"]))
    check(r.get("contentType") == "application/json",
          "Content-Type: application/json", r.get("contentType"))
    check(not r.get("rawQuery"), "no query string", r.get("rawQuery"))

    body = r.get("body")
    check(body == EXPECTED_BUNDLE_BODY,
          "body requests only esxLogs and sddcManagerLogs, scoped to the affected domain; "
          "options, includeFreeHosts, clusterNames and the other 14 log flags are absent",
          diff(EXPECTED_BUNDLE_BODY, body))

    if isinstance(body, dict):
        logs = body.get("logs")
        if isinstance(logs, dict):
            falsey = sorted(k for k, v in logs.items() if v is not True)
            check(not falsey,
                  "no log flag was sent as false, null or empty - unset flags are omitted",
                  falsey)
        scope = body.get("scope")
        if isinstance(scope, dict):
            check("includeFreeHosts" not in scope,
                  "includeFreeHosts omitted rather than sent as false")
            domains = scope.get("domains")
            if isinstance(domains, list) and domains and isinstance(domains[0], dict):
                check("clusterNames" not in domains[0],
                      "clusterNames omitted rather than sent as an empty array")
        check("options" not in body, "options object omitted entirely")

    check(first_index(reqs, "startSupportBundle") > first_index(reqs, "getNotifications"),
          "the bundle is requested after the notifications that name the domain")
    check(first_index(reqs, "startSupportBundle") > last_index(reqs, "getCredentialsSubTask"),
          "the bundle is requested after the failing subtasks have been read")


def verify_bundle_polling(reqs):
    section("getSupportBundleStatus")
    calls = only(reqs, "getSupportBundleStatus")
    expected_path = "/v1/system/support-bundles/%s" % BUNDLE_ID
    wrong = [r["path"] for r in calls if r["path"] != expected_path]
    check(not wrong, "every poll addresses the id returned by startSupportBundle", wrong)
    check(all(not r.get("rawQuery") for r in calls), "no query strings")
    if calls:
        check(first_index(reqs, "getSupportBundleStatus") > first_index(reqs, "startSupportBundle"),
              "polling starts after the bundle was requested")


def verify_diagnosis():
    section("out/diagnosis.json")
    if not check(os.path.isfile(DIAGNOSIS), "diagnosis.json was written", DIAGNOSIS):
        return
    try:
        doc = json.load(open(DIAGNOSIS))
    except ValueError as exc:
        check(False, "diagnosis.json is valid JSON", exc)
        return
    if not check(isinstance(doc, dict), "diagnosis.json is a JSON object", type(doc).__name__):
        return

    check(set(doc) == DIAGNOSIS_KEYS, "carries exactly the documented keys",
          diff(sorted(DIAGNOSIS_KEYS), sorted(doc)))
    for key, want in EXPECTED_DIAGNOSIS.items():
        check(doc.get(key) == want, "%s" % key, diff(want, doc.get(key)))

    summary = doc.get("rootCauseSummary")
    check(isinstance(summary, str) and len(summary.strip()) >= 20,
          "rootCauseSummary is a written sentence", repr(summary))
    check(isinstance(summary, str) and re.search(r"lock", summary, re.I) is not None,
          "rootCauseSummary names the actual cause rather than repeating the "
          "connection error the subtasks reported", repr(summary))


def main():
    print("verifying SDDC-51782 diagnostic client")
    print("request log : %s" % REQUEST_LOG)
    print("diagnosis   : %s" % DIAGNOSIS)

    reqs = load_requests()
    print("recorded %d request(s)" % len(reqs))

    verify_contract_provenance()
    verify_no_violations(reqs)
    verify_call_counts(reqs)
    verify_create_token(reqs)
    verify_bearer(reqs)
    verify_get_tasks(reqs)
    verify_credentials_task(reqs)
    verify_sub_tasks(reqs)
    verify_notifications(reqs)
    verify_support_bundle(reqs)
    verify_bundle_polling(reqs)
    verify_diagnosis()

    print("\n" + "-" * 62)
    if FAILURES:
        print("FAILED %d of %d checks:" % (len(FAILURES), CHECKS[0]))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("PASSED all %d checks" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
