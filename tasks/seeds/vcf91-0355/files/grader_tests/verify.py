#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "VcfAutomationClient.java"
TEST_MAIN = ROOT / "grader_tests" / "TestMain.java"
MOCK = ROOT / "grader_tests" / "mock_vcf.py"
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
REQUEST_ID = "22222222-2222-4222-8222-222222222291"
DEPLOYMENT_ID = "11111111-1111-4111-8111-111111111191"
EXPECTED_BODY = {
    "actionId": "Deployment.\"PowerOff\\safe\u2603",
    "inputs": {
        "force\"mode": "false\nwith-newline",
        "ticket\\id": "CHG-9100\tapproved",
    },
    "reason": "VCF 9.1 maintenance\nwindow",
}

SCENARIOS = {
    "terminal-APPROVAL_REJECTED": ["APPROVAL_REJECTED"],
    "terminal-ABORTED": ["ABORTED"],
    "terminal-SUCCESSFUL": ["PENDING", "INPROGRESS", "COMPLETION", "SUCCESSFUL"],
    "terminal-FAILED": ["FAILED"],
    # The submit response is already terminal, but only a later GET is authoritative.
    "submit-terminal": ["COMPLETION", "ABORTED"],
    "poll-error": None,
    "missing-deployment-poll": None,
    "missing-status-poll": None,
    "malformed-json-poll": None,
    "unknown-status": None,
    "interrupted": None,
    "submit-error": None,
    "missing-id-submit": None,
    "wrong-type-id-submit": None,
}


def fail(message):
    print("FAIL: " + message, file=sys.stderr)
    raise SystemExit(1)


def validate_documentation():
    try:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        source_record = json.loads(SOURCES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"documentation fixtures are unreadable: {error}")

    operation_ids = [item["operationId"] for item in contract.get("operations", [])]
    if operation_ids != ["submitDeploymentActionRequest", "getRequest"]:
        fail("contract operation set changed")
    if contract.get("mediaType") != "application/json":
        fail("contract media type changed")
    if contract.get("authentication") != {
            "type": "http", "scheme": "bearer", "header": "Authorization",
            "valueFormat": "Bearer {token}"}:
        fail("bearer authentication contract changed")
    operations = {item["operationId"]: item for item in contract["operations"]}
    if (operations["submitDeploymentActionRequest"].get("method") != "POST"
            or operations["submitDeploymentActionRequest"].get("pathTemplate")
            != "/deployment/api/deployments/{deploymentId}/requests"):
        fail("submission operation contract changed")
    if (operations["getRequest"].get("method") != "GET"
            or operations["getRequest"].get("pathTemplate")
            != "/deployment/api/requests/{requestId}"):
        fail("poll operation contract changed")
    if contract.get("sourceBasis", {}).get("kind") != "reference-documentation":
        fail("contract must identify its reference-documentation source")
    if contract.get("sourceBasis", {}).get("publishedSpecification") is not False:
        fail("contract must state that it is not a published specification")
    if contract.get("polling", {}).get("terminalStatuses") != [
            "APPROVAL_REJECTED", "ABORTED", "SUCCESSFUL", "FAILED"]:
        fail("terminal status contract changed")
    if contract.get("polling", {}).get("nonTerminalStatuses") != [
            "CREATED", "PENDING", "INITIALIZATION", "CHECKING_APPROVAL",
            "APPROVAL_PENDING", "USER_INTERACTION_PENDING", "INPROGRESS",
            "COMPLETION"]:
        fail("nonterminal status contract changed")

    sources = source_record.get("sources", [])
    if len(sources) != 2 or any(item.get("fetchedDate") != "2026-08-16" for item in sources):
        fail("official source provenance is incomplete")
    if {item.get("operation") for item in sources} != {
            "Submit Deployment Action Request", "Get Request"}:
        fail("official source operation mapping changed")
    if any(not item.get("url", "").startswith("https://developer.broadcom.com/xapis/")
           for item in sources):
        fail("official source URL is not a Broadcom xAPIs page")


