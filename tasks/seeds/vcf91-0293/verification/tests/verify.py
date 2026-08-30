#!/usr/bin/env python3
"""Protected verifier for the VCF Operations for Networks syslog rollout.

Runs three scenarios against a loopback mock appliance pinned to
docs/contract.json, then asserts both the returned report and the exact wire
shape of every request the mock recorded. No live VMware endpoint is contacted.

Standard library only. Run with: python3 tests/verify.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

from mock_appliance import ApplianceConfig, MockAppliance  # noqa: E402

from vcfon_syslog.plan import load_plan  # noqa: E402
from vcfon_syslog.rollout import apply_syslog_plan  # noqa: E402

TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA=="
AUTH_HEADER = "NetworkInsight " + TOKEN
TIMEOUT = 8.0

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        return True
    FAILURES.append(label + (": " + detail if detail else ""))
    return False


def check_equal(label, actual, expected):
    return check(
        label,
        actual == expected,
        "expected %s, got %s" % (json.dumps(expected, sort_keys=True, default=str), json.dumps(actual, sort_keys=True, default=str)),
    )


def fixture(name):
    return os.path.join(REPO_ROOT, "fixtures", name)


def operation_sequence(requests):
    return [entry["operation_id"] for entry in requests]


def assert_no_null_valued_keys(scenario, requests):
    """An optional field that is unset must be absent, never null/empty."""
    for entry in requests:
        body = entry["body_json"]
        if not isinstance(body, dict):
            continue
        stack = [("", body)]
        while stack:
            prefix, node = stack.pop()
            for key, value in node.items():
                path = prefix + "." + key if prefix else key
                check(
                    "%s: request %d (%s) encodes unset field %s"
                    % (scenario, entry["seq"], entry["operation_id"], path),
                    value is not None,
                    "found JSON null; the field must be omitted instead",
                )
                if isinstance(value, dict):
                    stack.append((path, value))


def assert_authenticated(scenario, requests):
    for entry in requests:
        if entry["operation_id"] == "create":
            check(
                "%s: request %d (create) must send no Authorization header" % (scenario, entry["seq"]),
                "authorization" not in entry["headers"],
                "got %r" % entry["headers"].get("authorization"),
            )
        else:
            check_equal(
                "%s: request %d (%s) Authorization header" % (scenario, entry["seq"], entry["operation_id"]),
                entry["headers"].get("authorization"),
                AUTH_HEADER,
            )


def assert_all_routed(scenario, requests):
    for entry in requests:
        check(
            "%s: request %d %s %s is not an operation the contract names"
            % (scenario, entry["seq"], entry["method"], entry["path"]),
            entry["operation_id"] is not None,
        )


def assert_json_body(scenario, entry, expected):
    label = "%s: request %d (%s) body" % (scenario, entry["seq"], entry["operation_id"])
    if not check(label + " must be a JSON object", isinstance(entry["body_json"], dict), "got %r" % entry["body_raw"]):
        return
    check_equal(label, entry["body_json"], expected)
    check_equal(
        "%s: request %d (%s) Content-Type" % (scenario, entry["seq"], entry["operation_id"]),
        entry["headers"].get("content-type"),
        "application/json",
    )
    if "port" in entry["body_json"]:
        check(
            "%s: request %d (%s) port must be a JSON number" % (scenario, entry["seq"], entry["operation_id"]),
            isinstance(entry["body_json"]["port"], int) and not isinstance(entry["body_json"]["port"], bool),
            "got %r" % (entry["body_json"]["port"],),
        )


def assert_empty_body(scenario, entry):
    check_equal(
        "%s: request %d (%s) must send no body" % (scenario, entry["seq"], entry["operation_id"]),
        entry["body_raw"],
        "",
    )


# ---------------------------------------------------------------------------
# Scenario A: a later step fails; earlier steps must be reported accurately.
# ---------------------------------------------------------------------------
def scenario_partial_failure(log_path):
    scenario = "partial-failure"
    plan = load_plan(fixture("plan_partial_failure.json"))
    config = ApplianceConfig(
        username="svc-netops@local",
        password="Fwd-Logs-2026",
        token=TOKEN,
        existing_targets=[
            {"ip_or_fqdn": "syslog-b.corp.example.net", "port": 514, "protocol": "UDP", "nick_name": "legacy-b"}
        ],
        unresolvable_hosts=["syslog-eu.corp.example.net"],
        test_results={
            "syslog-b.corp.example.net": (False, "No response from syslog-b.corp.example.net:1514"),
        },
    )

    with MockAppliance(config, log_path) as appliance:
        report = apply_syslog_plan(plan, appliance.base_url, timeout=TIMEOUT)
        requests = appliance.requests()
        final_targets = appliance.targets

    check_equal(
        scenario + ": report",
        report,
        {
            "outcome": "partial_failure",
            "existing_targets": ["syslog-b.corp.example.net"],
            "targets": [
                {
                    "ip_or_fqdn": "10.79.198.20",
                    "action": "add",
                    "status": "applied",
                    "verified": True,
                },
                {
                    "ip_or_fqdn": "syslog-b.corp.example.net",
                    "action": "update",
                    "status": "applied",
                    "verified": False,
                },
                {
                    "ip_or_fqdn": "syslog-eu.corp.example.net",
                    "action": "add",
                    "status": "failed",
                    "http_status": 400,
                    "error": {
                        "code": 400,
                        "message": "Cannot resolve syslog target host 'syslog-eu.corp.example.net'",
                    },
                },
                {
                    "ip_or_fqdn": "10.79.198.41",
                    "action": "add",
                    "status": "skipped",
                },
            ],
            "applied_count": 2,
            "failed_index": 2,
            "token_released": True,
        },
    )

    assert_all_routed(scenario, requests)
    assert_authenticated(scenario, requests)
    assert_no_null_valued_keys(scenario, requests)

    check_equal(
        scenario + ": operation sequence",
        operation_sequence(requests),
        [
            "create",
            "getSyslogTargetList",
            "addSyslogTarget",
            "updateSyslogTarget",
            "addSyslogTarget",
            "sendSyslogTestMessage",
            "sendSyslogTestMessage",
            "delete",
        ],
    )
    if len(requests) != 8:
        return

    body_a = {"ip_or_fqdn": "10.79.198.20", "port": 514, "protocol": "UDP", "nick_name": "sec-collector-a"}
    body_b = {"ip_or_fqdn": "syslog-b.corp.example.net", "port": 1514, "protocol": "UDP"}
    body_eu = {
        "ip_or_fqdn": "syslog-eu.corp.example.net",
        "port": 514,
        "protocol": "UDP",
        "nick_name": "eu-collector",
    }

    assert_json_body(scenario, requests[0], {
        "username": "svc-netops@local",
        "password": "Fwd-Logs-2026",
        "domain": {"domain_type": "LOCAL"},
    })

    check_equal(scenario + ": getSyslogTargetList path", requests[1]["path"], "/api/ni/settings/syslog")
    check_equal(scenario + ": getSyslogTargetList must send no query string", requests[1]["query"], "")
    assert_empty_body(scenario, requests[1])

    check_equal(scenario + ": addSyslogTarget path", requests[2]["path"], "/api/ni/settings/syslog")
    assert_json_body(scenario, requests[2], body_a)

    check_equal(
        scenario + ": updateSyslogTarget path",
        requests[3]["path"],
        "/api/ni/settings/syslog/syslog-b.corp.example.net",
    )
    assert_json_body(scenario, requests[3], body_b)

    check_equal(scenario + ": failing addSyslogTarget path", requests[4]["path"], "/api/ni/settings/syslog")
    assert_json_body(scenario, requests[4], body_eu)

    check_equal(
        scenario + ": test message path",
        requests[5]["path"],
        "/api/ni/settings/syslog/send-test-log",
    )
    assert_json_body(scenario, requests[5], body_a)
    check_equal(
        scenario + ": test message path",
        requests[6]["path"],
        "/api/ni/settings/syslog/send-test-log",
    )
    assert_json_body(scenario, requests[6], body_b)

    check_equal(scenario + ": token release method", requests[7]["method"], "DELETE")
    check_equal(scenario + ": token release path", requests[7]["path"], "/api/ni/auth/token")
    assert_empty_body(scenario, requests[7])

    check_equal(
        scenario + ": appliance state after the run",
        sorted(final_targets, key=lambda entry: entry["ip_or_fqdn"]),
        [
            {"ip_or_fqdn": "10.79.198.20", "port": 514, "protocol": "UDP", "nick_name": "sec-collector-a"},
            {"ip_or_fqdn": "syslog-b.corp.example.net", "port": 1514, "protocol": "UDP"},
        ],
    )


# ---------------------------------------------------------------------------
# Scenario B: every step applies; the LDAP domain carries a value.
# ---------------------------------------------------------------------------
def scenario_clean_apply(log_path):
    scenario = "clean-apply"
    plan = load_plan(fixture("plan_clean_apply.json"))
    config = ApplianceConfig(
        username="netops@corp.example.net",
        password="Fwd-Logs-2026",
        token=TOKEN,
    )

    with MockAppliance(config, log_path) as appliance:
        report = apply_syslog_plan(plan, appliance.base_url, timeout=TIMEOUT)
        requests = appliance.requests()

    check_equal(
        scenario + ": report",
        report,
        {
            "outcome": "applied",
            "existing_targets": [],
            "targets": [
                {"ip_or_fqdn": "10.79.199.11", "action": "add", "status": "applied", "verified": True},
                {"ip_or_fqdn": "10.79.199.12", "action": "add", "status": "applied", "verified": True},
            ],
            "applied_count": 2,
            "failed_index": None,
            "token_released": True,
        },
    )

    assert_all_routed(scenario, requests)
    assert_authenticated(scenario, requests)
    assert_no_null_valued_keys(scenario, requests)

    check_equal(
        scenario + ": operation sequence",
        operation_sequence(requests),
        [
            "create",
            "getSyslogTargetList",
            "addSyslogTarget",
            "addSyslogTarget",
            "sendSyslogTestMessage",
            "sendSyslogTestMessage",
            "delete",
        ],
    )
    if len(requests) != 7:
        return

    assert_json_body(scenario, requests[0], {
        "username": "netops@corp.example.net",
        "password": "Fwd-Logs-2026",
        "domain": {"domain_type": "LDAP", "value": "corp.example.net"},
    })
    assert_json_body(scenario, requests[2], {"ip_or_fqdn": "10.79.199.11", "port": 514, "protocol": "UDP"})
    assert_json_body(scenario, requests[3], {
        "ip_or_fqdn": "10.79.199.12",
        "port": 6514,
        "protocol": "UDP",
        "nick_name": "dr-collector",
    })
    assert_json_body(scenario, requests[4], {"ip_or_fqdn": "10.79.199.11", "port": 514, "protocol": "UDP"})
    assert_json_body(scenario, requests[5], {
        "ip_or_fqdn": "10.79.199.12",
        "port": 6514,
        "protocol": "UDP",
        "nick_name": "dr-collector",
    })


# ---------------------------------------------------------------------------
# Scenario C: no domain at all, and the only target already exists.
# ---------------------------------------------------------------------------
def scenario_no_domain(log_path):
    scenario = "no-domain"
    plan = load_plan(fixture("plan_no_domain.json"))
    config = ApplianceConfig(
        username="admin@local",
        password="Fwd-Logs-2026",
        token=TOKEN,
        existing_targets=[{"ip_or_fqdn": "10.79.200.5", "port": 514, "protocol": "UDP"}],
    )

    with MockAppliance(config, log_path) as appliance:
        report = apply_syslog_plan(plan, appliance.base_url, timeout=TIMEOUT)
        requests = appliance.requests()

    check_equal(
        scenario + ": report",
        report,
        {
            "outcome": "applied",
            "existing_targets": ["10.79.200.5"],
            "targets": [
                {"ip_or_fqdn": "10.79.200.5", "action": "update", "status": "applied", "verified": True},
            ],
            "applied_count": 1,
            "failed_index": None,
            "token_released": True,
        },
    )

    assert_all_routed(scenario, requests)
    assert_authenticated(scenario, requests)
    assert_no_null_valued_keys(scenario, requests)

    check_equal(
        scenario + ": operation sequence",
        operation_sequence(requests),
        ["create", "getSyslogTargetList", "updateSyslogTarget", "sendSyslogTestMessage", "delete"],
    )
    if len(requests) != 5:
        return

    assert_json_body(scenario, requests[0], {"username": "admin@local", "password": "Fwd-Logs-2026"})
    check_equal(
        scenario + ": updateSyslogTarget path",
        requests[2]["path"],
        "/api/ni/settings/syslog/10.79.200.5",
    )
    assert_json_body(scenario, requests[2], {
        "ip_or_fqdn": "10.79.200.5",
        "port": 514,
        "protocol": "UDP",
        "nick_name": "hq-collector",
    })


def main():
    scenarios = (
        ("partial-failure", scenario_partial_failure),
        ("clean-apply", scenario_clean_apply),
        ("no-domain", scenario_no_domain),
    )
    with tempfile.TemporaryDirectory() as workdir:
        for name, runner in scenarios:
            log_path = os.path.join(workdir, name + ".jsonl")
            try:
                runner(log_path)
            except Exception:
                FAILURES.append("%s: raised %s" % (name, traceback.format_exc().strip().splitlines()[-1]))
                traceback.print_exc()

    if FAILURES:
        print("FAIL (%d of %d checks failed)" % (len(FAILURES), CHECKS[0]))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("PASS (%d checks)" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
