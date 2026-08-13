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
CLIENT = ROOT / "src" / "VcfLogClient.java"
HARNESS = ROOT / "test" / "TestMain.java"
MOCK = ROOT / "test" / "mock_server.py"

EXPECTED_OPERATION = (
    "getAllAgentGroupConfig", "GET", "/api/v2/agent/groups")
EXPECTED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"


def fail(message):
    raise AssertionError(message)


def assert_contract_provenance():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if contract.get("openapi") != "3.0.1":
        fail("contract is not OpenAPI 3.0.1")
    if contract.get("info", {}).get("version") != "9.1.0.0":
        fail("contract is not the pinned VCF 9.1 Log Management contract")
    if sources.get("repository") != "https://github.com/vmware/vcf-api-specs":
        fail("wrong official source repository")
    if sources.get("repository_commit_sha") != EXPECTED_COMMIT:
        fail("official source commit is not pinned")
    if sources.get("license") != "Apache-2.0":
        fail("official source license is not recorded")
    if sources.get("spec_path") != "specifications/vcf-operations/log-management-openapi.json":
        fail("wrong official specification path")

    recorded = [
        (entry["operationId"], entry["method"], entry["path"])
        for entry in sources.get("operationIds", [])
    ]
    if recorded != [EXPECTED_OPERATION]:
        fail(f"official operationId inventory differs: {recorded!r}")

    selected = []
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                selected.append((operation["operationId"], method.upper(), path))
    if selected != [EXPECTED_OPERATION]:
        fail(f"contract must contain only the named operation: {selected!r}")

    operation = contract["paths"][EXPECTED_OPERATION[2]]["get"]
    parameters = operation.get("parameters", [])
    expected_parameter = {
        "in": "query",
        "name": "pageable",
        "required": True,
        "schema": {"$ref": "#/components/schemas/Pageable"},
    }
    if parameters != [expected_parameter]:
        fail(f"pageable parameter differs from the pinned projection: {parameters!r}")
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    if response_schema != {
        "type": "array", "items": {"$ref": "#/components/schemas/Page"}
    }:
        fail("success response must remain the specification's array of Page")
    pageable = contract["components"]["schemas"]["Pageable"]
    if list(pageable.get("properties", {})) != ["page", "size", "sort"]:
        fail("Pageable fields differ from the pinned specification")
    if pageable["properties"]["page"].get("minimum") != 0:
        fail("Pageable.page minimum differs from the pinned specification")
    if pageable["properties"]["size"].get("minimum") != 1:
        fail("Pageable.size minimum differs from the pinned specification")
    page_fields = contract["components"]["schemas"]["Page"].get("properties", {})
    for required_projection_field in ("content", "last", "number", "numberOfElements"):
        if required_projection_field not in page_fields:
            fail(f"Page projection lacks {required_projection_field}")
    security = contract["components"]["securitySchemes"]["OPSTokenAuthorization"]
    if (security.get("type"), security.get("in"), security.get("name")) != (
            "apiKey", "header", "X-JWT-Token"):
        fail("pinned authentication contract differs")


def assert_headers(entry):
    headers = entry["headers"]
    if headers.get("x-jwt-token") != "integration-jwt-91":
        fail("missing or incorrect X-JWT-Token")
    if headers.get("accept") != "application/json":
        fail("Accept must be exactly application/json")
    if "content-type" in headers:
        fail(f"GET must omit Content-Type, got {headers['content-type']!r}")


def assert_wire_shape(entries):
    expected_targets = [
        "/api/v2/agent/groups?page=0&size=2",
        "/api/v2/agent/groups?page=1&size=2",
        "/api/v2/agent/groups?page=2&size=2",
    ]
    if len(entries) != len(expected_targets):
        fail(f"expected exactly three page requests, got {len(entries)}")

    for index, (entry, target) in enumerate(zip(entries, expected_targets)):
        expected = ("getAllAgentGroupConfig", "GET", target)
        actual = (entry["operationId"], entry["method"], entry["target"])
        if actual != expected:
            fail(f"request {index} wire target differs: {actual!r}")
        if entry["path"] != "/api/v2/agent/groups":
            fail(f"request {index} used the wrong path")
        if entry["query"] != f"page={index}&size=2":
            fail(f"request {index} did not use form/explode Pageable serialization")
        if "sort" in entry["query"] or "pageable" in entry["query"]:
            fail("unset optional sort must be omitted rather than sent empty")
        if entry["body"] != "":
            fail(f"GET request {index} must have no body")
        assert_headers(entry)


def main():
    assert_contract_provenance()
    production_sources = sorted(path.name for path in (ROOT / "src").glob("*.java"))
    if production_sources != ["VcfLogClient.java"]:
        fail(f"production client must remain one Java file: {production_sources!r}")

    with tempfile.TemporaryDirectory(prefix="vcf-agent-groups-") as temp_dir:
        temp = Path(temp_dir)
        classes = temp / "classes"
        classes.mkdir()
        request_log = temp / "requests.jsonl"
        port_file = temp / "port"

        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", str(classes), str(CLIENT), str(HARNESS)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("javac failed")

        server = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract", str(CONTRACT),
                "--request-log", str(request_log),
                "--port-file", str(port_file),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 3
            while not port_file.exists() and time.monotonic() < deadline:
                if server.poll() is not None:
                    stdout, stderr = server.communicate()
                    fail(f"mock server exited early:\n{stdout}{stderr}")
                time.sleep(0.01)
            if not port_file.exists():
                fail("mock server did not publish its loopback port")

            port = int(port_file.read_text(encoding="ascii"))
            run_result = subprocess.run(
                ["java", "-cp", str(classes), "TestMain", f"http://127.0.0.1:{port}"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
            )
            if run_result.returncode != 0:
                sys.stderr.write(run_result.stdout + run_result.stderr)
                fail("TestMain failed")
        finally:
            server.terminate()
            try:
                server.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate()

        entries = [
            json.loads(line)
            for line in request_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert_wire_shape(entries)

    print("PASS: VCF Log Management agent groups were completely and stably collected")


if __name__ == "__main__":
    main()