def wait_for_port(port_file, process):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            fail("loopback mock exited during startup:\n" + stderr)
        if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
            return int(port_file.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("loopback mock did not publish its port")


def validate_exchange(scenario, records):
    if not records or records[0].get("method") != "POST":
        fail(f"{scenario}: deployment action was not submitted first")
    post = records[0]
    if post.get("path") != f"/deployment/api/deployments/{DEPLOYMENT_ID}/requests":
        fail(f"{scenario}: submission path does not contain the deployment ID")
    if post.get("authorization") != "Bearer test-token-91":
        fail(f"{scenario}: submission bearer authentication is missing or incorrect")
    if not str(post.get("contentType", "")).lower().startswith("application/json"):
        fail(f"{scenario}: submission content type is not application/json")
    if post.get("body") != EXPECTED_BODY:
        fail(f"{scenario}: submission JSON did not preserve the API arguments")

    polls = records[1:]
    if scenario in {"submit-error", "missing-id-submit", "wrong-type-id-submit"}:
        if polls:
            fail(f"{scenario}: client polled after a failed submission")
        return
    for poll in polls:
        if poll.get("method") != "GET":
            fail(f"{scenario}: only Get Request may be used while polling")
        if poll.get("path") != f"/deployment/api/requests/{REQUEST_ID}":
            fail(f"{scenario}: poll path does not contain the returned request ID")
        if poll.get("authorization") != "Bearer test-token-91":
            fail(f"{scenario}: poll bearer authentication is missing or incorrect")

    expected_statuses = SCENARIOS[scenario]
    if expected_statuses is not None:
        observed = [item.get("responseStatus") for item in polls]
        if observed != expected_statuses:
            fail(f"{scenario}: expected every poll status {expected_statuses!r}, got {observed!r}")
    elif scenario != "interrupted" and not polls:
        fail(f"{scenario}: error path was not reached through Get Request")


def run_scenario(classes, temp, scenario):
    port_file = temp / f"{scenario}.port"
    log_file = temp / f"{scenario}.jsonl"
    mock = subprocess.Popen(
        [sys.executable, str(MOCK), "--port-file", str(port_file),
         "--log-file", str(log_file), "--scenario", scenario],
        cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        port = wait_for_port(port_file, mock)
        try:
            result = subprocess.run(
                ["java", "-cp", str(classes), "TestMain",
                 f"http://127.0.0.1:{port}", scenario],
                cwd=ROOT, text=True, capture_output=True, timeout=8)
        except subprocess.TimeoutExpired:
            fail(f"{scenario}: client did not finish in the expected state")
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=3)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.wait()

    if result.returncode != 0:
        fail(f"{scenario}: TestMain failed:\n{result.stderr}")
    if scenario == "interrupted":
        expected_output = "INTERRUPTED"
    elif scenario.startswith("terminal-"):
        expected_output = ("RESULT|" + REQUEST_ID + "|" + DEPLOYMENT_ID + "|"
                           + scenario.removeprefix("terminal-"))
    elif scenario == "submit-terminal":
        expected_output = "RESULT|" + REQUEST_ID + "|" + DEPLOYMENT_ID + "|ABORTED"
    else:
        expected_output = "IOEXCEPTION"
    if result.stdout.strip() != expected_output:
        fail(f"{scenario}: unexpected output {result.stdout.strip()!r}")

    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    validate_exchange(scenario, records)


def run_tests():
    with tempfile.TemporaryDirectory(prefix="vcf91-0355-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            ["javac", "-d", str(classes), str(SOURCE), str(TEST_MAIN)],
            cwd=ROOT, text=True, capture_output=True)
        if compile_result.returncode != 0:
            fail("Java compilation failed:\n" + compile_result.stderr)
        for scenario in SCENARIOS:
            run_scenario(classes, temp, scenario)


def main():
    validate_documentation()
    run_tests()
    print("PASS: VCF Automation client satisfies the documented polling contract")


if __name__ == "__main__":
    main()
