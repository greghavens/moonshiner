#!/usr/bin/env python3
"""Deterministic verifier for the VCF Automation 9.1 client task.

Compiles the project, runs TestMain against the loopback mock, then re-checks the recorded
wire traffic against docs/contract.json. It contacts no network endpoint: the only socket in
play is the mock's, bound to 127.0.0.1 by TestMain itself.

Protected files are restored from verify/pristine/ before anything is compiled, so a modified
harness cannot change the outcome.
"""

import json
import filecmp
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRISTINE = ROOT / "verify" / "pristine"
BUILD = ROOT / "build"
RUN = ROOT / "run"
LOG = RUN / "requests.jsonl"
RESULT = RUN / "result.json"

PROTECTED = [
    "docs/contract.json",
    "docs/official_sources.json",
    "src/com/broadcom/vcfa/Json.java",
    "harness/com/broadcom/vcfa/MockVcfAutomation.java",
    "harness/com/broadcom/vcfa/TestMain.java",
]

LOGIN_PATH = "/csp/gateway/am/api/login/oauth"
DEPLOYMENTS_PATH = "/deployment/api/deployments"
REFRESH_TOKEN = "rt-8c1f0a9e-4d2b-11f0-9a3e-0242ac120002"
PAGE_SIZE = 3
EXPECTED_IDS = ["dep-0%d" % n for n in range(1, 8)]
RENAMED = "search-cluster-blue"
REDESCRIBED = "Nightly ETL workers, retimed to 02:00 UTC"
NEW_ICON_ID = "7db3ea6f-7167-4d0f-846d-95f119695139"
ORIGINAL_DEP04_DESCRIPTION = "Elasticsearch cluster backing catalogue search"
OPERATION_IDS = {"exchangeRefreshToken", "listDeployments", "patchDeployment"}

failures = []


def fail(message):
    failures.append(message)


def require(condition, message):
    if not condition:
        fail(message)
    return condition


def restore_protected_files():
    """Puts the graded copies of the harness back, and reports any that had been edited."""
    for rel in PROTECTED:
        pristine = PRISTINE / rel
        working = ROOT / rel
        if not pristine.exists():
            fail("verify/pristine/%s is missing from the task" % rel)
            continue
        if working.exists() and filecmp.cmp(pristine, working, shallow=False):
            continue
        fail("%s was modified; it is part of the fixed harness and has been restored" % rel)
        working.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pristine, working)


