#!/usr/bin/env python3
"""Deterministic protected verifier for VcfIdentityProviderClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "docs/contract.json": "37a0225a80ffe62ae6d30cc9c80d5e93df32f1fdd666cc8fbca09917fda06ca3",
    "docs/official_sources.json": "07afa483d817f86508ad3d46b66c0f195b2c8b53016ae487357a01cb920ba91f",
    "tests/TestMain.java": "020992949c624fea5c943eed278c7c817c4f664022d6fed1eee1e92e7ed4dc28",
    "tests/mock_server.py": "46d030281e341fd60b009b9eb1754d16fbf10fb3ba2ecc5125319ccc4101cc56",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_files() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_contract_metadata() -> None:
    contract = json.loads((PROJECT / "docs/contract.json").read_text(encoding="utf-8"))
    source = json.loads(
        (PROJECT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
    spec_path = "specifications/sddc-manager/sddc-manager-openapi.json"
    expected_operations = {
        ("getIdentityPrecheckResult", "GET", "/v1/identity-broker/prechecks"),
        ("addExternalIdentityProvider", "POST", "/v1/identity-providers"),
    }
    if (
        contract["derived_from"]["repository_commit_sha"] != sha
        or source["repository"]["commit_sha"] != sha
    ):
        fail("official source commit is not pinned")
    if (
        contract["derived_from"]["spec_path"] != spec_path
        or source["specification"]["path"] != spec_path
    ):
        fail("official specification path changed")
    projected = {
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    }
    recorded = {
        (item["operationId"], item["method"], item["path"])
        for item in source["operations"]
    }
    if projected != expected_operations or recorded != expected_operations:
        fail("operationId source record changed")
    for operation in source["operations"]:
        if (
            operation["repository_commit_sha"] != sha
            or operation["spec_path"] != spec_path
        ):
            fail("operation provenance is not recorded at commit granularity")

    precheck, mutation = contract["operations"]
    if precheck["operationId"] != "getIdentityPrecheckResult":
        fail("precheck must be the first focused operation")
    if precheck["parameters"] != [
        {
            "name": "type",
            "in": "query",
            "description": "IDP type for which Precheck needs to be run",
            "required": False,
            "schema": {"type": "string"},
        }
    ]:
        fail("optional precheck query projection changed")
    if mutation["request_body"] != {
        "required": True,
        "media_type": "application/json",
        "schema_ref": "#/components/schemas/IdentityProviderSpec",
    }:
        fail("mutation body projection changed")
    provider_schema = contract["schemas"]["IdentityProviderSpec"]
    if provider_schema["required"] != ["name", "type"]:
        fail("IdentityProviderSpec required fields changed")
    if list(provider_schema["properties"]) != [
        "name",
        "type",
        "certChain",
        "ldap",
        "oidc",
        "fedIdpSpec",
    ]:
        fail("IdentityProviderSpec optional field projection changed")


def wait_for_server(port_file: Path, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited during startup\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
            return json.loads(port_file.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("mock did not publish its loopback port")


def check_wire_log(log_path: Path, server_info: dict) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(entries) != 3:
        fail(
            "expected failed precheck, passing precheck, and one mutation; "
            f"got {len(entries)} requests"
        )
    if [entry["sequence"] for entry in entries] != [1, 2, 3]:
        fail("request sequence is not deterministic")
    if [entry["operationId"] for entry in entries] != [
        "getIdentityPrecheckResult",
        "getIdentityPrecheckResult",
        "addExternalIdentityProvider",
    ]:
        fail("precheck did not hard-gate the mutation")

    for index, entry in enumerate(entries[:2], start=1):
        if (
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
        ) != (
            "GET",
            "/v1/identity-broker/prechecks",
            "/v1/identity-broker/prechecks",
            "",
        ):
            fail(f"precheck {index} has the wrong request target: {entry}")
        if entry["body"] != "":
            fail(f"precheck {index} unexpectedly sent a body")
        if "content-type" in entry["headers"]:
            fail(f"precheck {index} must omit Content-Type")
        if "transfer-encoding" in entry["headers"]:
            fail(f"precheck {index} must omit transfer encoding")
        if entry["mutationApplied"] or entry["mutationCountAfter"] != 0:
            fail("a precheck request changed mutation state")

    if [entry["precheckStatus"] for entry in entries[:2]] != [
        "FAILURE",
        "SUCCESS",
    ]:
        fail("mock precheck scenario changed")

    mutation = entries[2]
    if (
        mutation["method"],
        mutation["target"],
        mutation["path"],
        mutation["query"],
    ) != (
        "POST",
        "/v1/identity-providers",
        "/v1/identity-providers",
        "",
    ):
        fail(f"mutation has the wrong request target: {mutation}")
    expected_body = (
        '{"name":'
        + json.dumps(server_info["allowed_name"], ensure_ascii=False)
        + ',"type":'
        + json.dumps(server_info["provider_type"], ensure_ascii=False)
        + "}"
    )
    if mutation["body"] != expected_body:
        fail(
            "mutation body is not the exact compact IdentityProviderSpec: "
            f"{mutation['body']!r}"
        )
    try:
        decoded = json.loads(mutation["body"])
    except json.JSONDecodeError as error:
        fail(f"mutation body is not valid JSON: {error}")
    if decoded != {
        "name": server_info["allowed_name"],
        "type": server_info["provider_type"],
    }:
        fail("mutation JSON values were not escaped or decoded correctly")
    optional = {"certChain", "ldap", "oidc", "fedIdpSpec"}
    if optional.intersection(decoded):
        fail("unset IdentityProviderSpec optionals must be omitted")
    if not mutation["mutationApplied"] or mutation["mutationCountAfter"] != 1:
        fail("passing precheck did not produce exactly one mutation effect")
    if mutation["headers"].get("content-type") != "application/json":
        fail("mutation must send Content-Type: application/json")
    if mutation["headers"].get("content-length") != str(
        len(mutation["body"].encode("utf-8"))
    ):
        fail("mutation Content-Length does not match its UTF-8 body")
    if "transfer-encoding" in mutation["headers"]:
        fail("mutation must not use transfer encoding")

    for entry in entries:
        headers = entry["headers"]
        if headers.get("accept") != "application/json":
            fail("every request must send Accept: application/json")
        if headers.get("authorization") != "Bearer " + server_info["access_token"]:
            fail("every request must send the supplied bearer token")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0042-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(PROJECT / "VcfIdentityProviderClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("Java sources did not compile")

        request_log = temp / "requests.jsonl"
        port_file = temp / "port"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT / "tests/mock_server.py"),
                "--contract",
                str(PROJECT / "docs/contract.json"),
                "--log",
                str(request_log),
                "--port-file",
                str(port_file),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            server_info = wait_for_server(port_file, mock)
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{server_info['port']}/",
                    server_info["access_token"],
                    server_info["blocked_name"],
                    server_info["allowed_name"],
                    server_info["provider_type"],
                ],
                text=True,
                capture_output=True,
                timeout=12,
            )
            if run_result.returncode != 0:
                sys.stderr.write(run_result.stdout + run_result.stderr)
                fail("TestMain failed")
            if run_result.stdout.strip() != "SUCCESSFUL":
                fail(f"unexpected TestMain output: {run_result.stdout!r}")
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

        check_wire_log(request_log, server_info)

    print("PASS: spec-derived identity-provider precheck gate")


if __name__ == "__main__":
    main()
