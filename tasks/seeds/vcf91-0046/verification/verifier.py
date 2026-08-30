#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
PINNED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getDomains": ("GET", "/v1/domains"),
    "refreshAccessToken": ("PATCH", "/v1/tokens/access-token/refresh"),
}

# Filled with byte hashes after the protected fixtures are authored.
PROTECTED_SHA256 = {
    "docs/contract.json": "517376af7b000384659e224224a9752981b2f1638631e89293c680d128cff9eb",
    "docs/official_sources.json": "7fbeb917d8bf7c3b8a59d32aa6aca99726d5ad6ebd46d30a06d6b03ef88d6681",
    "tests/ContractMock.java": "1894fe4dac9b91cd67e80275c780ad488d368d296cf1f859c275bd45b1d9cf6a",
    "tests/TestMain.java": "d9fb86cf3e839013539422caea0a9c3dbfc93eec3509b4d59c26a2701c67c245",
}


def fail(message: str, detail: str = "") -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    raise SystemExit(1)


def verify_hashes() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"missing protected file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file changed: {relative}")


def verify_contract() -> None:
    try:
        contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
        sources = json.loads(
            (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        fail("unable to read the pinned contract", str(error))

    source = contract.get("source", {})
    if source.get("commitSha") != PINNED_COMMIT:
        fail("contract commit pin mismatch")
    if source.get("specPath") != SPEC_PATH or source.get("specVersion") != "9.1.0.0":
        fail("contract specification identity mismatch")

    actual_operations = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in contract.get("operations", [])
    }
    if actual_operations != EXPECTED_OPERATIONS:
        fail("contract operation set mismatch", repr(actual_operations))

    if (
        sources.get("commitSha") != PINNED_COMMIT
        or sources.get("specPath") != SPEC_PATH
        or sources.get("license") != "Apache-2.0"
    ):
        fail("official source provenance mismatch")
    source_operations = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in sources.get("operations", [])
    }
    if source_operations != EXPECTED_OPERATIONS:
        fail("official source operation set mismatch", repr(source_operations))


def run(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        fail("verification timed out", str(error))
    except OSError as error:
        fail(f"could not execute {command[0]}", str(error))


def main() -> None:
    verify_hashes()
    verify_contract()

    required = [
        ROOT / "src/VcfSddcClient.java",
        ROOT / "tests/ContractMock.java",
        ROOT / "tests/TestMain.java",
    ]
    for path in required:
        if not path.is_file():
            fail(f"missing required file: {path.relative_to(ROOT)}")
    if "return List.of();" in required[0].read_text(encoding="utf-8"):
        fail("client implementation is incomplete")

    with tempfile.TemporaryDirectory(prefix="vcf91-0046-") as output_directory:
        compile_result = run(
            [
                "javac",
                "--release",
                "17",
                "--add-modules",
                "jdk.httpserver",
                "-encoding",
                "UTF-8",
                "-d",
                output_directory,
                *(str(path) for path in required),
            ],
            timeout=15,
        )
        if compile_result.returncode != 0:
            fail("Java compilation failed", compile_result.stdout)

        test_result = run(
            [
                "java",
                "--add-modules",
                "jdk.httpserver",
                "-ea",
                "-cp",
                output_directory,
                "TestMain",
            ],
            timeout=15,
        )
        if test_result.returncode != 0:
            fail("acceptance test failed", test_result.stdout)
        if test_result.stdout.strip() != "PASS: VCF SDDC client contract":
            fail("unexpected acceptance output", test_result.stdout)

    print("PASS: protected contract and Java acceptance checks")


if __name__ == "__main__":
    main()
