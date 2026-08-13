#!/usr/bin/env python3
"""Offline protected verification for the resilient VCF Installer wrapper."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from mock_vcf_installer import EXPECTED_OPERATION_IDS, start_contract_server


ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_PATH = ROOT / "VcfInstaller.Resilient" / "VcfInstaller.Resilient.psm1"
MANIFEST_PATH = ROOT / "VcfInstaller.Resilient" / "VcfInstaller.Resilient.psd1"
SPEC_FIXTURE_PATH = ROOT / "fixtures" / "sddc-spec.json"
COMMIT_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"

SDK_COMMANDS = {
    "createToken": "Invoke-VcfInstallerCreateToken",
    "validateSddcSpec": "Invoke-VcfInstallerValidateSddcSpec",
    "deploySddc": "Invoke-VcfInstallerDeploySddc",
    "getTask": "Invoke-VcfInstallerGetTask",
    "refreshAccessToken": "Invoke-VcfInstallerRefreshAccessToken",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_seed_contract() -> dict:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    expected_operations = [
        {"operationId": "createToken", "method": "POST", "path": "/v1/tokens"},
        {
            "operationId": "validateSddcSpec",
            "method": "POST",
            "path": "/v1/sddcs/validations",
        },
        {"operationId": "deploySddc", "method": "POST", "path": "/v1/sddcs"},
        {"operationId": "getTask", "method": "GET", "path": "/v1/tasks/{id}"},
        {
            "operationId": "refreshAccessToken",
            "method": "PATCH",
            "path": "/v1/tokens/access-token/refresh",
        },
    ]

    require(contract["openapi"] == "3.0.1", "contract OpenAPI version changed")
    require(contract["api_version"] == "9.1.0.0", "contract product version changed")
    require(contract["source"]["commit_sha"] == COMMIT_SHA, "contract commit is not pinned")
    require(contract["source"]["spec_path"] == SPEC_PATH, "contract spec path changed")
    require(sources["commit_sha"] == COMMIT_SHA, "official source commit changed")
    require(sources["spec_path"] == SPEC_PATH, "official source path changed")
    require(sources["license"] == "Apache-2.0", "official source license changed")
    require(COMMIT_SHA in sources["spec_url"], "official spec URL is not commit-pinned")

    ids = [operation["operationId"] for operation in contract["operations"]]
    require(set(ids) == EXPECTED_OPERATION_IDS, "contract operationIds changed")
    require(ids == sources["operationIds"], "operationIds are not recorded in source order")
    operation_projection = [
        {key: operation[key] for key in ("operationId", "method", "path")}
        for operation in contract["operations"]
    ]
    require(operation_projection == expected_operations,
            "contract methods, paths, or operationIds changed")
    require(sources["operations"] == expected_operations,
            "official sources do not record every exact operation")

    token_schema = contract["schemas"]["TokenCreationSpec"]
    require("required" not in token_schema, "source makes token fields optional")
    require(
        set(token_schema["properties"]) == {"username", "password", "apiKey", "idToken"},
        "TokenCreationSpec properties changed",
    )
    deploy = next(item for item in contract["operations"] if item["operationId"] == "deploySddc")
    skip = deploy["parameters"][0]
    require(skip == {
        "name": "skipValidations",
        "in": "query",
        "required": False,
        "schema": {"type": "boolean", "default": False},
    }, "deploySddc optional query contract changed")
    require(contract["schemas"]["SddcNetworkSpec"]["properties"]["vlanId"] == {
        "type": "integer",
        "format": "int32",
        "minimum": 0,
        "maximum": 4094,
    }, "SddcNetworkSpec VLAN contract changed")
    return contract


def assert_module_shape() -> None:
    require(MODULE_PATH.is_file(), "missing VcfInstaller.Resilient.psm1")
    module_text = MODULE_PATH.read_text(encoding="utf-8")
    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    require("13.5.0.25380678" in manifest_text, "PowerCLI prerequisite is not pinned")
    require("Initialize-VcfInstallerTokenCreationSpec" in module_text,
            "implementation must use the SDK token model initializer")
    for operation_id, command in SDK_COMMANDS.items():
        require(operation_id in module_text, f"module does not name operationId {operation_id}")
        require(command in module_text, f"module does not guard SDK command {command}")

    forbidden_suffixes = {".dll", ".nupkg"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        require(path.suffix.lower() not in forbidden_suffixes,
                f"vendored binary/package is forbidden: {path.relative_to(ROOT)}")
        require(not path.name.startswith("VMware.Sdk.Vcf.Installer"),
                f"vendored VMware module file is forbidden: {path.relative_to(ROOT)}")


def read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def assert_json_content_type(record: dict) -> None:
    content_type = record["contentType"] or ""
    require(content_type.lower().startswith("application/json"),
            f"{record['operationId']} did not use application/json")


def assert_wire_shape(records: list[dict], expected_deploy_target: str) -> None:
    expected_sequence = [
        "createToken",
        "validateSddcSpec",
        "deploySddc",
        "getTask",
        "getTask",
        "refreshAccessToken",
        "getTask",
    ]
    require([item["operationId"] for item in records] == expected_sequence,
            "request sequence changed; work may have been repeated or lost")

    token, validation, deploy, poll_one, expired_poll, refresh, retried_poll = records
    specification = json.loads(SPEC_FIXTURE_PATH.read_text(encoding="utf-8"))

    require(token["method"] == "POST" and token["target"] == "/v1/tokens",
            "createToken target is wrong")
    require(token["authorization"] is None, "createToken must not send Authorization")
    assert_json_content_type(token)
    token_body = json.loads(token["bodyText"])
    require(token_body == {
        "username": "admin@local",
        "password": "Example-Installer1!Pass",
    }, "TokenCreationSpec must contain exactly username and password")
    require("apiKey" not in token_body and "idToken" not in token_body,
            "unset optional token fields were serialized")

    require(validation["method"] == "POST" and
            validation["target"] == "/v1/sddcs/validations",
            "validateSddcSpec target is wrong")
    require(validation["authorization"] == "Bearer access-before-expiry",
            "validation bearer token is wrong")
    assert_json_content_type(validation)
    require(json.loads(validation["bodyText"]) == specification,
            "validation body must equal the supplied SDDC specification")

    require(deploy["method"] == "POST" and
            deploy["target"] == expected_deploy_target,
            "deploySddc did not preserve the bound/unbound skipValidations value")
    expected_query = urlsplit(expected_deploy_target).query
    require(deploy["query"] == expected_query,
            "deploySddc query string does not match the caller binding")
    require(deploy["authorization"] == "Bearer access-before-expiry",
            "deployment bearer token is wrong")
    assert_json_content_type(deploy)
    require(json.loads(deploy["bodyText"]) == specification,
            "deployment body must equal the supplied SDDC specification")

    for poll in (poll_one, expired_poll):
        require(poll["method"] == "GET" and poll["target"] == "/v1/tasks/task-42",
                "poll target is wrong")
        require(poll["authorization"] == "Bearer access-before-expiry",
                "pre-refresh poll bearer is wrong")
        require(poll["bodyText"] == "", "GET poll unexpectedly sent a body")

    require(refresh["method"] == "PATCH" and
            refresh["target"] == "/v1/tokens/access-token/refresh",
            "refreshAccessToken target is wrong")
    require(refresh["authorization"] is None,
            "refresh request must not replay the expired bearer token")
    assert_json_content_type(refresh)
    require(json.loads(refresh["bodyText"]) == "refresh-for-run",
            "refresh request body must be the refresh id as a JSON string")

    require(retried_poll["method"] == "GET" and
            retried_poll["target"] == "/v1/tasks/task-42",
            "failed poll was not retried")
    require(retried_poll["authorization"] == "Bearer access-after-refresh",
            "retried poll did not use the refreshed bearer")
    require(retried_poll["bodyText"] == "", "retried GET unexpectedly sent a body")

    require(sum(item["operationId"] == "createToken" for item in records) == 1,
            "token creation was repeated")
    require(sum(item["operationId"] == "validateSddcSpec" for item in records) == 1,
            "validation was repeated")
    require(sum(item["operationId"] == "deploySddc" for item in records) == 1,
            "deployment was submitted more than once")
    require(sum(item["operationId"] == "refreshAccessToken" for item in records) == 1,
            "access token was refreshed an unexpected number of times")


def run_workflow(
    scenario: str,
    *,
    skip_mode: str = "Unbound",
    max_poll_count: int = 6,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    with tempfile.TemporaryDirectory(prefix=".vcf-verifier-", dir=ROOT) as temp_name:
        log_path = Path(temp_name) / "requests.jsonl"
        server = start_contract_server(CONTRACT_PATH, log_path, scenario)
        try:
            completed = subprocess.run(
                [
                    shutil.which("pwsh") or "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(ROOT / "tests" / "run_workflow.ps1"),
                    "-ServerUri",
                    server.uri,
                    "-SkipValidationsMode",
                    skip_mode,
                    "-MaxPollCount",
                    str(max_poll_count),
                ],
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=90,
            )
        finally:
            server.shutdown()
            server.server_close()

        records = read_log(log_path)
    return completed, records


def assert_no_secret_output(completed: subprocess.CompletedProcess[str]) -> None:
    output = completed.stdout + completed.stderr
    for secret in (
        "Example-Installer1!Pass",
        "access-before-expiry",
        "access-after-refresh",
        "refresh-for-run",
    ):
        require(secret not in output, f"workflow exposed sensitive value {secret!r}")


def assert_success_scenario(skip_mode: str, expected_target: str) -> None:
    completed, records = run_workflow("refresh-success", skip_mode=skip_mode)
    require(completed.returncode == 0,
            "PowerShell workflow failed:\n" + completed.stdout[-1000:] + completed.stderr[-2000:])
    assert_no_secret_output(completed)

    output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    require(output_lines, "PowerShell workflow produced no result")
    result = json.loads(output_lines[-1])
    require(result["id"] == "task-42", "workflow returned the wrong task")
    require(result["status"] == "SUCCESSFUL",
            "workflow did not return terminal success")
    assert_wire_shape(records, expected_target)


def assert_failure_scenario(
    scenario: str,
    expected_sequence: list[str],
    expected_message_parts: tuple[str, ...],
    *,
    max_poll_count: int = 6,
) -> None:
    completed, records = run_workflow(
        scenario,
        max_poll_count=max_poll_count,
    )
    require(completed.returncode != 0,
            f"{scenario} unexpectedly returned a successful result")
    assert_no_secret_output(completed)
    output = completed.stdout + completed.stderr
    for part in expected_message_parts:
        require(part in output,
                f"{scenario} did not fail clearly with {part!r}:\n{output[-2000:]}")
    require([item["operationId"] for item in records] == expected_sequence,
            f"{scenario} repeated or skipped workflow operations")
    require(sum(item["operationId"] == "deploySddc" for item in records) == 1,
            f"{scenario} resubmitted the deployment")


def main() -> int:
    assert_seed_contract()
    assert_module_shape()
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh prerequisite is unavailable")

    assert_success_scenario("Unbound", "/v1/sddcs")
    assert_success_scenario("True", "/v1/sddcs?skipValidations=true")
    assert_success_scenario("False", "/v1/sddcs?skipValidations=false")
    assert_failure_scenario(
        "deploy-error",
        ["createToken", "validateSddcSpec", "deploySddc"],
        ("503", "planned deployment rejection"),
    )
    assert_failure_scenario(
        "terminal-failure",
        ["createToken", "validateSddcSpec", "deploySddc", "getTask"],
        ("task-42", "FAILED"),
    )
    assert_failure_scenario(
        "poll-exhaustion",
        [
            "createToken",
            "validateSddcSpec",
            "deploySddc",
            "getTask",
            "getTask",
        ],
        ("task-42", "2"),
        max_poll_count=2,
    )

    print("PASS: VCF Installer token refresh preserved the deployment and exact wire contract")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
