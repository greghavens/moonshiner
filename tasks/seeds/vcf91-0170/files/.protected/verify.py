#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0170."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = (
    ROOT / "VcfOpsLogForwarder" / "VcfOpsLogForwarder.psd1"
)
MODULE_PATH = ROOT / "VcfOpsLogForwarder" / "VcfOpsLogForwarder.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"
FALLBACK_INVOKER_PATH = ROOT / ".protected" / "invoke_fallback.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = ["getAllLogForwarders", "createLogForwarder"]
ROUTES = [
    ("GET", "/api/v2/logs/forwarders"),
    ("POST", "/api/v2/logs/forwarders"),
]
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"
CREATE_PROPERTIES = [
    "enabled",
    "host",
    "name",
    "port",
    "protocol",
    "sslEnabled",
    "transportProtocol",
]
UNSET_PROPERTIES = [
    "certificate",
    "connectionRefreshInterval",
    "constraints",
    "forwardComplementaryFields",
    "id",
    "tags",
    "workerCount",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

    require(source["repository"] == "vmware/vcf-api-specs", "repository changed")
    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract blob changed")
    require(source["license"] == "Apache-2.0", "source license changed")
    require(source["openapi"] == "3.0.1", "OpenAPI version changed")
    require(source["apiVersion"] == "9.1.0.0", "API version changed")
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
        "log token security projection changed",
    )

    operations = contract["operations"]
    require(
        [item["operationId"] for item in operations] == OPERATION_IDS,
        "focused operationId order changed",
    )
    require(
        [(item["method"], item["path"]) for item in operations] == ROUTES,
        "focused routes changed",
    )
    require(
        all(
            item["security"] == ["OPSTokenAuthorization"]
            and item["parameters"] == []
            for item in operations
        ),
        "operation security or parameter projection changed",
    )
    require(
        operations[0]["requestBody"] is None
        and operations[0]["responses"]["200"]["schema"]
        == {"type": "array", "items": {"$ref": "LogForwarder"}},
        "list operation projection changed",
    )
    require(
        operations[1]["requestBody"]
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "LogForwarder",
        }
        and operations[1]["responses"]["201"]["schema"] == "LogForwarder",
        "create operation projection changed",
    )
    require(
        set(operations[1]["responses"]) == {
            "201",
            "400",
            "403",
            "422",
            "500",
            "502",
        },
        "create status projection changed",
    )

    schemas = contract["schemas"]
    log_properties = [
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
    require(
        list(schemas["LogForwarder"]["properties"].keys()) == log_properties
        and schemas["LogForwarder"]["required"] == [],
        "LogForwarder property projection changed",
    )
    require(
        schemas["LogForwarder"]["properties"]["protocol"]["enum"]
        == ["SYSLOG", "RAW", "RAWPLUS"]
        and schemas["LogForwarder"]["properties"]["transportProtocol"]["enum"]
        == ["TCP", "UDP"]
        and schemas["LogForwarder"]["properties"]["id"]["readOnly"] is True,
        "LogForwarder enum or read-only projection changed",
    )
    require(
        list(schemas["ErrorBody"]["properties"].keys())
        == ["errorCode", "errorDetails", "errorMessage"],
        "ErrorBody projection changed",
    )

    workflow = contract["focusedWorkflow"]
    require(
        workflow["operationOrder"] == OPERATION_IDS,
        "focused workflow order changed",
    )
    require(
        workflow["precheck"]
        == {
            "matchProperty": "name",
            "comparison": "ordinal",
            "failureBehavior": "stop-before-create",
        },
        "precheck gate changed",
    )
    require(
        workflow["createRequestBody"]["propertyOrder"] == CREATE_PROPERTIES
        and workflow["createRequestBody"]["unsetProperties"]
        == UNSET_PROPERTIES
        and workflow["createRequestBody"]["unsetBehavior"] == "omit",
        "focused body omission contract changed",
    )
    require(
        "no mutation occurs" in workflow["failureRule"]
        and "come from the pinned OpenAPI specification"
        in workflow["specificationBoundary"],
        "specification and policy boundary is unclear",
    )

    require(
        sources["repositoryCommitSha"] == COMMIT,
        "official source commit changed",
    )
    require(sources["specPath"] == SPEC_PATH, "official spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "official blob changed")
    require(sources["license"] == "Apache-2.0", "official license changed")
    require(
        sources["operationIds"] == OPERATION_IDS,
        "official operationIds changed",
    )
    require(
        COMMIT in sources["specUrl"]
        and sources["specUrl"].endswith(SPEC_PATH),
        "official specification URL is not immutable",
    )
    require(
        [
            {
                "operationId": item["operationId"],
                "method": item["method"],
                "path": item["path"],
                "specLine": item["specLine"],
                "repositoryCommitSha": item["repositoryCommitSha"],
                "specPath": item["specPath"],
            }
            for item in sources["operations"]
        ]
        == [
            {
                "operationId": "getAllLogForwarders",
                "method": "GET",
                "path": "/api/v2/logs/forwarders",
                "specLine": 765,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
            {
                "operationId": "createLogForwarder",
                "method": "POST",
                "path": "/api/v2/logs/forwarders",
                "specLine": 805,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
        ],
        "each operation must carry its exact pinned source",
    )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"]
        is False,
        "a documentation page must not be the contract source",
    )


def verify_manifest_and_shape() -> None:
    manifest_literal = str(MANIFEST_PATH).replace("'", "''")
    command = (
        "$d = Import-PowerShellDataFile -Path '"
        + manifest_literal
        + "'; "
        + "if ($d.RootModule -cne 'VcfOpsLogForwarder.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne "
        + "'New-VcfOpsLogForwarderIfAbsent') { exit 5 }; "
        + "$r = $d.RequiredModules[0]; "
        + "if ($r.ModuleName -cne '"
        + MODULE_NAME
        + "' -or [string]$r.RequiredVersion -cne '"
        + MODULE_VERSION
        + "') { exit 6 }"
    )
    result = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
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
    require(result.returncode == 0, "protected PowerShell manifest is invalid")

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    require(
        "system.net.http.httpclient" in folded
        or "net.http.httpclient" in folded,
        "implementation must use System.Net.Http.HttpClient",
    )
    for forbidden in (
        "invoke-restmethod",
        "invoke-webrequest",
        "start-process",
        "system.net.sockets",
        "tcpclient",
        "curl",
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


def wait_for_ready(
    process: subprocess.Popen[str],
    ready_path: Path,
    timeout: float = 10.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_path.exists() and ready_path.stat().st_size:
            return load_json(ready_path)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            if (
                "PermissionError" in stderr
                or "Operation not permitted" in stderr
            ):
                return None
            raise VerificationError(
                "mock exited before ready: " + stdout + stderr
            )
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def make_config(scenario: str) -> dict[str, Any]:
    first_flag = bool(secrets.randbits(1))
    protocols = ["SYSLOG", "RAW", "RAWPLUS"]
    transports = ["TCP", "UDP"]
    return {
        "scenario": scenario,
        "log_token": "log-token-" + secrets.token_urlsafe(24),
        "name": "guarded-forwarder-" + secrets.token_hex(7),
        "host": "relay-" + secrets.token_hex(6) + ".corp.example",
        "port": 1024 + secrets.randbelow(64511),
        "protocol": protocols[secrets.randbelow(len(protocols))],
        "ssl_enabled": first_flag,
        "transport_protocol": transports[secrets.randbelow(len(transports))],
        "enabled": not first_flag,
        "existing_id": "existing-" + secrets.token_hex(9),
        "created_id": "created-" + secrets.token_hex(9),
    }


def run_case(
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0170-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        output_path = temp / "result.json"
        config_path.write_text(
            json.dumps(config, separators=(",", ":")),
            encoding="utf-8",
        )

        mock = subprocess.Popen(
            [
                "python3",
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
        invocation = None
        try:
            ready = wait_for_ready(mock, ready_path)
            if ready is None:
                invocation_arguments = [
                    "-File",
                    str(FALLBACK_INVOKER_PATH),
                    "-ConfigPath",
                    str(config_path),
                    "-ContractPath",
                    str(CONTRACT_PATH),
                    "-LogPath",
                    str(log_path),
                    "-OutputPath",
                    str(output_path),
                ]
            else:
                require(ready["host"] == "127.0.0.1", "mock is not loopback")
                invocation_arguments = [
                    "-File",
                    str(INVOKER_PATH),
                    "-Port",
                    str(ready["port"]),
                    "-ConfigPath",
                    str(config_path),
                    "-OutputPath",
                    str(output_path),
                ]
            invocation = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    *invocation_arguments,
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=25,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
            require(
                invocation.returncode == 0,
                "PowerShell invocation failed: "
                + invocation.stdout
                + invocation.stderr,
            )
            require(output_path.exists(), "PowerShell result was not written")
            raw_output = output_path.read_text(encoding="utf-8")
            require(
                config["log_token"] not in raw_output
                and config["log_token"] not in invocation.stdout
                and config["log_token"] not in invocation.stderr,
                "credential leaked through output or diagnostics",
            )
            envelope = json.loads(raw_output)
            requests = read_jsonl(log_path)
            return envelope, requests, raw_output
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=5)


def relevant_header_values(
    request: dict[str, Any],
    name: str,
) -> list[str]:
    return request["headers"].get(name.casefold(), [])


def assert_common_request(
    request: dict[str, Any],
    config: dict[str, Any],
) -> None:
    require(
        relevant_header_values(request, "accept") == ["application/json"],
        "Accept must appear exactly once as application/json",
    )
    require(
        relevant_header_values(request, "x-jwt-token")
        == [config["log_token"]],
        "X-JWT-Token must appear exactly once with the caller value",
    )
    require(
        relevant_header_values(request, "authorization") == [],
        "unexpected Authorization header",
    )
    require(request["query"] == "", "query string must be absent")
    require("?" not in request["raw_target"], "raw target has query delimiter")


def verify_collision_case() -> None:
    config = make_config("collision")
    envelope, requests, raw_output = run_case(config)
    require(envelope.get("ok") is False, "name collision must fail")
    require(
        str(envelope.get("errorType", "")).endswith(
            "InvalidOperationException"
        ),
        "name collision must be an InvalidOperationException",
    )
    require(
        "already exists" in str(envelope.get("message", "")).casefold(),
        "collision error is not identifiable",
    )
    require(
        config["log_token"] not in raw_output,
        "collision output exposed the token",
    )
    require(
        len(requests) == 1,
        "failed precheck must stop before every mutating request",
    )
    precheck = requests[0]
    assert_common_request(precheck, config)
    require(
        precheck["method"] == "GET"
        and precheck["raw_target"] == "/api/v2/logs/forwarders"
        and precheck["operationId"] == "getAllLogForwarders",
        "collision precheck method, target, or operationId is wrong",
    )
    require(
        precheck["body_bytes"] == 0
        and precheck["body_raw"] == ""
        and precheck["body"] is None,
        "precheck must be bodyless",
    )
    require(
        relevant_header_values(precheck, "content-type") == [],
        "precheck must omit Content-Type",
    )
    require(
        precheck["response_status"] == 200
        and precheck["effect_committed"] is False,
        "precheck log incorrectly reports mutation",
    )


def verify_create_case() -> None:
    config = make_config("available")
    envelope, requests, raw_output = run_case(config)
    require(envelope.get("ok") is True, "available name must be created")
    require(list(envelope.keys()) == ["ok", "result"], "result envelope changed")
    result = envelope["result"]
    require(
        list(result.keys()) == ["Created", "OperationId", "Id"],
        "returned object property order or shape is wrong",
    )
    require(
        result
        == {
            "Created": True,
            "OperationId": "createLogForwarder",
            "Id": config["created_id"],
        },
        "returned creation result is inconsistent",
    )
    require(
        config["log_token"] not in raw_output,
        "success output exposed the token",
    )
    require(len(requests) == 2, "success must make exactly two requests")
    require(
        [item["operationId"] for item in requests] == OPERATION_IDS
        and [item["method"] for item in requests] == ["GET", "POST"],
        "operation order is wrong",
    )

    precheck, create = requests
    assert_common_request(precheck, config)
    require(
        precheck["raw_target"] == "/api/v2/logs/forwarders"
        and precheck["body_bytes"] == 0
        and precheck["body_raw"] == ""
        and precheck["body"] is None,
        "success precheck wire shape is wrong",
    )
    require(
        relevant_header_values(precheck, "content-type") == [],
        "success precheck must omit Content-Type",
    )
    require(precheck["effect_committed"] is False, "GET was marked mutating")

    assert_common_request(create, config)
    require(
        create["raw_target"] == "/api/v2/logs/forwarders",
        "create raw target is wrong",
    )
    require(
        relevant_header_values(create, "content-type")
        == ["application/json"],
        "create Content-Type must appear exactly once",
    )
    expected_body = {
        "enabled": config["enabled"],
        "host": config["host"],
        "name": config["name"],
        "port": config["port"],
        "protocol": config["protocol"],
        "sslEnabled": config["ssl_enabled"],
        "transportProtocol": config["transport_protocol"],
    }
    expected_raw = json.dumps(expected_body, separators=(",", ":"))
    require(
        create["body_raw"] == expected_raw,
        "create body bytes or property order are wrong",
    )
    require(
        create["body_bytes"] == len(expected_raw.encode("utf-8")),
        "create Content-Length/body byte count is wrong",
    )
    require(
        list(create["body"].keys()) == CREATE_PROPERTIES
        and create["body"] == expected_body,
        "create JSON shape or values are wrong",
    )
    require(
        not any(name in create["body"] for name in UNSET_PROPERTIES),
        "an unset optional LogForwarder property was serialized",
    )
    require(
        config["enabled"] is False or config["ssl_enabled"] is False,
        "test generation must exercise an explicit false value",
    )
    require(
        create["response_status"] == 201
        and create["effect_committed"] is True,
        "create was not the only committed mutation",
    )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        verify_collision_case()
        verify_create_case()
    except (
        VerificationError,
        json.JSONDecodeError,
        KeyError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print(
        "PASS: contract provenance, precheck gate, and exact create wire shape "
        "verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
