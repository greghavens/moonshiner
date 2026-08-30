#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0165."""

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
MANIFEST_PATH = ROOT / "VcfOpsLogSecret" / "VcfOpsLogSecret.psd1"
MODULE_PATH = ROOT / "VcfOpsLogSecret" / "VcfOpsLogSecret.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = ["createAgentSecret", "listAgentSecrets"]
ROUTE = "/api/v2/agent/secrets"
MODULE_NAME = "VMware.Sdk.Vcf.Ops"
MODULE_VERSION = "13.5.0.25380678"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> dict[str, Any]:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

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
        [(item["method"], item["path"]) for item in operations]
        == [("POST", ROUTE), ("GET", ROUTE)],
        "focused routes changed",
    )
    create = operations[0]
    require(
        create["requestBody"]
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "AgentSecretCreateRequest",
            "focusedPropertyOrder": ["name"],
            "unsetBehavior": (
                "omit every property not set by the focused request"
            ),
        },
        "create request projection changed",
    )
    require(
        create["responses"]["201"]["schema"]
        == "AgentSecretCreateResponse",
        "create response projection changed",
    )

    listing = operations[1]
    require(listing["requestBody"] is False, "list request must be bodyless")
    require(len(listing["parameters"]) == 1, "list parameters changed")
    pageable = listing["parameters"][0]
    require(
        {
            "name": pageable["name"],
            "in": pageable["in"],
            "required": pageable["required"],
            "schema": pageable["schema"],
            "style": pageable["style"],
            "explode": pageable["explode"],
            "focusedMembers": pageable["focusedMembers"],
            "focusedWireOrder": pageable["focusedWireOrder"],
            "unsetMembers": pageable["unsetMembers"],
            "unsetBehavior": pageable["unsetBehavior"],
        }
        == {
            "name": "pageable",
            "in": "query",
            "required": True,
            "schema": "Pageable",
            "style": "form",
            "explode": True,
            "focusedMembers": ["page", "size"],
            "focusedWireOrder": ["page", "size"],
            "unsetMembers": ["sort"],
            "unsetBehavior": "omit",
        },
        "Pageable projection or omission semantics changed",
    )
    require(
        listing["responses"]["200"]["schema"] == "AgentSecretListResponse",
        "list response projection changed",
    )

    schemas = contract["schemas"]
    require(
        schemas["AgentSecretCreateRequest"]["required"] == []
        and list(
            schemas["AgentSecretCreateRequest"]["properties"].keys()
        )
        == ["name"],
        "create request schema changed",
    )
    require(
        list(schemas["AgentSecretCreateResponse"]["properties"].keys())
        == ["id", "name", "secret", "status"],
        "create response properties changed",
    )
    require(
        list(schemas["AgentSecretListResponse"]["properties"].keys())
        == ["id", "modificationTime", "name", "status"],
        "list response properties changed",
    )
    require(
        list(schemas["Pageable"]["properties"].keys())
        == ["page", "size", "sort"]
        and schemas["Pageable"]["properties"]["sort"]["unsetBehavior"]
        == "omit",
        "Pageable schema changed",
    )

    asynchronous = contract["asynchronousActivation"]
    require(
        asynchronous["startOperation"] == "agentSecrets.create"
        and asynchronous["pollOperation"] == "agentSecrets.list"
        and asynchronous["identityFields"] == ["id", "name"]
        and asynchronous["statusField"] == "status"
        and asynchronous["nonTerminal"] == ["PENDING", "ACTIVATING"]
        and asynchronous["terminalSuccess"] == ["ACTIVE"]
        and asynchronous["terminalFailure"] == ["FAILED", "REVOKED"],
        "asynchronous terminal-state profile changed",
    )
    require(
        "acceptance, not activation completion" in asynchronous["rule"]
        and "without an enum" in asynchronous["specificationBoundary"],
        "specification/scenario boundary is unclear",
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
                "operationId": "createAgentSecret",
                "method": "POST",
                "path": ROUTE,
                "specLine": 438,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            },
            {
                "operationId": "listAgentSecrets",
                "method": "GET",
                "path": ROUTE,
                "specLine": 402,
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
    return contract


def verify_manifest_and_shape() -> None:
    command = (
        "$d = Import-PowerShellDataFile -Path '"
        + str(MANIFEST_PATH).replace("'", "''")
        + "'; "
        + "if ($d.RootModule -cne 'VcfOpsLogSecret.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne "
        + "'New-VcfOpsLogAgentSecretAndWait') { exit 5 }; "
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
    require(
        result.returncode == 0,
        "protected PowerShell manifest is invalid",
    )

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


def single_header(entry: dict[str, Any], name: str) -> str:
    values = entry["headers"].get(name.casefold(), [])
    require(len(values) == 1, f"{name} must appear exactly once")
    return values[0]


def verify_request_log(
    entries: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    statuses = config["status_sequence"]
    require(
        len(entries) == 1 + len(statuses),
        "create plus every required poll must be the only requests",
    )
    require(
        [item["operationId"] for item in entries]
        == ["createAgentSecret"]
        + ["listAgentSecrets"] * len(statuses),
        "request operation order changed",
    )
    require(
        [item["method"] for item in entries]
        == ["POST"] + ["GET"] * len(statuses),
        "request method order changed",
    )

    expected_body = json.dumps(
        {"name": config["secret_name"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    create = entries[0]
    require(create["raw_target"] == ROUTE, "create raw target changed")
    require(create["query"] == "", "create must not send a query")
    require(create["body_raw"] == expected_body, "create JSON bytes changed")
    require(
        create["body"] == {"name": config["secret_name"]},
        "create body must contain exactly name",
    )
    require(
        list(create["body"].keys()) == ["name"],
        "create JSON property order changed",
    )
    require(
        create["body_bytes"] == len(expected_body.encode("utf-8")),
        "create body byte count changed",
    )
    require(
        single_header(create, "content-type") == "application/json",
        "create Content-Type changed",
    )

    expected_target = f"{ROUTE}?page=0&size={config['page_size']}"
    for index, entry in enumerate(entries[1:], start=1):
        require(
            entry["raw_target"] == expected_target,
            f"poll {index} raw target changed",
        )
        require(
            entry["query"] == f"page=0&size={config['page_size']}",
            f"poll {index} query order changed",
        )
        require(entry["body_bytes"] == 0, f"poll {index} sent a body")
        require(entry["body_raw"] == "", f"poll {index} body changed")
        require(
            "content-type" not in entry["headers"],
            f"poll {index} sent Content-Type",
        )
        require(
            "sort" not in entry["query"]
            and "pageable" not in entry["query"]
            and "=&" not in entry["query"]
            and not entry["query"].endswith("="),
            f"poll {index} did not omit unset optional fields",
        )

    for index, entry in enumerate(entries):
        require(
            single_header(entry, "accept") == "application/json",
            f"request {index + 1} Accept changed",
        )
        require(
            single_header(entry, "x-jwt-token") == config["log_token"],
            f"request {index + 1} log token changed",
        )
        require(
            "authorization" not in entry["headers"]
            and "vmware-api-session-id" not in entry["headers"],
            f"request {index + 1} sent an unrelated credential",
        )


def execute_case(
    config: dict[str, Any],
) -> tuple[subprocess.CompletedProcess[str], Any | None, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="vcf91-0165-") as temp_text:
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
                    "-OutputPath",
                    str(output_path),
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


def runtime_config(statuses: list[str], max_polls: int) -> dict[str, Any]:
    return {
        "secret_name": f"collector-{secrets.token_hex(8)}",
        "secret_id": f"secret-{secrets.token_hex(12)}",
        "one_time_secret": f"once-{secrets.token_urlsafe(30)}",
        "log_token": f"jwt-{secrets.token_urlsafe(36)}",
        "create_status": "PENDING",
        "status_sequence": statuses,
        "page_size": 37,
        "max_polls": max_polls,
    }


def require_no_leak(
    result: subprocess.CompletedProcess[str],
    config: dict[str, Any],
) -> str:
    combined = result.stdout + result.stderr
    for value in (config["log_token"], config["one_time_secret"]):
        require(value not in combined, "credential leaked to console output")
    return combined


def run_success_case() -> None:
    config = runtime_config(["PENDING", "ACTIVATING", "ACTIVE"], 6)
    result, output, entries = execute_case(config)
    combined = require_no_leak(result, config)
    require(
        result.returncode == 0,
        "PowerShell case failed without leaking protected diagnostics: "
        + combined.strip(),
    )
    require(output is not None, "module did not produce a result")
    require(
        list(output.keys())
        == [
            "Status",
            "Id",
            "Name",
            "Secret",
            "PollCount",
            "ObservedStatuses",
        ],
        "result property order changed",
    )
    require(output["Status"] == "Activated", "success status changed")
    require(output["Id"] == config["secret_id"], "secret id changed")
    require(output["Name"] == config["secret_name"], "secret name changed")
    require(
        output["Secret"] == config["one_time_secret"],
        "one-time create secret was not preserved",
    )
    require(
        output["PollCount"] == len(config["status_sequence"]),
        "poll count changed",
    )
    require(
        output["ObservedStatuses"] == config["status_sequence"],
        "non-terminal or terminal observation was lost",
    )
    verify_request_log(entries, config)


def run_terminal_failure_case() -> None:
    config = runtime_config(["REVOKED"], 6)
    result, output, entries = execute_case(config)
    require_no_leak(result, config)
    require(result.returncode != 0, "terminal failure was treated as success")
    require(output is None, "terminal failure emitted a success object")
    verify_request_log(entries, config)


def run_exhaustion_case() -> None:
    config = runtime_config(["PENDING", "ACTIVATING"], 2)
    result, output, entries = execute_case(config)
    require_no_leak(result, config)
    require(result.returncode != 0, "poll exhaustion was treated as success")
    require(output is None, "poll exhaustion emitted a success object")
    verify_request_log(entries, config)


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        run_success_case()
        run_terminal_failure_case()
        run_exhaustion_case()
    except (VerificationError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: contract provenance, asynchronous polling, and exact wire shape")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
