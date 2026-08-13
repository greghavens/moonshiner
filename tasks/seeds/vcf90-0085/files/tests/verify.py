#!/usr/bin/env python3
"""Protected deterministic verification for the VCF Operations for Logs seed."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SHA256 = "7ab3ba88ee59d2a18767f9062f0f4c80a48d1cfc4d91a2d8705dd90641a278e8"
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"


def fail(message: str) -> None:
    raise AssertionError(message)


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return int(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited before readiness\nstdout: {stdout}\nstderr: {stderr}")
        time.sleep(0.05)
    fail("mock did not become ready")


def verify_contract() -> None:
    contract_path = ROOT / "docs" / "contract.json"
    actual_hash = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    if actual_hash != CONTRACT_SHA256:
        fail("docs/contract.json was changed from the protected VCF 9.0 contract slice")

    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    operations = {
        value["operationId"]: (method.upper(), path)
        for path, path_item in contract["paths"].items()
        for method, value in path_item.items()
        if isinstance(value, dict) and "operationId" in value
    }
    expected = {
        "POST_sessions": ("POST", "/sessions"),
        "PUT_log-forwarder-id": ("PUT", "/log-forwarder/{id}"),
    }
    if operations != expected:
        fail(f"contract operations differ: {operations!r}")

    put_schema = contract["components"]["schemas"]["forwarders.put.request"]
    if put_schema["required"] != ["host", "port", "protocol", "sslEnabled"]:
        fail("forwarders.put.request required fields differ from the 9.0 specification")

    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    if sources.get("tag") != "9.0.0.0":
        fail("official source tag must be 9.0.0.0")
    if sources.get("commitSha") != EXPECTED_COMMIT:
        fail("official source commit SHA is not the 9.0.0.0 tag commit")
    if sources.get("specificationPath") != EXPECTED_SPEC:
        fail("official source specification path differs")
    recorded = [item.get("operationId") for item in sources.get("operations", [])]
    if recorded != ["POST_sessions", "PUT_log-forwarder-id"]:
        fail("official_sources.json must record each selected operationId")


def verify_manifest() -> None:
    manifest = (ROOT / "src" / "VcfOpsLogs.psd1").read_text(encoding="utf-8")
    if "VMware.Sdk.Vcf.Ops" not in manifest:
        fail("module manifest must keep VMware.Sdk.Vcf.Ops as a prerequisite")
    runner = (ROOT / "scripts" / "Invoke-ForwarderRollout.ps1").read_text(encoding="utf-8")
    if (
        "VcfOpsLogs.psd1" not in runner
        or "VcfOpsLogs.psm1" in runner
        or "Invoke-VcfOpsLogsForwarderRollout" not in runner
    ):
        fail("scenario runner must import the module manifest so prerequisites are loaded")
    forbidden = [
        candidate
        for candidate in ROOT.rglob("*")
        if candidate.name == "VMware.Sdk.Vcf.Ops"
        or candidate.name.startswith("VMware.Sdk.Vcf.Ops.")
    ]
    if forbidden:
        fail(f"VMware prerequisite was vendored: {forbidden}")


def run_scenario() -> tuple[list[dict], dict]:
    with tempfile.TemporaryDirectory(prefix="vcf-logs-verify-") as temp_name:
        temp = Path(temp_name)
        ready = temp / "ready"
        request_log = temp / "requests.jsonl"
        outcome = temp / "outcome.json"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "mock" / "vcf_logs_mock.py"),
                "--contract",
                str(ROOT / "docs" / "contract.json"),
                "--request-log",
                str(request_log),
                "--ready-file",
                str(ready),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_ready(ready, mock)
            process_env = {
                **os.environ,
                "NO_PROXY": "127.0.0.1,localhost",
            }
            command = [
                shutil.which("pwsh") or "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(ROOT / "scripts" / "Invoke-ForwarderRollout.ps1"),
                "-ServerUri",
                f"http://127.0.0.1:{port}",
                "-ScenarioPath",
                str(ROOT / "scenario" / "forwarder-rollout.json"),
                "-OutputPath",
                str(outcome),
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                env=process_env,
            )
            if completed.returncode != 0:
                fail(
                    "scenario runner failed\n"
                    f"stdout: {completed.stdout}\n"
                    f"stderr: {completed.stderr}"
                )
            if not outcome.exists():
                fail("scenario runner did not write the requested output file")
            report = json.loads(outcome.read_text(encoding="utf-8"))

            error_outcome = temp / "error-outcome.json"
            error_command = command.copy()
            error_command[error_command.index("-ScenarioPath") + 1] = str(
                temp / "missing-scenario.json"
            )
            error_command[error_command.index("-OutputPath") + 1] = str(error_outcome)
            failed_run = subprocess.run(
                error_command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                env=process_env,
            )
            if failed_run.returncode == 0:
                fail("runner unexpectedly succeeded with a missing scenario")
            if not error_outcome.exists():
                fail("runner did not write a report after a terminating error")
            error_report = json.loads(error_outcome.read_text(encoding="utf-8"))
            if error_report != {"OverallStatus": "Failed", "Steps": []}:
                fail(f"runner wrote an invalid error report: {error_report!r}")

            lines = request_log.read_text(encoding="utf-8").splitlines()
            requests = [json.loads(line) for line in lines if line.strip()]
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=3)

        scenario = json.loads(
            (ROOT / "scenario" / "forwarder-rollout.json").read_text(encoding="utf-8")
        )
        success_scenario = temp / "success-scenario.json"
        success_scenario.write_text(
            json.dumps(
                {
                    "credentials": scenario["credentials"],
                    "updates": [scenario["updates"][0]],
                }
            ),
            encoding="utf-8",
        )
        success_ready = temp / "success-ready"
        success_log = temp / "success-requests.jsonl"
        success_outcome = temp / "success-outcome.json"
        success_mock = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "mock" / "vcf_logs_mock.py"),
                "--contract",
                str(ROOT / "docs" / "contract.json"),
                "--request-log",
                str(success_log),
                "--ready-file",
                str(success_ready),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            success_port = wait_for_ready(success_ready, success_mock)
            direct_script = temp / "invoke-module-directly.ps1"
            direct_script.write_text(
                "[CmdletBinding()]\n"
                "param([uri] $ServerUri, [string] $ScenarioPath, "
                "[string] $OutputPath, [string] $ModulePath)\n"
                "$ErrorActionPreference = 'Stop'\n"
                "Import-Module $ModulePath -Force\n"
                "$scenario = Get-Content -LiteralPath $ScenarioPath -Raw | "
                "ConvertFrom-Json -Depth 20\n"
                "$password = ConvertTo-SecureString $scenario.credentials.password "
                "-AsPlainText -Force\n"
                "$credential = [pscredential]::new($scenario.credentials.username, $password)\n"
                "$report = Invoke-VcfOpsLogsForwarderRollout -ServerUri $ServerUri "
                "-Credential $credential -Provider $scenario.credentials.provider "
                "-Updates @($scenario.updates)\n"
                "$report | ConvertTo-Json -Depth 20 | "
                "Set-Content -LiteralPath $OutputPath -Encoding utf8\n",
                encoding="utf-8",
            )
            direct_command = [
                shutil.which("pwsh") or "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(direct_script),
                "-ServerUri",
                f"http://127.0.0.1:{success_port}",
                "-ScenarioPath",
                str(success_scenario),
                "-OutputPath",
                str(success_outcome),
                "-ModulePath",
                str(ROOT / "src" / "VcfOpsLogs.psd1"),
            ]
            direct_run = subprocess.run(
                direct_command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                env=process_env,
            )
            if direct_run.returncode != 0:
                fail(
                    "direct module success scenario failed\n"
                    f"stdout: {direct_run.stdout}\n"
                    f"stderr: {direct_run.stderr}"
                )
            success_report = json.loads(success_outcome.read_text(encoding="utf-8"))
            success_steps = success_report.get("Steps")
            if (
                success_report.get("OverallStatus") != "Succeeded"
                or not isinstance(success_steps, list)
                or len(success_steps) != 1
                or success_steps[0].get("Status") != "Succeeded"
            ):
                fail(f"successful rollout report is inaccurate: {success_report!r}")
            success_requests = [
                json.loads(line)
                for line in success_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if [item["operationId"] for item in success_requests] != [
                "POST_sessions",
                "PUT_log-forwarder-id",
            ]:
                fail(f"direct module success sequence is wrong: {success_requests!r}")
        finally:
            success_mock.terminate()
            try:
                success_mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                success_mock.kill()
                success_mock.wait(timeout=3)

        return requests, report


def verify_wire(requests: list[dict]) -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    scenario = json.loads(
        (ROOT / "scenario" / "forwarder-rollout.json").read_text(encoding="utf-8")
    )
    base_path = contract["servers"][0]["url"].rstrip("/")
    session_operation = contract["paths"]["/sessions"]["post"]
    update_operation = contract["paths"]["/log-forwarder/{id}"]["put"]

    if len(scenario["updates"]) != 3:
        fail("protected scenario must include a post-failure update")
    if len(requests) != 3:
        fail(f"rollout did not stop after the first failed update: {requests!r}")

    session, *update_requests = requests
    if (session["operationId"], session["method"], session["path"], session["query"]) != (
        session_operation["operationId"],
        "POST",
        base_path + "/sessions",
        "",
    ):
        fail(f"session request target is wrong: {session!r}")
    session_schema = contract["components"]["schemas"]["sessions.post.request"]
    expected_session = {
        name: scenario["credentials"][name] for name in session_schema["required"]
    }
    if session["body"] != expected_session:
        fail(f"session request body is wrong: {session['body']!r}")
    if not session["headers"].get("content-type", "").lower().startswith("application/json"):
        fail("session request must use application/json")
    if "authorization" in session["headers"]:
        fail("session request must not carry a Bearer token")

    update_schema = contract["components"]["schemas"]["forwarders.put.request"]
    allowed_fields = set(update_schema["properties"])
    optional_fields = allowed_fields.difference(update_schema["required"])
    session_id = session["responseBody"]["sessionId"]
    attempted_updates = scenario["updates"][:2]
    for request, update in zip(update_requests, attempted_updates, strict=True):
        expected_body = {
            name: value
            for name, value in update.items()
            if name in allowed_fields
        }
        expected_path = (
            base_path + "/log-forwarder/{id}"
        ).replace("{id}", update["id"])
        if (request["operationId"], request["method"], request["path"], request["query"]) != (
            update_operation["operationId"],
            "PUT",
            expected_path,
            "",
        ):
            fail(f"forwarder request target is wrong: {request!r}")
        if request["headers"].get("authorization") != f"Bearer {session_id}":
            fail("forwarder update did not use the returned sessionId as a Bearer token")
        if not request["headers"].get("content-type", "").lower().startswith("application/json"):
            fail("forwarder update must use application/json")
        if request["body"] != expected_body:
            fail(f"forwarder request body differs on the wire: {request['body']!r}")
        for name, value in expected_body.items():
            if type(request["body"][name]) is not type(value):
                fail(f"forwarder field {name!r} changed JSON type on the wire")
        unset_optional = optional_fields.difference(update)
        unexpectedly_sent = unset_optional.intersection(request["body"])
        if unexpectedly_sent:
            fail(f"unset optional fields were sent: {sorted(unexpectedly_sent)}")


def verify_report(report: dict, requests: list[dict]) -> None:
    scenario = json.loads(
        (ROOT / "scenario" / "forwarder-rollout.json").read_text(encoding="utf-8")
    )
    update_requests = requests[1:]

    if report.get("OverallStatus") != "Failed":
        fail(f"OverallStatus must retain the later failure: {report!r}")
    steps = report.get("Steps")
    if not isinstance(steps, list) or len(steps) != 2:
        fail(f"report must contain both attempted changes: {report!r}")
    first, second = steps
    first_request, second_request = update_requests
    required_keys = {"OperationId", "TargetId", "Status", "HttpStatus", "ErrorCode", "Message"}
    for step in steps:
        if set(step) != required_keys:
            fail(f"step report shape is not stable: {step!r}")
        if step["OperationId"] != "PUT_log-forwarder-id":
            fail(f"step operationId is inaccurate: {step!r}")
    if first_request["responseStatus"] >= 400 or second_request["responseStatus"] < 400:
        fail("mock did not exercise an earlier success followed by a later failure")
    if (first["TargetId"], first["Status"], first["HttpStatus"]) != (
        scenario["updates"][0]["id"],
        "Succeeded",
        first_request["responseStatus"],
    ):
        fail(f"successful earlier change was not reported accurately: {first!r}")
    if first["ErrorCode"] is not None:
        fail(f"successful step ErrorCode must be null: {first!r}")
    if (second["TargetId"], second["Status"], second["HttpStatus"]) != (
        scenario["updates"][1]["id"],
        "Failed",
        second_request["responseStatus"],
    ):
        fail(f"later failed change was not reported accurately: {second!r}")
    if second["ErrorCode"] != second_request["responseBody"].get("errorCode"):
        fail(f"API errorCode was not preserved: {second!r}")
    if second["Message"] != second_request["responseBody"].get("errorMessage"):
        fail(f"API errorMessage was not preserved: {second!r}")


def main() -> None:
    verify_contract()
    verify_manifest()
    requests, report = run_scenario()
    verify_wire(requests)
    verify_report(report, requests)
    print("PASS: VCF Operations for Logs 9.0 wire contract and partial-failure report verified")


if __name__ == "__main__":
    main()
