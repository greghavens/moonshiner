#!/usr/bin/env python3
"""Protected deterministic verifier for vcf90-0040.

Checks the pinned provenance of the local contract, that the package stays
stdlib-only, and then drives it against the loopback mock. No live VMware
endpoint is contacted.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
PINNED_TAG = "9.0.0.0"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
API_VERSION = "9.0.0.0"
EXPECTED_OPERATIONS = {
    "Vcenter.Lcm.Deployment.MigrationUpgrade_get": (
        "GET",
        "/api/vcenter/lcm/deployment/migration-upgrade",
        {},
    ),
    "Vcenter.Lcm.Deployment.MigrationUpgrade_apply": (
        "POST",
        "/api/vcenter/lcm/deployment/migration-upgrade",
        {"action": "apply"},
    ),
    "Vcenter.Lcm.Deployment.MigrationUpgrade.Status_get": (
        "GET",
        "/api/vcenter/lcm/deployment/migration-upgrade/status",
        {},
    ),
    "Vcenter.Lcm.Deployment.MigrationUpgrade_cancel": (
        "POST",
        "/api/vcenter/lcm/deployment/migration-upgrade",
        {"action": "cancel"},
    ),
}
PROTECTED_SHA256 = {
    "docs/contract.json": "b45b4d429e9ab64fb10e5d90c98fff2958bef2177b61d0fa4a8e4b833e51ddf4",
    "docs/official_sources.json": "7b56eea7c36b669dec016eb30c63f62d3f9c1a5195f304c18827b4cdd9d26b04",
    "tests/mock_vcenter.py": "6fb8eab1de7bb25a1cf1ac6626f8948a5d9850584e407ca9bd2f8d14b6f3b2c6",
    "tests/run_scenarios.py": "49302a181827063ef597194e9c5c22782a50552465613c1f674b846c5355350b",
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
    if source.get("commit_sha") != PINNED_SHA or source.get("tag") != PINNED_TAG:
        fail("contract is not pinned to the 9.0.0.0 tag of the specification")
    if source.get("spec_path") != SPEC_PATH:
        fail("contract is not derived from the vcenter automation specification")
    if source.get("api_version") != API_VERSION or source.get("license") != "Apache-2.0":
        fail("contract API version or license changed")

    security = contract.get("security", {})
    if (security.get("scheme"), security.get("in"), security.get("name")) != (
        "api_key_auth",
        "header",
        "vmware-api-session-id",
    ):
        fail("contract security scheme differs from the specification")
    if contract.get("wire_rules", {}).get("omit_unset_optional_fields") is not True:
        fail("contract no longer requires unset optional fields to be omitted")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (
            operation.get("method"),
            operation.get("path"),
            operation.get("query") or {},
        )
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")

    by_id = {operation["operationId"]: operation for operation in operations}
    apply_schema = by_id["Vcenter.Lcm.Deployment.MigrationUpgrade_apply"]["request"]
    if apply_schema.get("required") is not False:
        fail("the apply request body must stay optional")
    properties = apply_schema["schema"]["properties"]
    for name in ("pause", "start_switchover"):
        if properties[name].get("omit_when_unset") is not True:
            fail(f"optional property {name} must be omitted when unset")
    if properties["pause"].get("enum") != ["BEFORE_SWITCHOVER"]:
        fail("PausePolicy enum changed")
    if apply_schema["schema"].get("mutually_exclusive") != [
        ["pause", "start_switchover"]
    ]:
        fail("the apply mutual exclusion rule changed")
    if by_id["Vcenter.Lcm.Deployment.MigrationUpgrade_apply"]["success"]["status"] != 204:
        fail("apply success status changed")
    if not by_id["Vcenter.Lcm.Deployment.MigrationUpgrade_apply"]["asynchronous"][
        "is_async"
    ]:
        fail("apply must remain an asynchronous operation")
    polling = by_id["Vcenter.Lcm.Deployment.MigrationUpgrade.Status_get"]["polling"]
    if polling.get("terminal_statuses") != ["SUCCEEDED", "FAILED", "CANCELED"]:
        fail("terminal status set changed")
    if polling.get("non_terminal_statuses") != ["PENDING", "RUNNING", "BLOCKED"]:
        fail("non-terminal status set changed")

    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    if sources.get("repository_commit_sha") != PINNED_SHA:
        fail("official source commit changed")
    if sources.get("repository_tag") != PINNED_TAG:
        fail("official source tag changed")
    if sources.get("spec_path") != SPEC_PATH:
        fail("official source path changed")
    if sources.get("spec_info_version") != API_VERSION:
        fail("official source records a different specification revision")
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
        if entry.get("method", "").upper() != expected[0]:
            fail(f"operation {operation_id} method does not match the contract")
        if entry.get("path") != expected[1]:
            fail(f"operation {operation_id} path does not match the contract")


def check_stdlib_only() -> None:
    package = ROOT / "src" / "vcflcm"
    if not (package / "__init__.py").is_file():
        fail("src/vcflcm must remain an importable package")
    modules = sorted(p for p in (ROOT / "src").rglob("*.py"))
    if not modules:
        fail("no python sources found under src/")
    local = {p.name for p in (ROOT / "src").iterdir()}
    local |= {p.stem for p in (ROOT / "src").glob("*.py")}
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                names = [(node.module or "").split(".")[0]]
            for name in names:
                if not name or name in local:
                    continue
                if name not in sys.stdlib_module_names:
                    relative = path.relative_to(ROOT)
                    fail(
                        f"{relative} imports third-party module {name!r}; the package "
                        "must stay stdlib-only"
                    )


def run_scenarios() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "run_scenarios.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        fail("scenario run failed:\n" + result.stdout + result.stderr)
    marker = "PASS: contract wire shape and poll-to-terminal upgrade verified"
    if marker not in result.stdout:
        fail("scenario run did not emit its success marker:\n" + result.stdout)


def main() -> None:
    check_protected_files()
    check_provenance()
    check_stdlib_only()
    run_scenarios()
    print("PASS: vcf90-0040")


if __name__ == "__main__":
    main()
