#!/usr/bin/env python3
"""Protected verifier.

Asserts the exact wire shape of every request the client made, using the mock's request
log, and cross-checks it against what TestMain observed. Deterministic and offline: it
reads two files and contacts nothing.
"""

import json
import sys

CONTRACT = "docs/contract.json"
LOG = "build/requests.jsonl"
RESULTS = "build/testmain.json"

BASE = "/suite-api"
AUTH_SCHEME = "vRealizeOpsToken"

USERNAME = "svc-report"
PASSWORD = "R3port!Pass"
RESOURCE_ID = "be82d29c-d82d-4d8c-8d9b-7f69d45b1c5f"
DEF_COMPLETES = "97417a6d-708d-4b12-9142-484b5a0df4dc"
DEF_FAILS = "1c0b9c1e-8f4a-4f52-9d6a-2b7c5e3a91fd"
DEF_STUCK = "5f2d7a34-6b19-4c88-a0e3-9d41f7b26c50"

CSV_BODY = "Cluster,Capacity Remaining %,Time Remaining (days)\r\nvcf-m01-cl01,42,118\r\nvcf-w01-cl01,17,26\r\n"

SCENARIOS = [
    {
        "name": "minimal",
        "definition": DEF_COMPLETES,
        "auth_source": None,
        "traversal": None,
        "format": None,
        "polls": 3,
        "terminal": "Completed",
        "downloads": True,
        "throws": None,
    },
    {
        "name": "full-optionals",
        "definition": DEF_COMPLETES,
        "auth_source": "Imported LDAP Server",
        "traversal": {
            "name": "vSphere Hosts and Clusters",
            "description": "All \"production\" hosts\nand clusters",
            "rootAdapterKindKey": "VMWARE",
            "rootResourceKindKey": "",
            "adapterInstanceAssociation": False,
        },
        "format": "CSV",
        "polls": 3,
        "terminal": "Completed",
        "downloads": True,
        "throws": None,
    },
    {
        "name": "terminal-failure",
        "definition": DEF_FAILS,
        "auth_source": None,
        "traversal": None,
        "format": None,
        "polls": 3,
        "terminal": "Failed",
        "downloads": False,
        "throws": None,
    },
    {
        "name": "poll-budget-exhausted",
        "definition": DEF_STUCK,
        "auth_source": None,
        "traversal": None,
        "format": None,
        "polls": 4,
        "terminal": None,
        "downloads": False,
        "throws": "java.lang.IllegalStateException",
    },
    {
        "name": "http-error",
        "definition": DEF_COMPLETES,
        "password": "definitely-wrong",
        "auth_source": None,
        "traversal": None,
        "format": None,
        "polls": 0,
        "terminal": None,
        "downloads": False,
        "throws": "java.io.IOException",
        "http_status": 401,
    },
]

FAILURES = []


def fail(where, message):
    FAILURES.append("%s: %s" % (where, message))


def check(cond, where, message):
    if not cond:
        fail(where, message)
    return cond


def media_type(value):
    """'application/json; charset=UTF-8' -> 'application/json'."""
    if value is None:
        return None
    return value.split(";")[0].strip().lower()


def charset_ok(value):
    parts = [p.strip().lower() for p in value.split(";")[1:]]
    for part in parts:
        if part.startswith("charset="):
            if part.split("=", 1)[1].strip('"') not in ("utf-8", "utf8"):
                return False
        else:
            return False
    return True


def load_jsonl(path):
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# --- shared header assertions -------------------------------------------------


