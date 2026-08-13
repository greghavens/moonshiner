#!/usr/bin/env python3
"""Protected verification for the VCF 9.0 failure triage module.

Six contract-pinned loopback instances are started on ephemeral 127.0.0.1
ports, each with its own request log, its own failed task, its own event
stream and its own support bundle outcome.  The module under test is driven
once per instance through a caller-owned genuine SDK connection.  Every
report is compared with the diagnosis the fixture actually supports, and
every request log is compared with the wire shape the contract requires,
including the omission of every member that was not set.  No live VMware
endpoint is contacted.
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
MANIFEST_PATH = os.path.join(ROOT, "VcfFailureTriage",
                             "VcfFailureTriage.psd1")
MODULE_PATH = os.path.join(ROOT, "VcfFailureTriage", "VcfFailureTriage.psm1")
INVOKE_PATH = os.path.join(HERE, "invoke_case.ps1")

EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_TAG = "9.0.0.0"
EXPECTED_OPERATIONS = ["getTask", "getNotifications", "startSupportBundle",
                       "getSupportBundleStatus"]
SDK_MODULE = "VMware.Sdk.Vcf.SddcManager"
SDK_USER_AGENT_PREFIX = "VCF_SDDC_Manager/"
LATER_VERSION_LOG_MEMBERS = ["hcxLogs", "vmsLogs"]
NEVER_SENT_LOG_MEMBERS = ["wcpLogs", "systemDebugLogs", "vmScreenshots",
                          "vraLogs", "vropsLogs", "vrliLogs", "vrslcmLogs",
                          "automationLogs", "operationsLogs",
                          "operationsForLogs", "lifecycleLogs"]

REPORT_PROPERTIES = ["status", "taskId", "taskStatus", "failedSubTaskNames",
                     "affectedResourceIds", "domainName", "clusterNames",
                     "includeFreeHosts", "correlatedEventCount",
                     "correlatedEventMessageIds", "requestedLogs",
                     "supportBundleId", "supportBundleStatus", "bundleName",
                     "errorCode", "errorMessage", "referenceToken", "steps"]

RAW_HTTP_PATTERNS = [
    r"Invoke-WebRequest", r"Invoke-RestMethod", r"System\.Net\.Http",
    r"HttpClient", r"WebClient", r"HttpWebRequest", r"Start-Process",
    r"\bcurl\b", r"\bwget\b", r"Net\.Sockets", r"TcpClient",
]

failures = []


def fail(message):
    failures.append(message)


def check(condition, message):
    if not condition:
        fail(message)
    return condition


def describe(value):
    return json.dumps(value, sort_keys=True)


def suffix():
    return uuid.uuid4().hex[:8]


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_status(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "_", value.strip()).upper()


# -- fixtures --------------------------------------------------------------
def resource(kind, identifier, fqdn=None, name=None):
    entry = {"resourceId": identifier, "type": kind}
    if fqdn:
        entry["fqdn"] = fqdn
    if name:
        entry["name"] = name
    return entry


def sub_task(name, status, resources, children=None, timestamp=None):
    entry = {
        "name": name,
        "type": "SUBTASK",
        "description": name,
        "status": status,
        "creationTimestamp": timestamp or "2026-04-14T09:20:00.000Z",
        "resources": resources,
    }
    if children:
        entry["subTasks"] = children
    return entry


def notification(message_id, timestamp, resources, domain=None,
                 severity="ERROR"):
    entry = {
        "type": "ALERT",
        "severity": severity,
        "message": {
            "id": message_id,
            "localizedMessage": "%s was raised for the failing operation"
                                % message_id,
        },
        "resources": resources,
    }
    if timestamp is not None:
        entry["creationTimestamp"] = timestamp
    if domain is not None:
        entry["domain"] = domain
    return entry


def notifiable(identifier, kind, name):
    return {"id": identifier, "type": kind, "name": name}


def build_case_a():
    tag = suffix()
    domain = {"id": "domain-%s" % uuid.uuid4(), "name": "sfo-w01-%s" % tag}
    vc_id = "vcenter-%s" % uuid.uuid4()
    vc_fqdn = "sfo-w01-vc01-%s.rainpole.io" % tag
    esx_id = "esxi-%s" % uuid.uuid4()
    esx_fqdn = "sfo01-w01-esx07-%s.rainpole.io" % tag
    cluster_one = ("cluster-%s" % uuid.uuid4(), "sfo-w01-cl03-%s" % tag)
    cluster_two = ("cluster-%s" % uuid.uuid4(), "sfo-w01-cl04-%s" % tag)
    other_domain = {"id": "domain-%s" % uuid.uuid4(), "name": "lax-w01-%s"
                    % tag}
    task = {
        "id": str(uuid.uuid4()),
        "name": "Adding host to vSphere cluster",
        "type": "CLUSTER_EXPANSION",
        "status": "FAILED",
        "creationTimestamp": "2026-04-14T09:12:03.118Z",
        "completionTimestamp": "2026-04-14T10:02:47.926Z",
        "subTasks": [
            sub_task("Validate host %s" % esx_fqdn, "SUCCESSFUL",
                     [resource("ESXI", esx_id, esx_fqdn, esx_fqdn)]),
            # The failing branch: its own resource is the vCenter, and the
            # host that actually could not be attached is a child sub-task.
            sub_task("Configure vSphere cluster %s" % cluster_one[1],
                     "Failed",
                     [resource("VCENTER", vc_id, vc_fqdn, vc_fqdn)],
                     children=[
                         sub_task("Attach host %s to cluster" % esx_fqdn,
                                  "FAILED",
                                  [resource("ESXI", esx_id, esx_fqdn,
                                            esx_fqdn)]),
                         sub_task("Enable vSAN on cluster", "SKIPPED",
                                  [resource("VCENTER", vc_id, vc_fqdn,
                                            vc_fqdn)]),
                     ]),
            sub_task("Refresh domain inventory", "SKIPPED",
                     [resource("VCENTER", vc_id, vc_fqdn, vc_fqdn)]),
        ],
        "errors": [{
            "errorCode": "VCF_CLUSTER_HOST_ATTACH_FAILED",
            "errorType": "SDDC_MANAGER",
            "message": "Host %s could not be attached to cluster %s"
                       % (esx_fqdn, cluster_one[1]),
            "remediationMessage": "Collect the logs and retry the task",
            "referenceToken": uuid.uuid4().hex.upper(),
        }],
        "resources": [
            resource("ESXI", esx_id, esx_fqdn, esx_fqdn),
            resource("VCENTER", vc_id, vc_fqdn, vc_fqdn),
        ],
        "resolutionStatus": "UNRESOLVED",
        "isCancellable": False,
        "isRetryable": True,
    }
    notifications = [
        notification("vcf.event.cluster.host.attach.failed",
                     "2026-04-14T09:47:15.004Z",
                     [notifiable(vc_id, "VCENTER", vc_fqdn),
                      notifiable(cluster_one[0], "CLUSTER", cluster_one[1])],
                     domain),
        # The task window is inclusive at both ends.
        notification("vcf.event.task.window.opened",
                     task["creationTimestamp"],
                     [notifiable(vc_id, "VCENTER", vc_fqdn)], domain),
        # Raised before the task started.
        notification("vcf.event.cluster.reconfigure.scheduled",
                     "2026-04-13T22:05:00.000Z",
                     [notifiable(vc_id, "VCENTER", vc_fqdn)], domain),
        # Same identifier in a different case: not the same resource.
        notification("vcf.event.vcenter.certificate.renewed",
                     "2026-04-14T09:50:00.000Z",
                     [notifiable(vc_id.upper(), "VCENTER", vc_fqdn)],
                     other_domain),
        notification("vcf.event.domain.cluster.degraded",
                     "2026-04-14T09:58:41.612Z",
                     [notifiable(cluster_one[0], "CLUSTER", cluster_one[1]),
                      notifiable(vc_id, "VCENTER", vc_fqdn),
                      notifiable(cluster_two[0], "CLUSTER", cluster_two[1]),
                      # Resource-name comparisons and de-duplication are
                      # ordinal, so this is a distinct cluster name.
                      notifiable("cluster-%s" % uuid.uuid4(), "CLUSTER",
                                 cluster_two[1].upper())],
                     domain),
        notification("vcf.event.task.window.closed",
                     task["completionTimestamp"],
                     [notifiable(vc_id, "VCENTER", vc_fqdn)], domain),
        # Inside the window, but about resources the task never touched.
        notification("vcf.event.host.storage.degraded",
                     "2026-04-14T09:55:00.000Z",
                     [notifiable("esxi-%s" % uuid.uuid4(), "ESXI",
                                 "lax01-w01-esx01-%s.rainpole.io" % tag),
                      notifiable("cluster-%s" % uuid.uuid4(), "CLUSTER",
                                 "lax-w01-cl01-%s" % tag)],
                     other_domain),
        # Raised after the task completed.
        notification("vcf.event.cluster.configuration.restored",
                     "2026-04-14T10:30:00.000Z",
                     [notifiable(vc_id, "VCENTER", vc_fqdn)], domain),
        # No creation timestamp at all: it cannot be placed in the window.
        notification("vcf.event.inventory.sync.pending", None,
                     [notifiable(vc_id, "VCENTER", vc_fqdn)], domain),
    ]
    return {
        "name": "cluster-expansion-task-failed",
        "mode": "normal",
        "include_health_check": False,
        "force_collection": True,
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
        "script": CaseScript(
            token="tok-%s" % uuid.uuid4().hex,
            task=task,
            notifications=notifications,
            bundle_id="sb-%s" % uuid.uuid4(),
            bundle_statuses=["PENDING", "IN_PROGRESS",
                             "COMPLETED_WITH_FAILURE"],
            bundle_name=None,
            bundle_description="Support bundle collection for %s"
                               % domain["name"],
        ),
        "expected": {
            "outcome": "report",
            "status": "FAILED",
            "domain_name": domain["name"],
            "cluster_names": [cluster_one[1], cluster_two[1],
                              cluster_two[1].upper()],
            "include_free_hosts": True,
            "correlated_message_ids": [
                "vcf.event.cluster.host.attach.failed",
                "vcf.event.task.window.opened",
                "vcf.event.domain.cluster.degraded",
                "vcf.event.task.window.closed",
            ],
            "failed_sub_task_names": [
                "Configure vSphere cluster %s" % cluster_one[1],
                "Attach host %s to cluster" % esx_fqdn,
            ],
            "affected_resource_ids": [vc_id, esx_id],
            "requested_logs": ["vcLogs", "esxLogs", "sddcManagerLogs",
                               "apiLogs"],
            "bundle_name": "",
            "polls": 3,
        },
    }


def build_case_b():
    tag = suffix()
    domain = {"id": "domain-%s" % uuid.uuid4(), "name": "lax-w01-%s" % tag}
    nsx_id = "nsx-%s" % uuid.uuid4()
    nsx_fqdn = "lax-w01-nsx01-%s.rainpole.io" % tag
    case_variant_id = nsx_id.upper()
    lower_type_id = "esxi-%s" % uuid.uuid4()
    task = {
        "id": str(uuid.uuid4()),
        "name": "Preparing NSX transport nodes",
        "type": "NSXT_TRANSPORT_NODE_PREPARATION",
        "status": " failed ",
        "creationTimestamp": "2026-05-02T14:03:19.441Z",
        "completionTimestamp": "2026-05-02T14:26:58.310Z",
        "subTasks": [
            sub_task("Prepare transport node collection", "FAILED",
                     [resource("NSXT_MANAGER", nsx_id, nsx_fqdn, nsx_fqdn),
                      # Identifiers that differ only by case remain distinct,
                      # and resource types are compared case-sensitively.
                      resource("VCENTER", case_variant_id,
                               "case-variant-%s.rainpole.io" % tag),
                      resource("esxi", lower_type_id,
                               "lower-type-%s.rainpole.io" % tag)],
                     timestamp="2026-05-02T14:05:00.000Z"),
            sub_task("Validate NSX inventory", "SUCCESSFUL",
                     [resource("NSXT_MANAGER", nsx_id, nsx_fqdn, nsx_fqdn)],
                     timestamp="2026-05-02T14:04:00.000Z"),
        ],
        "resources": [resource("NSXT_MANAGER", nsx_id, nsx_fqdn, nsx_fqdn)],
        "resolutionStatus": "UNRESOLVED",
        "isRetryable": True,
    }
    notifications = [
        # Correlated, and it names no cluster at all.
        notification("vcf.event.nsx.transport.node.profile.failed",
                     "2026-05-02T14:19:33.870Z",
                     [notifiable(nsx_id, "NSXT_MANAGER", nsx_fqdn)], domain),
        # Names clusters, but no resource of the failing task.
        notification("vcf.event.cluster.capacity.warning",
                     "2026-05-02T14:20:00.000Z",
                     [notifiable("cluster-%s" % uuid.uuid4(), "CLUSTER",
                                 "sfo-m01-cl01-%s" % tag)],
                     {"id": "domain-%s" % uuid.uuid4(),
                      "name": "sfo-m01-%s" % tag}),
    ]
    return {
        "name": "nsx-transport-node-task-failed",
        "mode": "normal",
        "include_health_check": True,
        "force_collection": True,
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
        "script": CaseScript(
            token="tok-%s" % uuid.uuid4().hex,
            task=task,
            notifications=notifications,
            bundle_id="sb-%s" % uuid.uuid4(),
            bundle_statuses=[" pending ", " completed with success "],
            bundle_name="vcf-sos-%s.tar.gz" % tag,
        ),
        "expected": {
            "outcome": "report",
            "status": "SUCCEEDED",
            "domain_name": domain["name"],
            "cluster_names": [],
            "include_free_hosts": False,
            "correlated_message_ids": [
                "vcf.event.nsx.transport.node.profile.failed",
            ],
            "failed_sub_task_names": ["Prepare transport node collection"],
            "affected_resource_ids": [nsx_id, case_variant_id,
                                      lower_type_id],
            "requested_logs": ["vcLogs", "nsxLogs", "sddcManagerLogs",
                               "apiLogs"],
            "bundle_name": "vcf-sos-%s.tar.gz" % tag,
            "polls": 2,
        },
    }


def running_task(tag):
    esx_id = "esxi-%s" % uuid.uuid4()
    esx_fqdn = "sfo01-m01-esx04-%s.rainpole.io" % tag
    return {
        "id": str(uuid.uuid4()),
        "name": "Commissioning host(s)",
        "type": "HOST_COMMISSION",
        "status": "In Progress",
        "creationTimestamp": "2026-06-01T11:00:00.000Z",
        "subTasks": [
            sub_task("Commission host %s" % esx_fqdn, "IN_PROGRESS",
                     [resource("ESXI", esx_id, esx_fqdn, esx_fqdn)],
                     timestamp="2026-06-01T11:00:30.000Z"),
        ],
        "resources": [resource("ESXI", esx_id, esx_fqdn, esx_fqdn)],
    }


def failed_host_task(tag):
    domain = {"id": "domain-%s" % uuid.uuid4(), "name": "sfo-m01-%s" % tag}
    esx_id = "esxi-%s" % uuid.uuid4()
    esx_fqdn = "sfo01-m01-esx09-%s.rainpole.io" % tag
    task = {
        "id": str(uuid.uuid4()),
        "name": "Commissioning host(s)",
        "type": "HOST_COMMISSION",
        "status": "FAILED",
        "creationTimestamp": "2026-06-01T11:00:00.000Z",
        "completionTimestamp": "2026-06-01T11:41:12.005Z",
        "subTasks": [
            sub_task("Commission host %s" % esx_fqdn, "FAILED",
                     [resource("ESXI", esx_id, esx_fqdn, esx_fqdn)],
                     timestamp="2026-06-01T11:00:30.000Z"),
        ],
        "errors": [{
            "errorCode": "HOST_COMMISSION_FAILED",
            "errorType": "SDDC_MANAGER",
            "message": "Host %s could not be commissioned" % esx_fqdn,
            "referenceToken": uuid.uuid4().hex.upper(),
        }],
        "resources": [resource("ESXI", esx_id, esx_fqdn, esx_fqdn)],
    }
    notifications = [
        notification("vcf.event.host.commission.failed",
                     "2026-06-01T11:39:00.000Z",
                     [notifiable(esx_id, "ESXI", esx_fqdn)], domain),
    ]
    return task, notifications, domain["name"]


def build_case_c():
    tag = suffix()
    return {
        "name": "task-still-running",
        "mode": "normal",
        "include_health_check": False,
        "force_collection": False,
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
        "script": CaseScript(
            token="tok-%s" % uuid.uuid4().hex,
            task=running_task(tag),
            notifications=[],
            bundle_id="sb-%s" % uuid.uuid4(),
            bundle_statuses=["PENDING"],
        ),
        "expected": {
            "outcome": "throws",
            "error_type": "System.InvalidOperationException",
            "requests": "task-only",
        },
    }


def build_case_d():
    tag = suffix()
    task, notifications, domain_name = failed_host_task(tag)
    return {
        "name": "support-bundle-never-finishes",
        "mode": "normal",
        "include_health_check": False,
        "force_collection": False,
        # The wait must be capped by the one-second timeout rather than
        # sleeping for the full ten-second poll interval.
        "poll_interval_seconds": 10,
        "timeout_seconds": 1,
        "script": CaseScript(
            token="tok-%s" % uuid.uuid4().hex,
            task=task,
            notifications=notifications,
            bundle_id="sb-%s" % uuid.uuid4(),
            bundle_statuses=["IN_PROGRESS"],
        ),
        "expected": {
            "outcome": "throws",
            "error_type": "System.TimeoutException",
            "requests": "polled-out",
            "max_elapsed_milliseconds": 5000,
            "domain_name": domain_name,
            "cluster_names": [],
            "include_free_hosts": False,
            "requested_logs": ["esxLogs", "sddcManagerLogs", "apiLogs"],
        },
    }


def build_rejected_case(name, mode):
    tag = suffix()
    task, notifications, _domain_name = failed_host_task(tag)
    return {
        "name": name,
        "mode": mode,
        "include_health_check": False,
        "force_collection": False,
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
        "script": CaseScript(
            token="tok-%s" % uuid.uuid4().hex,
            task=task,
            notifications=notifications,
            bundle_id="sb-%s" % uuid.uuid4(),
            bundle_statuses=["COMPLETED_WITH_SUCCESS"],
        ),
        "expected": {"outcome": "throws", "requests": "none"},
    }


def build_cases():
    return [
        build_case_a(),
        build_case_b(),
        build_case_c(),
        build_case_d(),
        build_rejected_case("null-server-rejected", "nullServer"),
        build_rejected_case("blank-task-id-rejected", "blankTaskId"),
    ]


# -- the diagnosis each fixture supports -----------------------------------
def expected_body(case):
    expected = case["expected"]
    domains = {"domainName": expected["domain_name"]}
    if expected["cluster_names"]:
        domains["clusterNames"] = list(expected["cluster_names"])
    scope = {}
    if expected["include_free_hosts"]:
        scope["includeFreeHosts"] = True
    scope["domains"] = [domains]
    body = {"scope": scope,
            "logs": {member: True for member in expected["requested_logs"]}}
    options = {}
    if case["force_collection"]:
        options["config"] = {"force": True}
    if case["include_health_check"]:
        options["include"] = {"healthCheck": True}
    if options:
        body["options"] = options
    return body


def expected_targets(case):
    script = case["script"]
    expected = case["expected"]
    task_target = ("GET", "/v1/tasks/%s" % script.task["id"])
    if expected.get("requests") == "none":
        return []
    if expected.get("requests") == "task-only":
        return [task_target]
    prefix = [task_target, ("GET", "/v1/notifications"),
              ("POST", "/v1/system/support-bundles")]
    if expected.get("requests") == "polled-out":
        return None
    return prefix + [("GET", "/v1/system/support-bundles/%s"
                      % script.bundle_id)] * expected["polls"]


# -- checks ----------------------------------------------------------------
def check_contract():
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        contract = json.load(handle)
    source = contract["source"]
    check(source["repositoryCommitSha"] == EXPECTED_SHA,
          "docs/contract.json is no longer pinned to commit %s" % EXPECTED_SHA)
    check(source["apiVersion"] == EXPECTED_TAG,
          "docs/contract.json is no longer the %s specification"
          % EXPECTED_TAG)
    check(source["specPath"]
          == "specifications/sddc-manager/sddc-manager-openapi.json",
          "docs/contract.json no longer names the SDDC Manager specification "
          "file")
    check([op["operationId"] for op in contract["operations"]]
          == EXPECTED_OPERATIONS,
          "docs/contract.json no longer names exactly %s"
          % ", ".join(EXPECTED_OPERATIONS))
    logs = contract["schemas"]["Logs"]["properties"]
    for member in LATER_VERSION_LOG_MEMBERS:
        check(member not in logs,
              "docs/contract.json Logs gained %s, which this API version "
              "does not define" % member)
    check(len(logs) == 16,
          "docs/contract.json Logs no longer carries the sixteen members of "
          "this API version")
    check(contract["logMemberOrder"] == list(logs),
          "docs/contract.json logMemberOrder no longer follows the Logs "
          "schema")
    with open(SOURCES_PATH, encoding="utf-8") as handle:
        sources = json.load(handle)
    check(sources["repositoryCommitSha"] == EXPECTED_SHA,
          "docs/official_sources.json is no longer pinned to commit %s"
          % EXPECTED_SHA)
    check(sources["repositoryTag"] == EXPECTED_TAG,
          "docs/official_sources.json is no longer pinned to tag %s"
          % EXPECTED_TAG)
    check(sources["operationIds"] == EXPECTED_OPERATIONS,
          "docs/official_sources.json no longer records the four operationIds")


def check_source():
    with open(MODULE_PATH, encoding="utf-8") as handle:
        text = handle.read()
    for pattern in RAW_HTTP_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            fail("the module reaches the API with %s instead of the generated "
                 "%s bindings" % (pattern.strip("\\b"), SDK_MODULE))
    check(re.search(r"VMware\.Sdk\.Vcf\.SddcManager", text) is not None,
          "the module never imports the generated %s bindings" % SDK_MODULE)


def check_headers(case, entries):
    name = case["name"]
    token = case["script"].token
    for entry in entries:
        agents = entry["userAgent"]
        if len(agents) != 1 or not agents[0].startswith(SDK_USER_AGENT_PREFIX):
            fail("%s: %s %s was not issued by the generated %s bindings "
                 "(user agent %s)"
                 % (name, entry["method"], entry["target"], SDK_MODULE,
                    describe(agents)))
        if entry["authorization"] != ["Bearer %s" % token]:
            fail("%s: %s %s carried authorization %s"
                 % (name, entry["method"], entry["target"],
                    describe(entry["authorization"])))
        accepts = [value.split(";")[0].strip().lower()
                   for value in entry["accept"]]
        if "application/json" not in accepts:
            fail("%s: %s %s did not accept application/json (%s)"
                 % (name, entry["method"], entry["target"],
                    describe(entry["accept"])))
        if entry["method"] == "POST":
            types = [value.split(";")[0].strip().lower()
                     for value in entry["contentType"]]
            if types != ["application/json"]:
                fail("%s: %s %s declared content type %s"
                     % (name, entry["method"], entry["target"],
                        describe(entry["contentType"])))
        elif entry["body"] != "":
            fail("%s: %s %s carried a request body"
                 % (name, entry["method"], entry["target"]))
        if entry["query"]:
            fail("%s: %s carried the query string %s; no operation of this "
                 "contract takes one"
                 % (name, entry["target"], describe(entry["query"])))


def check_body(case, entry):
    name = case["name"]
    try:
        body = json.loads(entry["body"])
    except ValueError:
        fail("%s: the startSupportBundle body was not JSON" % name)
        return
    expected = expected_body(case)
    if body != expected:
        fail("%s: the startSupportBundle body was %s, expected %s"
             % (name, describe(body), describe(expected)))
    logs = body.get("logs") if isinstance(body, dict) else {}
    logs = logs if isinstance(logs, dict) else {}
    for member in LATER_VERSION_LOG_MEMBERS:
        if member in logs:
            fail("%s: logs sent %s, which the %s specification does not "
                 "define" % (name, member, EXPECTED_TAG))
    for member in NEVER_SENT_LOG_MEMBERS:
        if member in logs:
            fail("%s: logs sent %s, which nothing in the diagnosis asked for"
                 % (name, member))
    for member, value in logs.items():
        if value is not True:
            fail("%s: logs.%s was sent as %s; an unset member is omitted"
                 % (name, member, describe(value)))
    scope = body.get("scope") if isinstance(body, dict) else {}
    scope = scope if isinstance(scope, dict) else {}
    if not case["expected"]["include_free_hosts"] \
            and "includeFreeHosts" in scope:
        fail("%s: scope sent includeFreeHosts although no affected host is "
             "outside the correlated events" % name)
    domains = as_list(scope.get("domains"))
    for index, entry_domain in enumerate(domains):
        if not isinstance(entry_domain, dict):
            continue
        if not case["expected"]["cluster_names"] \
                and "clusterNames" in entry_domain:
            fail("%s: domains[%d] sent clusterNames although the correlated "
                 "events name no cluster" % (name, index))
    options = body.get("options") if isinstance(body, dict) else None
    if not case["force_collection"] and not case["include_health_check"]:
        if options is not None:
            fail("%s: the body sent options although neither option was "
                 "asked for" % name)
    elif isinstance(options, dict):
        if not case["force_collection"] and "config" in options:
            fail("%s: options sent config although the collection was not "
                 "forced" % name)
        if not case["include_health_check"] and "include" in options:
            fail("%s: options sent include although no health check was "
                 "asked for" % name)


def check_wire(case, entries):
    name = case["name"]
    script = case["script"]
    expected = case["expected"]
    observed = [(entry["method"], entry["target"]) for entry in entries]
    targets = expected_targets(case)
    if targets is not None:
        if observed != targets:
            fail("%s: the request sequence was %s, expected %s"
                 % (name, describe(observed), describe(targets)))
            return
    else:
        prefix = [("GET", "/v1/tasks/%s" % script.task["id"]),
                  ("GET", "/v1/notifications"),
                  ("POST", "/v1/system/support-bundles")]
        poll = ("GET", "/v1/system/support-bundles/%s" % script.bundle_id)
        if observed[:3] != prefix:
            fail("%s: the request sequence started with %s, expected %s"
                 % (name, describe(observed[:3]), describe(prefix)))
            return
        if len(observed) < 4:
            fail("%s: the support bundle was never polled for its outcome"
                 % name)
            return
        if any(entry != poll for entry in observed[3:]):
            fail("%s: the run polled %s, expected only %s"
                 % (name, describe(observed[3:]), describe(poll)))
            return
    check_headers(case, entries)
    for entry in entries:
        if entry["method"] == "POST":
            check_body(case, entry)


def check_report(case, record):
    name = case["name"]
    script = case["script"]
    expected = case["expected"]
    if not check(record["serverIntact"],
                 "%s: the caller-owned server connection was mutated" % name):
        return
    if expected["outcome"] == "throws":
        if not check(record["failed"],
                     "%s: the run was expected to be refused but returned"
                     % name):
            return
        wanted = expected.get("error_type")
        if wanted:
            check(record["errorType"] == wanted,
                  "%s: the run threw %s, expected %s"
                  % (name, record["errorType"] or "nothing", wanted))
        max_elapsed = expected.get("max_elapsed_milliseconds")
        if max_elapsed is not None:
            check(record["elapsedMilliseconds"] < max_elapsed,
                  "%s: timeout took %d ms, expected less than %d ms"
                  % (name, record["elapsedMilliseconds"], max_elapsed))
        return
    if record["failed"]:
        fail("%s: Invoke-VcfFailureTriage threw %s: %s"
             % (name, record["errorType"], record["errorMessage"]))
        return
    if not check(record["outputCount"] == 1,
                 "%s: the function wrote %d objects to the pipeline, expected "
                 "exactly one report" % (name, record["outputCount"])):
        return
    report = record["report"]
    if not isinstance(report, dict):
        fail("%s: the report is not an object" % name)
        return
    order = record["propertyOrder"].split(",") if record["propertyOrder"] \
        else []
    check(order == REPORT_PROPERTIES,
          "%s: the report properties are %s, expected %s"
          % (name, describe(order), describe(REPORT_PROPERTIES)))

    error = (script.task.get("errors") or [{}])[0]
    scalars = {
        "status": expected["status"],
        "taskId": script.task["id"],
        "taskStatus": normalize_status(script.task["status"]),
        "domainName": expected["domain_name"],
        "includeFreeHosts": expected["include_free_hosts"],
        "correlatedEventCount": len(expected["correlated_message_ids"]),
        "supportBundleId": script.bundle_id,
        "supportBundleStatus": normalize_status(script.bundle_statuses[-1]),
        "bundleName": expected["bundle_name"],
        "errorCode": error.get("errorCode", ""),
        "errorMessage": error.get("message", ""),
        "referenceToken": error.get("referenceToken", ""),
    }
    for key, wanted in scalars.items():
        observed = report.get(key)
        check(observed == wanted,
              "%s: report.%s was %s, expected %s"
              % (name, key, describe(observed), describe(wanted)))

    lists = {
        "failedSubTaskNames": expected["failed_sub_task_names"],
        "affectedResourceIds": expected["affected_resource_ids"],
        "clusterNames": expected["cluster_names"],
        "correlatedEventMessageIds": expected["correlated_message_ids"],
        "requestedLogs": expected["requested_logs"],
    }
    for key, wanted in lists.items():
        observed = as_list(report.get(key))
        check(observed == wanted,
              "%s: report.%s was %s, expected %s"
              % (name, key, describe(observed), describe(wanted)))

    steps = as_list(report.get("steps"))
    final_bundle_status = normalize_status(script.bundle_statuses[-1])
    bundle_ok = final_bundle_status == "COMPLETED_WITH_SUCCESS"
    wanted_steps = [
        {"operationId": "getTask", "status": "SUCCEEDED",
         "detail": normalize_status(script.task["status"])},
        {"operationId": "getNotifications", "status": "SUCCEEDED",
         "detail": str(len(expected["correlated_message_ids"]))},
        {"operationId": "startSupportBundle", "status": "SUCCEEDED",
         "detail": script.bundle_id},
        {"operationId": "getSupportBundleStatus",
         "status": "SUCCEEDED" if bundle_ok else "FAILED",
         "detail": final_bundle_status},
    ]
    if len(steps) != 4:
        fail("%s: report.steps is %s, expected four ordered steps"
             % (name, describe(steps)))
        return
    for index, (observed, wanted) in enumerate(zip(steps, wanted_steps)):
        if observed != wanted:
            fail("%s: report.steps[%d] was %s, expected %s"
                 % (name, index, describe(observed), describe(wanted)))


def main():
    if not os.path.isfile(MODULE_PATH):
        print("FAIL: VcfFailureTriage/VcfFailureTriage.psm1 does not exist")
        return 1
    check_contract()
    check_source()

    cases = build_cases()
    workdir = tempfile.mkdtemp(prefix="vcf-triage-")
    servers = []
    payload = []
    for case in cases:
        log_path = os.path.join(workdir, "%s.jsonl" % case["name"])
        server = ContractMockServer(CONTRACT_PATH, case["script"], log_path)
        server.serve_in_background()
        servers.append(server)
        case["log_path"] = log_path
        payload.append({
            "name": case["name"],
            "baseUri": server.base_uri,
            "accessToken": case["script"].token,
            "taskId": case["script"].task["id"],
            "mode": case["mode"],
            "includeHealthCheck": case["include_health_check"],
            "forceCollection": case["force_collection"],
            "pollIntervalSeconds": case["poll_interval_seconds"],
            "timeoutSeconds": case["timeout_seconds"],
        })

    cases_path = os.path.join(workdir, "cases.json")
    with open(cases_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    output_path = os.path.join(workdir, "results.json")

    command = ["pwsh", "-NoProfile", "-NonInteractive", "-File", INVOKE_PATH,
               "-ModuleManifest", MANIFEST_PATH,
               "-CasesPath", cases_path,
               "-OutputPath", output_path]
    completed = subprocess.run(command, capture_output=True, text=True,
                               timeout=900)
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
    check(exported == ["Invoke-VcfFailureTriage"],
          "the module exports %s, expected only Invoke-VcfFailureTriage"
          % describe(exported))

    records = {record["name"]: record for record in results.get("cases", [])}
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
    print("PASS: %d triage runs diagnosed accurately against the "
          "contract-pinned mock" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
