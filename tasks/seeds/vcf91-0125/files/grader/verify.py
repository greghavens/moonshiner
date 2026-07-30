#!/usr/bin/env python3
"""Deterministic protected verifier for the VCF 9.1 Java exercise."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC = "specifications/vsphere/openapi/automation/vcenter.yaml"
EXPECTED_OPERATIONS = [
    ("Cis.Session_create", "POST", "/session"),
    ("Vcenter.Cluster_list", "GET", "/vcenter/cluster"),
    ("Cis.Session_delete", "DELETE", "/session"),
]

# These hashes are filled by the seed author after the fixtures are finalized.
# task.json also marks every path here as protected.
PROTECTED_SHA256 = {
    "docs/contract.json": (
        "6a50b2df98ab71ad7a16d270eb0183eae"
        "014a71ff733ce35979f964de6214de0"
    ),
    "docs/official_sources.json": (
        "605ff59d07f40a2b2c4c294162e901b8"
        "94249dd472610694213b650da502d243"
    ),
    "grader/MockVcenterServer.java": (
        "65ff0056c7b4553d38b7d1b0109926b0"
        "d8579c874b6b4735d98d54cb9b077aea"
    ),
    "grader/TestMain.java": (
        "48b13d4e08c7955b5d6f4988dc7f3d48"
        "d524e7984dd76149804538b52c75ad32"
    ),
}


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
            fail(f"protected fixture changed: {relative}")


def check_contract_provenance() -> None:
    contract = json.loads(
        (ROOT / "docs/contract.json").read_text(encoding="utf-8")
    )
    sources = json.loads(
        (ROOT / "docs/official_sources.json").read_text(encoding="utf-8")
    )

    if contract.get("openapi") != "3.0.3":
        fail("contract OpenAPI version changed")
    if contract.get("apiVersion") != "9.1.0.0":
        fail("contract is not pinned to vSphere Automation API 9.1.0.0")
    if contract.get("server", {}).get("basePath") != "/api":
        fail("contract base path changed")
    derived = contract.get("derivedFrom", {})
    if (
        derived.get("repositoryCommitSha") != EXPECTED_COMMIT
        or derived.get("specPath") != EXPECTED_SPEC
        or derived.get("license") != "Apache-2.0"
    ):
        fail("contract source provenance changed")

    expected_ids = [item[0] for item in EXPECTED_OPERATIONS]
    if contract.get("operationIds") != expected_ids:
        fail("contract operationId order changed")
    operations = contract.get("operations")
    if not isinstance(operations, list):
        fail("contract operations are missing")
    projected = [
        (
            item.get("operationId"),
            item.get("method"),
            item.get("path"),
        )
        for item in operations
    ]
    if projected != EXPECTED_OPERATIONS:
        fail("focused operation bindings changed")

    create, listing, deletion = operations
    if (
        create.get("security", {}).get("httpScheme") != "basic"
        or create.get("responses", {}).get("201", {})
        .get("schema", {}).get("type") != "string"
    ):
        fail("session-create projection changed")
    if [item.get("name") for item in listing.get("parameters", [])] != [
        "clusters",
        "names",
        "folders",
        "datacenters",
    ]:
        fail("cluster filter projection changed")
    for parameter in listing["parameters"]:
        if (
            parameter.get("required") is not False
            or parameter.get("in") != "query"
            or parameter.get("style") != "form"
            or parameter.get("explode") is not True
        ):
            fail("cluster optional-query projection changed")
    summary = contract.get("schemas", {}).get("Vcenter.Cluster.Summary", {})
    if summary.get("required") != [
        "cluster",
        "drs_enabled",
        "ha_enabled",
        "name",
    ]:
        fail("cluster summary required fields changed")
    if deletion.get("responses", {}).get("204", {}).get("content", "missing") is not None:
        fail("session-delete response projection changed")

    if (
        sources.get("repositoryCommitSha") != EXPECTED_COMMIT
        or sources.get("specPath") != EXPECTED_SPEC
        or sources.get("license") != "Apache-2.0"
    ):
        fail("official source record changed")
    source_operations = sources.get("operations")
    if not isinstance(source_operations, list):
        fail("official source operations are missing")
    recorded = [
        (
            item.get("operationId"),
            item.get("method"),
            item.get("path"),
        )
        for item in source_operations
    ]
    if recorded != EXPECTED_OPERATIONS:
        fail("official source operation records changed")
    for item in source_operations:
        if (
            item.get("repositoryCommitSha") != EXPECTED_COMMIT
            or item.get("specPath") != EXPECTED_SPEC
        ):
            fail("operation provenance is not recorded at commit granularity")


def compile_and_run() -> None:
    source = ROOT / "src/VcenterCredentialRotationClient.java"
    if not source.is_file():
        fail("missing src/VcenterCredentialRotationClient.java")

    with tempfile.TemporaryDirectory(prefix="vcf91-0125-") as output_dir:
        compiled = subprocess.run(
            [
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
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
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
            timeout=20,
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
