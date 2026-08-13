#!/usr/bin/env python3
"""Protected verifier for the VCF Operations for Networks application onboarding task.

Compiles the single-file client together with the contract-pinned loopback mock and
the TestMain harness, runs partial and successful onboarding passes, and asserts:

  * the exact request sequence on the wire, operation by operation
  * that every unset optional field is omitted rather than sent empty
  * that partial-failure and successful reports describe what actually happened

Only 127.0.0.1 is contacted. No live VMware endpoint is used.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- values fixed by the mock and the input fixture -------------------------
TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA=="
AUTH = "NetworkInsight " + TOKEN
USERNAME = "onboarding-svc@local"
PASSWORD = "Ins1ght!-Onboard"
APP_ID = "18230:561:271275765"
APP_NAME = "Payments-Prod"
TIERS_PATH = f"/api/ni/groups/applications/{APP_ID}/tiers"
WEB_TIER_ID = "18230:562:1266458745"
APP_TIER_ID = "18230:562:1266458746"
FAIL_MESSAGE = "Invalid membership criteria: no entity matches filter"
TIER_IDS = {
    "web-tier": WEB_TIER_ID,
    "app-tier": APP_TIER_ID,
    "db-tier": "18230:562:1266458747",
    "cache-tier": "18230:562:1266458748",
}

# --- provenance fixed by the pinned specification --------------------------
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
OPERATION_IDS = {"create", "addApplication", "addTier", "listApplicationTiers", "delete"}

failures = []


def check(condition, message):
    if not condition:
        failures.append(message)
    return bool(condition)


def fatal(message):
    print("VERIFY: FAIL")
    print("  - " + message)
    sys.exit(1)


def keys_exactly(obj, expected, where):
    if not isinstance(obj, dict):
        failures.append(f"{where}: expected a JSON object, got {type(obj).__name__}")
        return False
    actual = set(obj)
    expected = set(expected)
    ok = True
    for extra in sorted(actual - expected):
        value = obj[extra]
        failures.append(
            f"{where}: unset optional field {extra!r} must be omitted, "
            f"but was sent as {json.dumps(value)}"
        )
        ok = False
    for missing in sorted(expected - actual):
        failures.append(f"{where}: required field {missing!r} is missing")
        ok = False
    return ok


# ---------------------------------------------------------------- provenance

def verify_docs():
    contract_path = ROOT / "docs" / "contract.json"
    sources_path = ROOT / "docs" / "official_sources.json"
    if not contract_path.is_file() or not sources_path.is_file():
        fatal("docs/contract.json and docs/official_sources.json must both exist")

    contract = json.loads(contract_path.read_text())
    sources = json.loads(sources_path.read_text())

    derived = contract.get("derived_from", {})
    check(derived.get("spec_path") == SPEC_PATH,
          "docs/contract.json: derived_from.spec_path does not name the pinned spec")
    check(derived.get("commit_sha") == SPEC_COMMIT,
          "docs/contract.json: derived_from.commit_sha does not match the pinned revision")
    check(set(contract.get("operations", {})) == OPERATION_IDS,
          "docs/contract.json: operations must be exactly " + ", ".join(sorted(OPERATION_IDS)))
    for op_id, op in contract.get("operations", {}).items():
        check(op.get("operation_id") == op_id,
              f"docs/contract.json: operation {op_id} has a mismatched operation_id")

    primary = sources.get("primary_source", {})
    check(primary.get("spec_path") == SPEC_PATH,
          "docs/official_sources.json: primary_source.spec_path does not name the pinned spec")
    check(primary.get("commit_sha") == SPEC_COMMIT,
          "docs/official_sources.json: primary_source.commit_sha does not match the pinned revision")
    check(primary.get("repository") == "vmware/vcf-api-specs",
          "docs/official_sources.json: primary_source.repository must be vmware/vcf-api-specs")
    recorded = {e.get("operation_id") for e in sources.get("operation_ids", [])}
    check(recorded == OPERATION_IDS,
          "docs/official_sources.json: operation_ids must record exactly "
          + ", ".join(sorted(OPERATION_IDS)))


# ------------------------------------------------------------------ run pass

def run_harness(workdir, config_path=None):
    if shutil.which("javac") is None or shutil.which("java") is None:
        fatal("javac and java must be on PATH")

    client = ROOT / "src" / "AppOnboarder.java"
    if not client.is_file():
        fatal("src/AppOnboarder.java is missing")

    classes = workdir / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    sources = [
        str(client),
        str(ROOT / "tests" / "MockVcfOnServer.java"),
        str(ROOT / "tests" / "TestMain.java"),
    ]
    compile_proc = subprocess.run(
        ["javac", "-nowarn", "-d", str(classes)] + sources,
        capture_output=True, text=True, timeout=180,
    )
    if compile_proc.returncode != 0:
        fatal("compilation failed:\n" + (compile_proc.stderr or compile_proc.stdout).strip())

    out = workdir / "out"
    env = dict(os.environ)
    env["JAVA_TOOL_OPTIONS"] = ""
    run_proc = subprocess.run(
        ["java", "-cp", str(classes), "TestMain", str(out),
         str(config_path or ROOT / "config" / "onboarding.json")],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT), env=env,
    )
    if run_proc.returncode != 0:
        fatal("TestMain did not complete:\n"
              + (run_proc.stderr or run_proc.stdout).strip()[:4000])

    harness_path = out / "harness.json"
    if not harness_path.is_file():
        fatal("harness did not produce out/harness.json")
    harness = json.loads(harness_path.read_text())

    log_path = out / "requests.jsonl"
    if not log_path.is_file():
        fatal("the mock produced no request log")
    requests = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    report_path = out / "report.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else None
    return harness, requests, report


# ----------------------------------------------------------- wire assertions

def verify_requests(requests):
    off_contract = [r for r in requests if r.get("operation_id") is None]
    for r in off_contract:
        failures.append(
            f"request #{r['seq']} {r['method']} {r['path']} is not an operation named "
            "by docs/contract.json"
        )

    got = [(r.get("operation_id"), r.get("method"), r.get("path")) for r in requests]
    want = [
        ("create", "POST", "/api/ni/auth/token"),
        ("addApplication", "POST", "/api/ni/groups/applications"),
        ("addTier", "POST", TIERS_PATH),
        ("addTier", "POST", TIERS_PATH),
        ("addTier", "POST", TIERS_PATH),
        ("listApplicationTiers", "GET", TIERS_PATH),
        ("delete", "DELETE", "/api/ni/auth/token"),
    ]
    if got != want:
        failures.append(
            "request sequence mismatch\n      expected: "
            + json.dumps(want) + "\n      actual:   " + json.dumps(got)
        )

    # Body-shape checks are keyed off the operation and the tier name rather than
    # off position, so a sequence mismatch still yields full wire diagnostics.
    def first(op_id):
        return next((r for r in requests if r.get("operation_id") == op_id), None)

    def tier_request(tier_name):
        for r in requests:
            if r.get("operation_id") != "addTier" or not r.get("body_raw"):
                continue
            try:
                if json.loads(r["body_raw"]).get("name") == tier_name:
                    return r
            except ValueError:
                continue
        return None

    login = first("create")
    create_app = first("addApplication")
    web = tier_request("web-tier")
    app = tier_request("app-tier")
    db = tier_request("db-tier")
    listing = first("listApplicationTiers")
    logout = first("delete")

    for label, entry in [("create", login), ("addApplication", create_app),
                         ("addTier web-tier", web), ("addTier app-tier", app),
                         ("addTier db-tier", db), ("delete", logout)]:
        if entry is None:
            failures.append(f"{label} was never sent")
    if listing is None:
        failures.append(
            "listApplicationTiers was never sent: the report must be reconciled "
            "against the server"
        )

    if login is not None:
        verify_login(login)
    if create_app is not None:
        verify_add_application(create_app)
    if web is not None:
        verify_web_tier(web)
    if app is not None:
        verify_app_tier(app)
    if db is not None:
        verify_tier_common(db, "db-tier", 400)
        keys_exactly(json.loads(db["body_raw"]), {"name", "group_membership_criteria"},
                     "addTier db-tier TierRequest")

    # -- cache-tier must never reach the wire --------------------------------
    for r in requests:
        if r.get("body_raw") and "cache-tier" in r["body_raw"]:
            failures.append(
                f"request #{r['seq']} attempted cache-tier: tier creation must stop "
                "at the first failure"
            )

    if listing is not None:
        check(listing["headers"].get("authorization") == AUTH,
              "listApplicationTiers must send Authorization: NetworkInsight {token}")
        check(listing["body_present"] is False,
              "listApplicationTiers must not send a request body")
        check(listing["query"] is None,
              "listApplicationTiers must not send query parameters")
        check(listing["response_status"] == 200,
              f"listApplicationTiers returned {listing['response_status']}, expected 200")

    if logout is not None:
        check(logout["headers"].get("authorization") == AUTH,
              "delete must send Authorization: NetworkInsight {token}")
        check(logout["body_present"] is False, "delete must not send a request body")
        check(logout["response_status"] == 204,
              f"delete returned {logout['response_status']}, expected 204")


def verify_login(login):
    # -- create: no Authorization header, no unset optional Domain -----------
    check(login["headers"].get("authorization") is None,
          "create must not send an Authorization header")
    check(str(login["headers"].get("content-type", "")).startswith("application/json"),
          "create must send Content-Type: application/json")
    check(login["response_status"] == 200,
          f"create returned {login['response_status']}, expected 200")
    body = json.loads(login["body_raw"])
    if keys_exactly(body, {"username", "password"}, "create UserCredential"):
        check(body["username"] == USERNAME, "create sent the wrong username")
        check(body["password"] == PASSWORD, "create sent the wrong password")


def verify_add_application(create_app):
    # -- addApplication ------------------------------------------------------
    check(create_app["headers"].get("authorization") == AUTH,
          "addApplication must send Authorization: NetworkInsight {token}")
    check(str(create_app["headers"].get("content-type", "")).startswith("application/json"),
          "addApplication must send Content-Type: application/json")
    check(create_app["response_status"] == 201,
          f"addApplication returned {create_app['response_status']}, expected 201")
    body = json.loads(create_app["body_raw"])
    if keys_exactly(body, {"name"}, "addApplication ApplicationRequest"):
        check(body["name"] == APP_NAME,
              f"addApplication sent name={body['name']!r}, expected {APP_NAME!r}")


def verify_web_tier(web):
    # -- addTier: web-tier, search membership only ---------------------------
    verify_tier_common(web, "web-tier", 201)
    body = json.loads(web["body_raw"])
    if keys_exactly(body, {"name", "group_membership_criteria"}, "addTier web-tier TierRequest"):
        criteria = body["group_membership_criteria"]
        if check(isinstance(criteria, list) and len(criteria) == 1,
                 "addTier web-tier must send exactly one group_membership_criteria entry"):
            entry = criteria[0]
            if keys_exactly(entry, {"membership_type", "search_membership_criteria"},
                            "addTier web-tier GroupMembershipCriteria"):
                check(entry["membership_type"] == "SearchMembershipCriteria",
                      "addTier web-tier membership_type must be SearchMembershipCriteria")
                smc = entry["search_membership_criteria"]
                if keys_exactly(smc, {"entity_type", "filter"},
                                "addTier web-tier SearchMembershipCriteria"):
                    check(smc["entity_type"] == "VirtualMachine",
                          "addTier web-tier entity_type must be VirtualMachine")
                    check(smc["filter"]
                          == "security_groups.entity_id = '18230:82:604573173'",
                          "addTier web-tier filter does not match the fixture")


def verify_app_tier(app):
    # -- addTier: app-tier, IP membership only -------------------------------
    verify_tier_common(app, "app-tier", 201)
    body = json.loads(app["body_raw"])
    if keys_exactly(body, {"name", "group_membership_criteria"}, "addTier app-tier TierRequest"):
        criteria = body["group_membership_criteria"]
        if check(isinstance(criteria, list) and len(criteria) == 1,
                 "addTier app-tier must send exactly one group_membership_criteria entry"):
            entry = criteria[0]
            if keys_exactly(entry, {"membership_type", "ip_address_membership_criteria"},
                            "addTier app-tier GroupMembershipCriteria"):
                check(entry["membership_type"] == "IPAddressMembershipCriteria",
                      "addTier app-tier membership_type must be IPAddressMembershipCriteria")
                ipc = entry["ip_address_membership_criteria"]
                if keys_exactly(ipc, {"ip_addresses"},
                                "addTier app-tier IpAddressMembershipCriteria"):
                    check(ipc["ip_addresses"]
                          == ["10.24.8.0/24", "10.24.9.10-10.24.9.60"],
                          "addTier app-tier ip_addresses do not match the fixture")


def verify_tier_common(entry, tier_name, expected_status):
    label = f"addTier {tier_name}"
    check(entry["headers"].get("authorization") == AUTH,
          f"{label} must send Authorization: NetworkInsight {{token}}")
    check(str(entry["headers"].get("content-type", "")).startswith("application/json"),
          f"{label} must send Content-Type: application/json")
    check(entry["response_status"] == expected_status,
          f"{label} returned {entry['response_status']}, expected {expected_status}")
    try:
        body = json.loads(entry["body_raw"])
    except (TypeError, ValueError):
        failures.append(f"{label} sent a body that is not valid JSON")
        return
    check(isinstance(body, dict) and body.get("name") == tier_name,
          f"{label} sent name={body.get('name')!r}, expected {tier_name!r}")


# --------------------------------------------------------- report assertions

def verify_report(harness, report):
    check(harness.get("error") is None,
          f"the client raised out of run(): {harness.get('error')}")
    if not check(report is not None, "the client did not write out/report.json"):
        return
    check(harness.get("exit_code") == 3,
          f"exit code was {harness.get('exit_code')}, expected 3 for a partial onboarding")

    app = report.get("application")
    if check(isinstance(app, dict), "report.application must be an object"):
        check(app.get("name") == APP_NAME, "report.application.name is wrong")
        check(app.get("created") is True,
              "report.application.created must be true: the application was created")
        check(app.get("entity_id") == APP_ID,
              "report.application.entity_id must be the entity_id returned by addApplication")

    tiers = report.get("tiers")
    if not check(isinstance(tiers, list) and len(tiers) == 4,
                 "report.tiers must list all four configured tiers"):
        return
    by_name = {t.get("name"): t for t in tiers if isinstance(t, dict)}
    check([t.get("name") if isinstance(t, dict) else None for t in tiers]
          == ["web-tier", "app-tier", "db-tier", "cache-tier"],
          "report.tiers must stay in configuration order")

    web = by_name.get("web-tier", {})
    check(web.get("status") == "created",
          f"web-tier was created but is reported as {web.get('status')!r}")
    check(web.get("entity_id") == WEB_TIER_ID,
          "web-tier entity_id must be the one addTier returned")

    appt = by_name.get("app-tier", {})
    check(appt.get("status") == "created",
          f"app-tier was created but is reported as {appt.get('status')!r}")
    check(appt.get("entity_id") == APP_TIER_ID,
          "app-tier entity_id must be the one addTier returned")

    db = by_name.get("db-tier", {})
    check(db.get("status") == "failed",
          f"db-tier failed but is reported as {db.get('status')!r}")
    check(db.get("http_status") == 400, "db-tier http_status must be 400")
    check(db.get("error_code") == 400, "db-tier error_code must be ApiError.code (400)")
    check(db.get("error_message") == FAIL_MESSAGE,
          f"db-tier error_message must be the server's ApiError.message, got "
          f"{db.get('error_message')!r}")
    check("entity_id" not in db, "db-tier was never created, so it has no entity_id")

    cache = by_name.get("cache-tier", {})
    check(cache.get("status") == "not_attempted",
          f"cache-tier was never sent, so its status must be 'not_attempted', "
          f"got {cache.get('status')!r}")
    check("entity_id" not in cache, "cache-tier was never created, so it has no entity_id")

    check(report.get("server_tiers") == ["web-tier", "app-tier"],
          f"report.server_tiers must come from listApplicationTiers, got "
          f"{report.get('server_tiers')!r}")
    check(report.get("reconciled") is True,
          "report.reconciled must be true: listApplicationTiers succeeded")
    check(report.get("outcome") == "partial",
          f"report.outcome must be 'partial', got {report.get('outcome')!r}")
    check(report.get("failed_at") == "db-tier",
          f"report.failed_at must be 'db-tier', got {report.get('failed_at')!r}")


def verify_success_requests(requests):
    got = [(r.get("operation_id"), r.get("method"), r.get("path")) for r in requests]
    want = [
        ("create", "POST", "/api/ni/auth/token"),
        ("addApplication", "POST", "/api/ni/groups/applications"),
        *(('addTier', 'POST', TIERS_PATH) for _ in range(4)),
        ("listApplicationTiers", "GET", TIERS_PATH),
        ("delete", "DELETE", "/api/ni/auth/token"),
    ]
    check(got == want,
          "successful run request sequence must authenticate, create the application, "
          "create all four tiers, reconcile once, then revoke the token")

    tier_names = []
    for r in requests:
        if r.get("operation_id") != "addTier":
            continue
        try:
            tier_names.append(json.loads(r["body_raw"])["name"])
        except (KeyError, TypeError, ValueError):
            failures.append("successful run sent an addTier body without a valid name")
    check(tier_names == ["web-tier", "app-tier", "db-tier", "cache-tier"],
          "successful run must create all tiers in configuration order")
    for r in requests:
        if r.get("operation_id") == "addTier":
            check(r.get("response_status") == 201,
                  "every addTier call in the successful run must succeed")


def verify_success_report(harness, report):
    check(harness.get("error") is None,
          f"successful run raised out of run(): {harness.get('error')}")
    if not check(report is not None, "successful run did not write out/report.json"):
        return
    check(harness.get("exit_code") == 0,
          f"successful onboarding exit code was {harness.get('exit_code')}, expected 0")
    check("failed_at" not in report,
          "successful report must omit failed_at")

    app = report.get("application")
    if check(isinstance(app, dict), "successful report.application must be an object"):
        check(app.get("name") == APP_NAME, "successful report.application.name is wrong")
        check(app.get("created") is True,
              "successful report.application.created must be true")
        check(app.get("entity_id") == APP_ID,
              "successful report.application.entity_id must come from addApplication")

    tiers = report.get("tiers")
    names = ["web-tier", "app-tier", "db-tier", "cache-tier"]
    if check(isinstance(tiers, list) and len(tiers) == len(names),
             "successful report.tiers must list all four configured tiers"):
        check([t.get("name") for t in tiers if isinstance(t, dict)] == names,
              "successful report.tiers must stay in configuration order")
        for tier, name in zip(tiers, names):
            check(isinstance(tier, dict) and tier.get("status") == "created",
                  f"successful {name} status must be 'created'")
            check(isinstance(tier, dict) and tier.get("entity_id") == TIER_IDS[name],
                  f"successful {name} report entry must carry its addTier entity_id")

    check(report.get("server_tiers") == names,
          "successful report.server_tiers must come from listApplicationTiers")
    check(report.get("reconciled") is True,
          "successful report.reconciled must be true")
    check(report.get("outcome") == "succeeded",
          "successful report.outcome must be 'succeeded'")


def main():
    verify_docs()
    with tempfile.TemporaryDirectory(prefix="vcfon-verify-") as tmp:
        tmp = Path(tmp)
        harness, requests, report = run_harness(tmp / "partial")
        verify_requests(requests)
        verify_report(harness, report)

        success_config = json.loads((ROOT / "config" / "onboarding.json").read_text())
        success_config["tiers"][2]["search_membership"]["filter"] = (
            "security_groups.entity_id = '18230:82:604573173'"
        )
        success_config_path = tmp / "success-onboarding.json"
        success_config_path.write_text(json.dumps(success_config))
        harness, requests, report = run_harness(tmp / "success", success_config_path)
        verify_success_requests(requests)
        verify_success_report(harness, report)

    if failures:
        print("VERIFY: FAIL")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("VERIFY: PASS")
    print("  contract provenance, request wire shape and partial-failure report all match")


if __name__ == "__main__":
    main()
