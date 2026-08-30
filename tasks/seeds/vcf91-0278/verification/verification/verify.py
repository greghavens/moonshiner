#!/usr/bin/env python3
"""Deterministic verification for the VCF Operations custom-group reconciler.

Everything here runs against the loopback mock in harness/. No VMware endpoint,
and no network of any kind, is contacted.

Checks, in order:
  1. the harness was not tampered with, and the client is still a single file
  2. docs/official_sources.json records the specification this contract came from
  3. docs/contract.json matches the published OpenAPI specification exactly
  4. the client compiles, and the harness run converges on one group
  5. the request log shows the exact wire shape, including that unset optional
     fields are omitted rather than sent empty
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Pinned facts.
#
# specifications/vcf-operations/vcf-operations-openapi.json in vmware/vcf-api-specs.
# The task pins the 9.1 revision published by commit c3f3b52....
# --------------------------------------------------------------------------

SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_TITLE = "VMware Cloud Foundation Operations API"
SPEC_VERSION = "9.1.0.0"
OPENAPI_VERSION = "3.0.1"
SERVER_BASE_PATH = "/suite-api"
REPO_URL = "https://github.com/vmware/vcf-api-specs"

EXPECTED_OPERATIONS = {
    "acquireToken": {
        "method": "POST",
        "path": "/api/auth/token/acquire",
        "successStatus": 200,
        "requestSchema": "username-password",
        "responseSchema": "auth-token",
        "optionalQueryParameters": [],
    },
    "getCustomGroups": {
        "method": "GET",
        "path": "/api/resources/groups",
        "successStatus": 200,
        "requestSchema": None,
        "responseSchema": "cgroups",
        "optionalQueryParameters": ["groupId", "includePolicy"],
    },
    "createCustomGroup": {
        "method": "POST",
        "path": "/api/resources/groups",
        "successStatus": 201,
        "requestSchema": "custom-group",
        "responseSchema": "custom-group",
        "optionalQueryParameters": [],
    },
    "modifyCustomGroup": {
        "method": "PUT",
        "path": "/api/resources/groups",
        "successStatus": 200,
        "requestSchema": "custom-group",
        "responseSchema": "custom-group",
        "optionalQueryParameters": [],
    },
}

EXPECTED_SCHEMAS = {
    "auth-token": {"required": ["token", "validity"], "optional": ["expiresAt", "roles"]},
    "cgroups": {"required": [], "optional": ["groups"]},
    "custom-group": {
        "required": ["membershipDefinition", "resourceKey"],
        "optional": ["autoResolveMembership", "id", "links", "policy"],
    },
    "custom-group-membership": {
        "required": [],
        "optional": ["custom-group-properties", "excludedResources", "includedResources", "rules"],
    },
    "membership-rule-group": {
        "required": ["resourceKindKey"],
        "optional": [
            "propertyConditionRules",
            "relationshipConditionRules",
            "resourceNameConditionRules",
            "resourceTagConditionRules",
            "statConditionRules",
        ],
    },
    "resource-key": {
        "required": ["adapterKindKey", "name", "resourceKindKey"],
        "optional": ["extension", "links", "resourceIdentifiers"],
    },
    "resource-kind-key": {"required": ["adapterKind", "resourceKind"], "optional": []},
    "resource-name-condition-rule": {"required": ["compareOperator", "name"], "optional": []},
    "username-password": {"required": ["password", "username"], "optional": ["authSource"]},
}

PROTECTED = {
    "README.md": "d9b72e7233e5cb7465be6b19cca43ef7eeb121cdc070485f5544b9465939856a",
    "harness/TestMain.java": "c0b567380e56f5eabcd1d7068631caaea8509bd8dce482fbe8e590d904e4017a",
    "harness/mock_vcf_operations.py": "7ed0a04f3ed0bea608938c32f5a7c335b0053afc12e49b1853fa815f906ce6f6",
    "harness/run_tests.sh": "b94ae58df8b3ec50b88a6db5fafe2dc9a829892e5dd5c9294e7be360799aed59",
}

CLIENT_PATH = "src/main/java/com/vmware/vcfops/VcfOperationsClient.java"

# Values the harness drives, mirrored from harness/TestMain.java.
USERNAME = "svc-fleet-automation"
PASSWORD = "vcf-ops-9.1-Str0ng!"
AUTH_SOURCE = "Local Users"
GROUP_NAME = "VCF Fleet - Noisy Production VMs"
ADAPTER_KIND_KEY = "Container"
RESOURCE_KIND_KEY = "Environment"
RULE_ADAPTER_KIND = "VMWARE"
RULE_RESOURCE_KIND = "VirtualMachine"
NAME_CONTAINS = "prod-"
POLICY = "0f4c1f5e-2a8b-4d1c-9f77-6b3a2c5d8e91"

GROUPS_PATH = SERVER_BASE_PATH + "/api/resources/groups"
TOKEN_PATH = SERVER_BASE_PATH + "/api/auth/token/acquire"

EXPECTED_GROUP_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "vcf-ops-group:" + GROUP_NAME))


def token_for(auth_source):
    seed = "%s|%s" % (USERNAME, auth_source or "")
    return "tok-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


MEMBERSHIP_DEFINITION = {
    "rules": [
        {
            "resourceKindKey": {
                "adapterKind": RULE_ADAPTER_KIND,
                "resourceKind": RULE_RESOURCE_KIND,
            },
            "resourceNameConditionRules": [
                {"compareOperator": "CONTAINS", "name": NAME_CONTAINS}
            ],
        }
    ]
}

CREATE_BODY = {
    "resourceKey": {
        "name": GROUP_NAME,
        "adapterKindKey": ADAPTER_KIND_KEY,
        "resourceKindKey": RESOURCE_KIND_KEY,
    },
    "autoResolveMembership": True,
    "membershipDefinition": MEMBERSHIP_DEFINITION,
}

UPDATE_BODY = dict(CREATE_BODY, id=EXPECTED_GROUP_ID, policy=POLICY)


class Report:
    def __init__(self):
        self.failures = []

    def check(self, condition, message):
        if condition:
            print("  ok   %s" % message)
        else:
            print("  FAIL %s" % message)
            self.failures.append(message)
        return bool(condition)

    def fail(self, message):
        return self.check(False, message)

    def section(self, title):
        print("\n== %s" % title)


def read_json(report, relative):
    full = os.path.join(ROOT, relative)
    if not os.path.isfile(full):
        report.fail("%s exists" % relative)
        return None
    try:
        with open(full, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        report.fail("%s is valid JSON (%s)" % (relative, exc))
        return None


def sha256_of(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------------------
# 1. integrity
# --------------------------------------------------------------------------

def check_integrity(report):
    report.section("harness integrity")
    for relative, digest in PROTECTED.items():
        full = os.path.join(ROOT, relative)
        if not os.path.isfile(full):
            report.fail("%s is present" % relative)
            continue
        report.check(sha256_of(full) == digest, "%s is unmodified" % relative)

    client = os.path.join(ROOT, CLIENT_PATH)
    report.check(os.path.isfile(client), "%s exists" % CLIENT_PATH)

    sources = []
    src_root = os.path.join(ROOT, "src")
    for base, _dirs, names in os.walk(src_root):
        for name in names:
            if name.endswith(".java"):
                sources.append(os.path.relpath(os.path.join(base, name), ROOT))
    report.check(
        sorted(sources) == [CLIENT_PATH],
        "the client is a single file (found: %s)" % (sorted(sources) or "none"),
    )

    if os.path.isfile(client):
        with open(client, "r", encoding="utf-8") as fh:
            text = fh.read()
        report.check(
            "vmware.com" not in text and "VMWARE_OPS_HOST" not in text,
            "the client hardcodes no VMware host",
        )


# --------------------------------------------------------------------------
# 2. official sources
# --------------------------------------------------------------------------

def check_official_sources(report):
    report.section("docs/official_sources.json")
    doc = read_json(report, "docs/official_sources.json")
    if doc is None:
        return
    spec = doc.get("specification")
    if not isinstance(spec, dict):
        report.fail("carries a 'specification' object")
        return
    report.check(spec.get("repository") == REPO_URL, "names the vcf-api-specs repository")
    report.check(spec.get("license") == "Apache-2.0", "records the Apache-2.0 licence")
    report.check(spec.get("path") == SPEC_PATH, "records the spec path %s" % SPEC_PATH)
    commit = spec.get("commit")
    report.check(
        commit == SPEC_COMMIT,
        "records the pinned commit sha of the 9.1 specification (got %r)" % (commit,),
    )
    report.check(spec.get("title") == SPEC_TITLE, "records the spec title")
    report.check(str(spec.get("version")) == SPEC_VERSION, "records the spec version %s" % SPEC_VERSION)
    report.check(
        doc.get("operationIds") == sorted(EXPECTED_OPERATIONS),
        "lists exactly the operationIds used, sorted (got %r)" % (doc.get("operationIds"),),
    )


# --------------------------------------------------------------------------
# 3. contract
# --------------------------------------------------------------------------

def check_contract(report):
    report.section("docs/contract.json")
    doc = read_json(report, "docs/contract.json")
    if doc is None:
        return None

    source = doc.get("source")
    if not isinstance(source, dict):
        report.fail("carries a 'source' object")
    else:
        report.check(source.get("repository") == REPO_URL, "source.repository names vcf-api-specs")
        report.check(source.get("specPath") == SPEC_PATH, "source.specPath is %s" % SPEC_PATH)
        commit = source.get("commit")
        report.check(
            commit == SPEC_COMMIT,
            "source.commit pins the requested 9.1 revision (got %r)" % (commit,),
        )
        report.check(source.get("openapiVersion") == OPENAPI_VERSION, "source.openapiVersion is 3.0.1")
        report.check(str(source.get("apiVersion")) == SPEC_VERSION, "source.apiVersion is 9.1.0.0")
        report.check(
            source.get("serverBasePath") == SERVER_BASE_PATH,
            "source.serverBasePath is %s" % SERVER_BASE_PATH,
        )

    operations = doc.get("operations")
    if not isinstance(operations, list):
        report.fail("carries an 'operations' array")
        return doc
    by_id = {}
    for entry in operations:
        if isinstance(entry, dict) and isinstance(entry.get("operationId"), str):
            by_id[entry["operationId"]] = entry
    report.check(
        len(operations) == len(EXPECTED_OPERATIONS)
        and len(by_id) == len(EXPECTED_OPERATIONS)
        and sorted(by_id) == sorted(EXPECTED_OPERATIONS),
        "contains exactly one entry for each of the four operations used (got %r)"
        % ([entry.get("operationId") if isinstance(entry, dict) else entry
            for entry in operations],),
    )
    for name, expected in sorted(EXPECTED_OPERATIONS.items()):
        entry = by_id.get(name)
        if entry is None:
            continue
        for field, value in expected.items():
            actual = entry.get(field)
            if field == "successStatus":
                ok = actual == value
            elif field == "optionalQueryParameters":
                ok = actual == value
            else:
                ok = actual == value
            report.check(ok, "%s.%s == %r (got %r)" % (name, field, value, actual))

    schemas = doc.get("schemas")
    if not isinstance(schemas, dict):
        report.fail("carries a 'schemas' object")
        return doc
    report.check(
        sorted(schemas) == sorted(EXPECTED_SCHEMAS),
        "describes exactly the nine component schemas the client uses (got %r)" % (sorted(schemas),),
    )
    for name, expected in sorted(EXPECTED_SCHEMAS.items()):
        entry = schemas.get(name)
        if not isinstance(entry, dict):
            report.fail("schemas['%s'] is an object" % name)
            continue
        report.check(
            entry.get("required") == expected["required"],
            "schemas['%s'].required == %r (got %r)" % (name, expected["required"], entry.get("required")),
        )
        report.check(
            entry.get("optional") == expected["optional"],
            "schemas['%s'].optional == %r (got %r)" % (name, expected["optional"], entry.get("optional")),
        )
    return doc


# --------------------------------------------------------------------------
# 4. run
# --------------------------------------------------------------------------

def run_harness(report):
    report.section("harness run against the loopback mock")
    build = os.path.join(ROOT, ".verify-build")
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)
    empty_classpath = os.path.join(build, "empty-classpath")
    os.makedirs(empty_classpath)
    log_path = os.path.join(build, "requests.jsonl")
    port_path = os.path.join(build, "port")
    contract_path = os.path.join(ROOT, "docs", "contract.json")

    if not os.path.isfile(contract_path):
        report.fail("docs/contract.json exists so the mock can start")
        return None

    compile_result = subprocess.run(
        [
            "javac", "-classpath", empty_classpath, "-sourcepath", empty_classpath,
            "-d", os.path.join(build, "classes"),
            os.path.join(ROOT, CLIENT_PATH),
            os.path.join(ROOT, "harness", "TestMain.java"),
        ],
        capture_output=True, text=True,
    )
    if not report.check(compile_result.returncode == 0, "the client compiles with the harness"):
        print(compile_result.stderr.strip()[:4000])
        return None

    mock = subprocess.Popen(
        [
            sys.executable, os.path.join(ROOT, "harness", "mock_vcf_operations.py"),
            "--contract", contract_path,
            "--log", log_path,
            "--port-file", port_path,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        port = None
        for _ in range(200):
            if os.path.isfile(port_path) and os.path.getsize(port_path) > 0:
                with open(port_path, "r", encoding="utf-8") as fh:
                    port = fh.read().strip()
                break
            if mock.poll() is not None:
                break
            time.sleep(0.05)
        if not report.check(bool(port), "the contract-pinned mock starts"):
            print((mock.stderr.read() if mock.stderr else "")[:2000])
            return None

        try:
            run = subprocess.run(
                [
                    "java", "-cp", os.path.join(build, "classes"),
                    "com.vmware.vcfops.TestMain", "http://127.0.0.1:%s" % port,
                ],
                capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            report.fail("the harness run finishes within 180s")
            return None
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=10)
        except subprocess.TimeoutExpired:
            mock.kill()

    ok = report.check(run.returncode == 0, "the harness run succeeds")
    if not ok:
        print((run.stdout + "\n" + run.stderr).strip()[:4000])
    report.check(
        ("HARNESS_OK " + EXPECTED_GROUP_ID) in run.stdout,
        "the reconcile converges on group %s" % EXPECTED_GROUP_ID,
    )

    entries = []
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


# --------------------------------------------------------------------------
# 5. wire shape
# --------------------------------------------------------------------------

def check_wire(report, entries):
    report.section("request wire shape")
    if entries is None:
        report.fail("a request log was produced")
        return

    default_token = token_for(None)
    scoped_token = token_for(AUTH_SOURCE)

    expected = [
        {
            "label": "#1 acquireToken without an auth source",
            "method": "POST", "path": TOKEN_PATH, "status": 200,
            "body": {"username": USERNAME, "password": PASSWORD},
            "authorization": None,
            "json_content_type": True,
        },
        {
            "label": "#2 acquireToken against '%s'" % AUTH_SOURCE,
            "method": "POST", "path": TOKEN_PATH, "status": 200,
            "body": {"username": USERNAME, "password": PASSWORD, "authSource": AUTH_SOURCE},
            "authorization": "optional",
            "json_content_type": True,
        },
        {
            "label": "#3 getCustomGroups before the create",
            "method": "GET", "path": GROUPS_PATH, "status": 200,
            "body": None,
            "authorization": "OpsToken " + scoped_token,
            "json_content_type": False,
        },
        {
            "label": "#4 createCustomGroup",
            "method": "POST", "path": GROUPS_PATH, "status": 201,
            "body": CREATE_BODY,
            "authorization": "OpsToken " + scoped_token,
            "json_content_type": True,
        },
        {
            "label": "#5 getCustomGroups on the retry",
            "method": "GET", "path": GROUPS_PATH, "status": 200,
            "body": None,
            "authorization": "OpsToken " + scoped_token,
            "json_content_type": False,
        },
        {
            "label": "#6 modifyCustomGroup",
            "method": "PUT", "path": GROUPS_PATH, "status": 200,
            "body": UPDATE_BODY,
            "authorization": "OpsToken " + scoped_token,
            "json_content_type": True,
        },
    ]

    if not report.check(
        len(entries) == len(expected),
        "the client issues exactly %d requests, no more (got %d: %s)"
        % (len(expected), len(entries),
           ", ".join("%s %s" % (e["method"], e["path"]) for e in entries)),
    ):
        return

    for spec, entry in zip(expected, entries):
        label = spec["label"]
        report.check(entry["method"] == spec["method"] and entry["path"] == spec["path"],
                     "%s goes to %s %s (got %s %s)"
                     % (label, spec["method"], spec["path"], entry["method"], entry["path"]))
        report.check(entry["status"] == spec["status"],
                     "%s is accepted with HTTP %d (got %d)" % (label, spec["status"], entry["status"]))
        report.check(entry["rawQuery"] == "",
                     "%s sends no query string; unset optional query parameters are omitted "
                     "(got %r)" % (label, entry["rawQuery"]))

        headers = entry.get("headers") or {}
        accept = headers.get("accept", "")
        report.check("application/json" in accept,
                     "%s asks for application/json (Accept: %r)" % (label, accept))

        auth = headers.get("authorization")
        if spec["authorization"] is None:
            report.check(auth is None,
                         "%s carries no Authorization header (got %r)" % (label, auth))
        elif spec["authorization"] == "optional":
            # acquireToken declares an empty security requirement in the spec, so
            # the client may or may not present the token it already holds.
            report.check(auth is None or auth == "OpsToken " + default_token,
                         "%s either omits Authorization or presents the token it already "
                         "holds (got %r)" % (label, auth))
        else:
            report.check(auth == spec["authorization"],
                         "%s authenticates with the most recently acquired token (got %r)"
                         % (label, auth))

        content_type = (headers.get("content-type") or "").split(";")[0].strip()
        if spec["json_content_type"]:
            report.check(content_type == "application/json",
                         "%s declares Content-Type: application/json (got %r)" % (label, content_type))
        else:
            report.check(entry.get("body") == "",
                         "%s carries no request body (got %r)" % (label, entry.get("body")))

        if spec["body"] is None:
            continue
        actual = entry.get("bodyJson")
        if not report.check(actual == spec["body"],
                            "%s sends exactly the fields it has, and no others" % label):
            print("       expected: %s" % json.dumps(spec["body"], sort_keys=True))
            print("       actual:   %s" % json.dumps(actual, sort_keys=True))


def main():
    report = Report()
    try:
        check_integrity(report)
        check_official_sources(report)
        check_contract(report)
        entries = run_harness(report)
        check_wire(report, entries)

        print()
        if report.failures:
            print("FAILED (%d check%s)" % (len(report.failures), "" if len(report.failures) == 1 else "s"))
            for failure in report.failures:
                print("  - %s" % failure)
            return 1
        print("PASSED — contract derived from the specification, wire shape exact, "
              "reconcile safe to retry.")
        return 0
    finally:
        shutil.rmtree(os.path.join(ROOT, ".verify-build"), ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
