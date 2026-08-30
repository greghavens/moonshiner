#!/usr/bin/env python3
"""Protected verifier for the single-file VCF/VKS Java change client."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_OPERATION = "Vcenter.Namespaces.Instances_update"
KUBERNETES_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch"
)
PROTECTED_SHA256 = {
    "docs/contract.json":
        "9512606494f001a188efc6cb410eb941daabc97e9c47bbe89412212487c37168",
    "docs/official_sources.json":
        "d38581d63fc56a08a407592fe131560109debd71ae968f86516af76f08a80d03",
    "tests/ContractMockServer.java":
        "30596686b156a3939eec0450c0b3e1b9ea1308df9e6e40d29663e4472a75f4b7",
    "tests/TestMain.java":
        "4b0615f7b693e7f6b9a738fd3ff84bff8d4c7f08854a6394929db53b6d022373",
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
    operation = operations[0]
    if operation.get("operationId") != VCENTER_OPERATION:
        fail("contract contains the wrong VMware operationId")
    if operation.get("method") != "PATCH":
        fail("vCenter contract method is wrong")
    if operation.get("pathTemplate") != (
        "/api/vcenter/namespaces/instances/{namespace}"
    ):
        fail("vCenter contract path is wrong")

    kubernetes = contract.get("kubernetesApi", {}).get("operations")
    if not isinstance(kubernetes, list) or len(kubernetes) != 1:
        fail("contract must contain exactly one Kubernetes operation")
    kube_operation = kubernetes[0]
    if kube_operation.get("operationKey") != KUBERNETES_OPERATION:
        fail("contract contains the wrong Kubernetes operation key")
    if "operationId" in kube_operation:
        fail("Kubernetes operation has a fictional VMware operationId")
    if kube_operation.get("method") != "PATCH":
        fail("Kubernetes contract method is wrong")

    if sources.get("repositoryCommitSha") != COMMIT:
        fail("official sources repository commit is not pinned")
    if sources.get("specPath") != SPEC:
        fail("official sources specification path is wrong")
    if sources.get("operationIds") != [VCENTER_OPERATION]:
        fail("official sources do not record every operationId")
    source_operations = sources.get("operations")
    if not isinstance(source_operations, list) or len(source_operations) != 1:
        fail("official sources operation list is not focused")
    recorded = source_operations[0]
    if recorded.get("operationId") != VCENTER_OPERATION:
        fail("official sources operationId is wrong")
    if recorded.get("repositoryCommitSha") != COMMIT:
        fail("operation entry does not repeat the pinned commit")
    if recorded.get("specPath") != SPEC:
        fail("operation entry does not repeat the specification path")


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
    client = ROOT / "src/VcfVksChangeClient.java"
    if not client.is_file():
        fail("editable client is missing")

    with tempfile.TemporaryDirectory(prefix="vcf91-0160-") as classes:
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
            timeout=25,
        )

    sentinel = (
        "PASS: contract-pinned partial VCF/VKS change is reported"
    )
    if sentinel not in completed.stdout:
        fail("TestMain did not report its completion sentinel")
    print(sentinel)


if __name__ == "__main__":
    main()
