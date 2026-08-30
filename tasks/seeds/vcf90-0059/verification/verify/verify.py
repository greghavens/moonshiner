#!/usr/bin/env python3
"""Protected verifier for the vCenter appliance-update precheck-gating task.

Runs entirely offline against the loopback mock. No VMware endpoint and no network
service of any kind is contacted.

Checks, in order:
  1. protected harness and fixture files are unmodified
  2. docs/official_sources.json pins the 9.0.0.0 spec revision and names the operations
  3. docs/contract.json matches the operations as defined by that specification revision
  4. the client compiles and, across six scenario/mode runs, emits exactly the request
     wire shape the contract implies -- including omitting unset optional inputs
  5. the precheck gates the install: a blocking precheck leaves mock state untouched
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Pinned facts from vmware/vcf-api-specs, tag 9.0.0.0 (Apache-2.0),
# specifications/vsphere/openapi/automation/vcenter.yaml
# ---------------------------------------------------------------------------
REPOSITORY = "vmware/vcf-api-specs"
TAG = "9.0.0.0"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
COMMIT_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
API_VERSION = "9.0.0.0"
BASE_PATH = "/api"
AUTH_HEADER = "vmware-api-session-id"

# The 9.1.0.0 revision of the same file; using it is an explicit failure.
REJECTED_SHAS = {"3949fc33339fc5ea1b77eadb258f1cf49aa88e26"}

OP_LIST = "Appliance.Update.Pending_list"
OP_PRECHECK = "Appliance.Update.Pending_precheck"
OP_INSTALL = "Appliance.Update.Pending_install"
OPERATION_IDS = [OP_LIST, OP_PRECHECK, OP_INSTALL]

EXPECTED_OPERATIONS = {
    OP_LIST: {
        "method": "GET",
        "path": "/appliance/update/pending",
        "path_parameters": {},
        "query_parameters": {
            "source_type": {"required": True},
            "url": {"required": False},
            "enable_list_major_upgrade_versions": {"required": False},
        },
        "request_body": None,
        "success_status": 200,
    },
    OP_PRECHECK: {
        "method": "POST",
        "path": "/appliance/update/pending/{version}",
        "path_parameters": {"version": {"required": True}},
        "query_parameters": {"action": {"required": True, "constant": "precheck"}},
        "request_body": {"required": False, "fields": {"component": {"required": False}}},
        "success_status": 200,
    },
    OP_INSTALL: {
        "method": "POST",
        "path": "/appliance/update/pending/{version}",
        "path_parameters": {"version": {"required": True}},
        "query_parameters": {"action": {"required": True, "constant": "install"}},
        "request_body": {
            "required": True,
            "fields": {"user_data": {"required": True}, "component": {"required": False}},
        },
        "success_status": 204,
    },
}

PROTECTED = {
    "harness/MockVcenter.java": "4f886a9779e4dd0aa6981020dcf17b657eb1ebf2d0ee399e32b463f155a241b3",
    "harness/TestMain.java": "735d4a1563502a74b3356f5abb29031fb0ab5f72dcf1e2a624d23099e5815e1b",
    "fixtures/clean/pending_list.json": "587b91c702a491ee39d5ce0021b38006bd304d93c8b40eb5605afe7108dbeea5",
    "fixtures/clean/precheck_result.json": "2006eeedc42e886ebcace3d7a701ce03f3db506e3885d2a91c522092c4b0bf4b",
    "fixtures/blocked/pending_list.json": "587b91c702a491ee39d5ce0021b38006bd304d93c8b40eb5605afe7108dbeea5",
    "fixtures/blocked/precheck_result.json": "40b498f189be566e2a5083af070071a43e31246f3db1121a3b3b429df5591e75",
    "fixtures/none/pending_list.json": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
    "fixtures/none/precheck_result.json": "2006eeedc42e886ebcace3d7a701ce03f3db506e3885d2a91c522092c4b0bf4b",
    "fixtures/advisory/pending_list.json": "587b91c702a491ee39d5ce0021b38006bd304d93c8b40eb5605afe7108dbeea5",
    "fixtures/advisory/precheck_result.json": "de1c2b230790e24d40447e9ad6b13ae48ac74967ee18591e26616a1e02fa9cee",
    "fixtures/status_error/pending_list.json": "587b91c702a491ee39d5ce0021b38006bd304d93c8b40eb5605afe7108dbeea5",
    "fixtures/status_error/precheck_result.json": "2006eeedc42e886ebcace3d7a701ce03f3db506e3885d2a91c522092c4b0bf4b",
    "fixtures/status_error/pending_list_status.txt": "14993db977ff54ff86b0c447cbf330d1718c108468ea0f0fe2ec1fbfdb73af2b",
}

SESSION_ID = "vcf90-test-session-0001"
TARGET_VERSION = "9.0.1.0100"
USER_DATA = {
    "vcsa.root.password": "VMw@re-9.0-\"Test\"\\line\nnext",
    "backup.confirmed": "true",
}
COMPONENT = "VMware-vCenter-Server-Appliance"
FULL_URL = "https://vcsa-repo.example.com/vc/9.0.1.0100/?channel=ga&arch=x86_64"
BLOCKING_IDS = [
    "com.vmware.appliance.update.pending.precheck.error.insufficient_disk_space",
    "com.vmware.appliance.update.pending.precheck.error.vcha_active",
]

FAILURES = []


def fail(msg):
    FAILURES.append(msg)


def check(cond, msg):
    if not cond:
        fail(msg)
    return cond


# ---------------------------------------------------------------------------
# 1. protected files
# ---------------------------------------------------------------------------
def check_protected():
    for rel, expected in PROTECTED.items():
        p = ROOT / rel
        if not p.is_file():
            fail(f"protected file {rel} is missing")
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file {rel} was modified (sha256 {actual}, expected {expected})")


# ---------------------------------------------------------------------------
# 2. docs/official_sources.json
# ---------------------------------------------------------------------------
def load_json(rel):
    p = ROOT / rel
    if not p.is_file():
        fail(f"{rel} is missing")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"{rel} is not valid JSON: {e}")
        return None


def check_official_sources():
    doc = load_json("docs/official_sources.json")
    if doc is None:
        return
    entries = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(entries, list) or not entries:
        fail("docs/official_sources.json must have a non-empty 'sources' array")
        return

    matches = [e for e in entries if isinstance(e, dict) and e.get("spec_path") == SPEC_PATH]
    if not matches:
        fail(f"docs/official_sources.json has no source with spec_path {SPEC_PATH!r}")
        return
    src = matches[0]

    check(src.get("repository") == REPOSITORY,
          f"official_sources: repository must be {REPOSITORY!r}, got {src.get('repository')!r}")
    check(src.get("tag") == TAG,
          f"official_sources: tag must be {TAG!r}, got {src.get('tag')!r}")

    sha = str(src.get("commit_sha", "")).strip().lower()
    if sha in REJECTED_SHAS:
        fail("official_sources: commit_sha is the 9.1 revision of vcenter.yaml; the contract "
             f"must be derived from tag {TAG}")
    elif sha != COMMIT_SHA:
        fail(f"official_sources: commit_sha must be the full 40-hex sha of tag {TAG} "
             f"({COMMIT_SHA}), got {src.get('commit_sha')!r}")

    lic = str(src.get("license", ""))
    check(lic.lower().replace(" ", "-") in ("apache-2.0", "apache-license-2.0"),
          f"official_sources: license must be Apache-2.0, got {src.get('license')!r}")

    ops = src.get("operation_ids")
    if not isinstance(ops, list):
        fail("official_sources: operation_ids must be an array")
    else:
        strings_only = all(isinstance(o, str) for o in ops)
        check(strings_only, "official_sources: every operation_ids entry must be a string")
        missing = [o for o in OPERATION_IDS if o not in ops]
        extra = [o for o in ops if o not in OPERATION_IDS]
        if strings_only:
            check(len(ops) == len(set(ops)),
                  "official_sources: operation_ids must not contain duplicates")
        check(not missing, f"official_sources: operation_ids missing {missing}")
        check(not extra, f"official_sources: operation_ids has unexpected entries {extra}")


# ---------------------------------------------------------------------------
# 3. docs/contract.json
# ---------------------------------------------------------------------------
def as_param_map(value, label, opid):
    if value is None:
        return {}
    if not isinstance(value, list):
        fail(f"contract[{opid}]: {label} must be an array")
        return None
    out = {}
    for item in value:
        if (not isinstance(item, dict) or "name" not in item
                or not isinstance(item.get("name"), str)):
            fail(f"contract[{opid}]: every {label} entry needs a 'name'")
            return None
        if item["name"] in out:
            fail(f"contract[{opid}]: {label} repeats parameter {item['name']!r}")
            return None
        out[item["name"]] = item
    return out


def check_contract():
    doc = load_json("docs/contract.json")
    if doc is None:
        return

    source = doc.get("source")
    if not isinstance(source, dict):
        fail("contract.json must have a 'source' object")
    else:
        for key, want in (("repository", REPOSITORY), ("tag", TAG), ("spec_path", SPEC_PATH),
                          ("api_version", API_VERSION), ("base_path", BASE_PATH),
                          ("auth_header", AUTH_HEADER)):
            got = source.get(key)
            check(got == want, f"contract.source.{key} must be {want!r}, got {got!r}")

    ops = doc.get("operations")
    if not isinstance(ops, list):
        fail("contract.json must have an 'operations' array")
        return

    by_id = {}
    for op in ops:
        if (not isinstance(op, dict) or "operationId" not in op
                or not isinstance(op.get("operationId"), str)):
            fail("contract.json: every operation needs a string 'operationId'")
            return
        if op["operationId"] in by_id:
            fail(f"contract.json repeats operationId {op['operationId']!r}")
            continue
        by_id[op["operationId"]] = op

    check(len(ops) == len(OPERATION_IDS),
          f"contract.json must contain exactly {len(OPERATION_IDS)} operations, got {len(ops)}")
    extra = sorted(set(by_id) - set(OPERATION_IDS))
    check(not extra, f"contract.json names operations outside the scenario: {extra}")

    for opid, want in EXPECTED_OPERATIONS.items():
        op = by_id.get(opid)
        if op is None:
            fail(f"contract.json is missing operation {opid}")
            continue

        check(op.get("method") == want["method"],
              f"contract[{opid}].method must be {want['method']!r}, got {op.get('method')!r}")
        check(op.get("path") == want["path"],
              f"contract[{opid}].path must be {want['path']!r}, got {op.get('path')!r}")
        check(op.get("success_status") == want["success_status"],
              f"contract[{opid}].success_status must be {want['success_status']}, "
              f"got {op.get('success_status')!r}")

        for label in ("path_parameters", "query_parameters"):
            got = as_param_map(op.get(label), label, opid)
            if got is None:
                continue
            exp = want[label]
            if set(got) != set(exp):
                fail(f"contract[{opid}].{label} names {sorted(got)}, expected {sorted(exp)}")
                continue
            for name, spec in exp.items():
                if got[name].get("required") != spec["required"]:
                    fail(f"contract[{opid}].{label}[{name}].required must be "
                         f"{spec['required']}, got {got[name].get('required')!r}")
                if "constant" in spec and got[name].get("constant") != spec["constant"]:
                    fail(f"contract[{opid}].{label}[{name}].constant must be "
                         f"{spec['constant']!r}, got {got[name].get('constant')!r}")

        body = op.get("request_body")
        exp_body = want["request_body"]
        if exp_body is None:
            check(body is None,
                  f"contract[{opid}].request_body must be null (the operation sends no body)")
            continue
        if not isinstance(body, dict):
            fail(f"contract[{opid}].request_body must be an object")
            continue
        check(body.get("required") == exp_body["required"],
              f"contract[{opid}].request_body.required must be {exp_body['required']}, "
              f"got {body.get('required')!r}")
        fields = as_param_map(body.get("fields"), "request_body.fields", opid)
        if fields is None:
            continue
        if set(fields) != set(exp_body["fields"]):
            fail(f"contract[{opid}].request_body.fields names {sorted(fields)}, "
                 f"expected {sorted(exp_body['fields'])}")
            continue
        for name, spec in exp_body["fields"].items():
            if fields[name].get("required") != spec["required"]:
                fail(f"contract[{opid}].request_body.fields[{name}].required must be "
                     f"{spec['required']}, got {fields[name].get('required')!r}")


# ---------------------------------------------------------------------------
# 4/5. behaviour against the loopback mock
# ---------------------------------------------------------------------------
def build(workdir):
    build_dir = workdir / "classes"
    build_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(str(p) for p in (ROOT / "harness").glob("*.java"))
    sources += sorted(str(p) for p in (ROOT / "src").glob("*.java"))
    proc = subprocess.run(
        ["javac", "-nowarn", "-d", str(build_dir), *sources],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail("javac failed:\n" + (proc.stdout + proc.stderr).strip())
        return None
    return build_dir


def run_case(build_dir, workdir, scenario, mode):
    log = workdir / f"{scenario}-{mode}.log.jsonl"
    res = workdir / f"{scenario}-{mode}.result.json"
    env = dict(os.environ)
    env["JAVA_TOOL_OPTIONS"] = ""
    proc = subprocess.run(
        ["java", "-cp", str(build_dir), "TestMain",
         str(ROOT / "fixtures" / scenario), mode, str(log), str(res)],
        capture_output=True, text=True, timeout=180, env=env,
    )
    if proc.returncode != 0:
        fail(f"[{scenario}/{mode}] TestMain exited {proc.returncode}:\n"
             + (proc.stdout + proc.stderr).strip())
        return None, None
    if not res.is_file():
        fail(f"[{scenario}/{mode}] no result file was produced")
        return None, None

    result = json.loads(res.read_text(encoding="utf-8"))
    entries = []
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    return result, entries


def query_of(entry):
    raw = entry.get("raw_query")
    if not raw:
        return {}
    out = {}
    for pair in raw.split("&"):
        if not pair:
            continue
        name, sep, value = pair.partition("=")
        out.setdefault(urllib.parse.unquote_plus(name), []).append(
            urllib.parse.unquote_plus(value) if sep else None)
    return out


def body_of(entry, tag):
    raw = entry.get("body") or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        fail(f"{tag}: request body is not valid JSON: {raw!r}")
        return None
    if not isinstance(parsed, dict):
        fail(f"{tag}: request body must be a JSON object, got {type(parsed).__name__}")
        return None
    return parsed


def assert_common(entry, tag):
    check(entry.get("session_header") == SESSION_ID,
          f"{tag}: must send {AUTH_HEADER}: {SESSION_ID!r}, got {entry.get('session_header')!r}")
    check(entry.get("status") in (200, 204),
          f"{tag}: mock answered {entry.get('status')} -- the request did not match a "
          f"contract operation")


def assert_list_call(entry, tag, source_type, url, list_major):
    check(entry.get("method") == "GET", f"{tag}: method must be GET, got {entry.get('method')}")
    check(entry.get("raw_path") == "/api/appliance/update/pending",
          f"{tag}: path must be /api/appliance/update/pending, got {entry.get('raw_path')!r}")
    check((entry.get("body") or "") == "", f"{tag}: the list operation must send no request body")
    assert_common(entry, tag)

    q = query_of(entry)
    expected = {"source_type": [source_type]}
    if url is not None:
        expected["url"] = [url]
    if list_major is not None:
        expected["enable_list_major_upgrade_versions"] = [list_major]

    if q != expected:
        fail(f"{tag}: query parameters must be {expected}, got {q}")

    raw = entry.get("raw_query") or ""
    if url is not None:
        encoded_url = urllib.parse.quote_plus(url, safe="")
        check(f"url={encoded_url}" in raw.split("&"),
              f"{tag}: url value must be percent-encoded on the wire, got {raw!r}")
    if url is None:
        check("url" not in raw,
              f"{tag}: 'url' is unset and must be omitted from the query string entirely, "
              f"not sent empty -- got {raw!r}")
    if list_major is None:
        check("enable_list_major_upgrade_versions" not in raw,
              f"{tag}: 'enable_list_major_upgrade_versions' is unset and must be omitted from "
              f"the query string entirely, not sent empty -- got {raw!r}")


def assert_action_call(entry, tag, action, expected_body, component_set):
    check(entry.get("method") == "POST", f"{tag}: method must be POST, got {entry.get('method')}")
    check(entry.get("raw_path") == f"/api/appliance/update/pending/{TARGET_VERSION}",
          f"{tag}: path must be /api/appliance/update/pending/{TARGET_VERSION}, "
          f"got {entry.get('raw_path')!r}")
    assert_common(entry, tag)

    q = query_of(entry)
    if q != {"action": [action]}:
        fail(f"{tag}: query parameters must be {{'action': ['{action}']}}, got {q}")

    ctype = (entry.get("content_type") or "").lower()
    check(ctype.startswith("application/json"),
          f"{tag}: Content-Type must be application/json, got {entry.get('content_type')!r}")

    body = body_of(entry, tag)
    if body is None:
        return
    if body != expected_body:
        fail(f"{tag}: request body must be exactly {json.dumps(expected_body, sort_keys=True)}, "
             f"got {json.dumps(body, sort_keys=True)}")
    if not component_set:
        check("component" not in (entry.get("body") or ""),
              f"{tag}: 'component' is unset and must be omitted from the body entirely, not sent "
              f"as null or an empty string -- got {entry.get('body')!r}")


def check_behaviour(build_dir, workdir):
    # --- clean / minimal: precheck passes, every optional input unset -------
    result, log = run_case(build_dir, workdir, "clean", "minimal")
    if result is not None:
        tag = "clean/minimal"
        check(result.get("error") is None, f"{tag}: client raised {result.get('error')}")
        if len(log) != 3:
            fail(f"{tag}: expected 3 requests (list, precheck, install), got {len(log)}: "
                 + json.dumps([(e['method'], e['raw_path'], e['raw_query']) for e in log]))
        else:
            assert_list_call(log[0], f"{tag} #1 list", "LOCAL_AND_ONLINE", None, None)
            assert_action_call(log[1], f"{tag} #2 precheck", "precheck", {}, False)
            assert_action_call(log[2], f"{tag} #3 install", "install",
                               {"user_data": USER_DATA}, False)
        check(result.get("installed") is True, f"{tag}: installed must be true")
        check(result.get("version") == TARGET_VERSION,
              f"{tag}: version must be {TARGET_VERSION!r}, got {result.get('version')!r}")
        check(result.get("blocking_issues") == [], f"{tag}: blocking_issues must be empty")
        mock = result.get("mock", {})
        check(mock.get("install_count") == 1, f"{tag}: mock install_count must be 1")
        check(mock.get("installed_version") == TARGET_VERSION,
              f"{tag}: mock installed_version must be {TARGET_VERSION!r}")

    # --- clean / full: every optional input supplied ------------------------
    result, log = run_case(build_dir, workdir, "clean", "full")
    if result is not None:
        tag = "clean/full"
        check(result.get("error") is None, f"{tag}: client raised {result.get('error')}")
        if len(log) != 3:
            fail(f"{tag}: expected 3 requests, got {len(log)}")
        else:
            assert_list_call(log[0], f"{tag} #1 list", "LOCAL", FULL_URL, "false")
            assert_action_call(log[1], f"{tag} #2 precheck", "precheck",
                               {"component": COMPONENT}, True)
            assert_action_call(log[2], f"{tag} #3 install", "install",
                               {"user_data": USER_DATA, "component": COMPONENT}, True)
        check(result.get("installed") is True, f"{tag}: installed must be true")
        check(result.get("mock", {}).get("install_count") == 1,
              f"{tag}: mock install_count must be 1")

    # --- advisory / minimal: info/warnings and [] errors do not block ------
    result, log = run_case(build_dir, workdir, "advisory", "minimal")
    if result is not None:
        tag = "advisory/minimal"
        check(result.get("error") is None, f"{tag}: client raised {result.get('error')}")
        if len(log) != 3:
            fail(f"{tag}: advisory issues and an empty errors array must allow all 3 requests; "
                 f"got {len(log)}")
        else:
            assert_list_call(log[0], f"{tag} #1 list", "LOCAL_AND_ONLINE", None, None)
            assert_action_call(log[1], f"{tag} #2 precheck", "precheck", {}, False)
            assert_action_call(log[2], f"{tag} #3 install", "install",
                               {"user_data": USER_DATA}, False)
        check(result.get("installed") is True, f"{tag}: installed must be true")
        check(result.get("version") == TARGET_VERSION,
              f"{tag}: version must be {TARGET_VERSION!r}, got {result.get('version')!r}")
        check(result.get("blocking_issues") == [], f"{tag}: blocking_issues must be empty")
        check(result.get("mock", {}).get("install_count") == 1,
              f"{tag}: mock install_count must be 1")

    # --- blocked / minimal: precheck errors must gate the install ----------
    result, log = run_case(build_dir, workdir, "blocked", "minimal")
    if result is not None:
        tag = "blocked/minimal"
        check(result.get("error") is None, f"{tag}: client raised {result.get('error')}")
        if len(log) != 2:
            fail(f"{tag}: the precheck reports errors, so exactly 2 requests (list, precheck) "
                 f"must be sent and the install must never leave the client; got {len(log)}: "
                 + json.dumps([(e['method'], e['raw_path'], e['raw_query']) for e in log]))
        else:
            assert_list_call(log[0], f"{tag} #1 list", "LOCAL_AND_ONLINE", None, None)
            assert_action_call(log[1], f"{tag} #2 precheck", "precheck", {}, False)
        for e in log:
            check("action=install" not in (e.get("raw_query") or ""),
                  f"{tag}: an install request was sent despite blocking precheck errors")
        check(result.get("installed") is False, f"{tag}: installed must be false")
        check(result.get("version") == TARGET_VERSION,
              f"{tag}: version must still report {TARGET_VERSION!r}, got {result.get('version')!r}")
        check(result.get("blocking_issues") == BLOCKING_IDS,
              f"{tag}: blocking_issues must be {BLOCKING_IDS} (errors only, in response order), "
              f"got {result.get('blocking_issues')}")
        mock = result.get("mock", {})
        check(mock.get("install_count") == 0,
              f"{tag}: nothing may change when the precheck fails, but install_count is "
              f"{mock.get('install_count')}")
        check(mock.get("installed_version") is None,
              f"{tag}: nothing may change when the precheck fails, but installed_version is "
              f"{mock.get('installed_version')!r}")

    # --- none / minimal: no pending update at all ---------------------------
    result, log = run_case(build_dir, workdir, "none", "minimal")
    if result is not None:
        tag = "none/minimal"
        check(result.get("error") is None, f"{tag}: client raised {result.get('error')}")
        if len(log) != 1:
            fail(f"{tag}: no update is pending, so only the list request may be sent; "
                 f"got {len(log)}")
        else:
            assert_list_call(log[0], f"{tag} #1 list", "LOCAL_AND_ONLINE", None, None)
        check(result.get("installed") is False, f"{tag}: installed must be false")
        check(result.get("version") is None,
              f"{tag}: version must be null, got {result.get('version')!r}")
        check(result.get("mock", {}).get("install_count") == 0,
              f"{tag}: mock install_count must be 0")

    # --- non-contract response status: must raise before another request ----
    result, log = run_case(build_dir, workdir, "status_error", "minimal")
    if result is not None:
        tag = "status_error/minimal"
        check(result.get("error") is not None,
              f"{tag}: HTTP 201 is not the list operation's success status and must raise")
        check(result.get("installed") is False, f"{tag}: installed must be false")
        if len(log) != 1:
            fail(f"{tag}: a non-contract list status must stop the workflow after 1 request; "
                 f"got {len(log)}")
        else:
            entry = log[0]
            check(entry.get("method") == "GET", f"{tag}: first request must be GET")
            check(entry.get("raw_path") == "/api/appliance/update/pending",
                  f"{tag}: wrong list path {entry.get('raw_path')!r}")
            check(entry.get("session_header") == SESSION_ID,
                  f"{tag}: list request must carry the session header")
            check(query_of(entry) == {"source_type": ["LOCAL_AND_ONLINE"]},
                  f"{tag}: wrong list query {query_of(entry)}")
            check(entry.get("status") == 201,
                  f"{tag}: fixture must answer 201, got {entry.get('status')!r}")
        check(result.get("mock", {}).get("install_count") == 0,
              f"{tag}: mock install_count must remain 0")


def main():
    for tool in ("javac", "java"):
        if shutil.which(tool) is None:
            print(f"FAIL: required tool {tool!r} is not on PATH")
            return 1

    check_protected()
    check_official_sources()
    check_contract()

    workdir = Path(tempfile.mkdtemp(prefix="vcf90-verify-"))
    try:
        build_dir = build(workdir)
        if build_dir is not None:
            check_behaviour(build_dir, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if FAILURES:
        print(f"FAIL: {len(FAILURES)} check(s) failed\n")
        for i, f in enumerate(FAILURES, 1):
            print(f"{i:3}. {f}")
        return 1

    print("PASS: contract, sources, wire shape and precheck gating all verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
