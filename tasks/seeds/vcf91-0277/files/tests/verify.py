#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0277.

Checks the specification provenance of the pinned contract, then compiles the
single-file client together with the loopback contract mock and the acceptance
harness and runs them. No live VMware endpoint is contacted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

PINNED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
REPOSITORY = "https://github.com/vmware/vcf-api-specs"
CREDENTIAL_VALUE_SOURCE = "https://developer.broadcom.com/xapis/vcf-operations-api/latest/"
CREDENTIAL_VALUE_TEMPLATE = "OpsToken {token}"
EXPECTED_OPERATIONS = {
    "acquireToken": ("POST", "/api/auth/token/acquire"),
    "getResources": ("GET", "/api/resources"),
}
EXPECTED_QUERY_ORDER = ["name", "adapterKind", "resourceKind", "page", "pageSize"]
EXPECTED_BODY_ORDER = ["username", "password", "authSource"]

PROTECTED_SHA256 = {
    "docs/contract.json": "f3bac58d220af3bda82bd692833a7e671c4727bf7c50708b1e14d091ca566864",
    "docs/official_sources.json": "b564e8b1d488b57a812d22adb1d2bc2450dfac99c045500fa19a7bc02c471909",
    "tests/MockVcfOpsServer.java": "832a1b430adb020603f2cb6512892dac3ea9def65950b0fba0f88bc584c2d23e",
    "tests/TestMain.java": "b7abc91ae8f98aa880fecc2c4b8828825b12d13b58da3c83ed1d3531f85ae4c9",
}

SUCCESS_MARKER = "PASS: contract wire shape and complete stable pagination verified"


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


def check_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))

    source = contract.get("source", {})
    if source.get("repository") != REPOSITORY:
        fail("contract repository is not the official vcf-api-specs repository")
    if source.get("commit_sha") != PINNED_SHA or source.get("spec_path") != SPEC_PATH:
        fail("contract source is not pinned to the selected specification revision")
    if source.get("license") != "Apache-2.0" or contract.get("api_version") != "9.1.0.0":
        fail("contract license or VCF API version changed")
    if source.get("credential_value_source") != CREDENTIAL_VALUE_SOURCE:
        fail("contract credential prefix source changed")
    if contract.get("basePath") != "/suite-api":
        fail("contract basePath is not the specification server url")

    security = contract.get("security", {})
    if (security.get("scheme"), security.get("in"), security.get("name")) != (
        "Token-based-authorization",
        "header",
        "Authorization",
    ):
        fail("contract security scheme differs from the specification")
    if security.get("value_template") != CREDENTIAL_VALUE_TEMPLATE:
        fail("contract credential template changed")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")

    acquire = next(item for item in operations if item["operationId"] == "acquireToken")
    if acquire.get("security") != []:
        fail("acquireToken must stay unsecured, as the specification declares it")
    request = acquire.get("request", {})
    if request.get("required") != ["username", "password"]:
        fail("acquireToken required properties changed")
    if request.get("optional") != ["authSource"]:
        fail("acquireToken optional properties changed")
    if request.get("bodyPropertyOrder") != EXPECTED_BODY_ORDER:
        fail("acquireToken body property order changed")

    resources = next(item for item in operations if item["operationId"] == "getResources")
    if resources.get("queryParameterOrder") != EXPECTED_QUERY_ORDER:
        fail("getResources query parameter order changed")
    declared = [parameter.get("name") for parameter in resources.get("queryParameters", [])]
    if declared != EXPECTED_QUERY_ORDER:
        fail("getResources query parameter set changed")
    for parameter in resources.get("queryParameters", []):
        if parameter.get("required") is True:
            fail("no getResources query parameter is required in the specification")
    pagination = resources.get("response", {}).get("pagination", {})
    if (
        pagination.get("pageParameter") != "page"
        or pagination.get("pageSizeParameter") != "pageSize"
        or pagination.get("firstPage") != 0
        or pagination.get("pageInfoProperty") != "pageInfo"
        or pagination.get("totalCountProperty") != "totalCount"
        or pagination.get("itemsProperty") != "resourceList"
    ):
        fail("getResources pagination contract changed")
    encoding = contract.get("encoding", {})
    if encoding.get("array_query_style") != "form" or encoding.get("array_query_explode") is not True:
        fail("array query encoding changed")


def check_official_sources() -> None:
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))
    if sources.get("repository") != REPOSITORY:
        fail("official source repository changed")
    if sources.get("repository_commit_sha") != PINNED_SHA:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official source path changed")
    if sources.get("license") != "Apache-2.0":
        fail("official source license changed")
    if sources.get("derived_artifact") != "docs/contract.json":
        fail("official sources do not record the derived contract")
    credential_source = sources.get("credential_value_source", {})
    if (
        credential_source.get("url") != CREDENTIAL_VALUE_SOURCE
        or credential_source.get("title") != "VMware Cloud Foundation Operations API"
        or credential_source.get("api_version") != "9.1.0.0"
        or credential_source.get("value_template") != CREDENTIAL_VALUE_TEMPLATE
    ):
        fail("official source for the VCF Operations credential prefix changed")

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
    if shutil.which("javac") is None or shutil.which("java") is None:
        fail("a JDK providing javac and java is required")

    production = sorted(path.name for path in (ROOT / "src").glob("*.java"))
    if production != ["VcfOpsInventoryClient.java"]:
        fail("the client must remain the single Java source file src/VcfOpsInventoryClient.java")

    sources = [
        ROOT / "src/VcfOpsInventoryClient.java",
        ROOT / "tests/MockVcfOpsServer.java",
        ROOT / "tests/TestMain.java",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf91-0277-") as output:
        compiled = subprocess.run(
            ["javac", "--release", "17", "-encoding", "UTF-8", "-d", output, *map(str, sources)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if compiled.returncode != 0:
            fail("javac failed:\n" + compiled.stdout + compiled.stderr)

        run = subprocess.run(
            [
                "java",
                "-ea",
                "-Dfile.encoding=UTF-8",
                "-cp",
                output,
                "TestMain",
                str(ROOT / "docs/contract.json"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if run.returncode != 0:
            fail("TestMain failed:\n" + run.stdout + run.stderr)
        if SUCCESS_MARKER not in run.stdout:
            fail("TestMain did not emit its success marker")


def main() -> None:
    check_protected_files()
    check_contract()
    check_official_sources()
    compile_and_run()
    print("PASS: vcf91-0277")


if __name__ == "__main__":
    main()
