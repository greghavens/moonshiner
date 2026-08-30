#!/usr/bin/env python3
"""Protected verification for the vcf_onboard package.

Starts the contract-pinned loopback mock on an ephemeral 127.0.0.1 port, runs the
package as a subprocess, then asserts the exact request wire shape from the mock's
request log and the accuracy of the produced report. Nothing outside this repository
is contacted.

Run:  python3 -B tests/verify.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mock_sddc_manager as mock  # noqa: E402

PLAN_PATH = os.path.join(REPO_ROOT, "fixtures", "onboarding_plan.json")
CONTRACT_PATH = os.path.join(REPO_ROOT, "docs", "contract.json")

SDDC_USERNAME = "administrator@vsphere.local"
SDDC_PASSWORD = "Vcf90-Sddc-Pa55phrase!"
WRONG_PASSWORD = "Vcf90-Not-The-Pa55phrase!"

VSAN_NETWORK_ID = mock.NETWORK_ID_PREFIX + "01"
VMOTION_NETWORK_ID = mock.NETWORK_ID_PREFIX + "02"
COMMISSIONED_RESOURCE_ID = mock.RESOURCE_ID_PREFIX + "01"

STEP_NAMES = ["authenticate", "create_network_pool", "add_ip_pool", "validate_hosts",
              "commission_hosts"]
STEP_OPERATIONS = ["createToken", "createNetworkPool", "addIpPoolToNetworkOfNetworkPool",
                   "validateHostCommissionSpec", "commissionHosts"]

EXPECTED_NETWORK_POOL_BODY = {
    "name": "np-rack-b01",
    "networks": [
        {"type": "VSAN", "vlanId": 2711, "mtu": 9000, "subnet": "172.27.11.0",
         "mask": "255.255.255.0", "gateway": "172.27.11.253"},
        {"type": "VMOTION", "vlanId": 2712, "mtu": 9000, "subnet": "172.27.12.0",
         "mask": "255.255.255.0", "gateway": "172.27.12.253",
         "ipPools": [{"start": "172.27.12.101", "end": "172.27.12.140"}]},
    ],
}

EXPECTED_HOST_BODY = [
    {"fqdn": "esxi-b01.rainpole.io", "username": "root", "password": "Esxi-b01-Pa55!",
     "storageType": "VSAN", "networkPoolId": mock.NETWORK_POOL_ID,
     "sslThumbprint": "3A:9B:11:C4:5E:70:82:D1:44:6F:AB:0C:29:53:E8:17:6D:B2:40:91"},
    {"fqdn": "esxi-b02.rainpole.io", "username": "root", "password": "Esxi-b02-Pa55!",
     "storageType": "VSAN", "networkPoolId": mock.NETWORK_POOL_ID},
    {"fqdn": "esxi-b03.rainpole.io", "username": "root", "password": "Esxi-b03-Pa55!",
     "storageType": "VVOL", "vvolStorageProtocolType": "FC",
     "networkPoolId": mock.NETWORK_POOL_ID,
     "sshThumbprint": "SHA256:5cE0oXqQ2rB7hVn1TzWJ9uKdM4pLbA6sYgFhX3RtQnU",
     "sslThumbprint": "7C:2D:65:0A:B8:19:F3:44:E1:90:5B:CC:38:71:2E:A6:04:DF:83:15"},
]

SECRETS = [SDDC_PASSWORD, WRONG_PASSWORD] + [h["password"] for h in EXPECTED_HOST_BODY]

FAILURES = []


def check(condition, label, detail=""):
    if condition:
        print("  ok   %s" % label)
    else:
        FAILURES.append(label if not detail else "%s -- %s" % (label, detail))
        print("  FAIL %s%s" % (label, ("\n         " + detail) if detail else ""))
    return bool(condition)


def check_equal(actual, expected, label):
    return check(actual == expected, label,
                 "expected %s\n         actual   %s" % (json.dumps(expected, sort_keys=True),
                                                        json.dumps(actual, sort_keys=True)))


def run_case(reject_credentials, password, report_name):
    workdir = tempfile.mkdtemp(prefix="vcf90-0013-")
    log_path = os.path.join(workdir, "requests.jsonl")
    report_path = os.path.join(workdir, report_name)
    open(log_path, "w", encoding="utf-8").close()
    httpd, base_url, _state = mock.start(log_path, reject_credentials=reject_credentials)
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["VCF_SDDC_USERNAME"] = SDDC_USERNAME
    env["VCF_SDDC_PASSWORD"] = password
    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "vcf_onboard",
             "--base-url", base_url,
             "--plan", PLAN_PATH,
             "--report", report_path,
             "--poll-interval", "0.01",
             "--poll-timeout", "20"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        proc = None
    finally:
        httpd.shutdown()
        httpd.server_close()
    report = None
    if os.path.exists(report_path):
        try:
            with open(report_path, encoding="utf-8") as fh:
                report = json.load(fh)
        except ValueError as exc:
            print("  report is not valid JSON: %s" % exc)
    return proc, report, mock.read_log(log_path), base_url


def run_cli(arguments, credentials=True):
    """Run the package without starting the appliance fixture (for preflight errors)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if credentials:
        env["VCF_SDDC_USERNAME"] = SDDC_USERNAME
        env["VCF_SDDC_PASSWORD"] = SDDC_PASSWORD
    else:
        env.pop("VCF_SDDC_USERNAME", None)
        env.pop("VCF_SDDC_PASSWORD", None)
    return subprocess.run(
        [sys.executable, "-B", "-m", "vcf_onboard"] + arguments,
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30)


