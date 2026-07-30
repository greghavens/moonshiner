#!/usr/bin/env python3
"""Deterministic verifier for the VCF 9.1 Java client exercise."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]

# Filled with byte-exact hashes by the seed author. task.json also marks these
# paths protected so a candidate submission cannot replace its oracle.
PROTECTED_SHA256 = {
    "docs/contract.json": "71d10e683ba60779e57f4815d600adc6db559212c25b0a6c9910404be7c26cef",
    "docs/official_sources.json": "11d6e1337545d2a7756fa3575200fe7f79830f0dcba5f3f7e3ae0c84a68dc4d1",
    "grader/MockVcenterServer.java": "f01ba19df891e4fd41200691ce73bc30fa82c338f6b40a35b818251d04658e65",
    "grader/TestMain.java": "4a9eab8e485c111843b51c7b10ab1f793c24d125c9cfbb0698513a80a7a54ab1",
}

EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATION = "Vcenter.Tagging.Categories_list"


def fail(message: str) -> None:
    print(f"VERIFY ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"missing protected file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file changed: {relative}")


def check_contract_provenance() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )

    if contract.get("apiVersion") != "9.1.0.0":
        fail("contract is not pinned to API version 9.1.0.0")
    if contract.get("operationIds") != [EXPECTED_OPERATION]:
        fail("contract operationIds do not match the protected operation")
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        fail("contract must contain exactly one operation")
    operation = operations[0]
    if (
        operation.get("operationId") != EXPECTED_OPERATION
        or operation.get("method") != "GET"
        or operation.get("path") != "/vcenter/tagging/categories"
    ):
        fail("contract operation binding changed")

    security = operation.get("security", {})
    if (
        security.get("type") != "apiKey"
        or security.get("in") != "header"
        or security.get("name") != "vmware-api-session-id"
    ):
        fail("contract authentication wire binding changed")

    if sources.get("repositoryCommitSha") != EXPECTED_COMMIT:
        fail("official source commit changed")
    if sources.get("specPath") != EXPECTED_SPEC:
        fail("official source spec path changed")
    if sources.get("license") != "Apache-2.0":
        fail("official source license changed")
    source_operations = sources.get("operations")
    if not isinstance(source_operations, list) or [
        item.get("operationId") for item in source_operations
    ] != [EXPECTED_OPERATION]:
        fail("official source operationIds changed")


def compile_and_run() -> None:
    source = ROOT / "src/VCenterCategoryClient.java"
    if not source.is_file():
        fail("missing src/VCenterCategoryClient.java")

    with tempfile.TemporaryDirectory(prefix="vcf91-0121-") as output_dir:
        compile_command = [
            "javac",
            "--release",
            "17",
            "--add-modules",
            "jdk.httpserver",
            "-encoding",
            "UTF-8",
            "-d",
            output_dir,
            str(source),
            str(ROOT / "grader/MockVcenterServer.java"),
            str(ROOT / "grader/TestMain.java"),
        ]
        compiled = subprocess.run(
            compile_command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
        if compiled.returncode != 0:
            print(compiled.stdout, end="", file=sys.stderr)
            fail("javac failed")

        executed = subprocess.run(
            [
                "java",
                "--add-modules",
                "jdk.httpserver",
                "-cp",
                output_dir,
                "TestMain",
                str(ROOT / "docs/contract.json"),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
        print(executed.stdout, end="")
        if executed.returncode != 0:
            fail("acceptance harness failed")


def main() -> None:
    check_protected_files()
    check_contract_provenance()
    compile_and_run()


if __name__ == "__main__":
    main()
