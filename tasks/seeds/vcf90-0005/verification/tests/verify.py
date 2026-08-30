#!/usr/bin/env python3
"""Protected verification for the VCF 9.0 host onboarding module.

Contract-pinned loopback instances are started on ephemeral 127.0.0.1 ports,
each with its own request log and runtime-only identifiers.  The module under
test is driven through caller-owned genuine SDK connections.  Reports and
documented exceptions are compared with what each instance actually did, and
the request logs are compared with the wire shape the contract requires.  No
live VMware endpoint is contacted.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from mock_server import CaseScript, ContractMockServer, read_log  # noqa: E402

CONTRACT_PATH = os.path.join(ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(ROOT, "docs", "official_sources.json")
MANIFEST_PATH = os.path.join(ROOT, "VcfHostOnboarding",
                             "VcfHostOnboarding.psd1")
MODULE_PATH = os.path.join(ROOT, "VcfHostOnboarding",
                           "VcfHostOnboarding.psm1")
INVOKE_PATH = os.path.join(HERE, "invoke_case.ps1")

EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_TAG = "9.0.0.0"
EXPECTED_OPERATIONS = ["createNetworkPool", "validateHostCommissionSpec",
                       "commissionHosts", "getTask"]
SDK_MODULE = "VMware.Sdk.Vcf.SddcManager"
SDK_USER_AGENT_PREFIX = "VCF_SDDC_Manager/"

STEP_ORDER = ["createNetworkPool", "validateHostCommissionSpec",
              "commissionHosts", "getTask"]
REPORT_PROPERTIES = ["status", "networkPoolId", "networkPoolName",
                     "validationId", "validationStatus", "taskId",
                     "taskStatus", "commissionedHostFqdns", "failedHostFqdns",
                     "errorCode", "errorMessage", "referenceToken", "steps"]

failures = []


def fail(message):
    failures.append(message)


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def suffix():
    return uuid.uuid4().hex[:8]


def normalized_status(value):
    return re.sub(r"\s+", "_", value.strip()).upper()


def build_plan(tag, site):
    return {
        "networkPool": {
            "name": "%s-m01-np-%s" % (site, tag),
            "networks": [
                {"type": "VMOTION", "vlanId": 1631, "mtu": 9000,
                 "subnet": "172.16.11.0", "mask": "255.255.255.0",
                 "gateway": "172.16.11.253",
                 "ipPools": [{"start": "172.16.11.101",
                              "end": "172.16.11.120"}]},
                {"type": "NFS", "vlanId": 1632, "mtu": 8900,
                 "subnet": "172.16.12.0", "mask": "255.255.255.0",
                 "gateway": "172.16.12.253"},
            ],
        },
        "hosts": [
            {"fqdn": "%s01-w01-esx01-%s.rainpole.io" % (site, tag),
             "username": "root", "password": "Esx!Onboard1-%s" % tag,
             "storageType": "VVOL", "vvolStorageProtocolType": "FC"},
            {"fqdn": "%s01-w01-esx02-%s.rainpole.io" % (site, tag),
             "username": "root", "password": "Esx!Onboard2-%s" % tag,
             "storageType": "VSAN_ESA",
             "sshThumbprint": "SHA256:%s" % uuid.uuid4().hex,
             "sslThumbprint": "SHA256:%s" % uuid.uuid4().hex},
        ],
    }


def build_cases():
    """Build runtime-only plans, identifiers and outcomes for every run."""
    tags = [suffix() for _ in range(8)]
    first_error = {
        "errorCode": "HOST_COMMISSION_VSAN_DISK_CLAIM_FAILED",
        "errorType": "SDDC_MANAGER",
        "message": "Host could not claim its vSAN ESA storage devices",
        "remediationMessage": "Clear the devices and retry the task",
        "referenceToken": uuid.uuid4().hex.upper(),
    }
    later_error = {
        "errorCode": "SECONDARY_DIAGNOSTIC",
        "errorType": "SDDC_MANAGER",
        "message": "This later error must not replace the first error",
        "referenceToken": uuid.uuid4().hex.upper(),
    }
    failed_plan = build_plan(tags[0], "sfo")
    failed_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()),
        validation_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        task_statuses=[" in progress ", "IN\tPROGRESS", " failed "],
        host_outcomes=[" successful ", " failed "],
        task_error=[first_error, later_error],
        task_name="Commissioning host(s) to VMware Cloud Foundation",
        network_pool_name="accepted-%s" % failed_plan["networkPool"]["name"],
        validation_execution_status=" completed ",
        validation_result_status=" succeeded ",
        reverse_subtasks=True,
        add_case_decoy_subtask=True,
    )
    case_failed = {
        "name": "commission-task-fails",
        "plan": failed_plan,
        "script": failed_script,
        "expected_result": "report",
        "expected_posts": 3,
        "expected_status": "FAILED",
        "expected_task_status": "FAILED",
        "expected_polls": 3,
    }

    success_plan = build_plan(tags[1], "lax")
    success_plan["networkPool"]["name"] += "-münchen"
    success_plan["hosts"][0]["password"] += "-påss"
    success_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()),
        validation_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        task_statuses=[" pending ", "successful"],
        host_outcomes=["successful", " SUCCESSFUL "],
        task_error=None,
        task_name="Commissioning host(s) to VMware Cloud Foundation",
        network_pool_name="accepted-%s" % success_plan["networkPool"]["name"],
        validation_execution_status="COMPLETED",
        validation_result_status="SUCCEEDED",
        reverse_subtasks=True,
    )
    case_success = {
        "name": "commission-task-succeeds",
        "plan": success_plan,
        "script": success_script,
        "expected_result": "report",
        "expected_posts": 3,
        "expected_status": "SUCCEEDED",
        "expected_task_status": "SUCCESSFUL",
        "expected_polls": 2,
    }

    validation_plan = build_plan(tags[2], "ord")
    validation_plan["networkPool"]["networks"] = \
        validation_plan["networkPool"]["networks"][:1]
    validation_plan["hosts"] = validation_plan["hosts"][:1]
    validation_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()),
        validation_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()),
        task_statuses=["SUCCESSFUL"], host_outcomes=[], task_error=None,
        task_name="Unused commission task",
        network_pool_name="accepted-%s" % validation_plan["networkPool"]["name"],
        validation_execution_status=" completed ",
        validation_result_status=" failed ",
    )
    case_validation = {
        "name": "validation-fails",
        "plan": validation_plan,
        "script": validation_script,
        "expected_result": "validation-failure-report",
        "expected_posts": 2,
        "expected_polls": 0,
    }

    execution_plan = build_plan(tags[7], "den")
    execution_plan["hosts"] = execution_plan["hosts"][:1]
    execution_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()),
        validation_id=str(uuid.uuid4()), task_id=str(uuid.uuid4()),
        task_statuses=["SUCCESSFUL"], host_outcomes=[], task_error=None,
        task_name="Unused commission task",
        network_pool_name="accepted-%s" % execution_plan["networkPool"]["name"],
        validation_execution_status=" in progress ",
        validation_result_status=" succeeded ",
    )
    case_execution = {
        "name": "validation-execution-is-not-completed",
        "plan": execution_plan,
        "script": execution_script,
        "expected_result": "validation-failure-report",
        "expected_posts": 2,
        "expected_polls": 0,
    }

    blank_pool_plan = build_plan(tags[6], "phx")
    blank_pool_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id="", validation_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()), task_statuses=["SUCCESSFUL"],
        host_outcomes=["SUCCESSFUL", "SUCCESSFUL"], task_error=None,
        task_name="Unused commission task",
        network_pool_name="accepted-%s" % blank_pool_plan["networkPool"]["name"],
    )
    case_blank_pool = {
        "name": "create-response-has-blank-pool-id",
        "plan": blank_pool_plan,
        "script": blank_pool_script,
        "expected_result": "error",
        "expected_posts": 1,
        "expected_polls": 0,
    }

    blank_task_plan = build_plan(tags[3], "dfw")
    blank_task_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()), validation_id=str(uuid.uuid4()),
        task_id="", task_statuses=["SUCCESSFUL"],
        host_outcomes=["SUCCESSFUL", "SUCCESSFUL"], task_error=None,
        task_name="Commissioning host(s) to VMware Cloud Foundation",
        network_pool_name="accepted-%s" % blank_task_plan["networkPool"]["name"],
    )
    case_blank_task = {
        "name": "commission-response-has-blank-task-id",
        "plan": blank_task_plan,
        "script": blank_task_script,
        "expected_result": "error",
        "expected_posts": 3,
        "expected_polls": 0,
    }

    unexpected_plan = build_plan(tags[4], "sea")
    unexpected_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()), validation_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()), task_statuses=[" cancelled "],
        host_outcomes=["FAILED", "FAILED"], task_error=None,
        task_name="Commissioning host(s) to VMware Cloud Foundation",
        network_pool_name="accepted-%s" % unexpected_plan["networkPool"]["name"],
    )
    case_unexpected = {
        "name": "commission-task-has-unexpected-terminal-status",
        "plan": unexpected_plan,
        "script": unexpected_script,
        "expected_result": "error",
        "expected_error_type": "System.InvalidOperationException",
        "expected_posts": 3,
        "expected_polls": 1,
    }

    timeout_plan = build_plan(tags[5], "iad")
    timeout_script = CaseScript(
        token="tok-%s" % uuid.uuid4().hex,
        network_pool_id=str(uuid.uuid4()), validation_id=str(uuid.uuid4()),
        task_id=str(uuid.uuid4()), task_statuses=[" in progress "],
        host_outcomes=["SUCCESSFUL", "SUCCESSFUL"], task_error=None,
        task_name="Commissioning host(s) to VMware Cloud Foundation",
        network_pool_name="accepted-%s" % timeout_plan["networkPool"]["name"],
    )
    case_timeout = {
        "name": "commission-task-times-out",
        "plan": timeout_plan,
        "script": timeout_script,
        "expected_result": "error",
        "expected_error_type": "System.TimeoutException",
        "expected_posts": 3,
        "expected_polls": 1,
        "poll_interval_seconds": 5,
        "timeout_seconds": 1,
    }
    return [case_failed, case_success, case_validation, case_execution,
            case_blank_pool, case_blank_task, case_unexpected, case_timeout]


def check_contract():
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        contract = json.load(handle)
    source = contract["source"]
    check(source["repositoryCommitSha"] == EXPECTED_SHA,
          "docs/contract.json is no longer pinned to commit %s" % EXPECTED_SHA)
    check(source["apiVersion"] == EXPECTED_TAG,
          "docs/contract.json is no longer the %s specification" % EXPECTED_TAG)
    check([op["operationId"] for op in contract["operations"]]
          == EXPECTED_OPERATIONS,
          "docs/contract.json no longer names exactly %s"
          % ", ".join(EXPECTED_OPERATIONS))
    network = contract["schemas"]["Network"]
    check(sorted(network["required"])
          == ["gateway", "mask", "mtu", "subnet", "type", "vlanId"],
          "docs/contract.json Network required members were changed")
    check("ipAddressVersion" not in network["properties"],
          "docs/contract.json Network gained a member this API version "
          "does not define")
    with open(SOURCES_PATH, encoding="utf-8") as handle:
        sources = json.load(handle)
    check(sources["repositoryCommitSha"] == EXPECTED_SHA,
          "docs/official_sources.json is no longer pinned to commit %s"
          % EXPECTED_SHA)
    check(sources["operationIds"] == EXPECTED_OPERATIONS,
          "docs/official_sources.json no longer records the four operationIds")
    return contract


def check_source_metadata(results):
    parse_errors = results.get("sourceParseErrors") or []
    check(not parse_errors,
          "the module has PowerShell parse errors: %s"
          % describe(parse_errors))
    commands = {name.casefold()
                for name in results.get("sourceCommands", [])}
    forbidden_commands = {
        "invoke-webrequest", "iwr", "invoke-restmethod", "irm",
        "start-process", "saps", "add-type", "curl", "curl.exe",
        "wget", "wget.exe", "python", "python3", "pwsh", "powershell",
        "powershell.exe",
    }
    used = sorted(commands & forbidden_commands)
    check(not used, "the module invokes prohibited bypass commands %s"
          % describe(used))
    forbidden_type_fragments = (
        "system.net.http", "net.http", "system.net.webclient",
        "net.webclient", "system.net.sockets", "net.sockets",
        "system.diagnostics.process", "diagnostics.process",
    )
    types = [name.casefold() for name in results.get("sourceTypes", [])]
    used_types = sorted({name for name in types if any(
        fragment in name for fragment in forbidden_type_fragments)})
    check(not used_types, "the module uses prohibited bypass types %s"
          % describe(used_types))
    vendor_commands = {
        "get-vcfsddcmanageroperation", "initialize-vcfippool",
        "initialize-vcfnetwork", "initialize-vcfnetworkpool",
        "initialize-vcfhostcommissionspec",
    }
    definitions = {name.casefold()
                   for name in results.get("sourceFunctions", [])}
    redefined = sorted(definitions & vendor_commands)
    check(not redefined, "the module redefines VMware commands %s"
          % describe(redefined))


def expected_pool_body(case):
    pool = case["plan"]["networkPool"]
    networks = []
    for network in pool["networks"]:
        entry = {key: network[key] for key in
                 ("type", "vlanId", "mtu", "subnet", "mask", "gateway")}
        if "ipPools" in network:
            entry["ipPools"] = [dict(pool_range)
                                for pool_range in network["ipPools"]]
        networks.append(entry)
    return {"name": pool["name"], "networks": networks}


def expected_specs(case):
    specs = []
    for host in case["plan"]["hosts"]:
        entry = {
            "fqdn": host["fqdn"],
            "username": host["username"],
            "password": host["password"],
            "storageType": host["storageType"],
            "networkPoolId": case["script"].network_pool_id,
        }
        for optional in ("vvolStorageProtocolType", "sshThumbprint",
                         "sslThumbprint"):
            if optional in host:
                entry[optional] = host[optional]
        specs.append(entry)
    return specs


def describe(value):
    return json.dumps(value, sort_keys=True)


def check_wire(case, entries):
    name = case["name"]
    script = case["script"]
    post_targets = [
        ("POST", "/v1/network-pools"),
        ("POST", "/v1/hosts/validations"),
        ("POST", "/v1/hosts"),
    ]
    expected_targets = post_targets[:case["expected_posts"]] + [
        ("GET", "/v1/tasks/%s" % script.task_id)
    ] * case["expected_polls"]
    observed = [(entry["method"], entry["target"]) for entry in entries]
    if observed != expected_targets:
        fail("%s: the request sequence was %s, expected %s"
             % (name, describe(observed), describe(expected_targets)))
        return
    for entry in entries:
        agents = entry["userAgent"]
        if len(agents) != 1 or not agents[0].startswith(SDK_USER_AGENT_PREFIX):
            fail("%s: %s %s was not issued by the generated %s bindings "
                 "(user agent %s)"
                 % (name, entry["method"], entry["target"], SDK_MODULE,
                    describe(agents)))
        if entry["authorization"] != ["Bearer %s" % script.token]:
            fail("%s: %s %s carried authorization %s"
                 % (name, entry["method"], entry["target"],
                    describe(entry["authorization"])))
        if entry["method"] == "POST":
            types = [value.split(";")[0].strip().lower()
                     for value in entry["contentType"]]
            if types != ["application/json"]:
                fail("%s: %s %s declared content type %s"
                     % (name, entry["method"], entry["target"],
                        describe(entry["contentType"])))
        else:
            if entry["body"] != "":
                fail("%s: %s %s carried a request body"
                     % (name, entry["method"], entry["target"]))

    pool_body = json.loads(entries[0]["body"])
    expected_pool = expected_pool_body(case)
    if pool_body != expected_pool:
        fail("%s: the createNetworkPool body was %s, expected %s"
             % (name, describe(pool_body), describe(expected_pool)))
    for index, network in enumerate(pool_body.get("networks", [])):
        for member in ("ipAddressVersion", "ipAddressAssignmentMode"):
            if member in network:
                fail("%s: networks[%d] sent %s, which this API version does "
                     "not define" % (name, index, member))

    expected = expected_specs(case)
    if case["expected_posts"] >= 2:
        validation_body = json.loads(entries[1]["body"])
        if validation_body != expected:
            fail("%s: the validateHostCommissionSpec body was %s, expected %s"
                 % (name, describe(validation_body), describe(expected)))
    commission_body = []
    if case["expected_posts"] >= 3:
        commission_body = json.loads(entries[2]["body"])
        if commission_body != expected:
            fail("%s: the commissionHosts body was %s, expected %s"
                 % (name, describe(commission_body), describe(expected)))
    for index, spec in enumerate(commission_body if
                                 isinstance(commission_body, list) else []):
        if not isinstance(spec, dict):
            continue
        for member in ("networkPoolName", "vvolStorageProtocolType",
                       "sshThumbprint", "sslThumbprint"):
            supplied = case["plan"]["hosts"][index].get(member) \
                if index < len(case["plan"]["hosts"]) else None
            if member in spec and supplied is None:
                fail("%s: host %d sent unset optional member %s"
                     % (name, index, member))


def normalize(value):
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value


def check_report(case, record):
    name = case["name"]
    script = case["script"]
    plan = case["plan"]
    if case["expected_result"] == "error":
        check(record["failed"],
              "%s: Invoke-VcfHostOnboarding did not throw" % name)
        expected_error_type = case.get("expected_error_type")
        if expected_error_type is not None:
            check(record["errorType"] == expected_error_type,
                  "%s: Invoke-VcfHostOnboarding threw %s, expected %s"
                  % (name, record["errorType"], expected_error_type))
        check(record["outputCount"] == 0,
              "%s: the failing invocation emitted %d partial objects"
              % (name, record["outputCount"]))
        check(record["serverIntact"],
              "%s: the caller-owned server connection was mutated" % name)
        return
    if record["failed"]:
        fail("%s: Invoke-VcfHostOnboarding threw %s: %s"
             % (name, record["errorType"], record["errorMessage"]))
        return
    if not check(record["outputCount"] == 1,
                 "%s: the function wrote %d objects to the pipeline, expected "
                 "exactly one report" % (name, record["outputCount"])):
        return
    if not check(record["serverIntact"],
                 "%s: the caller-owned server connection was mutated" % name):
        return
    report = record["report"]
    if not isinstance(report, dict):
        fail("%s: the report is not an object" % name)
        return
    check(record["reportType"] == "System.Management.Automation.PSCustomObject",
          "%s: the report type was %s, expected PSCustomObject"
          % (name, describe(record["reportType"])))
    order = record["propertyOrder"].split(",") if record["propertyOrder"] \
        else []
    check(order == REPORT_PROPERTIES,
          "%s: the report properties are %s, expected %s"
          % (name, describe(order), describe(REPORT_PROPERTIES)))

    validation_failure = case["expected_result"] == \
        "validation-failure-report"
    expectations = {
        "status": "FAILED" if validation_failure else case["expected_status"],
        "networkPoolId": script.network_pool_id,
        "networkPoolName": script.network_pool_name,
        "validationId": script.validation_id,
        "validationStatus": normalized_status(
            script.validation_result_status),
        "taskId": "" if validation_failure else script.task_id,
        "taskStatus": "" if validation_failure
                      else case["expected_task_status"],
    }
    for key, expected in expectations.items():
        observed = report.get(key)
        check(observed == expected,
              "%s: report.%s was %s, expected %s"
              % (name, key, describe(observed), describe(expected)))

    fqdns = [host["fqdn"] for host in plan["hosts"]]
    outcomes = [normalized_status(item) for item in script.host_outcomes]
    expected_ok = [fqdn for fqdn, outcome in zip(fqdns, outcomes)
                   if outcome == "SUCCESSFUL"] if not validation_failure else []
    expected_bad = [fqdn for fqdn, outcome in zip(fqdns, outcomes)
                    if outcome == "FAILED"] if not validation_failure else []
    check(normalize(report.get("commissionedHostFqdns")) == expected_ok,
          "%s: report.commissionedHostFqdns was %s, expected %s"
          % (name, describe(normalize(report.get("commissionedHostFqdns"))),
             describe(expected_ok)))
    check(normalize(report.get("failedHostFqdns")) == expected_bad,
          "%s: report.failedHostFqdns was %s, expected %s"
          % (name, describe(normalize(report.get("failedHostFqdns"))),
             describe(expected_bad)))

    error = {} if validation_failure else (script.task_error or {})
    for key, member in (("errorCode", "errorCode"),
                        ("errorMessage", "message"),
                        ("referenceToken", "referenceToken")):
        expected = error.get(member, "")
        observed = report.get(key)
        check(observed == expected,
              "%s: report.%s was %s, expected %s"
              % (name, key, describe(observed), describe(expected)))

    steps = normalize(report.get("steps"))
    if not isinstance(steps, list) or len(steps) != 4:
        fail("%s: report.steps is %s, expected four ordered steps"
             % (name, describe(steps)))
        return
    if validation_failure:
        expected_steps = [
            {"operationId": "createNetworkPool", "status": "SUCCEEDED",
             "detail": script.network_pool_id},
            {"operationId": "validateHostCommissionSpec", "status": "FAILED",
             "detail": script.validation_id},
            {"operationId": "commissionHosts", "status": "NOT_ATTEMPTED",
             "detail": ""},
            {"operationId": "getTask", "status": "NOT_ATTEMPTED",
             "detail": ""},
        ]
    else:
        expected_steps = [
            {"operationId": "createNetworkPool", "status": "SUCCEEDED",
             "detail": script.network_pool_id},
            {"operationId": "validateHostCommissionSpec",
             "status": "SUCCEEDED", "detail": script.validation_id},
            {"operationId": "commissionHosts", "status": "SUCCEEDED",
             "detail": script.task_id},
            {"operationId": "getTask",
             "status": "SUCCEEDED" if case["expected_status"] == "SUCCEEDED"
                       else "FAILED",
             "detail": case["expected_task_status"]},
        ]
    for index, (observed, expected) in enumerate(zip(steps, expected_steps)):
        if observed != expected:
            fail("%s: report.steps[%d] was %s, expected %s"
                 % (name, index, describe(observed), describe(expected)))
    check([step.get("operationId") for step in steps
           if isinstance(step, dict)] == STEP_ORDER,
          "%s: report.steps does not name the four operations in call order"
          % name)


def main():
    if not os.path.isfile(MODULE_PATH):
        print("FAIL: VcfHostOnboarding/VcfHostOnboarding.psm1 does not exist")
        return 1
    check_contract()

    cases = build_cases()
    workdir = tempfile.mkdtemp(prefix="vcf-onboarding-")
    servers = []
    payload = []
    for case in cases:
        log_path = os.path.join(workdir, "%s.jsonl" % case["name"])
        server = ContractMockServer(CONTRACT_PATH, case["script"], log_path)
        server.serve_in_background()
        servers.append(server)
        case["log_path"] = log_path
        plan_path = os.path.join(workdir, "%s-plan.json" % case["name"])
        with open(plan_path, "w", encoding="utf-8") as handle:
            json.dump(case["plan"], handle, indent=2, ensure_ascii=False)
        payload.append({
            "name": case["name"],
            "baseUri": server.base_uri,
            "accessToken": case["script"].token,
            "planPath": plan_path,
        })
        if "poll_interval_seconds" in case:
            payload[-1]["pollIntervalSeconds"] = \
                case["poll_interval_seconds"]
        if "timeout_seconds" in case:
            payload[-1]["timeoutSeconds"] = case["timeout_seconds"]

    empty_hosts_path = os.path.join(workdir, "empty-hosts-plan.json")
    empty_hosts_plan = build_plan(suffix(), "guard")
    empty_hosts_plan["hosts"] = []
    with open(empty_hosts_path, "w", encoding="utf-8") as handle:
        json.dump(empty_hosts_plan, handle)
    empty_networks_path = os.path.join(workdir, "empty-networks-plan.json")
    empty_networks_plan = build_plan(suffix(), "guard")
    empty_networks_plan["networkPool"]["networks"] = []
    with open(empty_networks_path, "w", encoding="utf-8") as handle:
        json.dump(empty_networks_plan, handle)
    missing_path = os.path.join(workdir, "does-not-exist.json")
    payload[0]["guardChecks"] = [
        {"name": "null-server", "nullServer": True,
         "planPath": payload[0]["planPath"]},
        {"name": "blank-plan-path", "planPath": "   "},
        {"name": "missing-plan-file", "planPath": missing_path},
        {"name": "empty-host-list", "planPath": empty_hosts_path},
        {"name": "empty-network-list", "planPath": empty_networks_path},
        {"name": "poll-interval-below-range", "planPath": payload[0]["planPath"],
         "pollIntervalSeconds": -1},
        {"name": "poll-interval-above-range", "planPath": payload[0]["planPath"],
         "pollIntervalSeconds": 61},
        {"name": "timeout-below-range", "planPath": payload[0]["planPath"],
         "timeoutSeconds": 0},
        {"name": "timeout-above-range", "planPath": payload[0]["planPath"],
         "timeoutSeconds": 901},
    ]

    cases_path = os.path.join(workdir, "cases.json")
    with open(cases_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    output_path = os.path.join(workdir, "results.json")

    command = ["pwsh", "-NoProfile", "-NonInteractive", "-File", INVOKE_PATH,
               "-ModuleManifest", MANIFEST_PATH,
               "-CasesPath", cases_path,
               "-OutputPath", output_path]
    completed = subprocess.run(command, capture_output=True, text=True,
                               timeout=600)
    for server in servers:
        server.shutdown()
        server.server_close()

    if not os.path.isfile(output_path):
        print("FAIL: the module could not be driven to completion")
        print("      exit code %d" % completed.returncode)
        tail = (completed.stderr or completed.stdout or "").strip()
        if tail:
            print("      " + tail.replace("\n", "\n      ")[:4000])
        return 1

    with open(output_path, encoding="utf-8") as handle:
        results = json.load(handle)

    exported = results.get("exportedFunctions") or []
    check(exported == ["Invoke-VcfHostOnboarding"],
          "the module exports %s, expected only Invoke-VcfHostOnboarding"
          % describe(exported))
    check_source_metadata(results)

    expected_guards = {
        "null-server", "blank-plan-path", "missing-plan-file",
        "empty-host-list", "empty-network-list",
        "poll-interval-below-range", "poll-interval-above-range",
        "timeout-below-range", "timeout-above-range",
    }
    guards = {record["name"]: record
              for record in results.get("guards", [])}
    check(set(guards) == set(expected_guards),
          "the pre-request guard results were %s, expected %s"
          % (describe(sorted(guards)), describe(sorted(expected_guards))))
    for guard_name in expected_guards:
        record = guards.get(guard_name)
        if record is None:
            continue
        check(record["failed"], "%s did not reject its invalid input"
              % guard_name)
        check(record["outputCount"] == 0,
              "%s emitted %d partial objects"
              % (guard_name, record["outputCount"]))

    records = {record["name"]: record for record in results.get("cases", [])}
    check(set(records) == {case["name"] for case in cases},
          "the run returned result names %s"
          % describe(sorted(records)))
    for case in cases:
        record = records.get(case["name"])
        if record is None:
            fail("%s: the run produced no result" % case["name"])
            continue
        check_report(case, record)
        check_wire(case, read_log(case["log_path"]))

    if failures:
        print("FAIL: %d check(s) failed" % len(failures))
        for message in failures:
            print("  - %s" % message)
        return 1
    print("PASS: onboarding handled %d contract-pinned runs and all input "
          "guards accurately" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
