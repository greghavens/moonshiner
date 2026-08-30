#!/usr/bin/env python3
"""Deterministic verifier for the VCF 9.1 NSX Policy Java task."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "README.md": "34ec53a9d18faa778b58696b892cadef4aab4da1409f7488079ac53815ff4525",
    "docs/contract.json": "b3b1dfcba348578b30ac0cab6c077a93e7880540ddc7beed39c2ae9bf7ee25ac",
    "docs/official_sources.json": "09cbf2b7b94214d8b8e3a81e3e8c2f4cfef0a263e19f2df9939a5729dc397c67",
    "tests/TestMain.java": "7192d255e5833b920b86dd882d5afcd8ed76a5c66a465a9f3294c5c6f8c826c1",
    "tests/mock_nsx.py": "44c9ec41718f507b350f92f676750c675e8cba742408c135722774e772efe9ed",
}
EXPECTED_OPERATION_IDS = [
    "OrgsOrgIdProjectsProjectIdInfraUpdateSecurityPolicyForDomain",
    "OrgsOrgIdProjectsProjectIdInfraReadIntentStatus",
    "OrgsOrgIdProjectsProjectIdInfraListSecurityPoliciesForDomain",
]


def fail(message: str) -> int:
    print(f"VERIFY FAILED: {message}", file=sys.stderr)
    return 1


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_protected_inputs() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        actual = sha256(ROOT / relative)
        if actual != expected:
            raise AssertionError(f"protected file changed: {relative}")

    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    operation_ids = [item["operationId"] for item in contract["operations"]]
    source_operation_ids = [item["operationId"] for item in sources["operationIds"]]
    if operation_ids != EXPECTED_OPERATION_IDS:
        raise AssertionError("contract operation IDs do not match the pinned selection")
    if source_operation_ids != EXPECTED_OPERATION_IDS:
        raise AssertionError("official source operation IDs do not match the contract")
    if contract["api"] != {
        "swagger": "2.0",
        "title": "NSX Policy API",
        "version": "9.1.0.0",
        "basePath": "/policy/api/v1",
        "produces": ["application/json"],
        "security": "BasicAuth",
    }:
        raise AssertionError("pinned NSX Policy API metadata changed")
    if (
        contract["source"]["commit_sha"]
        != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
        or sources["repository_commit_sha"]
        != contract["source"]["commit_sha"]
        or sources["spec_path"] != contract["source"]["spec_path"]
    ):
        raise AssertionError("specification provenance changed")


def run() -> int:
    try:
        validate_protected_inputs()
    except (AssertionError, KeyError, json.JSONDecodeError, OSError) as error:
        return fail(str(error))

    with tempfile.TemporaryDirectory(prefix="vcf91-0087-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "-encoding",
                "UTF-8",
                "-d",
                str(classes),
                str(ROOT / "src/NsxPolicyClient.java"),
                str(ROOT / "tests/TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout)
            sys.stderr.write(compile_result.stderr)
            return fail("Java compilation failed")

        request_log = temp / "requests.jsonl"
        ready_file = temp / "ready"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests/mock_nsx.py"),
                "--contract",
                str(ROOT / "docs/contract.json"),
                "--log",
                str(request_log),
                "--ready",
                str(ready_file),
                "--username",
                "integration-user",
                "--password",
                "integration-password",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready_file.exists():
                if mock.poll() is not None:
                    stdout, stderr = mock.communicate()
                    sys.stderr.write(stdout)
                    sys.stderr.write(stderr)
                    return fail("loopback mock exited before becoming ready")
                if time.monotonic() >= deadline:
                    return fail("loopback mock did not become ready")
                time.sleep(0.01)

            base_uri = ready_file.read_text(encoding="utf-8")
            test_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    base_uri,
                    str(request_log),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            if test_result.returncode != 0:
                sys.stderr.write(test_result.stdout)
                sys.stderr.write(test_result.stderr)
                return fail("TestMain rejected the client")
            if test_result.stdout.strip() != "PASS vcf91-0087":
                return fail(f"unexpected TestMain output: {test_result.stdout!r}")
        except subprocess.TimeoutExpired:
            return fail("client timed out")
        finally:
            mock.terminate()
            try:
                stdout, stderr = mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                stdout, stderr = mock.communicate(timeout=3)
            if mock.returncode not in (0, -15):
                sys.stderr.write(stdout)
                sys.stderr.write(stderr)

    print("PASS vcf91-0087 protected verifier")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
