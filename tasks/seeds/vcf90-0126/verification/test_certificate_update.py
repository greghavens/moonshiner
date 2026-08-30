#!/usr/bin/env python3
"""Protected acceptance verifier for the PowerShell certificate update module."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "mock" / "mock_server.py"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_provenance() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    expected_sha = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
    expected_path = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
    expected_operations = {
        "updateCertificate": ("PUT", "/settings/certificates/{id}"),
        "fetchCertificateUpdateStatusForUpdateId": (
            "GET",
            "/settings/certificates/status/{id}",
        ),
    }

    check(contract["source_revision"]["commit"] == expected_sha, "contract commit drift")
    check(contract["source_revision"]["spec_path"] == expected_path, "contract path drift")
    check(sources["commit_sha"] == expected_sha, "source commit drift")
    check(sources["spec_path"] == expected_path, "source path drift")
    check(sources["tag"] == "9.0.0.0", "source tag drift")
    check(sources["license"] == "Apache-2.0", "source license drift")

    operations = {
        name: (entry["method"], entry["path"])
        for name, entry in contract["operations"].items()
    }
    listed = {
        entry["operationId"]: (entry["method"], entry["path"])
        for entry in sources["operation_ids"]
    }
    check(operations == expected_operations, "contract operation drift")
    check(listed == expected_operations, "official source operation drift")
    check(contract["server_url"] == "/api/ni", "server base path drift")
    check(
        contract["schemas"]["CertificateUpdateStatus"]["properties"]["status"]["enum"]
        == ["SUBMITTED", "IN_PROGRESS", "SUCCESS", "FAILED"],
        "terminal state enum drift",
    )
    request_schema = contract["schemas"]["CertificateUpdateRequest"]
    check(request_schema["required"] == [], "request fields are optional in the source spec")
    check(list(request_schema["properties"]) == ["certificate", "private_key", "chain"], "request schema drift")


def submitted_paths():
    """Yield seed-owned paths without entering harness-injected toolchains."""
    for child in ROOT.iterdir():
        if child.name == ".sandbox-home":
            continue
        yield child
        if child.is_dir():
            yield from child.rglob("*")


def verify_powercli_prerequisite() -> None:
    manifest = (ROOT / "VcfOperationsNetworks.psd1").read_text(encoding="utf-8")
    check("VMware.Sdk.Vcf.Ops" in manifest, "PowerCLI VCF Ops prerequisite is missing")
    check("13.4.0.24798382" in manifest, "VCF 9.0 PowerCLI prerequisite is not pinned")
    forbidden = {".dll", ".nupkg", ".nuspec"}
    vendored = [
        path
        for path in submitted_paths()
        if path.is_file() and path.suffix.lower() in forbidden
    ]
    check(not vendored, f"VMware package artifacts must not be vendored: {vendored}")
    sdk_named = [
        path
        for path in submitted_paths()
        if path.name.lower().startswith("vmware.sdk.")
    ]
    check(not sdk_named, f"VMware SDK modules must not be vendored: {sdk_named}")


def wait_for_ready(ready_file: Path, process: subprocess.Popen[str]) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"mock exited early ({process.returncode}): {stdout}{stderr}")
        if ready_file.exists() and ready_file.stat().st_size:
            return json.loads(ready_file.read_text(encoding="utf-8"))["base_url"]
        time.sleep(0.01)
    raise AssertionError("mock did not become ready")


def assert_common_request(request: dict, method: str, target: str) -> None:
    check(request["method"] == method, f"expected {method}, got {request['method']}")
    check(request["target"] == target, f"wire target mismatch: {request['target']}")
    check(request["query"] == "", f"unexpected query string: {request['query']}")
    check(
        request["headers"].get("authorization") == "NetworkInsight fixture-token",
        "Authorization wire value mismatch",
    )


def assert_update(request: dict, target: str, expected_body: dict) -> None:
    assert_common_request(request, "PUT", target)
    content_type = request["headers"].get("content-type", "")
    check(content_type == "application/json", f"Content-Type wire value mismatch: {content_type!r}")
    try:
        observed_body = json.loads(request["body"])
    except json.JSONDecodeError as error:
        raise AssertionError(f"request body is not valid JSON: {request['body']!r}") from error
    check(observed_body == expected_body, f"JSON wire body mismatch: {observed_body!r}")


def assert_poll(request: dict, update_id: str) -> None:
    target = f"/api/ni/settings/certificates/status/{update_id}"
    assert_common_request(request, "GET", target)
    check(request["body"] == "", "status GET must not carry a body")
    check("content-type" not in request["headers"], "status GET must not carry Content-Type")


def verify_wire(requests: list[dict]) -> None:
    check(len(requests) == 14, f"expected exactly 14 requests, got {len(requests)}")

    assert_update(
        requests[0],
        "/api/ni/settings/certificates/platform%20certificate%2Fprimary",
        {
            "certificate": "fixture-certificate-alpha",
            "private_key": "fixture-key-alpha",
        },
    )
    first_body = json.loads(requests[0]["body"])
    check(set(first_body) == {"certificate", "private_key"}, "unset chain was not omitted")
    for request in requests[1:4]:
        assert_poll(request, "update%20id%2F0001")

    assert_update(
        requests[4],
        "/api/ni/settings/certificates/proxy-primary",
        {
            "certificate": "fixture-certificate-beta",
            "private_key": "fixture-key-beta",
            "chain": "fixture-chain-beta",
        },
    )
    for request in requests[5:8]:
        assert_poll(request, "update-0002")

    assert_update(
        requests[8],
        "/api/ni/settings/certificates/empty-chain-target",
        {
            "certificate": "fixture-certificate-gamma",
            "private_key": "fixture-key-gamma",
            "chain": "",
        },
    )
    for request in requests[9:12]:
        assert_poll(request, "update-0003")

    assert_update(
        requests[12],
        "/api/ni/settings/certificates/failed-target",
        {
            "certificate": "fixture-certificate-failure",
            "private_key": "fixture-key-failure",
        },
    )
    assert_poll(requests[13], "update-0004")


def main() -> None:
    verify_provenance()
    verify_powercli_prerequisite()
    check(shutil.which("pwsh") is not None, "PowerShell 7 (pwsh) is required")

    with tempfile.TemporaryDirectory(prefix="vcf-networks-test-") as temporary:
        temp = Path(temporary)
        request_log = temp / "requests.jsonl"
        ready_file = temp / "ready.json"
        env = os.environ.copy()
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--request-log",
                str(request_log),
                "--ready-file",
                str(ready_file),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            base_url = wait_for_ready(ready_file, mock)
            result = subprocess.run(
                ["pwsh", "-NoLogo", "-NoProfile", "-File", str(ROOT / "test_driver.ps1"), "-BaseUri", base_url],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
            )
            check(result.returncode == 0, f"PowerShell driver failed:\n{result.stdout}\n{result.stderr}")
            output_lines = [line for line in result.stdout.splitlines() if line.strip()]
            summary = json.loads(output_lines[-1])
            check(summary["first_status"] == "SUCCESS", "first operation did not finish")
            check(summary["second_status"] == "SUCCESS", "second operation did not finish")
            check(summary["third_status"] == "SUCCESS", "third operation did not finish")
            check("fixture certificate update failed" in summary["failure_message"], "failure detail was lost")
            check(summary["sleeps"] == [1, 1, 1, 1, 1, 1], "poll waits did not follow nonterminal states")
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=3)

        requests = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]
        verify_wire(requests)

    print("all tests passed")


if __name__ == "__main__":
    main()