def compile_project():
    sources = sorted(
        str(p) for d in ("src", "harness") for p in (ROOT / d).rglob("*.java")
    )
    if not sources:
        fail("no Java sources found under src/ or harness/")
        return False
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)
    proc = subprocess.run(
        ["javac", "-nowarn", "-d", str(BUILD)] + sources,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        fail("javac failed:\n" + (proc.stdout + proc.stderr).strip())
        return False
    return True


def run_harness():
    if RUN.exists():
        shutil.rmtree(RUN)
    RUN.mkdir(parents=True)
    try:
        proc = subprocess.run(
            ["java", "-cp", str(BUILD), "com.broadcom.vcfa.TestMain", str(LOG), str(RESULT)],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        fail("TestMain did not finish within 180s")
        return None
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        fail("TestMain exited %d:\n%s" % (proc.returncode, output))
    return proc


def load_log():
    if not LOG.exists():
        fail("the mock wrote no request log at run/requests.jsonl")
        return []
    entries = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def parse_query(raw):
    params = {}
    if not raw:
        return params
    for pair in raw.split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        params[name] = value
    return params


def body_object(entry, label):
    raw = entry.get("body")
    if not raw:
        fail("%s was sent with an empty request body" % label)
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail("%s body is not valid JSON (%s): %r" % (label, exc, raw))
        return None
    if not isinstance(parsed, dict):
        fail("%s body is not a JSON object: %r" % (label, raw))
        return None
    return parsed


def check_no_empty_values(body, label):
    for key, value in body.items():
        if value is None:
            fail("%s sent %r as null; an unset optional field is omitted, not sent empty" % (label, key))
        elif isinstance(value, str) and value == "":
            fail("%s sent %r as an empty string; an unset optional field is omitted, not sent empty"
                 % (label, key))


def check_traffic(entries):
    for entry in entries:
        if entry["status"] == 404:
            fail("the client called an operation the contract does not name: %s %s"
                 % (entry["method"], entry["path"]))
        elif entry["status"] == 400:
            fail("the mock rejected %s %s as malformed (seq %d); body=%r"
                 % (entry["method"], entry["path"], entry["seq"], entry.get("body")))

    logins = [e for e in entries if e["path"] == LOGIN_PATH]
    gets = [e for e in entries if e["method"] == "GET" and e["path"] == DEPLOYMENTS_PATH]
    patches = [e for e in entries if e["method"] == "PATCH"
               and e["path"].startswith(DEPLOYMENTS_PATH + "/")]

    if not require(len(entries) == 9,
                   "expected exactly 9 requests (1 token, 3 pages, 1 rejected page, 1 token, "
                   "3 patches) but the mock logged %d: %s"
                   % (len(entries), [(e["method"], e["path"], e["status"]) for e in entries])):
        return

    require(len(logins) == 2,
            "expected exactly 2 token exchanges, one at start-up and one after the 401, got %d"
            % len(logins))
    require(len(gets) == 4,
            "expected exactly 4 listDeployments calls (pages 0, 1, 2 and the replay of 2), got %d"
            % len(gets))
    require(len(patches) == 3, "expected exactly 3 patchDeployment calls, got %d" % len(patches))

    require(entries[0]["path"] == LOGIN_PATH,
            "the first request was %s %s; an access token must be obtained before the first "
            "Deployment API call" % (entries[0]["method"], entries[0]["path"]))

    # --- token exchange wire shape -------------------------------------------------
    for n, entry in enumerate(logins, start=1):
        label = "token exchange #%d" % n
        require(entry["method"] == "POST", "%s used %s, expected POST" % (label, entry["method"]))
        require((entry.get("contentType") or "").lower().startswith("application/json"),
                "%s sent Content-Type %r, expected application/json"
                % (label, entry.get("contentType")))
        require(not entry.get("query"),
                "%s carried a query string %r; the contract defines no query parameters for it"
                % (label, entry.get("query")))
        body = body_object(entry, label)
        if body is None:
            continue
        check_no_empty_values(body, label)
        expected_keys = {"grant_type", "state", "refresh_token"}
        require(set(body) == expected_keys,
                "%s body carried keys %s, expected exactly %s: the optional fields this flow does "
                "not use (client_id, client_secret, code, redirect_uri, scope, orgId) must be "
                "omitted, not sent empty" % (label, sorted(body), sorted(expected_keys)))
        require(body.get("grant_type") == "refresh_token",
                "%s sent grant_type=%r, expected 'refresh_token'" % (label, body.get("grant_type")))
        require(body.get("refresh_token") == REFRESH_TOKEN,
                "%s sent the wrong refresh_token: %r" % (label, body.get("refresh_token")))
        state = body.get("state")
        require(isinstance(state, str) and state.strip() != "",
                "%s sent state=%r; the reference marks state required" % (label, state))

    # --- pagination and resume ------------------------------------------------------
    pages = []
    for entry in gets:
        params = parse_query(entry.get("query"))
        unknown = set(params) - {"page", "size", "sort"}
        require(not unknown,
                "listDeployments was called with query parameters %s that the contract does not "
                "pin for this project" % sorted(unknown))
        try:
            page = int(params.get("page", 0))
            size = int(params.get("size", 20))
        except ValueError:
            fail("listDeployments sent a non-integer page/size: %r" % entry.get("query"))
            continue
        require(size == PAGE_SIZE,
                "listDeployments sent size=%d, expected the requested page size %d" % (size, PAGE_SIZE))
        pages.append(page)

    require(pages == [0, 1, 2, 2],
            "listDeployments requested pages %s, expected [0, 1, 2, 2]: page 2 is rejected with 401, "
            "and after refreshing the token only page 2 is replayed - pages 0 and 1 are already "
            "fetched and must not be requested again" % pages)

    statuses = [e["status"] for e in gets]
    require(statuses == [200, 200, 401, 200],
            "listDeployments statuses were %s, expected [200, 200, 401, 200]" % statuses)

    # The 401 must be answered by exactly one token exchange, then the same page replayed.
    rejected = gets[2]
    replay = gets[3]
    between = [e for e in entries if rejected["seq"] < e["seq"] < replay["seq"]]
    require(len(between) == 1 and between[0]["path"] == LOGIN_PATH,
            "between the 401 and the replay the client sent %s, expected exactly one token exchange"
            % [(e["method"], e["path"]) for e in between])

    # --- token reuse ----------------------------------------------------------------
    second_login_seq = logins[1]["seq"]
    authorised = gets + patches
    tokens = {}
    for entry in authorised:
        header = entry.get("authorization")
        if not require(isinstance(header, str) and header.startswith("Bearer "),
                       "%s %s was sent with Authorization=%r, expected 'Bearer <access_token>'"
                       % (entry["method"], entry["path"], entry.get("authorization"))):
            continue
        token = header[len("Bearer "):].strip()
        require(token != "", "%s %s sent an empty bearer token" % (entry["method"], entry["path"]))
        tokens[entry["seq"]] = token

    before = {t for seq, t in tokens.items() if seq < second_login_seq}
    after = {t for seq, t in tokens.items() if seq > second_login_seq}
    require(len(before) == 1,
            "the calls before the refresh used %d distinct access tokens (%s); one token is "
            "obtained and reused" % (len(before), sorted(before)))
    require(len(after) == 1,
            "the calls after the refresh used %d distinct access tokens (%s); the refreshed token "
            "is reused, not re-fetched per call" % (len(after), sorted(after)))
    if len(before) == 1 and len(after) == 1:
        require(before != after,
                "the calls after the refresh reused the expired access token %s" % sorted(before))

    # --- patch wire shape -----------------------------------------------------------
    expected_patches = [
        ("dep-04", {"name": RENAMED}),
        ("dep-05", {"description": REDESCRIBED}),
        ("dep-06", {"iconId": NEW_ICON_ID}),
    ]
    for entry, (deployment_id, expected_body) in zip(patches, expected_patches):
        label = "patchDeployment %s" % deployment_id
        require(entry["path"] == DEPLOYMENTS_PATH + "/" + deployment_id,
                "expected a PATCH to %s/%s, got %s"
                % (DEPLOYMENTS_PATH, deployment_id, entry["path"]))
        require((entry.get("contentType") or "").lower().startswith("application/json"),
                "%s sent Content-Type %r, expected application/json"
                % (label, entry.get("contentType")))
        require(not entry.get("query"),
                "%s carried a query string %r" % (label, entry.get("query")))
        body = body_object(entry, label)
        if body is None:
            continue
        check_no_empty_values(body, label)
        require(body == expected_body,
                "%s body was %s, expected exactly %s: the fields the caller left unset must be "
                "absent from the JSON object, not sent as \"\" or null"
                % (label, json.dumps(body, sort_keys=True), json.dumps(expected_body, sort_keys=True)))
        require(entry["status"] == 200, "%s was answered %d" % (label, entry["status"]))


def check_result():
    if not RESULT.exists():
        fail("TestMain wrote no result file at run/result.json")
        return
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    require(result.get("failures") == [],
            "TestMain reported assertion failures: %s" % result.get("failures"))

    deployments = result.get("deployments")
    if not require(isinstance(deployments, list),
                   "listAllDeployments returned no deployment list"):
        return
    ids = [d.get("id") for d in deployments]
    require(ids == EXPECTED_IDS,
            "listAllDeployments returned ids %s, expected %s with nothing dropped or duplicated "
            "across the token refresh" % (ids, EXPECTED_IDS))
    require(len(set(ids)) == len(ids), "listAllDeployments returned duplicate deployments: %s" % ids)

    renamed = result.get("renamed") or {}
    require(renamed.get("name") == RENAMED,
            "dep-04 name is %r after the update, expected %r" % (renamed.get("name"), RENAMED))
    require(renamed.get("description") == ORIGINAL_DEP04_DESCRIPTION,
            "dep-04 description is %r after a name-only update, expected it untouched at %r"
            % (renamed.get("description"), ORIGINAL_DEP04_DESCRIPTION))

    redescribed = result.get("redescribed") or {}
    require(redescribed.get("description") == REDESCRIBED,
            "dep-05 description is %r after the update, expected %r"
            % (redescribed.get("description"), REDESCRIBED))
    require(redescribed.get("name") == "batch-etl",
            "dep-05 name is %r after a description-only update, expected it untouched at 'batch-etl'"
            % redescribed.get("name"))

    reiconed = result.get("reiconed") or {}
    require(reiconed.get("id") == "dep-06",
            "icon-only update returned deployment %r, expected 'dep-06'" % reiconed.get("id"))
    require(reiconed.get("name") == "observability",
            "dep-06 name is %r after an icon-only update, expected it untouched at 'observability'"
            % reiconed.get("name"))
    require(reiconed.get("description") == "Log and metric collectors",
            "dep-06 description is %r after an icon-only update, expected it untouched"
            % reiconed.get("description"))


def check_documentation():
    """The contract must still say what it is and still point at the pages it came from."""
    try:
        contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
        sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("docs/contract.json or docs/official_sources.json is unreadable: %s" % exc)
        return

    require(contract.get("source_kind") == "reference-documentation",
            "docs/contract.json must record source_kind 'reference-documentation'")
    statement = contract.get("source_statement", "")
    require("reference documentation" in statement and "not a published specification" in statement,
            "docs/contract.json must state plainly that its source is reference documentation "
            "rather than a published specification")

    operations = contract.get("operations", [])
    require({o.get("operation_id") for o in operations} == OPERATION_IDS,
            "docs/contract.json must define exactly the operations %s" % sorted(OPERATION_IDS))

    pages = sources.get("pages", [])
    urls = {p.get("url") for p in pages}
    for page in pages:
        url = page.get("url", "")
        require(url.startswith("https://developer.broadcom.com/xapis"),
                "docs/official_sources.json page %r is not an xAPIs reference URL" % url)
        require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", page.get("date_fetched") or "") is not None,
                "docs/official_sources.json page %r has no YYYY-MM-DD date_fetched" % url)
    for operation_id in OPERATION_IDS:
        require(any(p.get("operation") == operation_id for p in pages),
                "docs/official_sources.json records no page for operation %r" % operation_id)
    for operation in operations:
        require(operation.get("source_url") in urls,
                "operation %r cites %r, which is not recorded in docs/official_sources.json"
                % (operation.get("operation_id"), operation.get("source_url")))


def main():
    restore_protected_files()
    if compile_project():
        run_harness()
        check_traffic(load_log())
        check_result()
    check_documentation()

    if failures:
        print("FAIL (%d problem%s)" % (len(failures), "" if len(failures) == 1 else "s"))
        for message in failures:
            print("  - " + message)
        return 1
    print("PASS")
    print("  9 requests, all against operations named in docs/contract.json")
    print("  access token refreshed once after the 401 and no page re-fetched")
    print("  unset optional fields omitted from every request body")
    return 0


if __name__ == "__main__":
    sys.exit(main())
