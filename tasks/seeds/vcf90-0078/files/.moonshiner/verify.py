#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
CLIENT = ROOT / "VcfOperationsClient.java"
TEST_MAIN = ROOT / "tests" / "TestMain.java"
MOCK = ROOT / "tests" / "mock_vcf_operations.py"
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_TAG = "9.0.0.0"
EXPECTED_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
EXPECTED_OPERATION_ID = "getSymptomDefinitions"


def fail(message):
    print(f"VERIFY_FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_contract():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if sources["repository_tag"] != EXPECTED_TAG:
        fail("official source tag changed")
    if sources["repository_commit_sha"] != EXPECTED_COMMIT:
        fail("official source commit changed")
    if sources["spec_path"] != EXPECTED_PATH:
        fail("official source path changed")
    if sources["operationIds"] != [EXPECTED_OPERATION_ID]:
        fail("official source operationIds changed")
    if len(sources.get("operations", [])) != 1:
        fail("per-operation provenance changed")
    provenance = sources["operations"][0]
    if provenance != {
        "operationId": EXPECTED_OPERATION_ID,
        "spec_path": EXPECTED_PATH,
        "repository_tag": EXPECTED_TAG,
        "repository_commit_sha": EXPECTED_COMMIT,
    }:
        fail("operation provenance is incomplete")

    if contract["source"]["tag"] != EXPECTED_TAG:
        fail("contract source tag changed")
    if contract["source"]["commit_sha"] != EXPECTED_COMMIT:
        fail("contract source commit changed")
    if contract["source"]["spec_path"] != EXPECTED_PATH:
        fail("contract source path changed")
    operations = {item["operationId"]: item for item in contract["operations"]}
    if set(operations) != {EXPECTED_OPERATION_ID}:
        fail("contract must name exactly getSymptomDefinitions")
    operation = operations[EXPECTED_OPERATION_ID]
    if operation["method"] != "GET" or operation["path"] != "/api/symptomdefinitions":
        fail("getSymptomDefinitions method or path changed")
    expected_parameters = [
        ("adapterKind", False),
        ("resourceKind", False),
        ("id", False),
        ("name", False),
        ("page", False),
        ("pageSize", False),
    ]
    actual_parameters = [
        (item["name"], item["required"]) for item in operation["parameters"]
    ]
    if actual_parameters != expected_parameters:
        fail("getSymptomDefinitions query contract changed")
    if contract["base_path"] != "/suite-api":
        fail("VCF Operations base path changed")
    if contract["security"] != {
        "scheme": "Token-based-authorization",
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
    }:
        fail("authorization contract changed")
    return contract


def read_requests(log_path):
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def verify_wire_shape(contract, requests, expected_pages):
    if len(requests) != len(expected_pages):
        fail(
            f"expected {len(expected_pages)} page requests, got {len(requests)}"
        )
    operation = contract["operations"][0]
    path = contract["base_path"] + operation["path"]
    optional_names = {"adapterKind", "resourceKind", "id", "name"}
    for page, request in zip(expected_pages, requests):
        expected_target = f"{path}?page={page}&pageSize=2"
        if request["operationId"] != EXPECTED_OPERATION_ID:
            fail(f"request {page} used an operation outside the contract")
        if request["method"] != "GET" or request["target"] != expected_target:
            fail(
                f"page {page} has wrong exact request target: "
                f"{request['method']} {request['target']}"
            )
        if request["body"] != "":
            fail(f"page {page} GET unexpectedly sent a body")
        headers = request["headers"]
        if headers.get("authorization") != "vcf-ops-test-token":
            fail(f"page {page} Authorization header is missing or changed")
        if headers.get("accept") != "application/json":
            fail(f"page {page} Accept header must be application/json")
        if "content-type" in headers:
            fail(f"page {page} bodyless GET unexpectedly sent Content-Type")
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(request["target"]).query,
            keep_blank_values=True,
        )
        expected_query = {"page": [str(page)], "pageSize": ["2"]}
        if query != expected_query:
            fail(f"page {page} sent malformed or extra query fields: {query}")
        leaked = optional_names.intersection(query)
        if leaked:
            fail(f"unset optional query fields were serialized: {sorted(leaked)}")


def main():
    contract = validate_contract()
    if shutil.which("javac") is None or shutil.which("java") is None:
        fail("Java compiler/runtime not found")

    with tempfile.TemporaryDirectory(prefix="vcf90-0078-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                str(CLIENT),
                str(TEST_MAIN),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        scenarios = {
            "complete": [0, 1, 2],
            "http-error-page-1": [0, 1],
            "malformed-json": [0],
            "missing-item-field": [0],
            "wrong-total-type": [0],
            "premature-empty-page": [0, 1],
        }
        for scenario, expected_pages in scenarios.items():
            run_scenario(contract, classes, temp, scenario, expected_pages)

    print("VERIFY_OK: complete stable VCF Operations pagination and wire contract passed")


def run_scenario(contract, classes, temp, scenario, expected_pages):
    request_log = temp / f"requests-{scenario}.jsonl"
    request_log.touch()
    mock = subprocess.Popen(
        [
            sys.executable,
            str(MOCK),
            "--contract",
            str(CONTRACT),
            "--log",
            str(request_log),
            "--scenario",
            scenario,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port_line = mock.stdout.readline().strip()
        if not port_line.isdigit():
            stderr = mock.stderr.read()
            fail(f"mock did not start: {port_line} {stderr}")
        base_uri = f"http://127.0.0.1:{port_line}"
        run_result = subprocess.run(
            [
                "java",
                "-cp",
                str(classes),
                "TestMain",
                base_uri,
                str(request_log),
                scenario,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if run_result.returncode != 0:
            fail("TestMain failed:\n" + run_result.stdout + run_result.stderr)
        if run_result.stdout.strip() != "TEST_MAIN_OK":
            fail(f"unexpected TestMain output: {run_result.stdout!r}")
        verify_wire_shape(
            contract,
            read_requests(request_log),
            expected_pages,
        )
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=3)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.wait(timeout=3)


if __name__ == "__main__":
    main()
