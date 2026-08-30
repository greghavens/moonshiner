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

EXPECTED_OPERATIONS = [
    ("createAgentSecret", "POST", "/api/v2/agent/secrets"),
    ("listAgentSecrets", "GET", "/api/v2/agent/secrets"),
    ("createAgentSession", "POST", "/api/v2/agent/secrets/exchange"),
]
EXPECTED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SECRET_NAME = 'edge "collector" \\ west'


def fail(message):
    raise AssertionError(message)


def assert_contract_provenance():
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if contract.get("openapi") != "3.0.1" or contract.get("info", {}).get("version") != "9.1.0.0":
        fail("contract is not the pinned VCF 9.1 OpenAPI contract")
    if sources.get("repository_commit_sha") != EXPECTED_COMMIT:
        fail("official source commit is not pinned")
    if sources.get("spec_path") != "specifications/vcf-operations/log-management-openapi.json":
        fail("wrong official specification path")

    recorded = [
        (entry["operationId"], entry["method"], entry["path"])
        for entry in sources.get("operationIds", [])
    ]
    if recorded != EXPECTED_OPERATIONS:
        fail(f"official operationId inventory differs: {recorded!r}")

    selected = []
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                selected.append((operation["operationId"], method.upper(), path))
    if sorted(selected) != sorted(EXPECTED_OPERATIONS):
        fail(f"contract must contain only the named operations: {selected!r}")


def assert_headers(entry, has_body):
    headers = entry["headers"]
    if headers.get("x-jwt-token") != "integration-jwt-91":
        fail(f"missing or incorrect X-JWT-Token on {entry['operationId']}")
    if headers.get("accept") != "application/json":
        fail(f"Accept must be application/json on {entry['operationId']}")
    content_type = headers.get("content-type")
    if has_body and content_type != "application/json":
        fail(f"Content-Type must be application/json on {entry['operationId']}")
    if not has_body and content_type is not None:
        fail(f"GET must not send a Content-Type header, got {content_type!r}")


def assert_wire_shape(entries):
    if len(entries) != 4:
        fail(f"expected create, two polls, and exchange; got {len(entries)} requests")

    expected_sequence = [
        ("createAgentSecret", "POST", "/api/v2/agent/secrets"),
        ("listAgentSecrets", "GET", "/api/v2/agent/secrets?page=0&size=50"),
        ("listAgentSecrets", "GET", "/api/v2/agent/secrets?page=0&size=50"),
        ("createAgentSession", "POST", "/api/v2/agent/secrets/exchange"),
    ]
    actual_sequence = [
        (entry["operationId"], entry["method"], entry["target"])
        for entry in entries
    ]
    if actual_sequence != expected_sequence:
        fail(f"wrong request sequence or target: {actual_sequence!r}")

    expected_create = json.dumps({"name": SECRET_NAME}, separators=(",", ":"))
    if entries[0]["body"] != expected_create:
        fail(f"createAgentSecret body differs on the wire: {entries[0]['body']!r}")
    assert_headers(entries[0], True)

    for poll in entries[1:3]:
        if poll["query"] != "page=0&size=50" or poll["body"] != "":
            fail(f"poll must use exploded pageable query and no body: {poll!r}")
        assert_headers(poll, False)

    expected_exchange = json.dumps({"secret": "secret-once-42"}, separators=(",", ":"))
    exchange = entries[3]
    if exchange["body"] != expected_exchange:
        fail(f"createAgentSession body differs on the wire: {exchange['body']!r}")
    parsed_exchange = json.loads(exchange["body"])
    if set(parsed_exchange) != {"secret"}:
        fail("unset optional ttl must be omitted, not serialized empty")
    assert_headers(exchange, True)


def main():
    assert_contract_provenance()
    production_sources = sorted(path.name for path in (ROOT / "src").glob("*.java"))
    if production_sources != ["VcfLogClient.java"]:
        fail(f"production client must remain a single Java file: {production_sources!r}")

    with tempfile.TemporaryDirectory(prefix="vcf-log-client-") as temp_dir:
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
                "--contract",
                str(CONTRACT),
                "--request-log",
                str(request_log),
                "--port-file",
                str(port_file),
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

    print("PASS: VCF Log Management client follows the pinned async wire contract")


if __name__ == "__main__":
    main()
