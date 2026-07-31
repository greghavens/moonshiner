#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0171."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfOpsLogCredential" / "VcfOpsLogCredential.psd1"
MODULE_PATH = ROOT / "VcfOpsLogCredential" / "VcfOpsLogCredential.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = [
    "createAgentSecret",
    "createAgentSession",
    "revokeAgentSecret",
]
ROUTES = [
    ("POST", "/api/v2/agent/secrets"),
    ("POST", "/api/v2/agent/secrets/exchange"),
    ("POST", "/api/v2/agent/secrets/{secretName}/revoke"),
]
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"
EXPORTS = [
    "New-VcfOpsLogCredentialGate",
    "Get-VcfOpsLogCredentialLease",
    "Invoke-VcfOpsLogCredentialRotation",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract_and_provenance() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)

    source = contract["source"]
    require(source["kind"] == "pinned-openapi-specification", "source kind changed")
    require(source["repository"] == "vmware/vcf-api-specs", "repository changed")
    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract spec blob changed")
    require(source["license"] == "Apache-2.0", "source license changed")
    require(source["openapi"] == "3.0.1", "OpenAPI version changed")
    require(source["apiVersion"] == "9.1.0.0", "API version changed")
    require(source["title"] == "Log Management API", "API title changed")
    require(
        source["serverUrlInSpecification"] == "http://localhost:8787",
        "specification server projection changed",
    )
    require(
        contract["securitySchemes"]["OPSTokenAuthorization"]
        == {
            "type": "apiKey",
            "in": "header",
            "name": "X-JWT-Token",
        },
        "security scheme projection changed",
    )

    operations = contract["operations"]
    require(
        [item["operationId"] for item in operations] == OPERATION_IDS,
        "focused operationIds changed",
    )
    require(
        [(item["method"], item["path"]) for item in operations] == ROUTES,
        "focused methods or paths changed",
    )
    require(
        all(item["security"] == ["OPSTokenAuthorization"] for item in operations),
        "focused operation security changed",
    )
    require(
        operations[0]["requestBody"]
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "AgentSecretCreateRequest",
        },
        "create request projection changed",
    )
    require(
        operations[1]["requestBody"]
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "AgentAuthenticationRequest",
        },
        "exchange request projection changed",
    )
    require(
        operations[2]["parameters"]
        == [
            {
                "name": "secretName",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
        and operations[2]["requestBody"] is None,
        "revoke path parameter or body projection changed",
    )
    require(
        "does not invalidate any previously created token"
        in operations[2]["description"],
        "revoke token-survival rule changed",
    )
    require(
        operations[0]["responses"]["201"]["schema"]
        == "AgentSecretCreateResponse"
        and operations[1]["responses"]["200"]["schema"]
        == "AgentAuthenticationResponse"
        and operations[2]["responses"]["200"]["schema"]
        == "AgentSecretRevokeResponse",
        "success response schemas changed",
    )

    schemas = contract["schemas"]
    require(
        schemas["AgentAuthenticationRequest"]["required"] == ["secret"]
        and list(schemas["AgentAuthenticationRequest"]["properties"])
        == ["secret", "ttl"]
        and schemas["AgentAuthenticationRequest"]["properties"]["ttl"]
        == {"type": "integer", "format": "int64"},
        "exchange request schema changed",
    )
    require(
        schemas["AgentAuthenticationResponse"]["required"]
        == ["access_token", "name", "new_secret", "ttl"]
        and list(schemas["AgentAuthenticationResponse"]["properties"])
        == ["access_token", "name", "new_secret", "ttl"],
        "exchange response schema changed",
    )
    require(
        list(schemas["AgentSecretCreateRequest"]["properties"]) == ["name"],
        "create request schema changed",
    )
    require(
        list(schemas["AgentSecretCreateResponse"]["properties"])
        == ["id", "name", "secret", "status"],
        "create response schema changed",
    )
    require(
        list(schemas["AgentSecretRevokeResponse"]["properties"])
        == ["id", "name", "status"],
        "revoke response schema changed",
    )

    focused = contract["focusedRotation"]
    require(
        focused["operationOrder"] == OPERATION_IDS,
        "focused workflow order changed",
    )
    require(
        focused["exchangeRequestPropertyOrder"] == ["secret", "ttl"]
        and focused["optionalExchangeProperties"] == ["ttl"]
        and focused["unsetBehavior"] == "omit",
        "focused optional-field rules changed",
    )
    require(
        focused["sessionTtl"]
        == {
            "unit": "milliseconds",
            "minimum": 60_000,
            "maximum": 15_552_000_000,
            "defaultWhenOmittedOrZero": 1_800_000,
            "source": "createAgentSession operation description",
        },
        "specification-derived TTL semantics changed",
    )
    require(
        "old generation has no active leases" in focused["drainRule"]
        and "old service secret unrevoked" in focused["timeoutRule"],
        "focused drain safety rules changed",
    )

    require(sources["repository"] == "vmware/vcf-api-specs", "source repo changed")
    require(sources["repositoryCommitSha"] == COMMIT, "source commit changed")
    require(sources["specPath"] == SPEC_PATH, "source spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "source spec blob changed")
    require(sources["license"] == "Apache-2.0", "source license changed")
    require(sources["operationIds"] == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources["specUrl"] and sources["specUrl"].endswith(SPEC_PATH),
        "specification URL is not immutable",
    )
    expected_lines = [437, 488, 528]
    for record, operation_id, route, line in zip(
        sources["operations"],
        OPERATION_IDS,
        ROUTES,
        expected_lines,
    ):
        require(record["operationId"] == operation_id, "operation source id changed")
        require(
            (record["method"], record["path"]) == route,
            "operation source route changed",
        )
        require(
            record["operationIdSpecLine"] == line,
            "operationId specification line changed",
        )
        require(
            record["repositoryCommitSha"] == COMMIT
            and record["specPath"] == SPEC_PATH,
            "each operation must record the pinned commit and spec path",
        )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"] is False,
        "a documentation page must not be the contract source",
    )


def verify_manifest_and_module_shape() -> None:
    command = (
        "$d = Import-PowerShellDataFile -Path '"
        + str(MANIFEST_PATH).replace("'", "''")
        + "'; "
        + "if ($d.RootModule -cne 'VcfOpsLogCredential.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if (($d.FunctionsToExport -join ',') -cne '"
        + ",".join(EXPORTS)
        + "') { exit 5 }; "
        + "$r = $d.RequiredModules[0]; "
        + "if ($r.ModuleName -cne '"
        + MODULE_NAME
        + "' -or [string]$r.RequiredVersion -cne '"
        + MODULE_VERSION
        + "') { exit 6 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
        env={
            **os.environ,
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        },
    )
    require(result.returncode == 0, "protected module manifest is invalid")

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    require(
        "system.net.http.httpclient" in folded
        or "net.http.httpclient" in folded,
        "implementation must use System.Net.Http.HttpClient",
    )
    for function_name in EXPORTS:
        require(
            re.search(
                rf"(?im)^\s*function\s+{re.escape(function_name)}\b",
                source,
            )
            is not None,
            f"missing exported function implementation: {function_name}",
        )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.net.sockets",
        "tcpclient",
        "curl ",
    ):
        require(forbidden not in folded, f"forbidden transport found: {forbidden}")

    vendored_suffixes = {".dll", ".nupkg", ".nuspec"}
    vendored_names = {"VMware.Sdk.Vcf.Ops.psm1", "VMware.Sdk.Vcf.Ops.psd1"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        require(
            path.suffix.casefold() not in vendored_suffixes
            and path.name not in vendored_names,
            f"vendored VMware dependency or binary found: {path.relative_to(ROOT)}",
        )


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return load_json(path)
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(
                "mock exited before ready: " + (stderr or stdout).strip()
            )
        time.sleep(0.025)
    raise VerificationError("mock did not become ready")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def execute_case(
    config: dict[str, Any],
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0171-") as temp_text:
        temp = Path(temp_text)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        output_path = temp / "output.json"
        config_path.write_text(
            json.dumps(config, separators=(",", ":")),
            encoding="utf-8",
        )

        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--config",
                str(config_path),
                "--log",
                str(log_path),
                "--ready",
                str(ready_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready = wait_for_ready(ready_path, process)
            require(ready["host"] == "127.0.0.1", "mock host is not loopback")
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-Port",
                    str(ready["port"]),
                    "-ConfigPath",
                    str(config_path),
                    "-RequestLogPath",
                    str(log_path),
                    "-OutputPath",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        output = load_json(output_path) if output_path.exists() else None
        entries = read_log(log_path)
        return result, output, entries


def runtime_config(
    *,
    hold_old_lease: bool,
    bind_ttl: bool,
    max_drain_checks: int,
    drain_interval: int,
) -> dict[str, Any]:
    request_ttl = 60_000 + secrets.randbelow(120_000)
    return {
        "old_name": f"old collectör/雪?{secrets.token_hex(5)}",
        "old_secret": f"old-secret-{secrets.token_urlsafe(28)}",
        "old_access_token": f"old-access-{secrets.token_urlsafe(32)}",
        "new_name": f'new collectör-雪-"{secrets.token_hex(7)}',
        "created_id": f"created-{secrets.token_hex(10)}",
        "create_secret": f"once-{secrets.token_urlsafe(30)}",
        "create_status": "ACTIVE",
        "new_secret": f"next-{secrets.token_urlsafe(30)}",
        "new_access_token": f"new-access-{secrets.token_urlsafe(34)}",
        "response_ttl": request_ttl if bind_ttl else 1_800_000,
        "revoked_id": f"revoked-{secrets.token_hex(10)}",
        "revoke_status": "REVOKED",
        "log_token": f"jwt-{secrets.token_urlsafe(36)}",
        "hold_old_lease": hold_old_lease,
        "bind_ttl": bind_ttl,
        "request_ttl": request_ttl,
        "max_drain_checks": max_drain_checks,
        "drain_interval": drain_interval,
    }


def single_header(entry: dict[str, Any], name: str) -> str:
    values = entry["headers"].get(name.casefold(), [])
    require(len(values) == 1, f"{name} must appear exactly once")
    return values[0]


def verify_request_log(
    entries: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    expect_revoke: bool,
) -> None:
    expected_ids = OPERATION_IDS if expect_revoke else OPERATION_IDS[:2]
    require(
        [item["operationId"] for item in entries] == expected_ids,
        "request operation order or count changed",
    )
    require(
        [item["method"] for item in entries] == ["POST"] * len(expected_ids),
        "focused requests must all use POST",
    )

    create_body = json.dumps(
        {"name": config["new_name"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    create = entries[0]
    require(create["raw_target"] == ROUTES[0][1], "create target changed")
    require(create["query"] == "", "create sent a query")
    require(create["body_raw"] == create_body, "create JSON bytes changed")
    require(list(create["body"]) == ["name"], "create property order changed")
    require(
        create["body"] == {"name": config["new_name"]},
        "create body member set changed",
    )

    exchange_value: dict[str, Any] = {"secret": config["create_secret"]}
    if config["bind_ttl"]:
        exchange_value["ttl"] = config["request_ttl"]
    exchange_body = json.dumps(
        exchange_value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    exchange = entries[1]
    require(exchange["raw_target"] == ROUTES[1][1], "exchange target changed")
    require(exchange["query"] == "", "exchange sent a query")
    require(exchange["body_raw"] == exchange_body, "exchange JSON bytes changed")
    require(
        list(exchange["body"]) == list(exchange_value),
        "exchange property order changed",
    )
    require(exchange["body"] == exchange_value, "exchange body values changed")
    if not config["bind_ttl"]:
        require("ttl" not in exchange["body"], "unset ttl was serialized")
        require(
            exchange["body_raw"]
            == json.dumps(
                {"secret": config["create_secret"]},
                separators=(",", ":"),
            ),
            "unset optional fields changed the exchange bytes",
        )

    for index, entry in enumerate(entries[:2], start=1):
        require(
            single_header(entry, "content-type") == "application/json",
            f"JSON request {index} Content-Type changed",
        )
        require(
            entry["body_bytes"] == len(entry["body_raw"].encode("utf-8")),
            f"JSON request {index} byte count changed",
        )
        require(
            all(value not in (None, "", [], {}) for value in entry["body"].values()),
            f"JSON request {index} contains an empty optional value",
        )

    if expect_revoke:
        revoke = entries[2]
        encoded_name = quote(config["old_name"], safe="")
        expected_target = f"/api/v2/agent/secrets/{encoded_name}/revoke"
        require(revoke["raw_target"] == expected_target, "revoke target changed")
        require(revoke["query"] == "", "revoke sent a query")
        require(
            revoke["path_parameters"] == {"secretName": config["old_name"]},
            "old name was not encoded as one path segment",
        )
        require(revoke["body_bytes"] == 0, "revoke sent a request body")
        require(revoke["body_raw"] == "", "revoke body changed")
        require(
            "content-type" not in revoke["headers"],
            "bodyless revoke sent Content-Type",
        )

    for index, entry in enumerate(entries, start=1):
        require("?" not in entry["raw_target"], f"request {index} sent query syntax")
        require(
            single_header(entry, "accept") == "application/json",
            f"request {index} Accept changed",
        )
        require(
            single_header(entry, "x-jwt-token") == config["log_token"],
            f"request {index} log token changed",
        )
        require(
            "authorization" not in entry["headers"]
            and "vmware-api-session-id" not in entry["headers"],
            f"request {index} sent an unrelated credential",
        )


def verify_create_request(
    entry: dict[str, Any],
    config: dict[str, Any],
) -> None:
    expected_body = json.dumps(
        {"name": config["new_name"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    require(entry["operationId"] == OPERATION_IDS[0], "create operation changed")
    require(entry["method"] == "POST", "create method changed")
    require(entry["raw_target"] == ROUTES[0][1], "create target changed")
    require(entry["query"] == "", "create sent a query")
    require(entry["body_raw"] == expected_body, "create JSON bytes changed")
    require(entry["body"] == {"name": config["new_name"]}, "create body changed")
    require(
        single_header(entry, "content-type") == "application/json",
        "create Content-Type changed",
    )
    require(
        single_header(entry, "accept") == "application/json",
        "create Accept changed",
    )
    require(
        single_header(entry, "x-jwt-token") == config["log_token"],
        "create log token changed",
    )


def require_no_console_leak(
    result: subprocess.CompletedProcess[str],
    config: dict[str, Any],
) -> str:
    combined = result.stdout + result.stderr
    sensitive = [
        config["old_secret"],
        config["old_access_token"],
        config["create_secret"],
        config["new_secret"],
        config["new_access_token"],
        config["log_token"],
    ]
    for value in sensitive:
        require(value not in combined, "credential leaked to console output")
    return combined


def require_no_error_leak(output: dict[str, Any], config: dict[str, Any]) -> None:
    error_message = str(output["ErrorMessage"])
    for credential in (
        config["old_secret"],
        config["old_access_token"],
        config["create_secret"],
        config["new_secret"],
        config["new_access_token"],
        config["log_token"],
    ):
        require(credential not in error_message, "error leaked a credential")


def expected_lease(
    config: dict[str, Any], generation: str
) -> dict[str, str]:
    if generation == "old":
        return {
            "SecretName": config["old_name"],
            "Secret": config["old_secret"],
            "AccessToken": config["old_access_token"],
        }
    return {
        "SecretName": config["new_name"],
        "Secret": config["new_secret"],
        "AccessToken": config["new_access_token"],
    }


def verify_rotation_result(
    result: dict[str, Any],
    config: dict[str, Any],
    *,
    drain_checks: int,
    waited: bool,
) -> None:
    require(
        list(result)
        == [
            "Status",
            "OldName",
            "NewName",
            "CreatedId",
            "DrainCheckCount",
            "WaitedForDrain",
        ],
        "rotation result property order changed",
    )
    require(result["Status"] == "Rotated", "rotation status changed")
    require(result["OldName"] == config["old_name"], "old name changed")
    require(result["NewName"] == config["new_name"], "new name changed")
    require(result["CreatedId"] == config["created_id"], "created id changed")
    require(result["DrainCheckCount"] == drain_checks, "drain count changed")
    require(result["WaitedForDrain"] is waited, "waited flag changed")
    serialized = json.dumps(result, separators=(",", ":"))
    for credential in (
        config["old_secret"],
        config["old_access_token"],
        config["create_secret"],
        config["new_secret"],
        config["new_access_token"],
        config["log_token"],
    ):
        require(credential not in serialized, "rotation result leaked a credential")


def run_overlap_case() -> None:
    config = runtime_config(
        hold_old_lease=True,
        bind_ttl=False,
        max_drain_checks=5,
        drain_interval=17,
    )
    config["test_gate_ownership"] = True
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "overlap case failed without leaking diagnostics: " + combined.strip(),
    )
    require(output is not None, "overlap case produced no output")
    require(output["CaseStatus"] == "success", "overlap rotation failed")
    verify_rotation_result(
        output["Result"],
        config,
        drain_checks=2,
        waited=True,
    )
    require(output["SleepCalls"] == 1, "overlap did not wait exactly once")
    require(
        output["SleepArguments"] == [config["drain_interval"]],
        "sleep action received the wrong interval",
    )
    require(
        output["BeforeReleaseOperations"] == OPERATION_IDS[:2],
        "revoke occurred before the old lease was released",
    )
    require(
        output["OldDuringCutover"] == expected_lease(config, "old"),
        "in-flight old lease did not retain its generation",
    )
    require(
        output["NewDuringCutover"] == expected_lease(config, "new"),
        "new work did not receive the published generation",
    )
    require(
        output["FinalValues"] == expected_lease(config, "new"),
        "gate did not retain the new generation",
    )
    require(
        output["OwnershipErrorType"] not in (None, "NoError"),
        "a second rotation acquired an already-owned gate",
    )
    require(
        output["CallerClientDisposed"] is False,
        "rotation disposed the caller-owned HTTP client",
    )
    verify_request_log(entries, config, expect_revoke=True)


def run_bound_ttl_case() -> None:
    config = runtime_config(
        hold_old_lease=False,
        bind_ttl=True,
        max_drain_checks=4,
        drain_interval=13,
    )
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "bound TTL case failed without leaking diagnostics: " + combined.strip(),
    )
    require(output is not None, "bound TTL case produced no output")
    require(output["CaseStatus"] == "success", "bound TTL rotation failed")
    verify_rotation_result(
        output["Result"],
        config,
        drain_checks=1,
        waited=False,
    )
    require(output["SleepCalls"] == 0, "zero-count drain slept")
    require(output["SleepArguments"] == [], "zero-count drain recorded a sleep")
    require(
        output["FinalValues"] == expected_lease(config, "new"),
        "bound TTL case did not publish the exchange response",
    )
    require(
        output["CallerClientDisposed"] is False,
        "rotation disposed the caller-owned HTTP client",
    )
    verify_request_log(entries, config, expect_revoke=True)


def run_timeout_case() -> None:
    config = runtime_config(
        hold_old_lease=True,
        bind_ttl=False,
        max_drain_checks=1,
        drain_interval=19,
    )
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "timeout driver failed without leaking diagnostics: " + combined.strip(),
    )
    require(output is not None, "timeout case produced no output")
    require(output["CaseStatus"] == "error", "drain exhaustion was successful")
    require(
        output["ErrorType"] == "VcfOpsLogDrainTimeoutException",
        "drain exhaustion used the wrong exception class",
    )
    require(output["OldName"] == config["old_name"], "timeout old name changed")
    require(output["NewName"] == config["new_name"], "timeout new name changed")
    require(output["DrainCheckCount"] == 1, "timeout drain count changed")
    require(output["SleepCalls"] == 0, "final failed check slept")
    require(output["SleepArguments"] == [], "timeout recorded a sleep")
    require(
        output["PostFailureValues"] == expected_lease(config, "new"),
        "timeout rolled the gate back or failed to publish it",
    )
    require(
        output["CallerClientDisposed"] is False,
        "timeout disposed the caller-owned HTTP client",
    )
    require_no_error_leak(output, config)
    verify_request_log(entries, config, expect_revoke=False)


def run_validation_case() -> None:
    config = runtime_config(
        hold_old_lease=False,
        bind_ttl=False,
        max_drain_checks=1,
        drain_interval=0,
    )
    config["validation_only"] = True
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "validation driver failed without leaking diagnostics: " + combined.strip(),
    )
    require(output is not None, "validation case produced no output")
    require(output["CaseStatus"] == "validation", "validation case did not run")
    require(
        output["ExpectedFailureCount"] == 15,
        "validation case count changed",
    )
    require(
        output["UnexpectedSuccesses"] == [],
        "an invalid credential or origin passed validation",
    )
    require(output["RequestCount"] == 0, "validation performed HTTP traffic")
    require(entries == [], "validation reached the mock service")
    require(
        output["CallerClientDisposed"] is False,
        "validation disposed the caller-owned HTTP client",
    )


def run_create_redirect_failure_case() -> None:
    config = runtime_config(
        hold_old_lease=False,
        bind_ttl=False,
        max_drain_checks=2,
        drain_interval=0,
    )
    config.update(
        {
            "supply_http_client": False,
            "create_http_status": 302,
            "create_redirect_target": ROUTES[1][1],
        }
    )
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "redirect failure driver failed without leaking diagnostics: "
        + combined.strip(),
    )
    require(output is not None, "redirect failure produced no output")
    require(output["CaseStatus"] == "error", "HTTP redirect was accepted")
    require(
        output["PostFailureValues"] == expected_lease(config, "old"),
        "create failure changed the gate",
    )
    require(output["SleepCalls"] == 0, "create failure entered drain polling")
    require(output["CallerClientDisposed"] is None, "owned client was reported as caller")
    require(len(entries) == 1, "owned HTTP client followed or retried a redirect")
    verify_create_request(entries[0], config)
    require_no_error_leak(output, config)


def run_invalid_create_response_case() -> None:
    config = runtime_config(
        hold_old_lease=False,
        bind_ttl=False,
        max_drain_checks=2,
        drain_interval=0,
    )
    config["create_invalid_fields"] = True
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "invalid create response driver failed without leaking diagnostics: "
        + combined.strip(),
    )
    require(output is not None, "invalid create response produced no output")
    require(output["CaseStatus"] == "error", "invalid create response was accepted")
    require(
        output["PostFailureValues"] == expected_lease(config, "old"),
        "invalid create response changed the gate",
    )
    require(output["SleepCalls"] == 0, "create response failure entered drain polling")
    require(
        output["CallerClientDisposed"] is False,
        "create response failure disposed the caller-owned HTTP client",
    )
    require(len(entries) == 1, "create response failure made an extra request")
    verify_create_request(entries[0], config)
    require_no_error_leak(output, config)


def run_invalid_exchange_response_case() -> None:
    config = runtime_config(
        hold_old_lease=False,
        bind_ttl=False,
        max_drain_checks=2,
        drain_interval=0,
    )
    config["exchange_invalid_fields"] = True
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "invalid exchange response driver failed without leaking diagnostics: "
        + combined.strip(),
    )
    require(output is not None, "invalid exchange response produced no output")
    require(output["CaseStatus"] == "error", "invalid exchange response was accepted")
    require(
        output["PostFailureValues"] == expected_lease(config, "old"),
        "session failure changed the gate",
    )
    require(output["SleepCalls"] == 0, "session failure entered drain polling")
    require(
        output["CallerClientDisposed"] is False,
        "session failure disposed the caller-owned HTTP client",
    )
    verify_request_log(entries, config, expect_revoke=False)
    require_no_error_leak(output, config)


def run_invalid_revoke_response_case() -> None:
    config = runtime_config(
        hold_old_lease=False,
        bind_ttl=False,
        max_drain_checks=2,
        drain_interval=0,
    )
    config["revoke_invalid_fields"] = True
    result, output, entries = execute_case(config)
    combined = require_no_console_leak(result, config)
    require(
        result.returncode == 0,
        "invalid revoke response driver failed without leaking diagnostics: "
        + combined.strip(),
    )
    require(output is not None, "invalid revoke response produced no output")
    require(output["CaseStatus"] == "error", "invalid revoke response was accepted")
    require(
        output["PostFailureValues"] == expected_lease(config, "new"),
        "revoke response failure rolled back the published gate",
    )
    require(output["SleepCalls"] == 0, "revoke response failure slept")
    require(
        output["CallerClientDisposed"] is False,
        "revoke response failure disposed the caller-owned HTTP client",
    )
    verify_request_log(entries, config, expect_revoke=True)
    require_no_error_leak(output, config)


def main() -> int:
    try:
        verify_contract_and_provenance()
        verify_manifest_and_module_shape()
        run_validation_case()
        run_overlap_case()
        run_bound_ttl_case()
        run_timeout_case()
        run_create_redirect_failure_case()
        run_invalid_create_response_case()
        run_invalid_exchange_response_case()
        run_invalid_revoke_response_case()
    except (
        VerificationError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: pinned contract, exact wire shape, cutover, drain, and safe revoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
