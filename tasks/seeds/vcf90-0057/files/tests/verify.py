#!/usr/bin/env python3
"""Protected deterministic verifier for vcf90-0057.

Checks the provenance of the local REST contract, then compiles the single-file client together
with the protected harness and runs it against a loopback mock vCenter. No live VMware endpoint is
contacted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]

REPOSITORY = "https://github.com/vmware/vcf-api-specs"
PINNED_TAG = "9.0.0.0"
PINNED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
# The same specification file exists at tag 9.1.0.0; the contract must not be derived from it.
REJECTED_REVISIONS = {
    "9.1.0.0": "3949fc33339fc5ea1b77eadb258f1cf49aa88e26",
}
EXPECTED_OPERATIONS = {
    "Vcenter.VM_list": ("GET", "/vcenter/vm", "/api/vcenter/vm"),
    "Vcenter.VM_create": ("POST", "/vcenter/vm", "/api/vcenter/vm"),
}
PROTECTED_SHA256 = {
    "docs/contract.json": "76223ab02b23c920f037cea432dcfd1519f4dc8844cbce7dced4de2ba6394e59",
    "docs/official_sources.json": "7484cd68b81eca4baa1614b1b3afb4af50c630029a36b3d188ff1480f6292b72",
    "tests/MockVcenterServer.java": "fe5c611812aac326330166f484a7c3154c31c027c8dec34dce8dd2e10072b76b",
    "tests/TestMain.java": "d573c2efc9bdec338f05500fe7529facea7fd22bdfd582d5e0916f42145f19e2",
}
SUCCESS_MARKER = "PASS: retry-safe VM provisioning wire contract verified"


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


def load(relative: str) -> dict:
    path = ROOT / relative
    if not path.is_file():
        fail(f"protected fixture missing: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected fixture missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_provenance() -> None:
    contract = load("docs/contract.json")
    source = contract.get("source", {})
    if source.get("repository") != REPOSITORY:
        fail("contract repository is not the official vcf-api-specs repository")
    if source.get("spec_path") != SPEC_PATH:
        fail("contract is not derived from the vCenter automation specification file")
    if source.get("tag") != PINNED_TAG or source.get("commit_sha") != PINNED_SHA:
        fail("contract is not pinned to the 9.0.0.0 revision of the specification")
    if source.get("api_version") != "9.0.0.0":
        fail("contract does not record the 9.0.0.0 API version")
    if source.get("license") != "Apache-2.0":
        fail("contract does not record the specification license")
    for tag, sha in REJECTED_REVISIONS.items():
        if source.get("commit_sha") == sha or source.get("tag") == tag:
            fail(f"contract is derived from the rejected {tag} revision of the specification")

    security = contract.get("security", {})
    if (security.get("scheme"), security.get("in"), security.get("name")) != (
        "api_key_auth", "header", "vmware-api-session-id"
    ):
        fail("contract security scheme differs from the specification")
    if contract.get("json_encoding", {}).get("unset_optional_properties") != "omit":
        fail("contract no longer requires unset optional properties to be omitted")
    if contract.get("idempotency", {}).get("client_token_supported") is not False:
        fail("contract no longer records that this revision defines no client token")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (
            operation.get("method"), operation.get("path"), operation.get("full_path")
        )
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")

    list_operation = next(o for o in operations if o["operationId"] == "Vcenter.VM_list")
    parameters = {p["name"]: p for p in list_operation.get("parameters", [])}
    for required_filter in ("names", "folders"):
        parameter = parameters.get(required_filter)
        if parameter is None:
            fail(f"contract lost the {required_filter} filter of Vcenter.VM_list")
        if parameter.get("style") != "form" or parameter.get("explode") is not True:
            fail(f"contract query serialization for {required_filter} changed")

    create_operation = next(o for o in operations if o["operationId"] == "Vcenter.VM_create")
    request = create_operation.get("request", {})
    if request.get("required_properties") != ["guest_os"]:
        fail("contract required properties of Vcenter.VM.CreateSpec changed")
    if request.get("content_type") != "application/json":
        fail("contract request content type changed")
    if create_operation.get("success", {}).get("status") != 201:
        fail("contract success status of Vcenter.VM_create changed")
    already_exists = [
        error for error in create_operation.get("errors", [])
        if error.get("error_type") == "ALREADY_EXISTS" and error.get("status") == 400
    ]
    if not already_exists:
        fail("contract no longer records the ALREADY_EXISTS outcome of Vcenter.VM_create")

    sources = load("docs/official_sources.json")
    if sources.get("repository") != REPOSITORY:
        fail("official sources do not name the vcf-api-specs repository")
    if sources.get("repository_commit_sha") != PINNED_SHA:
        fail("official sources record a different commit")
    if sources.get("repository_tag") != PINNED_TAG:
        fail("official sources record a different tag")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official sources record a different specification path")
    if sources.get("license") != "Apache-2.0":
        fail("official sources do not record the specification license")

    entries = sources.get("operations", [])
    if len(entries) != len(EXPECTED_OPERATIONS):
        fail("official sources do not record exactly the contract operations")
    for entry in entries:
        operation_id = entry.get("operationId")
        expected = EXPECTED_OPERATIONS.get(operation_id)
        if expected is None:
            fail(f"unrecognized official operationId: {operation_id}")
        if entry.get("repository_commit_sha") != PINNED_SHA:
            fail(f"operation {operation_id} is not commit-pinned")
        if entry.get("spec_path") != SPEC_PATH:
            fail(f"operation {operation_id} does not record the specification path")
        if (entry.get("method", "").upper(), entry.get("path")) != expected[:2]:
            fail(f"operation {operation_id} source mapping changed")


def compile_and_run() -> None:
    production = sorted((ROOT / "src").glob("*.java"))
    if production != [ROOT / "src/VcenterVmProvisioner.java"]:
        fail("the production client must remain a single Java source file")

    sources = [
        ROOT / "src/VcenterVmProvisioner.java",
        ROOT / "tests/MockVcenterServer.java",
        ROOT / "tests/TestMain.java",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf90-0057-") as output:
        compile_result = subprocess.run(
            ["javac", "--release", "17", "-d", output, *map(str, sources)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        run_result = subprocess.run(
            ["java", "-cp", output, "TestMain", str(ROOT / "docs/contract.json")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if run_result.returncode != 0:
            fail("TestMain failed:\n" + run_result.stdout + run_result.stderr)
        if SUCCESS_MARKER not in run_result.stdout:
            fail("TestMain did not emit its success marker")


def main() -> None:
    check_protected_files()
    check_provenance()
    compile_and_run()
    print("PASS: vcf90-0057")


if __name__ == "__main__":
    main()
