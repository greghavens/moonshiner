#!/usr/bin/env python3
"""Protected deterministic verifier for the VCF 9.1 Tier-1 gate."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tools" / "mock_nsx_policy.py"
TEST_MAIN = ROOT / "TestMain.java"
CLIENT = ROOT / "NsxPolicyClient.java"
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
SPEC_BLOB = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
OPERATION_IDS = ["GetTier1State", "PatchTier1"]
PROTECTED_SHA256 = {
    "TestMain.java": "3db9b9e54591bd041e2ec5a36385f4fd64cd9999af7c04d10e6896f1d30ae30a",
    "tools/mock_nsx_policy.py": "95f66dd8c497999b714b8254bd202d2c3e6cdd09087dda7d55aeabe6cdcdd936",
    "docs/contract.json": "4b81e1deb1964e58251ebfb6c2efaa81a98d9efce1f03edeb142bca5fcb93d13",
    "docs/official_sources.json": "9fa166e2e789a69eb2fc8370b0b88a7bb13c5a96c7f6c0e14c2ee52cb8242a71",
}


def fail(message: str) -> "NoReturn":
    raise AssertionError(message)


def verify_protected_files() -> tuple[dict[str, object], dict[str, object]]:
    for relative, expected in PROTECTED_SHA256.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file changed: {relative}")

    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if contract.get("swagger") != "2.0":
        fail("contract must remain OpenAPI 2.0")
    if contract.get("info", {}).get("version") != "9.1.0.0":
        fail("contract product version changed")
    if contract.get("basePath") != "/policy/api/v1":
        fail("contract basePath changed")
    if contract.get("security") != [{"BasicAuth": []}]:
        fail("contract authentication changed")

    operations = contract.get("operations")
    if not isinstance(operations, list):
        fail("contract operations must be an array")
    if [operation.get("operationId") for operation in operations] != OPERATION_IDS:
        fail("contract operationIds changed")
    expected_wire = [
        ("GET", "/infra/tier-1s/{tier-1-id}/state"),
        ("PATCH", "/infra/tier-1s/{tier-1-id}"),
    ]
    if [
        (operation.get("method"), operation.get("path"))
        for operation in operations
    ] != expected_wire:
        fail("contract methods or paths changed")

    if sources.get("repository_commit_sha") != COMMIT:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official source path changed")
    if sources.get("spec_blob_sha") != SPEC_BLOB:
        fail("official source blob changed")
    if sources.get("license") != "Apache-2.0":
        fail("official source license changed")
    source_operations = sources.get("operations")
    if not isinstance(source_operations, list):
        fail("official source operations must be an array")
    if [
        operation.get("operationId") for operation in source_operations
    ] != OPERATION_IDS:
        fail("official source operationIds changed")
    for operation in source_operations:
        if operation.get("repository_commit_sha") != COMMIT:
            fail("operation does not repeat the pinned commit")
        if operation.get("spec_path") != SPEC_PATH:
            fail("operation does not repeat the pinned spec path")

    source = contract.get("source")
    if not isinstance(source, dict):
        fail("contract source metadata missing")
    if source.get("repository_commit_sha") != COMMIT:
        fail("contract and source commit disagree")
    if source.get("spec_path") != SPEC_PATH:
        fail("contract and source path disagree")
    if source.get("spec_blob_sha") != SPEC_BLOB:
        fail("contract and source blob disagree")
    return contract, sources


def write_scenarios(path: Path, token: str) -> None:
    scenarios = {
        f"blocked/{token}": {
            "precheck": {
                "status": 200,
                "body": {
                    "tier1_state": {
                        "state": "failed",
                        "failure_code": 9407,
                        "failure_message": f"gateway realization blocked {token}",
                    }
                },
            }
        },
        f"malformed/{token}": {
            "precheck": {
                "status": 200,
                "body": {"tier1_status": {}},
            }
        },
        f"outage/{token}": {
            "precheck": {
                "status": 503,
                "body": {
                    "error_code": 50384,
                    "error_message": "gateway-state service unavailable",
                    "module_name": "contract-mock",
                },
            }
        },
        f"ready/core ?#% Δ-{token}": {
            "precheck": {
                "status": 200,
                "body": {"tier1_state": {"state": "success"}},
            },
            "mutation": {"status": 200, "body": None},
        },
        f"options/{token}": {
            "precheck": {
                "status": 200,
                "body": {"tier1_state": {"state": "success"}},
            },
            "mutation": {"status": 200, "body": None},
        },
    }
    path.write_text(
        json.dumps(
            {"scenarios": scenarios},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def wait_for_port(process: subprocess.Popen[str], port_file: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if port_file.exists():
            return int(port_file.read_text(encoding="ascii").strip())
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            fail(
                "loopback mock exited before startup\n"
                f"stdout:\n{stdout}\nstderr:\n{stderr}"
            )
        time.sleep(0.02)
    fail("loopback mock did not publish its port")


def main() -> None:
    verify_protected_files()
    token = hashlib.sha256(
        f"{COMMIT}:{SPEC_BLOB}:vcf91-0084".encode("ascii")
    ).hexdigest()[:18]

    with tempfile.TemporaryDirectory(prefix="vcf91-0084-") as temp_name:
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
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(
                "javac failed\n"
                f"stdout:\n{compile_result.stdout}\n"
                f"stderr:\n{compile_result.stderr}"
            )

        scenarios = temp / "scenarios.json"
        request_log = temp / "requests.tsv"
        effects = temp / "effects.txt"
        port_file = temp / "port.txt"
        write_scenarios(scenarios, token)
        mock = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--scenarios",
                str(scenarios),
                "--log",
                str(request_log),
                "--effects",
                str(effects),
                "--port-file",
                str(port_file),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_port(mock, port_file)
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{port}",
                    str(request_log),
                    str(effects),
                    token,
                    SPEC_BLOB,
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if run_result.returncode != 0:
                fail(
                    "TestMain failed\n"
                    f"stdout:\n{run_result.stdout}\n"
                    f"stderr:\n{run_result.stderr}"
                )
            if "ALL NSX POLICY CONTRACT CHECKS PASSED" not in run_result.stdout:
                fail(f"missing success marker:\n{run_result.stdout}")
            lines = [
                line
                for line in request_log.read_text(
                    encoding="ascii"
                ).splitlines()
                if line
            ]
            if len(lines) != 7:
                fail(f"expected 7 logged requests, found {len(lines)}")
            if effects.read_text(encoding="ascii") != "2\n":
                fail("mock effect count does not prove exactly two mutations")
            print(run_result.stdout.strip())
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=3)
            if mock.returncode not in (-15, -9, 0):
                stdout, stderr = mock.communicate()
                fail(
                    "loopback mock exited unexpectedly\n"
                    f"stdout:\n{stdout}\nstderr:\n{stderr}"
                )


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"VERIFY FAILED: {error}", file=sys.stderr)
        raise SystemExit(1)
