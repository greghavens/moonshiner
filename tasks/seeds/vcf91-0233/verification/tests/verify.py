#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0233.

Checks that the local REST contract is still pinned to the published VCF 9.1
SDDC LCM specification, then builds and runs the Go test suite under the race
detector against the loopback mock. The Go toolchain runs with GOPROXY=off and
the mock binds only to 127.0.0.1, so no live VMware endpoint and no network
egress is involved.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
SPEC_BLOB_SHA1 = "fa97e0975ac108c81173b5bdd4fde57f20b2e190"
REPOSITORY = "https://github.com/vmware/vcf-api-specs"

EXPECTED_OPERATIONS = {
    "getTasks": ("GET", "/v1/tasks"),
    "getComponents": ("GET", "/v1/components"),
}
EXPECTED_QUERY_PARAMETERS = {
    "getTasks": [
        "status", "type", "createdBy", "name", "description",
        "startTimeGt", "startTimeLt", "updateTimeGt", "updateTimeLt",
        "endTimeGt", "endTimeLt", "resourceId", "resourceType",
        "includeSystemTasks", "pageNumber", "pageSize",
    ],
    "getComponents": ["scope"],
}

PROTECTED_SHA256 = {
    "go.mod": "917819b51a6f09e9e15a05c98008408901fd8724c35a0bac6f9d8cf15f843313",
    "docs/contract.json": "c5e21dcd4dfbc6f8b034fcf8c98b9da942fae6d06a19eda9560f81a8288b3f4f",
    "docs/official_sources.json": "d562b35c42c6cadd980b7b6eaf4432041f27666b080d4ff5270aa73cbb0ee766",
    "internal/mockvcf/server.go": "4d7a761062321472df0891b7833b0efa604a53601d308992aeb0f40f59f48eaf",
    "sddclcm/client_test.go": "3f67bde1330e4e96d77ae81f5d8a60a95233002cc6096a5a0f66802a3a524ed9",
    "tests/verify.py": None,  # self, not hashed
}

EXPECTED_TESTS = [
    "TestGetTasksRequestWireShape",
    "TestGetComponentsRequestWireShape",
    "TestListAllTasksRetrievesEveryPage",
    "TestListAllTasksStableOrder",
    "TestFollowerPagesAreFetchedConcurrently",
    "TestConcurrencyIsBounded",
    "TestPageFailurePropagates",
    "TestSinglePageCollection",
    "TestConcurrentCallersShareTheClient",
    "TestMockServesOnlyContractOperations",
]

EXPECTED_LAYOUT = {
    "sddclcm": {"client.go", "client_test.go"},
    "internal/mockvcf": {"server.go"},
    "docs": {"contract.json", "official_sources.json"},
}


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        if expected is None:
            continue
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected fixture missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_layout() -> None:
    for relative, expected in EXPECTED_LAYOUT.items():
        directory = ROOT / relative
        if not directory.is_dir():
            fail(f"missing directory: {relative}")
        actual = {p.name for p in directory.iterdir() if p.is_file()}
        if actual != expected:
            fail(
                f"{relative} contains {sorted(actual)}, want exactly "
                f"{sorted(expected)}; the implementation lives in sddclcm/client.go"
            )
    if (ROOT / "vendor").exists():
        fail("vendor/ is not allowed: the package must stay dependency-free")
    go_sum = ROOT / "go.sum"
    if go_sum.is_file() and go_sum.read_text(encoding="utf-8").strip():
        fail("go.sum has entries: the package must stay dependency-free")


def check_no_live_endpoint() -> None:
    """No source file may address a real VMware/Broadcom service."""
    forbidden = re.compile(r"https?://[^\s\"']*(broadcom\.com|vmware\.com)", re.I)
    for path in sorted(ROOT.rglob("*.go")):
        text = path.read_text(encoding="utf-8", errors="replace")
        hit = forbidden.search(text)
        if hit:
            fail(f"{path.relative_to(ROOT)} addresses a live endpoint: {hit.group(0)}")


