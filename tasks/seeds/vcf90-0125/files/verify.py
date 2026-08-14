#!/usr/bin/env python3
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXPECTED_SOURCE = {
    "tag": "9.0.0.0",
    "commit_sha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
    "spec_path": "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml",
}
EXPECTED_OFFICIAL_SOURCES = {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "license": "Apache-2.0",
    "tag": "9.0.0.0",
    "commit_sha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
    "spec_path": "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml",
    "spec_sha256": "695d4a5a5f343609539c1ca02578803fcf8df7ed520cf66710c618ffef237cd6",
    "source_url": "https://raw.githubusercontent.com/vmware/vcf-api-specs/85151f6b1bb58f13b6ac0304bfec53904bea085f/specifications/vsan-data-protection/vsan-data-protection-openapi.yaml",
    "operationIds": [
        "Snapservice.Clusters.ProtectionGroups_create$Task",
        "Snapservice.Clusters.ProtectionGroups_get",
    ],
}
CREATE_ID = "Snapservice.Clusters.ProtectionGroups_create$Task"
GET_ID = "Snapservice.Clusters.ProtectionGroups_get"
CREATE_TARGET = "/api/snapservice/clusters/domain%20c8%2Fblue/protection-groups?vmw-task=true"
GET_TARGET = "/api/snapservice/clusters/domain%20c8%2Fblue/protection-groups/pg%2042%2Fblue"
EXPECTED_BODY = {
    "name": "Nightly \"critical\"\nset",
    "target_entities": {"vms": ["vm-101", "vm\\202"]},
}
PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def fail(message):
    raise AssertionError(message)


def canonical_target(target):
    return PERCENT_ESCAPE.sub(lambda match: match.group(0).upper(), target)


def check_contract():
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    if sources != EXPECTED_OFFICIAL_SOURCES:
        fail("official_sources.json does not exactly pin the tagged official specification")
    if contract["source"] != EXPECTED_SOURCE:
        fail("contract source is not pinned to the required 9.0 specification")
    for key, value in EXPECTED_SOURCE.items():
        if sources.get(key) != value:
            fail(f"official_sources.json has the wrong {key}")
    expected_ids = [CREATE_ID, GET_ID]
    if sources.get("operationIds") != expected_ids:
        fail("official_sources.json operationIds do not match the contract")
    operations = contract.get("operations", [])
    if [item.get("operationId") for item in operations] != expected_ids:
        fail("contract must contain exactly the two pinned operationIds")
    if [(item.get("method"), item.get("path")) for item in operations] != [
        ("POST", "/snapservice/clusters/{cluster}/protection-groups?vmw-task=true"),
        ("GET", "/snapservice/clusters/{cluster}/protection-groups/{pg}"),
    ]:
        fail("contract method/path data differs from the tagged specification")
    auth = contract.get("securitySchemes", {}).get("api_key_auth")
    if auth != {"type": "apiKey", "name": "vmware-api-session-id", "in": "header"}:
        fail("contract authentication header differs from the tagged specification")


def header_values(entry, wanted):
    result = []
    for name, value in entry["headers"]:
        if name.lower() == wanted.lower():
            result.append(value)
    return result


def assert_request(entry, method, target, operation_id, token, body):
    if entry["method"] != method:
        fail(f"expected {method}, got {entry['method']}")
    if canonical_target(entry["target"]) != canonical_target(target):
        fail(f"wrong request target: {entry['target']}")
    if entry["operationId"] != operation_id:
        fail(f"request did not resolve to {operation_id}")
    if method == "POST":
        try:
            document = json.loads(entry["body"])
        except json.JSONDecodeError as error:
            fail(f"create body is not valid JSON: {error}")
        if document != body:
            fail(f"wrong create body: {document!r}")
        if header_values(entry, "Content-Type") != ["application/json"]:
            fail("POST must use Content-Type: application/json")
    elif entry["body"] != "":
        fail("GET request must not have a body")

    if header_values(entry, "vmware-api-session-id") != [token]:
        fail(f"request must send exactly one session header with token {token!r}")


def check_source_does_not_embed_fixture_answers():
    source = (ROOT / "VsanDataProtectionClient.java").read_text(encoding="utf-8")
    for forbidden in ("access-old", "access-new", "pg 42/blue", "Nightly \\\"critical\\\""):
        if forbidden in source:
            fail(f"client embeds fixture value {forbidden!r}")


def run():
    check_contract()
    check_source_does_not_embed_fixture_answers()
    with tempfile.TemporaryDirectory(prefix="vcf90-vsan-dp-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac", "-J-XX:-UsePerfData", "--release", "17", "-d", str(classes),
                str(ROOT / "VsanDataProtectionClient.java"),
                str(ROOT / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        for scenario in ("create-401", "read-401"):
            run_scenario(temp, classes, scenario)


def run_scenario(temp, classes, scenario):
    port_file = temp / f"{scenario}.port"
    log_file = temp / f"{scenario}.jsonl"
    server = subprocess.Popen(
        [
            sys.executable, str(ROOT / "mock_vsan_dp.py"),
            "--port-file", str(port_file),
            "--log-file", str(log_file),
            "--scenario", scenario,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not port_file.exists() and time.monotonic() < deadline:
            if server.poll() is not None:
                stdout, stderr = server.communicate()
                fail("mock exited before startup:\n" + stdout + stderr)
            time.sleep(0.01)
        if not port_file.exists():
            fail("mock did not publish its loopback port")
        port = int(port_file.read_text(encoding="ascii"))
        base_uri = f"http://127.0.0.1:{port}/api"
        result = subprocess.run(
            ["java", "-XX:-UsePerfData", "-cp", str(classes), "TestMain", base_uri],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            fail(f"TestMain failed in {scenario}:\n" + result.stdout + result.stderr)
        if result.stdout != "PASS\n":
            fail(f"unexpected TestMain output in {scenario}: {result.stdout!r}")
    finally:
        server.terminate()
        try:
            server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate()

    entries = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    if len(entries) != 3:
        fail(f"expected exactly 3 HTTP requests in {scenario}, got {len(entries)}")
    if scenario == "create-401":
        expected = [
            ("POST", CREATE_TARGET, CREATE_ID, "access-old", EXPECTED_BODY),
            ("POST", CREATE_TARGET, CREATE_ID, "access-new", EXPECTED_BODY),
            ("GET", GET_TARGET, GET_ID, "access-new", ""),
        ]
    else:
        expected = [
            ("POST", CREATE_TARGET, CREATE_ID, "access-old", EXPECTED_BODY),
            ("GET", GET_TARGET, GET_ID, "access-old", ""),
            ("GET", GET_TARGET, GET_ID, "access-new", ""),
        ]
    for entry, request in zip(entries, expected):
        assert_request(entry, *request)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
    print("verification passed")
