#!/usr/bin/env python3
"""Protected verifier for the single-file VCF/VKS Java client."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_OPERATION = "Vcenter.Namespaces.User.Instances_list"
KUBERNETES_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)
PROTECTED_SHA256 = {
    "docs/contract.json":
        "50cbb74f253dfa13322e6b7bd046c0b30b57cf353bef743aea3c6305c70b38fd",
    "docs/official_sources.json":
        "bdffac95d38e7d67082e662f777dbc52b88dd78ad38a44f90e145b28fd098341",
    "tests/ContractMockServer.java":
        "c2435869a605485677a8edd71c380a464fcb0cb993974dd16e91194e1d68fe16",
    "tests/TestMain.java":
        "5ae9e009590d5056ba0dc5c92f66a69f4d2f4791f97465c8027104bfa56138bc",
}


def fail(message: str) -> NoReturn:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def verify_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected file is missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected file was modified: {relative}")


def verify_contract() -> None:
    try:
        contract = json.loads(
            (ROOT / "docs/contract.json").read_text(encoding="utf-8")
        )
        sources = json.loads(
            (ROOT / "docs/official_sources.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"protected contract cannot be loaded: {exc}")

    source = contract.get("source", {})
    if source.get("repositoryCommitSha") != COMMIT:
        fail("contract repository commit is not pinned")
    if source.get("specPath") != SPEC:
        fail("contract specification path is wrong")
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        fail("contract must contain exactly one VMware operation")
    if operations[0].get("operationId") != VCENTER_OPERATION:
        fail("contract contains the wrong VMware operationId")
    kubernetes = contract.get("kubernetesApi", {}).get("operations")
    if not isinstance(kubernetes, list) or len(kubernetes) != 1:
        fail("contract must contain exactly one Kubernetes operation")
    if kubernetes[0].get("operationKey") != KUBERNETES_OPERATION:
        fail("contract contains the wrong Kubernetes operation key")
    if "operationId" in kubernetes[0]:
        fail("Kubernetes operation must not have a fictional VMware operationId")
    if sources.get("repositoryCommitSha") != COMMIT:
        fail("official sources repository commit is not pinned")
    if sources.get("specPath") != SPEC:
        fail("official sources specification path is wrong")
    if sources.get("operationIds") != [VCENTER_OPERATION]:
        fail("official sources do not record every operationId")


def run_checked(
    command: list[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        fail(f"required command is unavailable: {command[0]}")
    except subprocess.TimeoutExpired:
        fail(f"command timed out: {command[0]}")
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, end="", file=sys.stderr)
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        fail(
            f"command exited with status {completed.returncode}: "
            f"{command[0]}"
        )
    return completed


def main() -> None:
    verify_protected_files()
    verify_contract()
    client = ROOT / "src/VcfVksInventoryClient.java"
    if not client.is_file():
        fail("editable client is missing")

    with tempfile.TemporaryDirectory(prefix="vcf91-0157-") as classes:
        run_checked(
            [
                "javac",
                "--release",
                "17",
                "--add-modules",
                "jdk.httpserver",
                "-encoding",
                "UTF-8",
                "-d",
                classes,
                str(client),
                str(ROOT / "tests/ContractMockServer.java"),
                str(ROOT / "tests/TestMain.java"),
            ],
            timeout=20,
        )
        completed = run_checked(
            [
                "java",
                "--add-modules",
                "jdk.httpserver",
                "-cp",
                classes,
                "TestMain",
            ],
            timeout=20,
        )

    sentinel = (
        "PASS: contract-pinned Supervisor token refresh preserves work"
    )
    if sentinel not in completed.stdout:
        fail("TestMain did not report its completion sentinel")
    print(sentinel)


if __name__ == "__main__":
    main()