def scan_for_empties(node, path, found):
    if isinstance(node, dict):
        for key, value in node.items():
            child = "%s.%s" % (path, key)
            if value is None or value == "" or value == [] or value == {}:
                found.append(child)
            else:
                scan_for_empties(value, child, found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = "%s[%d]" % (path, index)
            if value is None or value == "" or value == [] or value == {}:
                found.append(child)
            else:
                scan_for_empties(value, child, found)


def integers_are_real_integers(body, found):
    for index, network in enumerate(body.get("networks", []) if isinstance(body, dict) else []):
        for field in ("vlanId", "mtu"):
            value = network.get(field)
            if not isinstance(value, int) or isinstance(value, bool):
                found.append("networks[%d].%s is %r" % (index, field, value))


def header_of(entry, name):
    return entry["headers"].get(name)


def count_header(entry, name):
    return sum(1 for k, _ in entry["headerPairs"] if k == name)


def verify_main_case():
    print("\ncase 1: full onboarding run, commissioning fails at the last step")
    proc, report, log, base_url = run_case(False, SDDC_PASSWORD, "report.json")
    if proc is None:
        check(False, "the package finished inside the timeout")
        return
    if proc.returncode not in (0, 2):
        print("  stdout:\n%s\n  stderr:\n%s" % (proc.stdout[-4000:], proc.stderr[-4000:]))
    check_equal(proc.returncode, 2,
                "exit status is 2 because a step of the change failed")

    # ---- request sequence -------------------------------------------------
    expected_sequence = [
        ["POST", "/v1/tokens", "createToken", 201],
        ["POST", "/v1/network-pools", "createNetworkPool", 201],
        ["POST",
         "/v1/network-pools/%s/networks/%s/ip-pools" % (mock.NETWORK_POOL_ID, VSAN_NETWORK_ID),
         "addIpPoolToNetworkOfNetworkPool", 200],
        ["POST", "/v1/hosts/validations", "validateHostCommissionSpec", 202],
        ["POST", "/v1/hosts", "commissionHosts", 202],
        ["GET", "/v1/tasks/%s" % mock.TASK_ID, "getTask", 200],
        ["GET", "/v1/tasks/%s" % mock.TASK_ID, "getTask", 200],
    ]
    actual_sequence = [[e["method"], e["path"], e["operationId"], e["status"]] for e in log]
    if not check_equal(actual_sequence, expected_sequence,
                       "exact request sequence, paths and response statuses"):
        for entry in log:
            if entry["note"]:
                print("         rejected: %s %s -- %s"
                      % (entry["method"], entry["target"], entry["note"]))
        return
    check(all(e["operationId"] for e in log),
          "every request targets an operation the contract names")
    check(all(e["query"] is None for e in log),
          "no request carries a query string; none of these operations declares one")

    tokens, pool, ippool, validation, commission = log[0], log[1], log[2], log[3], log[4]
    polls = log[5:]

    # ---- headers ----------------------------------------------------------
    check(header_of(tokens, "authorization") is None,
          "createToken is sent unauthenticated")
    for entry in log[1:]:
        if not check_equal(header_of(entry, "authorization"),
                           "Bearer " + mock.ACCESS_TOKEN,
                           "request %d carries the bearer token from createToken"
                           % entry["seq"]):
            break
    check(all(count_header(e, "authorization") <= 1 for e in log),
          "the Authorization header is never sent twice")
    check(all("application/json" in (header_of(e, "accept") or "") for e in log),
          "every request asks for application/json")
    check(all((header_of(e, "content-type") or "").startswith("application/json")
              for e in log),
          "every request declares Content-Type application/json")
    check(all(not e["bodyText"] for e in polls),
          "getTask requests are bodyless")

    # ---- bodies -----------------------------------------------------------
    check_equal(tokens["bodyJson"], {"username": SDDC_USERNAME, "password": SDDC_PASSWORD},
                "createToken body holds only username and password; apiKey and idToken "
                "are omitted")
    check_equal(pool["bodyJson"], EXPECTED_NETWORK_POOL_BODY,
                "createNetworkPool body matches the plan exactly, with no server assigned "
                "id or hostsCount and no ipPools key on the network that has none")
    numeric = []
    integers_are_real_integers(pool["bodyJson"], numeric)
    check(not numeric, "vlanId and mtu are sent as JSON numbers", "; ".join(numeric))
    check_equal(ippool["bodyJson"], {"start": "172.27.11.101", "end": "172.27.11.140"},
                "addIpPoolToNetworkOfNetworkPool body holds only start and end")
    check_equal(validation["bodyJson"], EXPECTED_HOST_BODY,
                "validateHostCommissionSpec body omits every unset optional field "
                "(vvolStorageProtocolType, networkPoolName, sshThumbprint, sslThumbprint)")
    check_equal(commission["bodyJson"], EXPECTED_HOST_BODY,
                "commissionHosts sends the specifications that were validated, unchanged")
    check(all(host["networkPoolId"] == mock.NETWORK_POOL_ID
              for host in (commission["bodyJson"] or [])),
          "hosts reference the network pool id returned by createNetworkPool")

    empties = []
    for entry in log:
        if entry["bodyJson"] is not None:
            scan_for_empties(entry["bodyJson"], "request%d" % entry["seq"], empties)
    check(not empties,
          "no request body contains a null, empty string, empty array or empty object",
          "; ".join(empties))

    check(len(polls) == 2,
          "getTask is polled until the task reaches a terminal status",
          "polled %d time(s)" % len(polls))

    # ---- report -----------------------------------------------------------
    if not check(isinstance(report, dict), "the report file was written and parses as JSON"):
        return
    check_equal(report.get("schemaVersion"), "vcf-onboard/report/1",
                "report carries the required schema version")
    check_equal(report.get("planName"), "rack-b01-expansion",
                "report carries the plan name")
    check_equal(report.get("sddcManager"), base_url,
                "report records the exact appliance base URL")
    check_equal(report.get("overallStatus"), "FAILED", "report overallStatus is FAILED")
    steps = report.get("steps")
    if not check(isinstance(steps, list) and len(steps) == 5,
                 "the report lists all five steps", json.dumps(steps)[:400]):
        return
    check_equal([s.get("name") for s in steps], STEP_NAMES, "report step names and order")
    check_equal([s.get("operationId") for s in steps], STEP_OPERATIONS,
                "each report step names the operationId it used")
    check(all({"name", "operationId", "status", "details"}.issubset(s) for s in steps),
          "every report step has the required fields")
    check_equal([s.get("status") for s in steps],
                ["SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "FAILED"],
                "the four steps that landed are reported as SUCCEEDED and only "
                "commission_hosts as FAILED")

    pool_details = steps[1].get("details") or {}
    check_equal(pool_details.get("networkPoolId"), mock.NETWORK_POOL_ID,
                "report carries the network pool id returned by SDDC Manager")
    check_equal(pool_details.get("networkPoolName"), "np-rack-b01",
                "report carries the network pool name")
    check_equal(pool_details.get("networkIds"),
                {"VSAN": VSAN_NETWORK_ID, "VMOTION": VMOTION_NETWORK_ID},
                "report maps each network type to its server assigned id")
    ip_details = steps[2].get("details") or {}
    check_equal({k: ip_details.get(k) for k in ("networkPoolId", "networkId", "start", "end")},
                {"networkPoolId": mock.NETWORK_POOL_ID, "networkId": VSAN_NETWORK_ID,
                 "start": "172.27.11.101", "end": "172.27.11.140"},
                "report records the IP pool that was attached to the vSAN network")
    val_details = steps[3].get("details") or {}
    check_equal(val_details.get("validationId"), mock.VALIDATION_ID,
                "report carries the validation id")
    check_equal(val_details.get("executionStatus"), "COMPLETED",
                "report carries the validation execution status")
    check_equal(val_details.get("resultStatus"), "SUCCEEDED",
                "report carries the validation result status")
    warnings = val_details.get("warnings")
    check_equal(warnings,
                ["SSL thumbprint was not supplied for esxi-b02.rainpole.io; "
                 "commissioning will verify the host certificate against SDDC Manager "
                 "policy"],
                "report surfaces the validation warning description verbatim")
    commission_details = steps[4].get("details") or {}
    check_equal(commission_details.get("taskId"), mock.TASK_ID,
                "report carries the commissioning task id")
    check_equal(commission_details.get("taskStatus"), "FAILED",
                "report carries the terminal task status verbatim")

    hosts = report.get("hosts")
    check_equal([{k: h.get(k) for k in ("fqdn", "status")} for h in (hosts or [])],
                [{"fqdn": "esxi-b01.rainpole.io", "status": "SUCCESSFUL"},
                 {"fqdn": "esxi-b02.rainpole.io", "status": "FAILED"},
                 {"fqdn": "esxi-b03.rainpole.io", "status": "PENDING"}],
                "each host is reported with the status its sub-task actually reached")
    if isinstance(hosts, list) and len(hosts) == 3:
        check_equal(hosts[0].get("resourceId"), COMMISSIONED_RESOURCE_ID,
                    "the commissioned host carries the resource id SDDC Manager assigned")
        check_equal(hosts[1].get("errorCode"), "HOST_COMMISSION_SSL_THUMBPRINT_UNVERIFIED",
                    "the failed host carries the error code from its sub-task")
        check(bool((hosts[1].get("message") or "").strip()),
              "the failed host carries a message from its sub-task")
        check(not hosts[2].get("resourceId"),
              "the host that was never attempted carries no resource id")

    changes = report.get("completedChanges")
    check_equal(changes,
                [{"kind": "NETWORK_POOL", "id": mock.NETWORK_POOL_ID, "name": "np-rack-b01"},
                 {"kind": "IP_POOL", "networkPoolId": mock.NETWORK_POOL_ID,
                  "networkId": VSAN_NETWORK_ID, "start": "172.27.11.101",
                  "end": "172.27.11.140"},
                 {"kind": "HOST", "fqdn": "esxi-b01.rainpole.io",
                  "resourceId": COMMISSIONED_RESOURCE_ID}],
                "completedChanges lists exactly what survived on the appliance: the pool, "
                "the added IP pool and the one host that was commissioned")

    failure = report.get("failure") or {}
    check_equal(failure.get("step"), "commission_hosts", "failure names the step that failed")
    check_equal(failure.get("operationId"), "commissionHosts",
                "failure names the operation that failed")
    check_equal(failure.get("errorCode"), "HOST_COMMISSION_FAILED",
                "failure carries the task error code")
    check_equal(failure.get("taskId"), mock.TASK_ID, "failure carries the task id")
    check(bool((failure.get("message") or "").strip()), "failure carries a message")

    blob = json.dumps(report) + proc.stdout + proc.stderr
    leaked = [s for s in SECRETS if s in blob]
    check(not leaked, "no password appears in the report, stdout or stderr",
          "leaked %d secret(s)" % len(leaked))


def verify_auth_failure_case():
    print("\ncase 2: the first step fails, nothing later may be claimed")
    proc, report, log, base_url = run_case(True, WRONG_PASSWORD, "report-auth.json")
    if proc is None:
        check(False, "the package finished inside the timeout")
        return
    check_equal(proc.returncode, 2, "exit status is 2 when authentication fails")
    check_equal([[e["method"], e["path"], e["status"]] for e in log],
                [["POST", "/v1/tokens", 401]],
                "the run stops at the rejected createToken call and sends nothing else")
    if not check(isinstance(report, dict), "the report file was written and parses as JSON"):
        return
    check_equal(report.get("schemaVersion"), "vcf-onboard/report/1",
                "authentication failure report carries the required schema version")
    check_equal(report.get("planName"), "rack-b01-expansion",
                "authentication failure report carries the plan name")
    check_equal(report.get("sddcManager"), base_url,
                "authentication failure report records the exact appliance base URL")
    check_equal(report.get("overallStatus"), "FAILED", "report overallStatus is FAILED")
    steps = report.get("steps") or []
    check_equal([s.get("name") for s in steps], STEP_NAMES,
                "the report still lists all five steps")
    check_equal([s.get("operationId") for s in steps], STEP_OPERATIONS,
                "authentication failure report preserves all operationIds")
    check_equal([s.get("status") for s in steps],
                ["FAILED", "NOT_ATTEMPTED", "NOT_ATTEMPTED", "NOT_ATTEMPTED",
                 "NOT_ATTEMPTED"],
                "the four steps that never ran are reported as NOT_ATTEMPTED")
    check_equal(report.get("completedChanges"), [],
                "nothing is claimed as changed on the appliance")
    check_equal(report.get("hosts"), [], "no host outcome is claimed")
    failure = report.get("failure") or {}
    check_equal(failure.get("step"), "authenticate", "failure names the authenticate step")
    check_equal(failure.get("operationId"), "createToken",
                "authentication failure names the operation that failed")
    check_equal(failure.get("httpStatus"), 401, "failure carries the HTTP status")
    check_equal(failure.get("errorCode"), "UNAUTHORIZED",
                "failure carries the error code SDDC Manager returned")
    check(bool((failure.get("message") or "").strip()),
          "authentication failure carries a message")
    blob = json.dumps(report) + proc.stdout + proc.stderr
    check(not [s for s in SECRETS if s in blob],
          "no password appears in the report, stdout or stderr")


def verify_usage_error_case():
    print("\ncase 3: command-line usage errors use the documented exit status")
    proc = run_cli(["--plan", PLAN_PATH, "--report", "unused-report.json"])
    check_equal(proc.returncode, 1,
                "a missing required command-line argument exits 1, not argparse's default 2")


def verify_repository_shape():
    print("\nrepository preconditions")
    contract = mock.load_contract(CONTRACT_PATH)
    check_equal(contract["derivedFrom"]["commitSha"],
                "85151f6b1bb58f13b6ac0304bfec53904bea085f",
                "the contract is pinned to the 9.0.0.0 commit of vmware/vcf-api-specs")
    check_equal(contract["derivedFrom"]["infoVersion"], "9.0.0.0",
                "the contract was derived from the 9.0.0.0 specification revision")
    check_equal(sorted(contract["operations"]),
                sorted(["addIpPoolToNetworkOfNetworkPool", "commissionHosts", "createToken",
                        "createNetworkPool", "getTask", "validateHostCommissionSpec"]),
                "the contract names the six operationIds this task uses")
    check(os.path.exists(os.path.join(REPO_ROOT, "vcf_onboard", "__main__.py")),
          "vcf_onboard is runnable with python3 -m vcf_onboard")


def main():
    print("vcf90-0013 verification")
    verify_repository_shape()
    verify_main_case()
    verify_auth_failure_case()
    verify_usage_error_case()
    print("\n%d check(s) failed" % len(FAILURES) if FAILURES else "\nall checks passed")
    for failure in FAILURES:
        print("  - %s" % failure.splitlines()[0])
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
