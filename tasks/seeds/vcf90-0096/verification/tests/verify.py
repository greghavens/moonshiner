#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
CLIENT = ROOT / "src" / "VcfOperationsForLogsClient.java"
HARNESS = ROOT / "tests" / "TestMain.java"
MOCK = ROOT / "tests" / "mock_server.py"

EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
EXPECTED_IDS = [
    "POST_deployment-join",
    "POST_deployment-waitUntilStarted",
]
MASTER_FQDN = 'primary-9-0.\\"quoted\\".example.com'
SCENARIOS = {
    "success-retries": 4,
    "success-escaped": 2,
    "join-error": 1,
    "wait-error": 2,
}


def fail(message):
    raise AssertionError(message)


def verify_contract():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))

    for document in (contract["source"], sources):
        if document["tag"] != "9.0.0.0":
            fail("contract is not pinned to tag 9.0.0.0")
        if document["commitSha"] != EXPECTED_SHA:
            fail("unexpected tag commit SHA")
        if document["specPath"] != EXPECTED_SPEC:
            fail("unexpected specification path")
        if document["repository"] != "https://github.com/vmware/vcf-api-specs":
            fail("contract source must be the official specification repository")
        if document["license"] != "Apache-2.0":
            fail("unexpected specification license")

    operation_ids = [operation["operationId"] for operation in contract["operations"]]
    source_ids = [operation["operationId"] for operation in sources["operations"]]
    if operation_ids != EXPECTED_IDS or source_ids != EXPECTED_IDS:
        fail("operationIds are not the pinned 9.0 deployment operations")
    if contract["serverBasePath"] != "/api/v2":
        fail("incorrect server base path")

    join, wait = contract["operations"]
    if join["security"] != [] or wait["security"] != []:
        fail("bootstrap operations are unauthenticated in the pinned contract")
    if (join["method"], join["path"]) != ("POST", "/deployment/join"):
        fail("incorrect join wire contract")
    schema = join["requestBody"]["content"]["application/json"]["schema"]
    if schema["required"] != ["masterFQDN"]:
        fail("incorrect required join fields")
    if set(schema["properties"]) != {"masterFQDN", "masterPort", "acceptCert"}:
        fail("incorrect join request properties")
    if (wait["method"], wait["path"], wait["requestBody"]) != (
            "POST", "/deployment/waitUntilStarted", None):
        fail("incorrect wait operation contract")
    if set(wait["responses"]) != {"200", "500"}:
        fail("incorrect wait response contract")
    if set(join["responses"]) != {"200", "400", "403", "409", "495"}:
        fail("incorrect join response contract")
    response_schema = join["responses"]["200"]["content"]["application/json"]["schema"]
    expected_result_fields = {
        "masterAddress", "workerAddress", "workerPort", "workerToken", "masterUiPort"
    }
    if set(response_schema["properties"]) != expected_result_fields:
        fail("incorrect join response properties")
    if set(response_schema["required"]) != expected_result_fields:
        fail("incorrect required join response fields")


def wait_for_port(port_file, process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if port_file.exists():
            return int(port_file.read_text(encoding="ascii"))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited before startup: {stdout}\n{stderr}")
        time.sleep(0.01)
    fail("mock did not start")


def media_type(value):
    return value.split(";", 1)[0].strip().lower()


def verify_wire(scenario, entries):
    expected_count = SCENARIOS[scenario]
    if len(entries) != expected_count:
        fail(
            f"{scenario}: expected {expected_count} requests, got {len(entries)}"
        )

    join = entries[0]
    if (join["method"], join["path"], join["query"]) != (
            "POST", "/api/v2/deployment/join", ""):
        fail("join method/path/query mismatch")
    try:
        parsed = json.loads(join["body"])
    except json.JSONDecodeError as error:
        fail(f"join body is not valid JSON: {error}")
    if set(parsed) != {"masterFQDN"}:
        fail("unset masterPort/acceptCert must be omitted, not sent empty")
    if parsed["masterFQDN"] != MASTER_FQDN:
        fail("join masterFQDN value was not preserved through JSON encoding")
    if media_type(join["headers"].get("content-type", "")) != "application/json":
        fail("join Content-Type must be application/json")
    if "authorization" in join["headers"]:
        fail("bootstrap join operation must not invent authentication")

    if scenario == "join-error":
        return

    for index, poll in enumerate(entries[1:], start=1):
        if (poll["method"], poll["path"], poll["query"]) != (
                "POST", "/api/v2/deployment/waitUntilStarted", ""):
            fail(f"poll {index} method/path/query mismatch")
        if poll["bodyLength"] != 0 or poll["body"] != "":
            fail(f"poll {index} must be bodyless")
        if "content-type" in poll["headers"]:
            fail(f"poll {index} must omit Content-Type with its absent body")
        if "authorization" in poll["headers"]:
            fail("bootstrap wait operation must not invent authentication")


def run_scenario(temp, classes, scenario):
    request_log = temp / f"{scenario}-requests.jsonl"
    port_file = temp / f"{scenario}-port"
    mock = subprocess.Popen(
        [sys.executable, str(MOCK),
         "--contract", str(CONTRACT),
         "--request-log", str(request_log),
         "--port-file", str(port_file),
         "--scenario", scenario],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port = wait_for_port(port_file, mock)
        completed = subprocess.run(
            ["java", "-cp", str(classes), "TestMain",
             scenario, f"http://127.0.0.1:{port}", MASTER_FQDN],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if completed.returncode != 0:
            fail(
                f"TestMain failed for {scenario}:\n"
                f"{completed.stdout}\n{completed.stderr}"
            )
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=3)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.wait()

    if not request_log.exists():
        fail(f"{scenario}: mock received no requests")
    entries = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    verify_wire(scenario, entries)
    return completed.stdout.strip()


def main():
    verify_contract()
    with tempfile.TemporaryDirectory(prefix="vcf90-0096-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        subprocess.run(
            ["javac", "--release", "17", "-encoding", "UTF-8",
             "-d", str(classes), str(CLIENT), str(HARNESS)],
            cwd=ROOT,
            check=True,
        )

        for scenario in SCENARIOS:
            print(run_scenario(temp, classes, scenario))
        print("wire contract passed")


if __name__ == "__main__":
    main()
