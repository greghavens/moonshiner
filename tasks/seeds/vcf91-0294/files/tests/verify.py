#!/usr/bin/env python3
"""Verifier for the precheck-gated vCenter onboarding tool.

Starts the contract-pinned loopback mock on an ephemeral 127.0.0.1 port, runs
three plans through it, and checks both the returned report and the recorded
request log: the exact ordered operations, the exact JSON body of every request,
the Authorization header on every request, and the omission of every optional
field the operator did not set.

No live VMware endpoint is contacted.

Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

from mock_appliance import ApplianceConfig, MockAppliance  # noqa: E402

from vcfon_vcenter.client import (  # noqa: E402
    datasource_body,
    user_credential_body,
    validation_body,
)
from vcfon_vcenter.onboarding import onboard_vcenters  # noqa: E402
from vcfon_vcenter.plan import load_plan  # noqa: E402

FIXTURES = os.path.join(ROOT, "fixtures")
CONTRACT = json.load(open(os.path.join(ROOT, "docs", "contract.json"), encoding="utf-8"))

PROXY_A = "18230:901:1585583463"
PROXY_B = "18230:901:1585583991"
PROXY_UNKNOWN = "18230:901:0000000000"

VC_USER = "svc-vrni@vsphere.local"


class Failures(list):
    def check(self, condition, message):
        if not condition:
            self.append(message)
        return bool(condition)

    def equal(self, actual, expected, message):
        return self.check(
            actual == expected,
            "%s\n      expected: %s\n      actual:   %s"
            % (message, json.dumps(expected, sort_keys=True), json.dumps(actual, sort_keys=True)),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def empty_paths(value, prefix=""):
    """Every path in ``value`` whose leaf is null, "", {} or []."""
    found = []
    if isinstance(value, dict):
        if not value and prefix:
            found.append(prefix)
        for key, item in value.items():
            found.extend(empty_paths(item, "%s.%s" % (prefix, key) if prefix else key))
    elif isinstance(value, list):
        if not value and prefix:
            found.append(prefix)
        for index, item in enumerate(value):
            found.extend(empty_paths(item, "%s[%d]" % (prefix, index)))
    elif value is None or value == "":
        found.append(prefix or "<root>")
    return found


def creds(password):
    return {"username": VC_USER, "password": password}


def operation(operation_id):
    for entry in CONTRACT["operations"]:
        if entry["operationId"] == operation_id:
            return entry
    raise KeyError(operation_id)


def run_plan(failures, name, fixture, config):
    """Run one fixture through a fresh mock and return (report, log, state)."""
    plan = load_plan(os.path.join(FIXTURES, fixture))
    handle, log_path = tempfile.mkstemp(prefix="vcfon-%s-" % name, suffix=".jsonl")
    os.close(handle)
    with MockAppliance(config, log_path) as mock:
        try:
            report = onboard_vcenters(plan, mock.base_url, timeout=10.0)
        except Exception as error:  # noqa: BLE001 - a raised run is a failed run
            failures.append("%s: onboard_vcenters raised %r" % (name, error))
            return None, [], None
        log = mock.requests()
        created = mock.datasources
        outstanding = mock.token_outstanding
    return report, log, {"created": created, "token_outstanding": outstanding}


def check_wire_hygiene(failures, name, log):
    """Rules that hold for every request in every run."""
    for entry in log:
        label = "%s request #%d (%s %s)" % (name, entry["seq"], entry["method"], entry["path"])
        headers = entry["headers"]
        if entry["operation_id"] == "create":
            failures.check(
                "authorization" not in headers,
                "%s: 'create' must send no Authorization header, got %r"
                % (label, headers.get("authorization")),
            )
        else:
            failures.check(
                headers.get("authorization", "").startswith("NetworkInsight "),
                "%s: expected 'Authorization: NetworkInsight {token}', got %r"
                % (label, headers.get("authorization")),
            )
        if entry["body_raw"]:
            failures.check(
                headers.get("content-type") == "application/json",
                "%s: expected Content-Type application/json, got %r"
                % (label, headers.get("content-type")),
            )
            bad = empty_paths(entry["body_json"])
            failures.check(
                not bad,
                "%s: unset optional fields must be omitted, but these were sent empty or null: %s"
                % (label, ", ".join(bad)),
            )
        failures.check(entry["query"] == "", "%s: no query string is expected" % label)


def check_sequence(failures, name, log, expected):
    """Assert the exact ordered (operation_id, method, path, body) of the run."""
    actual = [(entry["operation_id"], entry["method"], entry["path"]) for entry in log]
    wanted = [(op, method, path) for op, method, path, _ in expected]
    if not failures.equal(actual, wanted, "%s: wrong request sequence" % name):
        return
    for entry, (op, _method, _path, body) in zip(log, expected):
        failures.equal(
            entry["body_json"],
            body,
            "%s: wrong request body for #%d (%s)" % (name, entry["seq"], op),
        )


# ---------------------------------------------------------------------------
# case 1 -- a soft precheck rejection blocks exactly one creation
# ---------------------------------------------------------------------------


def case_mixed(failures):
    name = "plan_mixed"
    config = ApplianceConfig(
        username="admin@vrni.com",
        password="Netw0rk!ns1ght",
        domain={"domain_type": "LDAP", "value": "corp.example.com"},
        token="1rT7tm4riiACSfxrO2BvkA==",
        expiry=1509332642427,
        known_proxy_ids=(PROXY_A, PROXY_B),
        vcenter_passwords={
            "10.197.17.68": "Alpha-Pass-1",
            # vc-beta rotated its service account; the plan carries the old one.
            "vc-beta.corp.example.com": "Beta-Pass-ROTATED",
            "vc-gamma.corp.example.com": "Gamma-Pass-3",
        },
    )
    report, log, state = run_plan(failures, name, "plan_mixed.json", config)
    if report is None:
        return

    check_wire_hygiene(failures, name, log)
    check_sequence(
        failures,
        name,
        log,
        [
            (
                "create",
                "POST",
                "/api/ni/auth/token",
                {
                    "username": "admin@vrni.com",
                    "password": "Netw0rk!ns1ght",
                    "domain": {"domain_type": "LDAP", "value": "corp.example.com"},
                },
            ),
            (
                "validateVCenter",
                "POST",
                "/api/ni/data-sources/vcenters/validate",
                {
                    "ip": "10.197.17.68",
                    "proxy_id": PROXY_A,
                    "credentials": creds("Alpha-Pass-1"),
                },
            ),
            (
                "addVcenterDatasource",
                "POST",
                "/api/ni/data-sources/vcenters",
                {
                    "ip": "10.197.17.68",
                    "proxy_id": PROXY_A,
                    "nickname": "vc-alpha",
                    "credentials": creds("Alpha-Pass-1"),
                },
            ),
            (
                "validateVCenter",
                "POST",
                "/api/ni/data-sources/vcenters/validate",
                {
                    "fqdn": "vc-beta.corp.example.com",
                    "proxy_id": PROXY_A,
                    "credentials": creds("Beta-Pass-2"),
                },
            ),
            (
                "validateVCenter",
                "POST",
                "/api/ni/data-sources/vcenters/validate",
                {
                    "fqdn": "vc-gamma.corp.example.com",
                    "proxy_id": PROXY_B,
                    "credentials": creds("Gamma-Pass-3"),
                    "ipfix_enabled": True,
                },
            ),
            (
                "addVcenterDatasource",
                "POST",
                "/api/ni/data-sources/vcenters",
                {
                    "fqdn": "vc-gamma.corp.example.com",
                    "proxy_id": PROXY_B,
                    "nickname": "vc-gamma",
                    "notes": "Located in DC3",
                    "enabled": False,
                    "credentials": creds("Gamma-Pass-3"),
                    "ipfix_request": {"enable_for_dvs": "dvs-54,dvs-67"},
                },
            ),
            ("delete", "DELETE", "/api/ni/auth/token", None),
        ],
    )

    failures.equal(
        [entry["nickname"] for entry in state["created"]],
        ["vc-alpha", "vc-gamma"],
        "%s: the appliance must hold exactly the two data sources whose precheck passed" % name,
    )
    failures.check(
        not state["token_outstanding"],
        "%s: the auth token must be released before returning" % name,
    )
    failures.equal(
        report,
        {
            "outcome": "partial",
            "created_count": 2,
            "blocked_count": 1,
            "failed_count": 0,
            "token_released": True,
            "datasources": [
                {
                    "nickname": "vc-alpha",
                    "host": "10.197.17.68",
                    "status": "created",
                    "precheck": {
                        "http_status": 200,
                        "code": 200,
                        "message": "Validation successful.",
                    },
                    "entity_id": "18230:902:993642895",
                },
                {
                    "nickname": "vc-beta",
                    "host": "vc-beta.corp.example.com",
                    "status": "precheck_failed",
                    "precheck": {
                        "http_status": 200,
                        "code": 401,
                        "message": (
                            "Cannot complete login to 'vc-beta.corp.example.com' due to an "
                            "incorrect user name or password."
                        ),
                    },
                },
                {
                    "nickname": "vc-gamma",
                    "host": "vc-gamma.corp.example.com",
                    "status": "created",
                    "precheck": {
                        "http_status": 200,
                        "code": 200,
                        "message": "Validation successful.",
                    },
                    "entity_id": "18230:902:993642896",
                },
            ],
        },
        "%s: wrong report" % name,
    )


# ---------------------------------------------------------------------------
# case 2 -- a hard precheck rejection, and a creation that fails on its own
# ---------------------------------------------------------------------------


def case_hard_reject(failures):
    name = "plan_hard_reject"
    config = ApplianceConfig(
        username="admin@vrni.com",
        password="Netw0rk!ns1ght",
        domain=None,
        token="9pQ2vv0mDlkPQ0aJrKuiZQ==",
        known_proxy_ids=(PROXY_A,),
        registered_hosts=("10.197.17.91",),
    )
    report, log, state = run_plan(failures, name, "plan_hard_reject.json", config)
    if report is None:
        return

    check_wire_hygiene(failures, name, log)
    check_sequence(
        failures,
        name,
        log,
        [
            (
                "create",
                "POST",
                "/api/ni/auth/token",
                {"username": "admin@vrni.com", "password": "Netw0rk!ns1ght"},
            ),
            (
                "validateVCenter",
                "POST",
                "/api/ni/data-sources/vcenters/validate",
                {
                    "ip": "10.197.17.90",
                    "proxy_id": PROXY_UNKNOWN,
                    "credentials": creds("Delta-Pass-4"),
                },
            ),
            (
                "validateVCenter",
                "POST",
                "/api/ni/data-sources/vcenters/validate",
                {
                    "ip": "10.197.17.91",
                    "proxy_id": PROXY_A,
                    "credentials": creds("Epsilon-Pass-5"),
                },
            ),
            (
                "addVcenterDatasource",
                "POST",
                "/api/ni/data-sources/vcenters",
                {
                    "ip": "10.197.17.91",
                    "proxy_id": PROXY_A,
                    "nickname": "vc-epsilon",
                    "is_vmc": True,
                    "credentials": creds("Epsilon-Pass-5"),
                },
            ),
            ("delete", "DELETE", "/api/ni/auth/token", None),
        ],
    )

    failures.equal(
        state["created"],
        [],
        "%s: no data source may exist on the appliance after this run" % name,
    )
    failures.equal(
        report,
        {
            "outcome": "blocked",
            "created_count": 0,
            "blocked_count": 1,
            "failed_count": 1,
            "token_released": True,
            "datasources": [
                {
                    "nickname": "vc-delta",
                    "host": "10.197.17.90",
                    "status": "precheck_failed",
                    "precheck": {
                        "http_status": 400,
                        "code": 400,
                        "message": "Proxy node '%s' was not found in /infra/nodes" % PROXY_UNKNOWN,
                    },
                },
                {
                    "nickname": "vc-epsilon",
                    "host": "10.197.17.91",
                    "status": "create_failed",
                    "precheck": {
                        "http_status": 200,
                        "code": 200,
                        "message": "Validation successful.",
                    },
                    "error": {
                        "http_status": 400,
                        "code": 400,
                        "message": "A data source for '10.197.17.91' is already registered",
                    },
                },
            ],
        },
        "%s: wrong report" % name,
    )


# ---------------------------------------------------------------------------
# case 3 -- a LOCAL domain with no value, and a whole-vDS IPFIX intent
# ---------------------------------------------------------------------------


def case_local_domain(failures):
    name = "plan_local_domain"
    config = ApplianceConfig(
        username="admin@local",
        password="L0cal-Only",
        domain={"domain_type": "LOCAL"},
        token="Mgs2YX0ZSY+gHW6RYypeeA==",
        known_proxy_ids=(PROXY_A,),
    )
    report, log, state = run_plan(failures, name, "plan_local_domain.json", config)
    if report is None:
        return

    check_wire_hygiene(failures, name, log)
    check_sequence(
        failures,
        name,
        log,
        [
            (
                "create",
                "POST",
                "/api/ni/auth/token",
                {
                    "username": "admin@local",
                    "password": "L0cal-Only",
                    "domain": {"domain_type": "LOCAL"},
                },
            ),
            (
                "validateVCenter",
                "POST",
                "/api/ni/data-sources/vcenters/validate",
                {
                    "fqdn": "vc-zeta.corp.example.com",
                    "proxy_id": PROXY_A,
                    "credentials": creds("Zeta-Pass-6"),
                    "ipfix_enabled": True,
                },
            ),
            (
                "addVcenterDatasource",
                "POST",
                "/api/ni/data-sources/vcenters",
                {
                    "fqdn": "vc-zeta.corp.example.com",
                    "proxy_id": PROXY_A,
                    "nickname": "vc-zeta",
                    "enabled": True,
                    "credentials": creds("Zeta-Pass-6"),
                    "ipfix_request": {"enable_all": True},
                },
            ),
            ("delete", "DELETE", "/api/ni/auth/token", None),
        ],
    )

    failures.equal(
        report,
        {
            "outcome": "onboarded",
            "created_count": 1,
            "blocked_count": 0,
            "failed_count": 0,
            "token_released": True,
            "datasources": [
                {
                    "nickname": "vc-zeta",
                    "host": "vc-zeta.corp.example.com",
                    "status": "created",
                    "precheck": {
                        "http_status": 200,
                        "code": 200,
                        "message": "Validation successful.",
                    },
                    "entity_id": "18230:902:993642895",
                }
            ],
        },
        "%s: wrong report" % name,
    )


# ---------------------------------------------------------------------------
# case 4 -- the mock serves only the operations the contract names
# ---------------------------------------------------------------------------


def case_contract_pinning(failures):
    name = "contract_pinning"
    contract_ids = [operation["operationId"] for operation in CONTRACT["operations"]]
    failures.equal(
        sorted(contract_ids),
        sorted(["create", "validateVCenter", "addVcenterDatasource", "delete"]),
        "%s: docs/contract.json must name exactly the four spec operationIds" % name,
    )

    config = ApplianceConfig(username="admin@vrni.com", password="Netw0rk!ns1ght")
    handle, log_path = tempfile.mkstemp(prefix="vcfon-pinning-", suffix=".jsonl")
    os.close(handle)
    probes = [
        ("GET", "/api/ni/data-sources/vcenters"),
        ("GET", "/api/ni/infra/nodes"),
        ("POST", "/api/ni/data-sources/vcenters/18230:902:993642895/enable"),
        ("PUT", "/api/ni/data-sources/vcenters/validate"),
    ]
    with MockAppliance(config, log_path) as mock:
        for method, path in probes:
            request = urllib.request.Request(mock.base_url + path, method=method)
            try:
                with urllib.request.urlopen(request, timeout=10.0) as response:
                    status = response.status
            except urllib.error.HTTPError as error:
                with error:
                    status = error.code
                    error.read()
            failures.equal(
                status,
                404,
                "%s: %s %s is not in the contract and must not be served" % (name, method, path),
            )
        log = mock.requests()

    failures.equal(
        [entry["operation_id"] for entry in log],
        [None] * len(probes),
        "%s: unrouted probes must be recorded with no operation_id" % name,
    )


# ---------------------------------------------------------------------------
# case 5 -- the body builders on their own, with no socket involved
# ---------------------------------------------------------------------------


def case_body_builders(failures):
    name = "body_builders"
    plan = load_plan(os.path.join(FIXTURES, "plan_mixed.json"))
    alpha, beta, gamma = plan.datasources

    failures.equal(
        user_credential_body(plan.credentials, plan.domain),
        {
            "username": "admin@vrni.com",
            "password": "Netw0rk!ns1ght",
            "domain": {"domain_type": "LDAP", "value": "corp.example.com"},
        },
        "%s: user_credential_body with an LDAP domain" % name,
    )
    failures.equal(
        user_credential_body(plan.credentials),
        {"username": "admin@vrni.com", "password": "Netw0rk!ns1ght"},
        "%s: user_credential_body must omit an unset domain entirely" % name,
    )

    failures.equal(
        validation_body(beta),
        {
            "fqdn": "vc-beta.corp.example.com",
            "proxy_id": PROXY_A,
            "credentials": creds("Beta-Pass-2"),
        },
        "%s: validation_body must carry only VCenterDataSourceValidationRequest members "
        "(vc-beta has notes, which that schema does not declare)" % name,
    )
    failures.equal(
        validation_body(gamma),
        {
            "fqdn": "vc-gamma.corp.example.com",
            "proxy_id": PROXY_B,
            "credentials": creds("Gamma-Pass-3"),
            "ipfix_enabled": True,
        },
        "%s: validation_body spells the IPFIX intent as the flat ipfix_enabled boolean" % name,
    )
    failures.equal(
        datasource_body(alpha),
        {
            "ip": "10.197.17.68",
            "proxy_id": PROXY_A,
            "nickname": "vc-alpha",
            "credentials": creds("Alpha-Pass-1"),
        },
        "%s: datasource_body for a minimal spec" % name,
    )
    failures.equal(
        datasource_body(gamma),
        {
            "fqdn": "vc-gamma.corp.example.com",
            "proxy_id": PROXY_B,
            "nickname": "vc-gamma",
            "notes": "Located in DC3",
            "enabled": False,
            "credentials": creds("Gamma-Pass-3"),
            "ipfix_request": {"enable_for_dvs": "dvs-54,dvs-67"},
        },
        "%s: datasource_body must keep an explicit enabled=false and omit the IPFIX keys "
        "the operator left unset" % name,
    )

    validation_members = set(operation("validateVCenter")["request"]["closed_property_set"])
    for spec in plan.datasources:
        extra = sorted(set(validation_body(spec)) - validation_members)
        failures.check(
            not extra,
            "%s: validation_body(%s) sent %s, which VCenterDataSourceValidationRequest "
            "does not declare" % (name, spec.nickname, ", ".join(extra)),
        )


def main():
    failures = Failures()
    for case in (
        case_body_builders,
        case_mixed,
        case_hard_reject,
        case_local_domain,
        case_contract_pinning,
    ):
        try:
            case(failures)
        except Exception as error:  # noqa: BLE001
            failures.append("%s crashed: %r" % (case.__name__, error))
    if failures:
        print("FAIL (%d)" % len(failures))
        for index, message in enumerate(failures, 1):
            print("  %2d. %s" % (index, message))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
