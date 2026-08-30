#!/usr/bin/env python3
"""Protected verifier for the VCF 9.1 SDDC LCM rollout client.

Compiles the fixture and the client, runs one rollout against the loopback SDDC LCM
fixture on an ephemeral port, then checks the recorded requests and the rollout report.

It contacts nothing but 127.0.0.1. Run it from anywhere:  python3 verify.py
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, "build-verify")

TOKEN = "eyJhbGciOiJIUzI1NiJ9.verifier-lcm-token.Rk9nQ2xMdVh3TnBaYjRUcw"

# --- fixture facts the client must discover at runtime, not assume ------------------
OPS_ID = "b7b2f1a4-3f4e-4a2c-9a4b-6c1d0e5f7a21"
TASK_DEPOT = "1f0b6c2e-8a41-4d1b-93b7-2c9f5a7e10d4"
TASK_PRECHECK = "5c93a7d1-0e26-4f83-b1aa-77d4e2c9f018"
TASK_APPLY = "9a4e1d70-63b8-4c25-8f19-be0c37a5d962"
OPS_CURRENT_VERSION = "9.0.2.0.23984011"
OPS_TARGET_VERSION = "9.1.0.0.24010188"
AUTOMATION_VERSION = "9.1.0.0.24010199"
OPS_BINARY_URL = ("https://depot.vcf.lab.local/bundles/vcf-operations/"
                  "9.1.0.0.24010188/bundle.manifest")
AUTOMATION_BINARY_URL = ("https://depot.vcf.lab.local/bundles/vcf-automation/"
                         "9.1.0.0.24010199/bundle.manifest")
FAILED_STAGE_ID = "stage-appliance-upgrade"
FAILURE_TEMPLATE = ("Appliance upgrade failed on ops.vcf.lab.local: post-upgrade service "
                    "startup did not complete within 1800s (correlationId={cid})")
PRECHECK_FAILED_STAGE_ID = "stage-compatibility-matrix"
PRECHECK_FAILURE_TEMPLATE = ("Compatibility validation failed for ops.vcf.lab.local "
                             "(correlationId={cid})")

# Literals that only the fixture knows: a client that contains them is asserting the
# answer instead of reading it off the wire.
FORBIDDEN_IN_CLIENT = [
    OPS_ID, TASK_DEPOT, TASK_PRECHECK, TASK_APPLY,
    FAILED_STAGE_ID, PRECHECK_FAILED_STAGE_ID, AUTOMATION_VERSION, OPS_CURRENT_VERSION,
    "CR-2026-0518-vcfops-91", "VCF_OPERATIONS", "VCF_AUTOMATION",
    OPS_TARGET_VERSION, "depot.vcf.lab.local", "MIIB8jCCAZigAwIBAgIU",
    "post-upgrade service startup", "Compatibility validation failed",
    "com.broadcom.lcm.apply.appliance.startup.timeout",
    "com.broadcom.lcm.precheck.compatibility.failed",
    "bundles/vcf-operations", "bundles/vcf-automation",
    "SddcLcmMock", "requests.jsonl", "build-verify",
]

# Files the solution must not change; sha256 of each as shipped.
PROTECTED = {
    "docs/contract.json": "9b91f5b635522d507d1e275e8aefd675a741b8ee1cdf20269051a951e57369f5",
    "docs/official_sources.json": "9b37b5e4002d3c39247fed91dc67d4d614339f9e35302d813dd20932d300c49d",
    "docs/client_api.md": "86d9fe27a660875bb9d76c678741545b02be10eb9d0a62db63cf7c7e32bd2de1",
    "fixtures/upgrade-request.json": "b70374178624fa805db53fcdac2f261cbf2333be76547f222930cf39a547b434",
    "harness/Json.java": "54aa0a65640d340ea40bda8b2bf752bc53bdf09b071a209376a2a6a8f22ff782",
    "harness/TestMain.java": "b64c5dd2438459d104678864c6fb726547dc7bb5f8a13189c49933ca525d9852",
    "mock/SddcLcmMock.java": "14c1e3463ed274ed753644c27c68ebb79c42d80c683229ff6b7c5637a3a5b3e5",
    "verify.py": None,  # self, not checked
}

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
    return bool(condition)


def fatal(message):
    FAILURES.append(message)
    report_and_exit()


def report_and_exit():
    print()
    if FAILURES:
        print("FAIL (%d problem%s)" % (len(FAILURES), "" if len(FAILURES) == 1 else "s"))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("PASS")
    sys.exit(0)


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ---------------------------------------------------------------- static checks

def check_layout():
    for rel, want in PROTECTED.items():
        path = os.path.join(ROOT, rel)
        if not check(os.path.isfile(path), "protected file is missing: " + rel):
            continue
        if want:
            check(sha256(path) == want, "protected file was modified: " + rel)

    src_dir = os.path.join(ROOT, "src")
    if not check(os.path.isdir(src_dir), "src/ is missing"):
        return
    entries = sorted(os.listdir(src_dir))
    check(entries == ["VcfLcmClient.java"],
          "src/ must contain exactly one file, VcfLcmClient.java; found: %s" % entries)

    client = os.path.join(src_dir, "VcfLcmClient.java")
    if not os.path.isfile(client):
        return
    with open(client, encoding="utf-8") as fh:
        source = fh.read()
    for literal in FORBIDDEN_IN_CLIENT:
        check(literal not in source,
              "src/VcfLcmClient.java hardcodes a value it should read from the service: %r"
              % literal)


# ------------------------------------------------------------------- run the rollout

def run_rollout(*mock_options):
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    os.makedirs(BUILD)

    mock_classes = os.path.join(BUILD, "mock-classes")
    app_classes = os.path.join(BUILD, "classes")
    log_path = os.path.join(BUILD, "requests.jsonl")
    port_path = os.path.join(BUILD, "port")
    report_path = os.path.join(BUILD, "report.json")

    javac = [
        (["javac", "-d", mock_classes, "mock/SddcLcmMock.java", "harness/Json.java"],
         "fixture"),
        (["javac", "-d", app_classes, "harness/Json.java", "harness/TestMain.java",
          "src/VcfLcmClient.java"], "client + harness"),
    ]
    for cmd, what in javac:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            fatal("compiling the %s failed:\n%s\n%s" % (what, proc.stdout, proc.stderr))

    mock_log = open(os.path.join(BUILD, "mock.log"), "w")
    mock = subprocess.Popen(
        ["java", "-cp", mock_classes, "SddcLcmMock",
         "--port", "0", "--log", log_path, "--portfile", port_path, "--token", TOKEN,
         *mock_options],
        cwd=ROOT, stdout=mock_log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(port_path) and os.path.getsize(port_path) > 0:
                break
            if mock.poll() is not None:
                break
            time.sleep(0.05)
        if not (os.path.exists(port_path) and os.path.getsize(port_path) > 0):
            mock_log.close()
            with open(os.path.join(BUILD, "mock.log")) as fh:
                fatal("the loopback fixture did not start:\n" + fh.read())
        with open(port_path) as fh:
            port = fh.read().strip()

        base_url = "http://127.0.0.1:%s/sddc-lcm" % port
        try:
            proc = subprocess.run(
                ["java", "-cp", app_classes, "TestMain", base_url, TOKEN,
                 "fixtures/upgrade-request.json", report_path],
                cwd=ROOT, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            fatal("the rollout did not finish within 180s")
        print(proc.stdout)
        if proc.returncode != 0:
            fatal("TestMain exited %d:\n%s\n%s" % (proc.returncode, proc.stdout, proc.stderr))
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=15)
        except subprocess.TimeoutExpired:
            mock.kill()
        mock_log.close()

    if not os.path.isfile(report_path):
        fatal("no rollout report was written")
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)

    requests = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                requests.append(json.loads(line))
    return report, requests


# --------------------------------------------------------------- request checks

def no_blank_values(node, where):
    """Optional fields with no value must be omitted, never sent blank."""
    if node is None:
        FAILURES.append("%s was sent as null; omit the property instead" % where)
    elif isinstance(node, str):
        if node == "":
            FAILURES.append("%s was sent as an empty string; omit the property instead" % where)
    elif isinstance(node, dict):
        if not node:
            FAILURES.append("%s was sent as an empty object; omit the property instead" % where)
        for k, v in node.items():
            no_blank_values(v, "%s.%s" % (where, k))
    elif isinstance(node, list):
        if not node:
            FAILURES.append("%s was sent as an empty array; omit the property instead" % where)
        for i, v in enumerate(node):
            no_blank_values(v, "%s[%d]" % (where, i))


def keys_exactly(obj, expected, where):
    if not isinstance(obj, dict):
        FAILURES.append("%s is not a JSON object" % where)
        return
    got = set(obj)
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    if missing:
        FAILURES.append("%s is missing %s" % (where, missing))
    if extra:
        FAILURES.append("%s carries properties it should not send: %s" % (where, extra))


def check_requests(requests, fixture):
    if not check(requests, "the fixture recorded no requests at all"):
        return None

    for r in requests:
        where = "request #%d (%s %s)" % (r["seq"], r["method"], r["path"])
        check(r["responseStatus"] in (200, 202),
              "%s was rejected by the service with HTTP %d" % (where, r["responseStatus"]))
        check(r["headers"].get("Authorization") == "Bearer " + TOKEN,
              "%s did not carry 'Authorization: Bearer <token>'" % where)
        accept = r["headers"].get("Accept") or ""
        check("application/json" in accept,
              "%s did not ask for application/json via the Accept header" % where)
        if r["body"] is not None:
            ct = r["headers"].get("Content-Type") or ""
            check(ct.startswith("application/json"),
                  "%s sent a body without Content-Type: application/json" % where)
            no_blank_values(r["body"], "%s body" % where)

    ops = [r for r in requests if r["operationId"] != "getTask"]
    got = [(r["operationId"], r["query"].get("action")) for r in ops]
    want = [("getComponents", None), ("setDepot", None), ("resolveDepotComponents", None),
            ("performComponentAction", "precheck"), ("performComponentAction", "apply")]
    if not check(got == want,
                 "the rollout did not perform the expected operations in order.\n"
                 "      expected: %s\n      actual:   %s" % (want, got)):
        return None

    listing, depot, resolve, precheck, apply_ = ops
    cert = fixture["depot"]["certificate"]

    # getComponents ---------------------------------------------------------
    check(listing["query"] == {"scope": "FLEET"},
          "getComponents should be scoped to the fleet; sent query %s" % listing["query"])
    check(listing["headers"].get("X-Correlation-Id") is None,
          "getComponents sent X-Correlation-Id, which the contract does not define for it")

    # setDepot --------------------------------------------------------------
    keys_exactly(depot["body"], {"fqdn", "certificate"}, "the setDepot body")
    check(depot["body"].get("fqdn") == fixture["depot"]["fqdn"],
          "setDepot sent the wrong depot fqdn")
    check(depot["body"].get("certificate") == cert,
          "setDepot did not send the depot certificate verbatim")
    correlation_id = depot["headers"].get("X-Correlation-Id")
    check(correlation_id is not None and UUID_RE.match(correlation_id or ""),
          "setDepot should carry an X-Correlation-Id header holding the run's UUID; got %r"
          % correlation_id)

    # resolveDepotComponents ------------------------------------------------
    keys_exactly(resolve["body"], {"fleetDepotSpec", "componentVersions"},
                 "the resolveDepotComponents body")
    keys_exactly(resolve["body"].get("fleetDepotSpec"), {"fqdn", "certificate"},
                 "resolveDepotComponents fleetDepotSpec")
    check(resolve["body"].get("fleetDepotSpec", {}).get("fqdn") == fixture["depot"]["fqdn"]
          and resolve["body"].get("fleetDepotSpec", {}).get("certificate") == cert,
          "resolveDepotComponents did not send the registered depot verbatim")
    want_versions = [{"component": fixture["componentType"], "version": fixture["targetVersion"]}]
    for extra in fixture["additionalComponents"]:
        entry = {"component": extra["component"]}
        if "version" in extra:
            entry["version"] = extra["version"]
        want_versions.append(entry)
    check(resolve["body"].get("componentVersions") == want_versions,
          "resolveDepotComponents sent the wrong componentVersions.\n"
          "      expected: %s\n      actual:   %s"
          % (json.dumps(want_versions), json.dumps(resolve["body"].get("componentVersions"))))
    check(resolve["headers"].get("X-Correlation-Id") is None,
          "resolveDepotComponents sent X-Correlation-Id, which the contract does not define "
          "for it")

    # performComponentAction ------------------------------------------------
    want_component_spec = {
        "software": {"version": fixture["targetVersion"]},
        "depot": {"url": OPS_BINARY_URL, "certificate": [cert]},
    }
    for label, req in (("precheck", precheck), ("apply", apply_)):
        check(req["path"] == "/sddc-lcm/v1/components/" + OPS_ID,
              "performComponentAction (%s) targeted %s, not the component getComponents "
              "identified" % (label, req["path"]))
        check(req["query"] == {"action": label},
              "performComponentAction (%s) sent query %s" % (label, req["query"]))
        check(req["headers"].get("X-Correlation-Id") == correlation_id,
              "performComponentAction (%s) should carry the run's correlation id in the "
              "X-Correlation-Id header" % label)
        check(req["body"].get("correlationId") == correlation_id,
              "performComponentAction (%s) should carry the run's correlation id in the "
              "body" % label)
        keys_exactly(req["body"].get("componentSpec"), {"software", "depot"},
                     "the performComponentAction (%s) componentSpec" % label)
        check(req["body"].get("componentSpec") == want_component_spec,
              "performComponentAction (%s) sent the wrong componentSpec.\n"
              "      expected: %s\n      actual:   %s"
              % (label, json.dumps(want_component_spec),
                 json.dumps(req["body"].get("componentSpec"))))

    keys_exactly(precheck["body"], {"componentSpec", "correlationId"},
                 "the performComponentAction (precheck) body")
    keys_exactly(apply_["body"], {"componentSpec", "lcmPlatformSpec", "correlationId"},
                 "the performComponentAction (apply) body")
    check(apply_["body"].get("lcmPlatformSpec") == {"performBackup": fixture["performBackup"]},
          "performComponentAction (apply) sent lcmPlatformSpec %s"
          % json.dumps(apply_["body"].get("lcmPlatformSpec")))

    # getTask polling -------------------------------------------------------
    polls = [r for r in requests if r["operationId"] == "getTask"]
    for r in polls:
        check(r["headers"].get("X-Correlation-Id") is None,
              "getTask sent X-Correlation-Id, which the contract does not define for it")
        check(r["query"] == {},
              "getTask sent query parameters the contract does not define: %s" % r["query"])

    def polls_for(task_id):
        return [r for r in polls if r["path"] == "/sddc-lcm/v1/tasks/" + task_id]

    unknown = [r["path"] for r in polls
               if r["path"].rsplit("/", 1)[-1] not in (TASK_DEPOT, TASK_PRECHECK, TASK_APPLY)]
    check(not unknown, "getTask was called for ids no operation returned: %s" % unknown)

    for label, task_id, terminal_poll in (("depot registration", TASK_DEPOT, 2),
                                          ("upgrade precheck", TASK_PRECHECK, 3),
                                          ("upgrade apply", TASK_APPLY, 3)):
        n = len(polls_for(task_id))
        check(n == terminal_poll,
              "the %s task was polled %d time(s); its first terminal response is poll %d, "
              "and polling must stop there" % (label, n, terminal_poll))

    seqs = {r["seq"] for r in requests}
    check(max(seqs) == len(requests), "the request log is inconsistent")

    def between(task_id, after, before):
        idx = [r["seq"] for r in polls_for(task_id)]
        if not idx:
            return
        if after is not None:
            check(min(idx) > after["seq"],
                  "a task was polled before the operation that created it")
        if before is not None:
            check(max(idx) < before["seq"],
                  "the rollout moved on to the next operation before its predecessor's task "
                  "reached a terminal status")

    between(TASK_DEPOT, depot, resolve)
    between(TASK_PRECHECK, precheck, apply_)
    between(TASK_APPLY, apply_, None)

    check(requests[-1]["operationId"] == "getTask"
          and requests[-1]["path"].endswith(TASK_APPLY),
          "the rollout continued after the failing apply task instead of stopping there")

    return correlation_id


# ---------------------------------------------------------------- report checks

def check_report(report, fixture, correlation_id):
    if not isinstance(report, dict):
        fatal("the rollout report is not a JSON object")

    check(report.get("changeRequest") == fixture["changeRequest"],
          "report.changeRequest is %r" % report.get("changeRequest"))
    check(report.get("componentType") == fixture["componentType"],
          "report.componentType is %r" % report.get("componentType"))
    check(report.get("componentId") == OPS_ID,
          "report.componentId is %r, not the id getComponents returned for %s"
          % (report.get("componentId"), fixture["componentType"]))
    check(report.get("currentVersion") == OPS_CURRENT_VERSION,
          "report.currentVersion is %r, not the version the component reports"
          % report.get("currentVersion"))
    check(report.get("targetVersion") == fixture["targetVersion"],
          "report.targetVersion is %r" % report.get("targetVersion"))
    if correlation_id:
        check(report.get("correlationId") == correlation_id,
              "report.correlationId is %r but the requests carried %r"
              % (report.get("correlationId"), correlation_id))

    want_resolved = [
        {"component": "VCF_OPERATIONS", "version": OPS_TARGET_VERSION,
         "binaryUrl": OPS_BINARY_URL},
        {"component": "VCF_AUTOMATION", "version": AUTOMATION_VERSION,
         "binaryUrl": AUTOMATION_BINARY_URL},
    ]
    check(report.get("resolvedComponentVersions") == want_resolved,
          "report.resolvedComponentVersions does not match what resolveDepotComponents "
          "returned.\n      expected: %s\n      actual:   %s"
          % (json.dumps(want_resolved), json.dumps(report.get("resolvedComponentVersions"))))

    check(report.get("outcome") == "FAILED",
          "report.outcome is %r; the apply task ends FAILED" % report.get("outcome"))

    steps = report.get("steps")
    if not check(isinstance(steps, list) and len(steps) == 5,
                 "report.steps should hold one entry per operation performed (5); got %s"
                 % (len(steps) if isinstance(steps, list) else steps)):
        return

    want_steps = [
        ({"operationId", "status"},
         {"operationId": "getComponents", "status": "SUCCEEDED"}),
        ({"operationId", "status", "taskId"},
         {"operationId": "setDepot", "status": "SUCCEEDED", "taskId": TASK_DEPOT}),
        ({"operationId", "status"},
         {"operationId": "resolveDepotComponents", "status": "SUCCEEDED"}),
        ({"operationId", "action", "status", "taskId"},
         {"operationId": "performComponentAction", "action": "precheck",
          "status": "SUCCEEDED", "taskId": TASK_PRECHECK}),
        ({"operationId", "action", "status", "taskId"},
         {"operationId": "performComponentAction", "action": "apply",
          "status": "FAILED", "taskId": TASK_APPLY}),
    ]
    for i, (keys, want) in enumerate(want_steps):
        keys_exactly(steps[i], keys, "report.steps[%d]" % i)
        check(steps[i] == want,
              "report.steps[%d] is wrong.\n      expected: %s\n      actual:   %s"
              % (i, json.dumps(want), json.dumps(steps[i])))

    failure = report.get("failure")
    if not check(isinstance(failure, dict), "report.failure is missing"):
        return
    keys_exactly(failure, {"stepIndex", "operationId", "action", "taskId", "taskStatus",
                           "failedStageId", "message"}, "report.failure")
    want_failure = {
        "stepIndex": 4,
        "operationId": "performComponentAction",
        "action": "apply",
        "taskId": TASK_APPLY,
        "taskStatus": "FAILED",
        "failedStageId": FAILED_STAGE_ID,
        "message": FAILURE_TEMPLATE.format(cid=correlation_id),
    }
    check(failure == want_failure,
          "report.failure is wrong.\n      expected: %s\n      actual:   %s"
          % (json.dumps(want_failure, sort_keys=True), json.dumps(failure, sort_keys=True)))


def check_precheck_failure(report, requests):
    """The client must stop before apply when the precheck task itself fails."""
    ops = [r for r in requests if r["operationId"] != "getTask"]
    got = [(r["operationId"], r["query"].get("action")) for r in ops]
    want = [("getComponents", None), ("setDepot", None),
            ("resolveDepotComponents", None), ("performComponentAction", "precheck")]
    if not check(got == want,
                 "after a failed precheck, the rollout should stop before apply.\n"
                 "      expected: %s\n      actual:   %s" % (want, got)):
        return

    polls = [r for r in requests if r["operationId"] == "getTask"]
    depot_polls = [r for r in polls if r["path"].endswith(TASK_DEPOT)]
    precheck_polls = [r for r in polls if r["path"].endswith(TASK_PRECHECK)]
    check(len(depot_polls) == 2,
          "the early-failure run did not stop depot polling at its terminal response")
    check(len(precheck_polls) == 3,
          "the early-failure run did not stop precheck polling at its terminal response")
    check(requests[-1]["operationId"] == "getTask"
          and requests[-1]["path"].endswith(TASK_PRECHECK),
          "the rollout emitted a request after observing the failed precheck")

    correlation_id = ops[1]["headers"].get("X-Correlation-Id")
    steps = report.get("steps")
    want_steps = [
        {"operationId": "getComponents", "status": "SUCCEEDED"},
        {"operationId": "setDepot", "status": "SUCCEEDED", "taskId": TASK_DEPOT},
        {"operationId": "resolveDepotComponents", "status": "SUCCEEDED"},
        {"operationId": "performComponentAction", "action": "precheck",
         "status": "FAILED", "taskId": TASK_PRECHECK},
    ]
    check(report.get("outcome") == "FAILED",
          "the early-failure report did not mark the rollout FAILED")
    check(steps == want_steps,
          "the early-failure report recorded the wrong steps.\n"
          "      expected: %s\n      actual:   %s"
          % (json.dumps(want_steps), json.dumps(steps)))
    want_failure = {
        "stepIndex": 3,
        "operationId": "performComponentAction",
        "action": "precheck",
        "taskId": TASK_PRECHECK,
        "taskStatus": "FAILED",
        "failedStageId": PRECHECK_FAILED_STAGE_ID,
        "message": PRECHECK_FAILURE_TEMPLATE.format(cid=correlation_id),
    }
    check(report.get("failure") == want_failure,
          "the early-failure report did not identify the precheck's failed stage and error.\n"
          "      expected: %s\n      actual:   %s"
          % (json.dumps(want_failure, sort_keys=True),
             json.dumps(report.get("failure"), sort_keys=True)))


def main():
    for tool in ("javac", "java"):
        if shutil.which(tool) is None:
            fatal("%s is not on PATH" % tool)

    check_layout()
    if FAILURES:
        report_and_exit()

    with open(os.path.join(ROOT, "fixtures/upgrade-request.json"), encoding="utf-8") as fh:
        fixture = json.load(fh)

    report, requests = run_rollout()
    correlation_id = check_requests(requests, fixture)
    check_report(report, fixture, correlation_id)

    failed_report, failed_requests = run_rollout("--fail-precheck", "true")
    check_precheck_failure(failed_report, failed_requests)
    report_and_exit()


if __name__ == "__main__":
    main()
