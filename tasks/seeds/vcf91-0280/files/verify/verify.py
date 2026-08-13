#!/usr/bin/env python3
"""Protected verifier for the VCF Operations adapter-onboarding client.

Compiles the client together with the shipped harness, drives it against the loopback mock, then
asserts the exact wire shape recorded in the mock's request log.

No VMware endpoint is contacted: the only network traffic is to 127.0.0.1 on an ephemeral port
bound by MockOpsServer.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
LOG = BUILD / "request-log.jsonl"
CONTRACT = ROOT / "docs" / "contract.json"

# Files the task owner controls. A solution that edits any of them is not a solution.
PROTECTED = {
    ".gitignore": "644cd942b33791a68ebaabf435a0832002d7fcd41b37f5eaa8edb8da2f4de4af",
    "README.md": "fe7f1032c556ba7cbe0c8a224fbcbc3200a0c97698c0064afcd4caef455ae195",
    "docs/contract.json": "3c9a96cf08e8089800bababc856c36caee0283a3e982796491dddcfbf772eadf",
    "docs/official_sources.json": "a964d0ba2f7406077a5e2076a7e1c326b1fe7af682c74a95968bcb17e3613312",
    "harness/MockOpsServer.java": "0bc671985ba20ca5c425e8e2fe996823e39a8bc2d33e125f4c4446b20c5cbce0",
    "harness/TestMain.java": "7c73cfae63bc948fe7220b62114d4c085006e2659b9d1ca0ca416658ea9a1d88",
    "verify/verify.py": "",
}

AUTHORIZATION = "vRealizeOpsToken 4c1f0f5e-2b7a-4a01-9d3b-6e8f2a0c17d4::b3JzLWRlbW8"
BASE = "/suite-api"
PRECHECK_PATH = BASE + "/api/adapters/testConnection"
CREATE_PATH = BASE + "/api/adapters"

failures: list[str] = []
checks = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global checks
    checks += 1
    if not ok:
        failures.append(label + (": " + detail if detail else ""))
    return ok


def fatal(message: str) -> None:
    print("FAIL " + message)
    print("\nRESULT: FAIL")
    sys.exit(1)


def java_name_uuid(text: str) -> str:
    """Reproduces java.util.UUID.nameUUIDFromBytes (a version 3, MD5-based UUID)."""
    b = bytearray(hashlib.md5(text.encode("utf-8")).digest())
    b[6] = (b[6] & 0x0F) | 0x30
    b[8] = (b[8] & 0x3F) | 0x80
    h = b.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def content_digest(path: Path) -> str:
    """Digests a text file ignoring line-ending style and trailing whitespace, so that packaging
    a seed cannot break the integrity check while a real edit still does."""
    lines = [line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def verify_protected() -> None:
    for rel, expected in PROTECTED.items():
        path = ROOT / rel
        if not path.is_file():
            fatal(f"protected file is missing: {rel}")
        actual = content_digest(path)
        if expected and actual != expected:
            fatal(f"protected file was modified: {rel} (expected sha256 {expected}, found {actual})")


def compile_and_run() -> str:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    sources = [
        str(ROOT / "src" / "OpsAdapterClient.java"),
        str(ROOT / "harness" / "MockOpsServer.java"),
        str(ROOT / "harness" / "TestMain.java"),
    ]
    for s in sources:
        if not Path(s).is_file():
            fatal(f"source file is missing: {Path(s).relative_to(ROOT)}")

    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        fatal("a JDK is required on PATH (javac and java)")

    compiled = subprocess.run(
        [javac, "-nowarn", "-d", str(BUILD)] + sources,
        capture_output=True, text=True, cwd=ROOT, timeout=300,
    )
    if compiled.returncode != 0:
        print(compiled.stdout)
        print(compiled.stderr)
        fatal("compilation failed")

    run = subprocess.run(
        [java, "-cp", str(BUILD), "TestMain", str(CONTRACT), str(LOG)],
        capture_output=True, text=True, cwd=ROOT, timeout=300,
    )
    if run.returncode != 0:
        print(run.stdout)
        print(run.stderr)
        fatal(f"TestMain exited with status {run.returncode}")
    if run.stderr.strip():
        print("--- TestMain stderr ---")
        print(run.stderr.strip())
    return run.stdout


def parse_results(stdout: str) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("ERROR "):
            fatal("TestMain reported an error: " + line)
        if line.startswith("RESULT "):
            fields = {}
            for token in line[len("RESULT "):].split(" "):
                k, _, v = token.partition("=")
                fields[k] = v
            results.setdefault(fields["scenario"], {}).update(fields)
        elif line.startswith("DETAIL "):
            head, _, value = line[len("DETAIL "):].partition(" value=")
            parts = dict(t.split("=", 1) for t in head.split(" "))
            entry = results.setdefault(parts["scenario"], {})
            entry.setdefault("details", {})[parts["phase"]] = value
    return results


def parse_log() -> dict[str, list[dict]]:
    if not LOG.is_file():
        fatal("the mock wrote no request log")
    grouped: dict[str, list[dict]] = {}
    current = None
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["type"] == "marker":
            current = entry["scenario"]
            grouped.setdefault(current, [])
        else:
            if current is None:
                fatal("the mock received a request before any scenario started")
            grouped[current].append(entry)
    return grouped


def empty_placeholders(node, trail: str = "$") -> list[str]:
    """Finds values that stand in for 'unset' instead of the property being absent."""
    found = []
    if node is None:
        found.append(trail + " is null")
    elif isinstance(node, str) and node == "":
        found.append(trail + ' is ""')
    elif isinstance(node, dict):
        if not node and trail != "$":
            found.append(trail + " is {}")
        for k, v in node.items():
            found += empty_placeholders(v, f"{trail}.{k}")
    elif isinstance(node, list):
        if not node:
            found.append(trail + " is []")
        for i, v in enumerate(node):
            found += empty_placeholders(v, f"{trail}[{i}]")
    return found


def body_of(request: dict, label: str):
    try:
        return json.loads(request["body"])
    except (ValueError, TypeError):
        fatal(f"{label}: request body is not valid JSON: {request.get('body')!r}")


def assert_common(request: dict, label: str) -> None:
    check(request["operationId"] is not None, f"{label}: request reached an operation the contract names",
          f"{request['method']} {request['path']} matched no contracted operation")
    check(request["path"].startswith(BASE), f"{label}: path carries the contract's server base path",
          f"path was {request['path']}")
    headers = request["headers"]
    check(headers.get("Authorization") == AUTHORIZATION, f"{label}: Authorization header sent verbatim",
          f"header was {headers.get('Authorization')!r}")
    ctype = headers.get("Content-Type") or ""
    check(ctype == "application/json", f"{label}: Content-Type is exactly application/json",
          f"header was {ctype!r}")
    accept = headers.get("Accept") or ""
    check(accept == "application/json", f"{label}: Accept is exactly application/json",
          f"header was {accept!r}")


def assert_no_placeholders(body, label: str) -> None:
    stray = empty_placeholders(body)
    check(not stray, f"{label}: unset optional properties are omitted, not sent empty",
          "; ".join(stray))


def main() -> None:
    verify_protected()
    stdout = compile_and_run()
    print(stdout.rstrip())
    print()
    results = parse_results(stdout)
    log = parse_log()

    expected_scenarios = [
        "full_onboarding", "minimal_onboarding", "credential_without_fields",
        "precheck_blocks_mutation",
        "identifier_defaults_requested", "create_rejected",
    ]
    for name in expected_scenarios:
        if name not in results:
            fatal(f"TestMain printed no RESULT for scenario {name}")
        if name not in log:
            fatal(f"the request log has no marker for scenario {name}")

    total = sum(len(v) for v in log.values())
    check(total == 11, "exactly eleven requests were issued across the six scenarios", f"saw {total}")

    expected_sequences = {
        "full_onboarding": ["testConnection", "createAdapterInstance"],
        "minimal_onboarding": ["testConnection", "createAdapterInstance"],
        "credential_without_fields": ["testConnection", "createAdapterInstance"],
        "precheck_blocks_mutation": ["testConnection"],
        "identifier_defaults_requested": ["testConnection", "createAdapterInstance"],
        "create_rejected": ["testConnection", "createAdapterInstance"],
    }
    expected_queries = {
        "full_onboarding": [{}, {"force": "false"}],
        "minimal_onboarding": [{}, {"force": "false"}],
        "credential_without_fields": [{}, {"force": "false"}],
        "precheck_blocks_mutation": [{}],
        "identifier_defaults_requested": [
            {}, {"extractIdentifierDefaults": "true", "force": "false"}],
        "create_rejected": [{}, {"force": "false"}],
    }
    for scenario in expected_scenarios:
        reqs = log[scenario]
        check([q["operationId"] for q in reqs] == expected_sequences[scenario],
              f"{scenario}: only the gated operation sequence was called",
              f"sequence was {[q['operationId'] for q in reqs]}")
        for i, req in enumerate(reqs):
            assert_common(req, f"{scenario} request {i + 1}")
            if i < len(expected_queries[scenario]):
                expected_query = expected_queries[scenario][i]
                pairs = parse_qsl(req["rawQuery"] or "", keep_blank_values=True)
                check(len(pairs) == len(expected_query) and dict(pairs) == expected_query,
                      f"{scenario} request {i + 1}: query has exactly the contracted parameters",
                      f"raw query was {req['rawQuery']!r}")
            else:
                check(False, f"{scenario} request {i + 1}: unexpected extra request")
        if len(reqs) == 2:
            check(reqs[0]["body"] == reqs[1]["body"],
                  f"{scenario}: precheck and mutation carry the identical serialized payload",
                  f"precheck {reqs[0]['body']!r} vs mutation {reqs[1]['body']!r}")

    # --- full_onboarding: precheck passes, mutation follows with the identical payload -----------
    reqs = log["full_onboarding"]
    if check(len(reqs) == 2, "full_onboarding: precheck then mutation", f"saw {len(reqs)} requests"):
        pre, mut = reqs
        assert_common(pre, "full_onboarding precheck")
        assert_common(mut, "full_onboarding mutation")
        check(pre["operationId"] == "testConnection" and pre["method"] == "POST"
              and pre["path"] == PRECHECK_PATH,
              "full_onboarding: precheck is POST " + PRECHECK_PATH,
              f"saw {pre['method']} {pre['path']}")
        check(pre["query"] == {}, "full_onboarding: precheck sends no query parameters",
              f"saw {pre['query']}")
        check(mut["operationId"] == "createAdapterInstance" and mut["method"] == "POST"
              and mut["path"] == CREATE_PATH,
              "full_onboarding: mutation is POST " + CREATE_PATH,
              f"saw {mut['method']} {mut['path']}")
        check(mut["query"] == {"force": "false"},
              "full_onboarding: mutation sends force=false and nothing else",
              f"saw {mut['query']}")
        check(pre["seq"] < mut["seq"], "full_onboarding: the precheck is issued first")

        pre_body = body_of(pre, "full_onboarding precheck")
        mut_body = body_of(mut, "full_onboarding mutation")
        check(pre_body == mut_body, "full_onboarding: both calls carry the same payload",
              f"precheck {pre_body} vs mutation {mut_body}")
        for label, body in (("full_onboarding precheck", pre_body), ("full_onboarding mutation", mut_body)):
            check(set(body) == {"name", "adapterKindKey", "description", "collectorId",
                                "collectorGroupId", "physicalDatacenterId", "monitoringInterval",
                                "monitoringIntervalSeconds", "credential", "resourceIdentifiers"},
                  f"{label}: body carries exactly the properties the caller set",
                  f"saw {sorted(body)}")
            assert_no_placeholders(body, label)
            check(body.get("name") == "Prod VC Adapter Instance", f"{label}: name")
            check(body.get("adapterKindKey") == "VMWARE", f"{label}: adapterKindKey")
            check(body.get("collectorId") == "1", f"{label}: collectorId is a string",
                  f"saw {body.get('collectorId')!r}")
            check(body.get("collectorGroupId") == "11111111-1111-1111-1111-111111111111",
                  f"{label}: collectorGroupId")
            check(body.get("physicalDatacenterId") == "22222222-2222-2222-2222-222222222222",
                  f"{label}: physicalDatacenterId")
            check(body.get("monitoringInterval") == 0,
                  f"{label}: an explicitly set zero monitoringInterval is present as an integer",
                  f"saw {body.get('monitoringInterval')!r}")
            check(body.get("monitoringIntervalSeconds") == 300,
                  f"{label}: monitoringIntervalSeconds is present as an integer",
                  f"saw {body.get('monitoringIntervalSeconds')!r}")
            ids = body.get("resourceIdentifiers")
            check(ids == [{"name": "AUTODISCOVERY", "value": "true"},
                          {"name": "PROCESSCHANGEEVENTS", "value": "true"},
                          {"name": "VCURL", "value": "vcenter-a.lab.local"}],
                  f"{label}: resourceIdentifiers are name-value pairs in call order",
                  f"saw {ids}")
            cred = body.get("credential")
            if check(isinstance(cred, dict), f"{label}: credential is an object", f"saw {cred!r}"):
                check(set(cred) == {"name", "adapterKindKey", "credentialKindKey", "fields"},
                      f"{label}: credential omits id and editable",
                      f"saw {sorted(cred)}")
                check(cred.get("fields") == [{"name": "USER", "value": "svc-vcfops@lab.local"},
                                             {"name": "PASSWORD", "value": "s3cr3t"}],
                      f"{label}: credential fields are name-value pairs in call order",
                      f"saw {cred.get('fields')}")

    r = results["full_onboarding"]
    check(r.get("precheckPassed") == "true" and r.get("precheckStatus") == "201",
          "full_onboarding: precheck reported as passed", str(r))
    check(r.get("created") == "true" and r.get("createStatus") == "201",
          "full_onboarding: creation reported as done", str(r))
    check(r.get("adapterInstanceId") == java_name_uuid("created:Prod VC Adapter Instance"),
          "full_onboarding: the created adapter instance id is read from the response",
          f"saw {r.get('adapterInstanceId')}")

    # --- minimal_onboarding: only the two required properties reach the wire --------------------
    reqs = log["minimal_onboarding"]
    if check(len(reqs) == 2, "minimal_onboarding: precheck then mutation", f"saw {len(reqs)} requests"):
        for req, label in zip(reqs, ("minimal_onboarding precheck", "minimal_onboarding mutation")):
            assert_common(req, label)
            body = body_of(req, label)
            check(body == {"name": "Bare VC Adapter Instance", "adapterKindKey": "VMWARE"},
                  f"{label}: body is exactly the two required properties", f"saw {body}")
            assert_no_placeholders(body, label)
            raw = req["body"]
            for absent in ("resourceIdentifiers", "credential", "description", "collectorId"):
                check(absent not in raw, f"{label}: '{absent}' does not appear in the raw body",
                      f"raw body was {raw}")
        check(reqs[1]["query"] == {"force": "false"},
              "minimal_onboarding: mutation sends force=false and nothing else",
              f"saw {reqs[1]['query']}")

    r = results["minimal_onboarding"]
    check(r.get("created") == "true"
          and r.get("adapterInstanceId") == java_name_uuid("created:Bare VC Adapter Instance"),
          "minimal_onboarding: creation reported as done", str(r))

    # --- credential_without_fields: an unset optional nested array is omitted -------------------
    reqs = log["credential_without_fields"]
    if check(len(reqs) == 2, "credential_without_fields: precheck then mutation",
             f"saw {len(reqs)} requests"):
        pre_body = body_of(reqs[0], "credential_without_fields precheck")
        mut_body = body_of(reqs[1], "credential_without_fields mutation")
        check(pre_body == mut_body,
              "credential_without_fields: both calls carry the same payload",
              f"precheck {pre_body} vs mutation {mut_body}")
        for req, body, label in (
                (reqs[0], pre_body, "credential_without_fields precheck"),
                (reqs[1], mut_body, "credential_without_fields mutation")):
            assert_common(req, label)
            check(set(body) == {"name", "adapterKindKey", "credential"},
                  f"{label}: top-level unset optional properties are absent",
                  f"saw {sorted(body)}")
            cred = body.get("credential")
            if check(isinstance(cred, dict), f"{label}: credential is an object", f"saw {cred!r}"):
                check(cred == {"name": "Empty Principal Credential",
                               "adapterKindKey": "VMWARE",
                               "credentialKindKey": "PRINCIPALCREDENTIAL"},
                      f"{label}: unset fields is absent and id/editable are never sent",
                      f"saw {cred}")
            assert_no_placeholders(body, label)
        check(reqs[0]["query"] == {},
              "credential_without_fields: precheck sends no query parameters",
              f"saw {reqs[0]['query']}")
        check(reqs[1]["query"] == {"force": "false"},
              "credential_without_fields: mutation sends force=false and nothing else",
              f"saw {reqs[1]['query']}")

    r = results["credential_without_fields"]
    check(r.get("created") == "true"
          and r.get("adapterInstanceId")
              == java_name_uuid("created:Credential-only VC Adapter Instance"),
          "credential_without_fields: creation reported as done", str(r))

    # --- precheck_blocks_mutation: the gate holds ------------------------------------------------
    reqs = log["precheck_blocks_mutation"]
    check(len(reqs) == 1, "precheck_blocks_mutation: the failed precheck is the only request issued",
          f"saw {len(reqs)}: " + ", ".join(f"{q['method']} {q['path']}" for q in reqs))
    check(all(q["path"] != CREATE_PATH for q in reqs),
          "precheck_blocks_mutation: nothing was created after the precheck failed",
          "a request reached " + CREATE_PATH)
    if reqs:
        assert_common(reqs[0], "precheck_blocks_mutation precheck")
        check(reqs[0]["operationId"] == "testConnection",
              "precheck_blocks_mutation: the request is the precheck",
              f"saw {reqs[0]['operationId']}")
        body = body_of(reqs[0], "precheck_blocks_mutation precheck")
        check(set(body) == {"name", "adapterKindKey", "description", "credential", "resourceIdentifiers"},
              "precheck_blocks_mutation: body carries exactly the properties the caller set",
              f"saw {sorted(body)}")
        assert_no_placeholders(body, "precheck_blocks_mutation precheck")
        check("collectorId" not in reqs[0]["body"],
              "precheck_blocks_mutation: unset collectorId does not appear in the raw body",
              f"raw body was {reqs[0]['body']}")

    r = results["precheck_blocks_mutation"]
    check(r.get("precheckPassed") == "false" and r.get("precheckStatus") == "400",
          "precheck_blocks_mutation: precheck reported as failed", str(r))
    check(r.get("created") == "false" and r.get("createStatus") == "0",
          "precheck_blocks_mutation: the mutation is reported as never attempted", str(r))
    check(r.get("adapterInstanceId") == "null",
          "precheck_blocks_mutation: no adapter instance id is reported", str(r))
    detail = r.get("details", {}).get("precheck", "")
    check(detail == "Unable to establish a connection to the data source at "
                    "vcenter-down.lab.local: connection timed out",
          "precheck_blocks_mutation: the service's explanation is surfaced verbatim",
          f"detail was {detail!r}")
    check(set(r.get("details", {})) == {"precheck"},
          "precheck_blocks_mutation: only precheck detail is reported", str(r))

    # --- identifier_defaults_requested: the optional query parameter is added --------------------
    reqs = log["identifier_defaults_requested"]
    if check(len(reqs) == 2, "identifier_defaults_requested: precheck then mutation",
             f"saw {len(reqs)} requests"):
        pre, mut = reqs
        assert_common(pre, "identifier_defaults_requested precheck")
        assert_common(mut, "identifier_defaults_requested mutation")
        check(pre["query"] == {},
              "identifier_defaults_requested: the precheck takes no query parameters",
              f"saw {pre['query']}")
        check(mut["query"] == {"extractIdentifierDefaults": "true", "force": "false"},
              "identifier_defaults_requested: mutation sends extractIdentifierDefaults=true and force=false",
              f"saw {mut['query']}")
        assert_no_placeholders(body_of(mut, "identifier_defaults_requested mutation"),
                               "identifier_defaults_requested mutation")

    r = results["identifier_defaults_requested"]
    check(r.get("created") == "true"
          and r.get("adapterInstanceId") == java_name_uuid("created:Edge VC Adapter Instance"),
          "identifier_defaults_requested: creation reported as done", str(r))

    # --- create_rejected: precheck passes but the mutation is refused ----------------------------
    reqs = log["create_rejected"]
    if check(len(reqs) == 2, "create_rejected: precheck then mutation", f"saw {len(reqs)} requests"):
        check(reqs[0]["responseStatus"] == 201 and reqs[1]["responseStatus"] == 400,
              "create_rejected: the precheck passed and the mutation was refused",
              f"saw {reqs[0]['responseStatus']} then {reqs[1]['responseStatus']}")

    r = results["create_rejected"]
    check(r.get("precheckPassed") == "true" and r.get("precheckStatus") == "201",
          "create_rejected: precheck reported as passed", str(r))
    check(r.get("created") == "false" and r.get("createStatus") == "400",
          "create_rejected: the refused mutation is reported as not created", str(r))
    check(r.get("adapterInstanceId") == "null",
          "create_rejected: no adapter instance id is reported", str(r))
    detail = r.get("details", {}).get("create", "")
    check(detail == "An adapter instance named 'Duplicate VC Adapter Instance' already exists",
          "create_rejected: the service's explanation is surfaced verbatim",
          f"detail was {detail!r}")
    check(set(r.get("details", {})) == {"create"},
          "create_rejected: only mutation detail is reported", str(r))

    # --- nothing outside the contract was touched -------------------------------------------------
    unmatched = [q for reqs in log.values() for q in reqs if q["operationId"] is None]
    check(not unmatched, "every request targeted an operation the contract names",
          ", ".join(f"{q['method']} {q['path']}" for q in unmatched))

    print(f"{checks - len(failures)}/{checks} checks passed")
    if failures:
        print()
        for f in failures:
            print("FAIL " + f)
        print("\nRESULT: FAIL")
        sys.exit(1)
    print("\nRESULT: PASS")


if __name__ == "__main__":
    main()
