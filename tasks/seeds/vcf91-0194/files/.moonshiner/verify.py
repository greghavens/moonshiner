#!/usr/bin/env python3
"""Protected verifier for the single-file VCF log-forwarder client."""

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
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
EXPECTED_OPERATIONS = [
    (
        "testLogForwarderConnection",
        "POST",
        "/api/v2/logs/forwarders/test",
    ),
    (
        "createLogForwarder",
        "POST",
        "/api/v2/logs/forwarders",
    ),
]
PROTECTED_SHA256 = {
    "docs/contract.json": "3aba6f2ff7275a2cd3d908b7eaeb826f691f8b94c9510f062585ead065d0eef0",
    "docs/official_sources.json": "8f0f17477042d1d282e41aaf9c32b9914ee7e4f402bd334ad068e2edc00c3e97",
    "tests/MockVcfLogServer.java": "c9a76cf0ca7f53c2676979e0015bd2e76a7dc5b2bf9343d445f3a6afdad4541c",
    "tests/TestMain.java": "ae61b2af45a7fb418e4b51baa083d8d6b64d08de94ff5ddc1056196435d3fea2",
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


def load_json(relative: str) -> dict[str, object]:
    try:
        value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {relative}: {error}")
    if not isinstance(value, dict):
        fail(f"{relative} must contain a JSON object")
    return value


def verify_contract_projection() -> None:
    contract = load_json("docs/contract.json")
    source = contract.get("source")
    if not isinstance(source, dict):
        fail("contract source metadata is missing")
    expected_source = {
        "kind": "pinned-openapi-specification",
        "repository": "vmware/vcf-api-specs",
        "repositoryCommitSha": COMMIT,
        "specPath": SPEC_PATH,
        "license": "Apache-2.0",
        "openapi": "3.0.1",
        "apiVersion": "9.1.0.0",
    }
    for key, expected in expected_source.items():
        if source.get(key) != expected:
            fail(f"contract source has an unexpected {key}")

    operations = contract.get("operations")
    if not isinstance(operations, list):
        fail("contract operations are missing")
    actual_operations = []
    for operation in operations:
        if not isinstance(operation, dict):
            fail("contract operation is not an object")
        actual_operations.append(
            (
                operation.get("operationId"),
                operation.get("method"),
                operation.get("path"),
            )
        )
        body = operation.get("requestBody")
        if not isinstance(body, dict) or body.get("schema") != "LogForwarder":
            fail("focused operations must use the LogForwarder request schema")
    if actual_operations != EXPECTED_OPERATIONS:
        fail("contract must name only the two ordered focused operations")

    workflow = contract.get("focusedWorkflow")
    if not isinstance(workflow, dict):
        fail("focused workflow is missing")
    expected_ids = [item[0] for item in EXPECTED_OPERATIONS]
    if workflow.get("operationOrder") != expected_ids:
        fail("focused workflow operation order is inconsistent")
    bodies = workflow.get("requestBodies")
    if not isinstance(bodies, dict):
        fail("focused request-body projections are missing")
    expected_properties = {
        "testLogForwarderConnection": [
            "host",
            "port",
            "protocol",
            "sslEnabled",
            "transportProtocol",
        ],
        "createLogForwarder": [
            "enabled",
            "host",
            "name",
            "port",
            "protocol",
            "sslEnabled",
            "transportProtocol",
        ],
    }
    for operation_id, properties in expected_properties.items():
        projection = bodies.get(operation_id)
        if not isinstance(projection, dict):
            fail(f"request-body projection is missing: {operation_id}")
        if projection.get("propertyOrder") != properties:
            fail(f"request property order is inconsistent: {operation_id}")
        if projection.get("unsetBehavior") != "omit":
            fail(f"unset behavior must be omit: {operation_id}")

    schemas = contract.get("schemas")
    if not isinstance(schemas, dict):
        fail("contract schemas are missing")
    forwarder = schemas.get("LogForwarder")
    if not isinstance(forwarder, dict):
        fail("LogForwarder projection is missing")
    properties = forwarder.get("properties")
    expected_schema_order = [
        "certificate",
        "connectionRefreshInterval",
        "constraints",
        "enabled",
        "forwardComplementaryFields",
        "host",
        "id",
        "name",
        "port",
        "protocol",
        "sslEnabled",
        "tags",
        "transportProtocol",
        "workerCount",
    ]
    if not isinstance(properties, dict) or list(properties) != expected_schema_order:
        fail("LogForwarder schema projection is incomplete or reordered")
    identifier = properties.get("id")
    if not isinstance(identifier, dict) or identifier.get("readOnly") is not True:
        fail("LogForwarder.id must retain its readOnly contract marker")

    sources = load_json("docs/official_sources.json")
    if sources.get("repositoryCommitSha") != COMMIT:
        fail("official sources commit is not pinned")
    if sources.get("specPath") != SPEC_PATH:
        fail("official sources specification path is inconsistent")
    if sources.get("operationIds") != expected_ids:
        fail("official sources operationId list is inconsistent")
    source_operations = sources.get("operations")
    if not isinstance(source_operations, list) or len(source_operations) != 2:
        fail("official sources must record each focused operation")
    for expected, recorded in zip(EXPECTED_OPERATIONS, source_operations):
        if not isinstance(recorded, dict):
            fail("official source operation is not an object")
        if (
            recorded.get("operationId"),
            recorded.get("method"),
            recorded.get("path"),
        ) != expected:
            fail("official source operation details are inconsistent")
        if recorded.get("repositoryCommitSha") != COMMIT:
            fail("official source operation commit is not pinned")
        if recorded.get("specPath") != SPEC_PATH:
            fail("official source operation spec path is inconsistent")
    derivation = sources.get("derivation")
    if not isinstance(derivation, dict):
        fail("official source derivation record is missing")
    if derivation.get("documentationPageUsedAsContractSource") is not False:
        fail("the contract source must be the OpenAPI specification")


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
        fail(f"command exited with status {completed.returncode}: {command[0]}")
    return completed


def main() -> None:
    verify_protected_files()
    verify_contract_projection()

    client = ROOT / "VcfLogForwarderClient.java"
    if not client.is_file():
        fail("editable client is missing: VcfLogForwarderClient.java")
    production_java = [
        path
        for path in ROOT.rglob("*.java")
        if "tests" not in path.relative_to(ROOT).parts
    ]
    if production_java != [client]:
        fail("the implementation must remain a single production Java file")

    with tempfile.TemporaryDirectory(prefix="vcf91-0194-") as classes:
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
                str(ROOT / "tests" / "MockVcfLogServer.java"),
                str(ROOT / "tests" / "TestMain.java"),
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

    expected = "PASS: contract-pinned VCF log-forwarder precheck gate"
    if expected not in completed.stdout:
        fail("TestMain did not report its completion sentinel")
    print(expected)


if __name__ == "__main__":
    main()
