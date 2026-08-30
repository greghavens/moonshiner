#!/usr/bin/env python3
"""Deterministic verifier for the VCF Automation 9.1 triage client.

Reads two artifacts produced by run_tests.sh:

  $VCFA_RUN_DIR/requests.jsonl - every request the loopback mock received
  $VCFA_RUN_DIR/report.txt     - the report the client returned

and asserts both the diagnosis and the exact wire shape of the traffic. No
network access, no live VMware endpoint, no clock or randomness: the same
inputs always produce the same verdict.

PROTECTED FILE - part of the graded harness, do not modify.

Exit code 0 = all checks pass, 1 = at least one check failed.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
RUN = os.path.abspath(os.environ.get("VCFA_RUN_DIR", os.path.join(ROOT, ".run")))
TOKEN = os.environ.get("VCFA_MOCK_TOKEN", "")

with open(os.path.join(ROOT, "mock", "fixtures.json")) as fh:
    FIX = json.load(fh)

IDS = FIX["ids"]
DEP_ID = IDS["deploymentId"]
REQ_TARGET = IDS["failedRequestId"]
REQ_DISMISSED = IDS["dismissedRequestId"]
EV_FAIL = IDS["failingEventId"]
VM2 = IDS["failingResourceId"]
NEW_REQ_ID = IDS["submittedRequestId"]

EVENTS = FIX["events"][REQ_TARGET]
FAIL_LOG = FIX["logs"][EV_FAIL]

failures = []
passes = []


def check(name, ok, detail=""):
    if ok:
        passes.append(name)
    else:
        failures.append((name, detail))
    return ok


def fatal(name, detail):
    check(name, False, detail)
    report()
    sys.exit(1)


def report():
    for name in passes:
        print("PASS  %s" % name)
    for name, detail in failures:
        print("FAIL  %s" % name)
        if detail:
            for line in str(detail).splitlines():
                print("        %s" % line)
    print()
    print("%d passed, %d failed" % (len(passes), len(failures)))
    result = {
        "passed": len(passes),
        "failed": len(failures),
        "failures": [{"check": n, "detail": d} for n, d in failures],
    }
    try:
        os.makedirs(RUN, exist_ok=True)
        with open(os.path.join(RUN, "verify_result.json"), "w") as fh:
            json.dump(result, fh, indent=2)
    except OSError:
        pass


# --- expected values, derived from the fixtures -----------------------------

def expected_root_cause_row():
    for row in FAIL_LOG:
        if " ERROR " in row["message"]:
            return row
    raise AssertionError("fixtures contain no ERROR row")


def expected_action_id():
    for act in FIX["actions"][VM2]:
        if act["name"] == "Delete Snapshot":
            if not act["valid"]:
                raise AssertionError("fixture action is not valid")
            return act["id"]
    raise AssertionError("fixtures contain no Delete Snapshot action")


ROOT_ROW = expected_root_cause_row()
ACTION_ID = expected_action_id()
SNAPSHOT_NAME = "pre-patch-2026-03-01"

EXPECTED_REPORT = [
    "deployment=payments-api-prod status=UPDATE_FAILED",
    "failedRequest=%s" % REQ_TARGET,
    "failedEvent=%s" % EV_FAIL,
    "failedResource=payments-api-prod-vm-02",
    "rootCauseRow=%d" % ROOT_ROW["rownum"],
    "rootCause=%s" % ROOT_ROW["message"],
    "remediationResource=%s" % VM2,
    "remediationAction=%s" % ACTION_ID,
    "submittedRequest=%s status=CREATED" % NEW_REQ_ID,
]


# --- load artifacts ----------------------------------------------------------

log_path = os.path.join(RUN, "requests.jsonl")
report_path = os.path.join(RUN, "report.txt")

if not os.path.exists(log_path):
    fatal("artifacts.requestLogExists", "missing %s" % log_path)
if not os.path.exists(report_path):
    fatal("artifacts.reportExists", "missing %s" % report_path)

entries = []
with open(log_path) as fh:
    for line in fh:
        line = line.strip()
        if line:
            entries.append(json.loads(line))
entries.sort(key=lambda e: e["seq"])

calls = [e for e in entries if e.get("outcome") != "control"]

with open(report_path) as fh:
    report_text = fh.read()


def by_op(op):
    return [e for e in calls if e.get("operationId") == op]


def q(entry, name):
    """Value of a query parameter, or None when the parameter is absent."""
    for k, v in entry.get("queryPairs") or []:
        if k == name:
            return v
    return None


def has_q(entry, name):
    return any(k == name for k, _ in entry.get("queryPairs") or [])


def label(entry):
    tail = ("?" + entry["rawQuery"]) if entry.get("rawQuery") else ""
    return "#%d %s %s%s" % (entry["seq"], entry["method"], entry["path"], tail)


# === 1. the report ===========================================================

actual_lines = report_text.replace("\r\n", "\n").split("\n")

if not check("report.lineCount", len(actual_lines) == len(EXPECTED_REPORT),
             "expected %d lines, got %d:\n%s"
             % (len(EXPECTED_REPORT), len(actual_lines), "\n".join(actual_lines) or "<empty>")):
    pass

for i, want in enumerate(EXPECTED_REPORT):
    key = want.split("=", 1)[0]
    got = actual_lines[i] if i < len(actual_lines) else "<missing>"
    check("report.line[%d].%s" % (i + 1, key), got == want,
          "expected: %s\ngot:      %s" % (want, got))


# === 2. contract discipline ==================================================

check("wire.callsMade", len(calls) > 0, "the client made no HTTP requests at all")

outside = [label(e) for e in calls if e.get("outcome") == "not_in_contract"]
check("wire.noOperationsOutsideContract", not outside,
      "these paths are not named by docs/contract.json:\n" + "\n".join(outside))

rejected = ["%s -> %d" % (label(e), e["status"]) for e in calls if e["status"] >= 400]
check("wire.everyRequestAccepted", not rejected,
      "the service rejected:\n" + "\n".join(rejected))

bad_methods = [label(e) for e in calls if e["method"] not in ("GET", "POST")]
check("wire.onlyGetAndPost", not bad_methods,
      "unexpected methods:\n" + "\n".join(bad_methods))

bad_auth = [label(e) for e in calls
            if (e["headers"] or {}).get("authorization") != "Bearer " + TOKEN]
check("wire.bearerAuthOnEveryRequest", not bad_auth,
      "missing or malformed 'Authorization: Bearer <token>' on:\n" + "\n".join(bad_auth))

bad_accept = [label(e) for e in calls
              if (e["headers"] or {}).get("accept") != "application/json"]
check("wire.acceptsJsonOnEveryRequest", not bad_accept,
      "expected 'Accept: application/json' on:\n" + "\n".join(bad_accept))

empty_params = []
for e in calls:
    for k, v in e.get("queryPairs") or []:
        if v == "":
            empty_params.append("%s -> '%s' sent with an empty value" % (label(e), k))
check("wire.noEmptyOptionalQueryParameters", not empty_params,
      "an optional parameter with no value must be omitted, not sent empty:\n"
      + "\n".join(empty_params))


# === 3. the diagnostic walk ==================================================

dep_calls = by_op("getDeploymentById")
check("walk.deploymentFetched", len(dep_calls) >= 1,
      "the client never fetched the deployment")
check("walk.deploymentIdCorrect",
      all(e["path"] == "/deployment/api/deployments/" + DEP_ID for e in dep_calls),
      "unexpected deployment path:\n" + "\n".join(label(e) for e in dep_calls))

req_calls = by_op("getDeploymentRequests")
check("walk.requestListFetched", len(req_calls) >= 1,
      "the client never listed the deployment's requests, so it could not have "
      "distinguished the failed request from the dismissed one")

# --- events: every event must be seen, and nothing past the end asked for
ev_calls = by_op("getRequestEvents")
check("walk.eventsFetched", len(ev_calls) >= 1, "the client never fetched request events")

wrong_request = [label(e) for e in ev_calls
                 if not e["path"].startswith("/deployment/api/requests/" + REQ_TARGET + "/")]
check("walk.eventsFetchedForTheFailedRequest", not wrong_request,
      "events were fetched for a request other than %s (the newest FAILED, "
      "non-dismissed request); %s is dismissed and must not be triaged:\n%s"
      % (REQ_TARGET, REQ_DISMISSED, "\n".join(wrong_request)))

covered = set()
past_end = []
for e in ev_calls:
    page = int(q(e, "page") or 0)
    size = int(q(e, "size") or 20)
    start = page * size
    if start >= len(EVENTS):
        past_end.append(label(e))
    covered.update(range(start, min(start + size, len(EVENTS))))
missing = sorted(set(range(len(EVENTS))) - covered)
check("walk.everyEventPageRead", not missing,
      "the client stopped before reading every event; events at index %s were "
      "never retrieved, and the failing event is among the last of the %d"
      % (missing, len(EVENTS)))
check("walk.noEventPagePastTheEnd", not past_end,
      "the client kept paging after the last page; 'last'/'totalPages' says when to stop:\n"
      + "\n".join(past_end))

# --- logs of the failing event
fail_log_calls = [e for e in by_op("getEventLogs")
                  if e["path"].endswith("/events/" + EV_FAIL + "/logs")]
if not check("walk.failingEventLogsFetched", len(fail_log_calls) >= 1,
             "the client never pulled the logs of event %s; the root cause appears "
             "nowhere else in the API" % EV_FAIL):
    pass
else:
    first = fail_log_calls[0]
    check("wire.firstLogCallOmitsSinceRow", not has_q(first, "sinceRow"),
          "sinceRow is optional and the caller has no row number on the first call, "
          "so it must be omitted entirely rather than sent as a default; got: %s"
          % label(first))

    check("walk.logsFetchedInTwoSlices", len(fail_log_calls) == 2,
          "the slice holds %d rows and the event has %d, so exactly 2 calls are "
          "expected; got %d:\n%s"
          % (10, len(FAIL_LOG), len(fail_log_calls),
             "\n".join(label(e) for e in fail_log_calls)))

    if len(fail_log_calls) >= 2:
        second = fail_log_calls[1]
        check("wire.secondLogCallResumesAtNextRow", q(second, "sinceRow") == "11",
              "the first slice ends at rownum 10, so the follow-up must ask for "
              "sinceRow=11; got sinceRow=%r in %s" % (q(second, "sinceRow"), label(second)))

    over_read = [label(e) for e in fail_log_calls[2:]]
    check("walk.stopsAtEof", not over_read,
          "the client kept requesting logs after the slice reported eof=true:\n"
          + "\n".join(over_read))

res_calls = by_op("getDeploymentResources")
check("walk.deploymentResourcesFetched", len(res_calls) >= 1,
      "the client never listed deployment resources, so it could not have resolved "
      "the failing resource name to an id")

act_calls = by_op("getResourceActions")
check("walk.actionsQueried", len(act_calls) >= 1,
      "the client never fetched the actions available on the resource implicated by the logs; got %d:\n%s"
      % (len(act_calls), "\n".join(label(e) for e in act_calls)))
check("walk.actionsQueriedForFailingResource",
      all(e["path"] == "/deployment/api/resources/" + VM2 + "/actions" for e in act_calls),
      "actions must be queried for %s (payments-api-prod-vm-02):\n%s"
      % (VM2, "\n".join(label(e) for e in act_calls)))


# === 4. the submitted day-2 action ===========================================

posts = by_op("submitResourceActionRequest")
if not check("submit.exactlyOneRequest", len(posts) == 1,
             "expected exactly 1 day-2 submission; got %d:\n%s"
             % (len(posts), "\n".join(label(e) for e in posts))):
    pass
else:
    post = posts[0]
    check("submit.targetsFailingResource",
          post["path"] == "/deployment/api/resources/" + VM2 + "/requests",
          "expected POST /deployment/api/resources/%s/requests, got %s" % (VM2, post["path"]))

    ctype = ((post["headers"] or {}).get("content-type") or "").split(";")[0].strip().lower()
    check("submit.contentTypeJson", ctype == "application/json",
          "expected Content-Type: application/json, got %r"
          % (post["headers"] or {}).get("content-type"))

    check("submit.noQueryParameters", not (post.get("queryPairs") or []),
          "the operation defines no query parameters; got ?%s" % post.get("rawQuery"))

    raw = post.get("body") or ""
    try:
        body = json.loads(raw)
        parsed = True
    except Exception as exc:
        body, parsed = None, False
        check("submit.bodyIsJson", False, "%s\nbody was: %s" % (exc, raw))

    if parsed:
        check("submit.bodyIsJson", True)
        if check("submit.bodyIsObject", isinstance(body, dict),
                 "ResourceActionRequest must be a JSON object, got %s" % type(body).__name__):
            keys = set(body)
            check("submit.omitsUnsetOptionalInputs", "inputs" not in keys,
                  "'%s' declares no schema properties, so it takes no inputs and the "
                  "optional 'inputs' field must be omitted from the body entirely - not "
                  "sent as {} or null. Body was: %s" % (ACTION_ID, raw))
            check("submit.noUndocumentedFields",
                  not (keys - {"actionId", "inputs", "reason"}),
                  "ResourceActionRequest documents exactly actionId, inputs and reason; "
                  "found extra: %s" % sorted(keys - {"actionId", "inputs", "reason"}))
            check("submit.bodyFieldsExactlyActionIdAndReason",
                  keys == {"actionId", "reason"},
                  "expected exactly {actionId, reason} on the wire, got %s. Body was: %s"
                  % (sorted(keys), raw))
            check("submit.actionIdIsTheSnapshotRemediation",
                  body.get("actionId") == ACTION_ID,
                  "the ERROR row blames an active snapshot, so the runbook selects the "
                  "'Delete Snapshot' action (%s); got %r. Picking an action from the "
                  "deployment's lastRequest.details or from a WARN row is the trap."
                  % (ACTION_ID, body.get("actionId")))
            reason = body.get("reason")
            check("submit.reasonIsPresentAndNonEmpty",
                  isinstance(reason, str) and reason.strip() != "",
                  "reason must be a non-empty string, got %r" % (reason,))
            check("submit.reasonCitesTheSnapshotFromTheLogs",
                  isinstance(reason, str) and SNAPSHOT_NAME in reason,
                  "the reason must name the snapshot the ERROR row identifies (%r) so the "
                  "audit trail is grounded in the logs; got %r" % (SNAPSHOT_NAME, reason))


# === 5. causal order =========================================================

def first_seq(op, predicate=None):
    for e in calls:
        if e.get("operationId") == op and (predicate is None or predicate(e)):
            return e["seq"]
    return None


order = [
    ("the deployment", first_seq("getDeploymentById")),
    ("its requests", first_seq("getDeploymentRequests")),
    ("the request's events", first_seq("getRequestEvents")),
    ("the failing event's logs",
     first_seq("getEventLogs", lambda e: e["path"].endswith("/events/" + EV_FAIL + "/logs"))),
    ("the resource's actions", first_seq("getResourceActions")),
    ("the day-2 submission", first_seq("submitResourceActionRequest")),
]
known = [(n, s) for n, s in order if s is not None]
out_of_order = []
for i in range(1, len(known)):
    if known[i][1] < known[i - 1][1]:
        out_of_order.append("%s (#%d) was read before %s (#%d)"
                            % (known[i][0], known[i][1], known[i - 1][0], known[i - 1][1]))
check("order.diagnosisPrecedesRemediation", not out_of_order,
      "the submission has to follow the evidence:\n" + "\n".join(out_of_order))


report()
sys.exit(1 if failures else 0)
