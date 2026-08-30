#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0195."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
EXPECTED_OPERATIONS = {
    "createAgentSecret": ("POST", "/api/v2/agent/secrets"),
    "createAgentSession": ("POST", "/api/v2/agent/secrets/exchange"),
    "revokeAgentSecret": ("POST", "/api/v2/agent/secrets/{secretName}/revoke"),
}
PROTECTED_SHA256 = {
    "docs/contract.json": "00d5ecd31e10d5f86794d2240683849bc2e8aa831d1db5f11c0f2bcfb1213f39",
    "docs/official_sources.json": "bb9cf26cca2a8b78a663f78c7868c7fc2ba2d613338e4d6a1185ffe7eba0885b",
    "tests/MockVcfLogServer.java": "cd56a0a1af31f357191d1a880a693092a1656a5a3c836dfb587347bbe20f3536",
    "tests/TestMain.java": "001e1ecb1f8c823949cd51138f7a0c25b97290f6957ecebe8862289c937155a3",
}


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected fixture missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_provenance() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("repository") != "https://github.com/vmware/vcf-api-specs":
        fail("contract repository is not the official vcf-api-specs repository")
    if source.get("commit_sha") != PINNED_SHA or source.get("spec_path") != SPEC_PATH:
        fail("contract source is not pinned to the selected specification revision")
    if source.get("license") != "Apache-2.0" or source.get("api_version") != "9.1.0.0":
        fail("contract license or VCF API version changed")
    security = contract.get("security", {})
    if (security.get("scheme"), security.get("in"), security.get("name")) != (
        "OPSTokenAuthorization", "header", "X-JWT-Token"
    ):
        fail("contract security header differs from the specification")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")
    session_request = next(
        item for item in operations if item["operationId"] == "createAgentSession"
    )["request"]["schema"]
    if session_request.get("required") != ["secret"]:
        fail("createAgentSession required fields changed")
    ttl = session_request.get("properties", {}).get("ttl", {})
    if (ttl.get("required") is not False
            or ttl.get("units") != "milliseconds"
            or ttl.get("zero_uses_documented_default") is not True):
        fail("optional ttl contract changed")

    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    if sources.get("repository_commit_sha") != PINNED_SHA:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official source path changed")
    entries = sources.get("operations", [])
    if len(entries) != len(EXPECTED_OPERATIONS):
        fail("official source operation list changed")
    for entry in entries:
        operation_id = entry.get("operationId")
        expected = EXPECTED_OPERATIONS.get(operation_id)
        if expected is None:
            fail(f"unrecognized official operationId: {operation_id}")
        if entry.get("repository_commit_sha") != PINNED_SHA:
            fail(f"operation {operation_id} is not commit-pinned")
        if entry.get("spec_path") != SPEC_PATH:
            fail(f"operation {operation_id} does not record the specification path")
        if (entry.get("method", "").upper(), entry.get("path")) != expected:
            fail(f"operation {operation_id} source mapping changed")


def compile_and_run() -> None:
    production = sorted((ROOT / "src").glob("*.java"))
    if production != [ROOT / "src/VcfLogClient.java"]:
        fail("the production client must remain a single Java source file")

    sources = [
        ROOT / "src/VcfLogClient.java",
        ROOT / "tests/MockVcfLogServer.java",
        ROOT / "tests/TestMain.java",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf91-0195-") as output:
        compile_result = subprocess.run(
            ["javac", "--release", "17", "-d", output, *map(str, sources)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        run_result = subprocess.run(
            [
                "java",
                "-ea",
                "-cp",
                output,
                "TestMain",
                str(ROOT / "docs/contract.json"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=25,
            check=False,
        )
        if run_result.returncode != 0:
            fail("TestMain failed:\n" + run_result.stdout + run_result.stderr)
        marker = "PASS: contract wire shape and drain-safe rotation verified"
        if marker not in run_result.stdout:
            fail("TestMain did not emit its success marker")


def main() -> None:
    check_protected_files()
    check_provenance()
    compile_and_run()
    print("PASS: vcf91-0195")


if __name__ == "__main__":
    main()