def assert_json_post(entry, where, expected_body):
    headers = entry["headers"]
    ctype = headers.get("content-type")
    if check(ctype is not None, where, "no Content-Type header on a request with a JSON body"):
        check(
            media_type(ctype) == "application/json",
            where,
            "Content-Type media type is %r, expected application/json" % media_type(ctype),
        )
        check(charset_ok(ctype), where, "Content-Type carries unexpected parameters: %r" % ctype)
    accept = headers.get("accept")
    check(
        accept is not None and accept.strip() == "application/json",
        where,
        "Accept must be exactly 'application/json', got %r" % accept,
    )
    check(entry["hasBody"], where, "request carried no body")
    body = entry["jsonBody"]
    if not check(isinstance(body, dict), where, "body is not a JSON object: %r" % entry["rawBody"]):
        return
    got_keys = sorted(body.keys())
    want_keys = sorted(expected_body.keys())
    check(
        got_keys == want_keys,
        where,
        "JSON body properties are %s, expected exactly %s "
        "(unset optional properties must be omitted, not sent as null/\"\"/[]/{})"
        % (got_keys, want_keys),
    )
    for key in want_keys:
        if key in body:
            check(
                body[key] == expected_body[key],
                where,
                "property %r is %r, expected %r" % (key, body[key], expected_body[key]),
            )


def assert_bodyless_get(entry, where, expect_accept_json):
    headers = entry["headers"]
    check(
        "content-type" not in headers,
        where,
        "a GET with no body must not send Content-Type, got %r" % headers.get("content-type"),
    )
    check(not entry["hasBody"], where, "a GET must not carry a body, got %r" % entry["rawBody"])
    if expect_accept_json:
        accept = headers.get("accept")
        check(
            accept is not None and accept.strip() == "application/json",
            where,
            "Accept must be exactly 'application/json', got %r" % accept,
        )


def assert_auth(entry, where, token):
    got = entry["headers"].get("authorization")
    want = "%s %s" % (AUTH_SCHEME, token)
    check(got == want, where, "Authorization header is %r, expected %r" % (got, want))


# --- main ---------------------------------------------------------------------


