#!/usr/bin/env python3
"""Deterministic verifier for the VCF 9.1 vCenter Java task."""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "README.md": "6bf8909774aa7de5ef314f0e806308cad2a1de2a37e658ae7a18617829248001",
    "docs/contract.json": "95168b501b06ab3839f323f23dd186e25e736ff70286cef8aae821cd60cbe860",
    "docs/official_sources.json": "6aae864bf31eb0e9a3c7777054eb0f2b61778a3370f76946fc4376564aea7f9d",
    "tests/TestMain.java": "37e0761119b1e11922014bac34290f2a75025c72f6da4b03ef00213feb8f89a4",
    "tests/mock_vcenter.py": "0ce5d13003409302a1f757b82af8b78efaded258de3373eb42f8ae5ddfd683c2",
}
EXPECTED_OPERATION_IDS = [
    "Vcenter.VM_clone$Task",
    "Cis.Tasks_get",
    "Vcenter.VM_list",
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
        "openapi": "3.0.3",
        "title": "vSphere Automation API",
        "version": "9.1.0.0",
        "server_path": "/api",
        "media_type": "application/json",
        "security_scheme": {
            "name": "api_key_auth",
            "type": "apiKey",
            "in": "header",
            "header": "vmware-api-session-id",
        },
    }:
        raise AssertionError("pinned vSphere Automation API metadata changed")
    if (
        contract["source"]["commit_sha"]
        != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
        or contract["source"]["spec_blob_sha"]
        != "8028b0824c4ff3503d05f44814f967938a795c40"
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

    with tempfile.TemporaryDirectory(prefix="vcf91-0127-") as temporary:
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
                str(ROOT / "src/VCenterCloneClient.java"),
                str(ROOT / "tests/TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout)
            sys.stderr.write(compile_result.stderr)
            return fail("Java compilation failed")

        request_log = temp / "requests.jsonl"
        ready_file = temp / "ready"
        session_token = "session-" + secrets.token_urlsafe(24)
        nonce = secrets.token_hex(6)
        mock = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(ROOT / "tests/mock_vcenter.py"),
                "--contract",
                str(ROOT / "docs/contract.json"),
                "--log",
                str(request_log),
                "--ready",
                str(ready_file),
                "--session-token",
                session_token,
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
                    session_token,
                    nonce,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
            if test_result.returncode != 0:
                sys.stderr.write(test_result.stdout)
                sys.stderr.write(test_result.stderr)
                return fail("TestMain rejected the client")
            if test_result.stdout.strip() != "PASS vcf91-0127":
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

    print("PASS vcf91-0127 protected verifier")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