def check_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("repository") != REPOSITORY:
        fail("contract repository is not the official vcf-api-specs repository")
    if source.get("commit_sha") != PINNED_SHA or source.get("spec_path") != SPEC_PATH:
        fail("contract source is not pinned to the selected specification revision")
    if source.get("license") != "Apache-2.0":
        fail("contract license changed")
    if source.get("openapi") != "3.0.4" or source.get("api_version") != "9.1.0.0":
        fail("contract OpenAPI or VCF API version changed")
    if source.get("api_title") != "VCF SDDC LCM Service APIs":
        fail("contract API title changed")
    if contract.get("server_base_path") != "/sddc-lcm":
        fail("contract server base path differs from the specification servers entry")

    security = contract.get("security", {})
    if (
        security.get("scheme"),
        security.get("type"),
        security.get("http_scheme"),
        security.get("header"),
        security.get("value_prefix"),
    ) != ("bearerToken", "http", "Bearer", "Authorization", "Bearer "):
        fail("contract security scheme differs from the specification")

    operations = contract.get("operations", [])
    actual = {
        op.get("operationId"): (op.get("method"), op.get("path")) for op in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail(f"contract operation set changed: {sorted(actual)}")

    for op in operations:
        operation_id = op["operationId"]
        names = [p.get("name") for p in op.get("query_parameters", [])]
        if names != EXPECTED_QUERY_PARAMETERS[operation_id]:
            fail(f"{operation_id} query parameters changed: {names}")
        if op.get("request_body") is not None:
            fail(f"{operation_id} is a GET and must not declare a request body")
        success = op.get("success", {})
        if success.get("status") != 200 or success.get("content_type") != "application/json":
            fail(f"{operation_id} success response changed")

    scope = next(
        p for p in operations[1]["query_parameters"] if p["name"] == "scope"
    )
    if scope.get("enum") != ["FLEET", "INSTANCE"]:
        fail("getComponents scope enum changed")

    if contract.get("omit_unset_optional_query_parameters") is not True:
        fail("contract no longer requires unset optional parameters to be omitted")

    pagination = contract.get("pagination", {})
    if (
        pagination.get("operationId"),
        pagination.get("page_number_param"),
        pagination.get("page_size_param"),
        pagination.get("max_page_size"),
        pagination.get("first_page_number"),
        pagination.get("envelope_schema"),
        pagination.get("page_metadata_schema"),
    ) != ("getTasks", "pageNumber", "pageSize", 50, 0, "PageOfTaskSummary", "PageMetadata"):
        fail("contract pagination facts changed")
    if pagination.get("page_metadata_properties") != [
        "pageNumber", "pageSize", "totalElements", "totalPages"
    ]:
        fail("PageMetadata properties changed")
    if pagination.get("envelope_properties") != ["elements", "pageMetadata"]:
        fail("PageOfTaskSummary properties changed")


def check_official_sources() -> None:
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    if sources.get("repository") != REPOSITORY:
        fail("official source repository changed")
    if sources.get("repository_commit_sha") != PINNED_SHA:
        fail("official source commit changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official source specification path changed")
    if sources.get("spec_blob_sha1") != SPEC_BLOB_SHA1:
        fail("official source specification blob digest changed")
    expected_url = (
        f"https://raw.githubusercontent.com/vmware/vcf-api-specs/{PINNED_SHA}/{SPEC_PATH}"
    )
    if sources.get("spec_url") != expected_url:
        fail("official source specification URL is not commit-pinned")
    if sources.get("license") != "Apache-2.0":
        fail("official source license changed")
    if sources.get("derived_artifact") != "docs/contract.json":
        fail("official sources no longer point at the derived contract")

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


def summarize(output: str) -> str:
    """Drop the -v bookkeeping so the actual failures stay visible."""
    noise = re.compile(r"^\s*(=== (RUN|PAUSE|CONT|NAME)|--- PASS:|\s*--- PASS:|ok\s|\?\s)")
    kept = [line for line in output.splitlines() if not noise.match(line)]
    return "\n".join(kept)[-8000:]


def go_env(cache: str) -> dict:
    env = dict(os.environ)
    # A caller may have ccache configured outside this self-contained seed.
    # Disabling it keeps verification independent of host cache permissions.
    env["CCACHE_DISABLE"] = "1"
    env["GOPROXY"] = "off"
    env["GOFLAGS"] = "-mod=mod"
    env["GOTOOLCHAIN"] = "local"
    env["GOCACHE"] = cache
    env.pop("GOWORK", None)
    return env


def run_go(cache: str) -> None:
    vet = subprocess.run(
        ["go", "vet", "./..."],
        cwd=ROOT, env=go_env(cache), text=True, capture_output=True,
        timeout=180, check=False,
    )
    if vet.returncode != 0:
        fail("go vet failed:\n" + vet.stdout + vet.stderr)

    test = subprocess.run(
        ["go", "test", "-race", "-count=1", "-v", "-timeout=120s", "./..."],
        cwd=ROOT, env=go_env(cache), text=True, capture_output=True,
        timeout=300, check=False,
    )
    output = test.stdout + test.stderr
    if test.returncode != 0:
        fail("go test -race failed:\n" + summarize(output))
    if "WARNING: DATA RACE" in output:
        fail("the race detector reported a data race:\n" + summarize(output))
    for name in EXPECTED_TESTS:
        if not re.search(rf"^--- PASS: {re.escape(name)}\b", output, re.M):
            fail(f"{name} did not run and pass; it must not be skipped or shadowed")


def main() -> None:
    check_protected_files()
    check_layout()
    check_no_live_endpoint()
    check_contract()
    check_official_sources()

    # Never rely on a host or home-directory cache. A fresh local cache also
    # prevents one verification run from changing the behavior of the next.
    with tempfile.TemporaryDirectory(prefix="vcf91-0233-gocache-") as cache:
        run_go(cache)

    print("PASS: vcf91-0233")


if __name__ == "__main__":
    sys.exit(main())
