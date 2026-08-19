#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OPERATION = "PUT_notification-webhook"
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
EXPECTED_URLS = [
    "https://hooks.example.com/vcf-alerts",
    "https://backup.example.com/vcf-alerts?source=operations&severity=warning",
]
ALTERNATE_URLS = [
    "https://z.example.net/one?raw=\"quoted\"\\tail",
    "https://a.example.net/two?value=backslash%5Ctail",
    "https://middle.example.net/three#fragment",
]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify_provenance_and_contract():
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))

    require(contract["version"] == "9.0.0.0", "contract must stay pinned to VCF 9.0")
    require(contract["serverBasePath"] == "/api/v2", "wrong API base path")
    require(contract["operationIds"] == [EXPECTED_OPERATION], "wrong operationIds list")
    require(set(contract["operations"]) == {EXPECTED_OPERATION}, "mock contract must name one operation")
    operation = contract["operations"][EXPECTED_OPERATION]
    require(operation["method"] == "PUT", "wrong HTTP method in contract")
    require(operation["path"] == "/notification/webhook", "wrong operation path in contract")
    require(operation["security"] == {
        "type": "http", "scheme": "Bearer", "header": "Authorization"
    }, "wrong bearer security contract")
    require(operation["requestBody"]["contentType"] == "application/json", "wrong request media type")
    require(operation["requestBody"]["schema"]["required"] == [], "9.0 schema has no required properties")
    require(set(operation["requestBody"]["schema"]["properties"]) == {
        "proxyId", "URLs", "destinationApp", "contentType", "payload", "name",
        "headers", "acceptCert", "sendIndividualLogs"
    }, "request properties differ from the pinned 9.0 specification")
    require(set(operation["responses"]) == {"200", "400", "401", "440", "495", "500"},
            "response statuses differ from the pinned 9.0 specification")

    require(sources["tag"] == "9.0.0.0", "official source tag must be 9.0.0.0")
    require(sources["commitSha"] == EXPECTED_SHA, "wrong 9.0.0.0 tag commit SHA")
    require(sources["specPath"] == EXPECTED_SPEC_PATH, "wrong official specification path")
    require(sources["operationIds"] == [EXPECTED_OPERATION], "official source must record every operationId")
    require(sources["license"] == "Apache-2.0", "wrong source license")
    return contract


def run_case(classes, temporary_path, contract, case):
    request_log = temporary_path / f"{case['server_mode']}.jsonl"
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "tests" / "mock_server.py"),
         str(request_log), case["server_mode"]],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        startup = server.stdout.readline()
        if startup == "":
            # Read stderr only when there is a failure to explain: the mock
            # server outlives this call, so an eager read never returns.
            require(False, "mock server did not report a port: "
                    + server.stderr.read())
        port = json.loads(startup)["port"]
        base_uri = f"http://127.0.0.1:{port}"
        if case["trailing_slash"]:
            base_uri += "/"
        run_result = subprocess.run(
            ["java", "-cp", str(classes), "TestMain", base_uri, case["client_mode"]],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
        )
        require(run_result.returncode == 0,
                f"TestMain failed in {case['server_mode']} scenario:\n"
                + run_result.stdout + run_result.stderr)
        require(run_result.stdout.strip() == case["expected_output"],
                f"unexpected TestMain output in {case['server_mode']} scenario")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    records = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]
    require(len(records) == len(case["statuses"]),
            f"{case['server_mode']} scenario sent the wrong number of requests")

    operation = contract["operations"][EXPECTED_OPERATION]
    expected_target = contract["serverBasePath"] + operation["path"]
    for index, record in enumerate(records, start=1):
        require(record["operationId"] == EXPECTED_OPERATION, f"request {index} used wrong operationId")
        require(record["method"] == operation["method"], f"request {index} used wrong method")
        require(record["target"] == expected_target, f"request {index} used wrong path or query")
        require(record["headers"].get("authorization") == "Bearer " + case["session_id"],
                f"request {index} used wrong Authorization header")
        content_type = record["headers"].get("content-type", "").split(";", 1)[0].strip().lower()
        require(content_type == "application/json", f"request {index} used wrong Content-Type")
        require(record["headers"].get("accept") == "application/json",
                f"request {index} used wrong Accept header")
        parsed_body = json.loads(record["body"])
        require(parsed_body == {"URLs": case["urls"]},
                f"request {index} changed URL order or sent unset optional properties")
        require(record["mutationCount"] == case["mutation_counts"][index - 1],
                f"request {index} caused the wrong number of logical mutations")
        require(record["status"] == case["statuses"][index - 1],
                f"request {index} received the wrong mock status")

    if len(records) == 2:
        require(records[0]["body"] == records[1]["body"], "retry body changed")
        require(records[0]["target"] == records[1]["target"], "retry target changed")


def run_test(contract):
    with tempfile.TemporaryDirectory(prefix="vcf-logs-client-") as temporary:
        temporary_path = Path(temporary)
        classes = temporary_path / "classes"
        classes.mkdir()

        compile_result = subprocess.run(
            ["javac", "--release", "17", "-encoding", "UTF-8", "-d", str(classes),
             str(ROOT / "src" / "VcfOperationsForLogsClient.java"),
             str(ROOT / "tests" / "TestMain.java")],
            cwd=ROOT, text=True, capture_output=True,
        )
        require(compile_result.returncode == 0, "javac failed:\n" + compile_result.stderr)
        cases = [
            {
                "server_mode": "retry",
                "client_mode": "retry",
                "trailing_slash": False,
                "session_id": "session-test-token+/=",
                "urls": EXPECTED_URLS,
                "statuses": [500, 200],
                "mutation_counts": [1, 1],
                "expected_output": "webhook URLs replaced",
            },
            {
                "server_mode": "direct",
                "client_mode": "direct",
                "trailing_slash": True,
                "session_id": "alternate-session-token",
                "urls": ALTERNATE_URLS,
                "statuses": [200],
                "mutation_counts": [1],
                "expected_output": "webhook URLs replaced",
            },
            {
                "server_mode": "bad-request",
                "client_mode": "failure",
                "trailing_slash": False,
                "session_id": "session-test-token+/=",
                "urls": EXPECTED_URLS,
                "statuses": [400],
                "mutation_counts": [0],
                "expected_output": "request failed with IOException",
            },
            {
                "server_mode": "persistent-500",
                "client_mode": "failure",
                "trailing_slash": False,
                "session_id": "session-test-token+/=",
                "urls": EXPECTED_URLS,
                "statuses": [500, 500],
                "mutation_counts": [1, 1],
                "expected_output": "request failed with IOException",
            },
        ]
        for case in cases:
            run_case(classes, temporary_path, contract, case)


def main():
    require(shutil.which("javac") is not None, "javac is required")
    require(shutil.which("java") is not None, "java is required")
    contract = verify_provenance_and_contract()
    run_test(contract)
    print("PASS: exact VCF Operations for Logs 9.0 retry wire contract verified")


if __name__ == "__main__":
    main()