def main():
    try:
        contract = json.load(open(CONTRACT, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("FAIL: cannot read %s: %s" % (CONTRACT, exc))
        return 1

    if contract.get("basePath") != BASE:
        print("FAIL: %s basePath was changed from %r to %r" % (CONTRACT, BASE, contract.get("basePath")))
        return 1
    named = [op["operationId"] for op in contract.get("operations", [])]
    if sorted(named) != ["acquireToken", "createReport", "downloadReport", "getReport"]:
        print("FAIL: %s no longer names exactly the four contract operations: %s" % (CONTRACT, named))
        return 1

    try:
        entries = load_jsonl(LOG)
    except Exception as exc:  # noqa: BLE001
        print("FAIL: cannot read the mock request log %s: %s" % (LOG, exc))
        return 1
    try:
        results = json.load(open(RESULTS, encoding="utf-8"))["scenarios"]
    except Exception as exc:  # noqa: BLE001
        print("FAIL: cannot read %s: %s" % (RESULTS, exc))
        return 1

    # --- global -------------------------------------------------------------
    for entry in entries:
        where = "request #%d %s %s" % (entry["seq"], entry["method"], entry["target"])
        check(
            entry["operationId"] is not None,
            where,
            "request does not match any operation the contract names (mock answered 404)",
        )
        check(
            entry["path"].startswith(BASE + "/"),
            where,
            "path does not start with the contract base path %r" % BASE,
        )

    # --- segment by acquireToken -------------------------------------------
    segments = []
    for entry in entries:
        if entry["operationId"] == "acquireToken":
            segments.append([entry])
        elif segments:
            segments[-1].append(entry)
        else:
            fail("request #%d" % entry["seq"], "request issued before any acquireToken call")

    if not check(
        len(segments) == len(SCENARIOS),
        "request log",
        "expected %d acquireToken calls (one per scenario), saw %d" % (len(SCENARIOS), len(segments)),
    ):
        report()
        return 1
    if not check(
        len(results) == len(SCENARIOS),
        RESULTS,
        "expected %d scenario records, saw %d" % (len(SCENARIOS), len(results)),
    ):
        report()
        return 1

    for scenario, segment, record in zip(SCENARIOS, segments, results):
        verify_scenario(scenario, segment, record)

    report()
    return 1 if FAILURES else 0


def verify_scenario(scenario, segment, record):
    name = scenario["name"]
    check(record.get("name") == name, name, "scenario record out of order: %r" % record.get("name"))

    # 1. acquireToken
    acquire = segment[0]
    where = "%s/acquireToken" % name
    check(acquire["method"] == "POST", where, "method is %s, expected POST" % acquire["method"])
    check(
        acquire["target"] == BASE + "/api/auth/token/acquire",
        where,
        "request target is %r, expected %r" % (acquire["target"], BASE + "/api/auth/token/acquire"),
    )
    check(
        "authorization" not in acquire["headers"],
        where,
        "acquireToken must not send an Authorization header",
    )
    expected = {"username": USERNAME, "password": scenario.get("password", PASSWORD)}
    if scenario["auth_source"] is not None:
        expected["authSource"] = scenario["auth_source"]
    assert_json_post(acquire, where, expected)
    if "http_status" in scenario:
        check(
            acquire["responseStatus"] == scenario["http_status"],
            where,
            "mock answered %r, expected HTTP %d"
            % (acquire["responseStatus"], scenario["http_status"]),
        )
        check(len(segment) == 1, name, "no request may follow a failed acquireToken call")
        result_where = "%s/result" % name
        if check(record.get("threw") is True, result_where,
                 "expected generateReport to throw, it returned"):
            check(record.get("exceptionClass") == scenario["throws"], result_where,
                  "threw %r, expected %r"
                  % (record.get("exceptionClass"), scenario["throws"]))
            message = record.get("exceptionMessage") or ""
            check(str(scenario["http_status"]) in message, result_where,
                  "exception message %r does not contain HTTP status %d"
                  % (message, scenario["http_status"]))
        return
    check(acquire["responseStatus"] == 200, where,
          "mock answered HTTP %r, expected 200" % acquire["responseStatus"])
    token = acquire.get("issuedToken")
    if not check(token, where, "mock issued no token - credentials were rejected"):
        return

    rest = segment[1:]

    # 2. createReport
    if not check(rest and rest[0]["operationId"] == "createReport", name,
                 "expected createReport immediately after acquireToken, saw %r"
                 % (rest[0]["operationId"] if rest else None)):
        return
    create = rest[0]
    where = "%s/createReport" % name
    check(create["method"] == "POST", where, "method is %s, expected POST" % create["method"])
    check(
        create["target"] == BASE + "/api/reports",
        where,
        "request target is %r, expected %r (no query string)" % (create["target"], BASE + "/api/reports"),
    )
    assert_auth(create, where, token)
    check(create["responseStatus"] == 200, where,
          "mock answered HTTP %r, expected 200" % create["responseStatus"])
    expected = {"resourceId": RESOURCE_ID, "reportDefinitionId": scenario["definition"]}
    if scenario["traversal"] is not None:
        expected["traversalSpec"] = scenario["traversal"]
    assert_json_post(create, where, expected)
    if scenario["traversal"] is not None and isinstance(create["jsonBody"], dict):
        nested = create["jsonBody"].get("traversalSpec")
        if check(isinstance(nested, dict), where, "traversalSpec is not a JSON object: %r" % nested):
            got = sorted(nested.keys())
            want = sorted(scenario["traversal"].keys())
            check(
                got == want,
                where,
                "traversalSpec properties are %s, expected exactly %s "
                "(unset nested optionals must be omitted too)" % (got, want),
            )

    report_id = create.get("assignedReportId")
    if not check(report_id, where, "mock assigned no report id"):
        return

    # 3. getReport polls
    polls = []
    idx = 1
    while idx < len(rest) and rest[idx]["operationId"] == "getReport":
        polls.append(rest[idx])
        idx += 1
    where = "%s/getReport" % name
    check(
        len(polls) == scenario["polls"],
        where,
        "made %d getReport calls, expected exactly %d - the report must be polled to a "
        "terminal status, not assumed complete and not over-polled" % (len(polls), scenario["polls"]),
    )
    want_target = "%s/api/reports/%s" % (BASE, report_id)
    for n, poll in enumerate(polls, start=1):
        pw = "%s/getReport#%d" % (name, n)
        check(poll["method"] == "GET", pw, "method is %s, expected GET" % poll["method"])
        check(
            poll["target"] == want_target,
            pw,
            "request target is %r, expected %r (no query string)" % (poll["target"], want_target),
        )
        assert_auth(poll, pw, token)
        assert_bodyless_get(poll, pw, expect_accept_json=True)
        check(poll["responseStatus"] == 200, pw,
              "mock answered HTTP %r, expected 200" % poll["responseStatus"])
    if polls and scenario["terminal"] is not None:
        check(
            polls[-1].get("servedStatus") == scenario["terminal"],
            where,
            "the last poll was served status %r; the client stopped before the terminal status %r"
            % (polls[-1].get("servedStatus"), scenario["terminal"]),
        )
    for n, poll in enumerate(polls[:-1], start=1):
        check(
            poll.get("servedStatus", "").upper() not in ("COMPLETED", "FAILED"),
            "%s/getReport#%d" % (name, n),
            "polling continued past terminal status %r" % poll.get("servedStatus"),
        )

    # 4. downloadReport
    tail = rest[idx:]
    if scenario["downloads"]:
        if not check(
            len(tail) == 1 and tail[0]["operationId"] == "downloadReport",
            name,
            "expected exactly one downloadReport call after the polls, saw %r"
            % [e["operationId"] for e in tail],
        ):
            return
        dl = tail[0]
        where = "%s/downloadReport" % name
        want = "%s/api/reports/%s/download" % (BASE, report_id)
        if scenario["format"] is not None:
            want += "?format=" + scenario["format"]
        check(dl["method"] == "GET", where, "method is %s, expected GET" % dl["method"])
        check(
            dl["target"] == want,
            where,
            "request target is %r, expected %r%s"
            % (
                dl["target"],
                want,
                "" if scenario["format"] is not None
                else " - an unset optional query parameter must be omitted entirely, "
                     "leaving no '?' in the request target",
            ),
        )
        assert_auth(dl, where, token)
        assert_bodyless_get(dl, where, expect_accept_json=False)
        check(dl["responseStatus"] == 200, where,
              "mock answered HTTP %r, expected 200" % dl["responseStatus"])
    else:
        check(
            not tail,
            name,
            "no request may follow the polls in this scenario, saw %r"
            % [e["operationId"] for e in tail],
        )

    # 5. cross-check what the client returned
    where = "%s/result" % name
    if scenario["throws"] is not None:
        if check(record.get("threw") is True, where, "expected generateReport to throw, it returned"):
            check(
                record.get("exceptionClass") == scenario["throws"],
                where,
                "threw %r, expected %r" % (record.get("exceptionClass"), scenario["throws"]),
            )
            msg = record.get("exceptionMessage") or ""
            check(report_id in msg, where, "exception message %r does not contain the report id %r"
                  % (msg, report_id))
        return

    if not check(record.get("threw") is False, where,
                 "generateReport threw %s: %s"
                 % (record.get("exceptionClass"), record.get("exceptionMessage"))):
        return
    result = record.get("result")
    if not check(isinstance(result, dict), where, "no Result was returned"):
        return
    check(result.get("reportId") == report_id, where,
          "Result.reportId is %r, expected %r" % (result.get("reportId"), report_id))
    check(result.get("finalStatus") == scenario["terminal"], where,
          "Result.finalStatus is %r, expected %r" % (result.get("finalStatus"), scenario["terminal"]))
    check(result.get("pollCount") == scenario["polls"], where,
          "Result.pollCount is %r, expected %d" % (result.get("pollCount"), scenario["polls"]))
    if scenario["downloads"]:
        check(result.get("downloadBody") == CSV_BODY, where,
              "Result.downloadBody is %r, expected the downloaded report body verbatim"
              % result.get("downloadBody"))
    else:
        check(result.get("downloadBody") is None, where,
              "Result.downloadBody must be null when the terminal status is not COMPLETED, got %r"
              % result.get("downloadBody"))


def report():
    if FAILURES:
        print("WIRE VERIFICATION FAILED (%d problem(s)):" % len(FAILURES))
        for item in FAILURES:
            print("  - " + item)
    else:
        print("WIRE VERIFICATION PASSED: 5 scenarios, exact request shapes match docs/contract.json")


if __name__ == "__main__":
    sys.exit(main())
