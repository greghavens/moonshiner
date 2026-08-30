#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0167."""

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
from urllib.parse import parse_qsl


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = (
    ROOT / "VcfOpsLogAgentGroups" / "VcfOpsLogAgentGroups.psd1"
)
MODULE_PATH = (
    ROOT / "VcfOpsLogAgentGroups" / "VcfOpsLogAgentGroups.psm1"
)
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = ["getAllAgentGroupConfig"]
ROUTE = "/api/v2/agent/groups"
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
        "focused operationId set changed",
    )
    require(len(operations) == 1, "contract must name exactly one operation")
    operation = operations[0]
    require(
        (operation["method"], operation["path"]) == ("GET", ROUTE),
        "focused route changed",
    )
    require(operation["requestBody"] is False, "list request must be bodyless")
    require(
        operation["security"] == ["OPSTokenAuthorization"],
        "focused operation security changed",
    )
    require(len(operation["parameters"]) == 1, "list parameters changed")
    pageable = operation["parameters"][0]
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
        operation["responses"]["200"]["schema"]
        == {"type": "array", "items": "Page"},
        "list response projection changed",
    )

    schemas = contract["schemas"]
    require(
        list(schemas["Pageable"]["properties"].keys())
        == ["page", "size", "sort"]
        and schemas["Pageable"]["properties"]["page"]["minimum"] == 0
        and schemas["Pageable"]["properties"]["size"]["minimum"] == 1
        and schemas["Pageable"]["properties"]["sort"]["unsetBehavior"]
        == "omit",
        "Pageable schema changed",
    )
    require(
        list(schemas["Page"]["properties"].keys())
        == [
            "content",
            "empty",
            "first",
            "last",
            "number",
            "numberOfElements",
            "pageable",
            "size",
            "sort",
            "totalElements",
            "totalPages",
        ],
        "Page schema changed",
    )
    require(
        schemas["Page"]["properties"]["content"]
        == {"type": "array", "items": {"type": "object"}},
        "Page content schema changed",
    )
    require(
        list(schemas["AgentGroupResponse"]["properties"].keys())
        == [
            "agentConfig",
            "autoUpdate",
            "constraints",
            "id",
            "info",
            "mpId",
            "name",
        ],
        "AgentGroupResponse schema changed",
    )

    profile = contract["focusedCollectionProfile"]
    require(
        profile["operation"] == "agentGroups.list"
        and profile["responseArrayCardinalityPerRequest"] == 1
        and profile["contentItemProfile"] == "AgentGroupResponse"
        and profile["firstPage"] == 0
        and profile["requiredContentProperties"]
        == ["id", "name", "autoUpdate", "info"]
        and profile["ordering"]
        == {
            "comparison": "ordinal case-sensitive",
            "keys": ["Name", "Id"],
        }
        and profile["projectionPropertyOrder"]
        == ["Id", "Name", "AutoUpdate", "Info"],
        "focused collection policy changed",
    )
    require(
        "without an AgentGroupResponse reference"
        in profile["contentProfileBoundary"],
        "OpenAPI and focused-profile boundary is unclear",
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
                "openapiPointer": item["openapiPointer"],
                "specLine": item["specLine"],
                "repositoryCommitSha": item["repositoryCommitSha"],
                "specPath": item["specPath"],
            }
            for item in sources["operations"]
        ]
        == [
            {
                "operationId": "getAllAgentGroupConfig",
                "method": "GET",
                "path": ROUTE,
                "openapiPointer": (
                    "#/paths/~1api~1v2~1agent~1groups/get"
                ),
                "specLine": 38,
                "repositoryCommitSha": COMMIT,
                "specPath": SPEC_PATH,
            }
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
        + "if ($d.RootModule -cne 'VcfOpsLogAgentGroups.psm1') "
        + "{ exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne "
        + "'Get-VcfOpsLogAgentGroupInventory') { exit 5 }; "
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
    require(result.returncode == 0, "protected PowerShell manifest is invalid")

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    require(
        "system.net.http.httpclient" in folded
        or "net.http.httpclient" in folded,
        "implementation must use System.Net.Http.HttpClient",
    )
    for forbidden in (
        "curl",
        "invoke-restmethod",
        "invoke-webrequest",
        "processstartinfo",
        "start-process",
        "system.diagnostics.process",
        "system.net.sockets",
        "tcpclient",
    ):
        require(forbidden not in folded, f"forbidden transport found: {forbidden}")

    vendored_suffixes = {".dll", ".nupkg", ".nuspec"}
    vendored_names = {
        "VMware.Sdk.Vcf.Ops.psm1",
        "VMware.Sdk.Vcf.Ops.psd1",
    }
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        require(
            path.suffix.casefold() not in vendored_suffixes
            and path.name not in vendored_names,
            (
                "vendored VMware dependency or binary found: "
                + str(path.relative_to(ROOT))
            ),
        )


def wait_for_ready(
    path: Path, process: subprocess.Popen[str]
) -> dict[str, Any]:
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


def runtime_config() -> dict[str, Any]:
    nonce = secrets.token_hex(6)
    ids = [f"group-{nonce}-{part}" for part in ("40", "50", "30", "20", "10")]
    names = [
        f"zeta-{nonce}",
        f"Alpha-{nonce}",
        f"alpha-{nonce}",
        f"Echo-{nonce}",
        f"Echo-{nonce}",
    ]
    groups: list[dict[str, Any]] = []
    for index, (group_id, name) in enumerate(zip(ids, names, strict=True)):
        groups.append(
            {
                "id": group_id,
                "name": name,
                "autoUpdate": index % 2 == 0,
                "info": None if index == 2 else f"runtime-info-{nonce}-{index}",
                "agentConfig": f"runtime-config-{nonce}-{index}",
                "constraints": {
                    "text": f"runtime-query-{nonce}-{index}",
                },
                "mpId": f"runtime-mp-{nonce}-{index}",
            }
        )
    return {
        "log_token": "runtime-token-" + secrets.token_urlsafe(24),
        "page_size": 2,
        "groups": groups,
        "layouts": [
            [[4, 1], [3, 0], [2]],
            [[2], [0, 4], [3, 1]],
        ],
    }


def projected(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "Id": group["id"],
        "Name": group["name"],
        "AutoUpdate": group["autoUpdate"],
        "Info": group["info"],
    }


def verify_output(value: dict[str, Any], config: dict[str, Any]) -> None:
    require(list(value.keys()) == ["first", "second"], "result envelope changed")
    expected = [
        projected(group)
        for group in sorted(
            config["groups"],
            key=lambda item: (item["name"], item["id"]),
        )
    ]
    for key in ("first", "second"):
        actual = value[key]
        require(isinstance(actual, list), f"{key} inventory is not an array")
        require(actual == expected, f"{key} inventory is incomplete or unstable")
        require(
            all(
                list(item.keys()) == ["Id", "Name", "AutoUpdate", "Info"]
                for item in actual
            ),
            f"{key} projection property order changed",
        )
    require(
        value["first"] == value["second"],
        "changing page composition changed the inventory",
    )


def header_values(item: dict[str, Any], name: str) -> list[str]:
    return [
        value
        for key, value in item["header_pairs"]
        if key.casefold() == name.casefold()
    ]


def verify_wire(log: list[dict[str, Any]], config: dict[str, Any]) -> None:
    page_size = config["page_size"]
    expected_targets = [
        f"{ROUTE}?page={page}&size={page_size}"
        for _invocation in range(2)
        for page in range(3)
    ]
    require(len(log) == len(expected_targets), "unexpected request count")

    for index, (item, target) in enumerate(zip(log, expected_targets, strict=True)):
        require(item["method"] == "GET", f"request {index} method changed")
        require(item["raw_target"] == target, f"request {index} target changed")
        require(item["path"] == ROUTE, f"request {index} path changed")
        require(
            item["operationId"] == "getAllAgentGroupConfig",
            f"request {index} used an unnamed operation",
        )
        require(item["body_bytes"] == 0, f"request {index} carried a body")
        require(item["body_raw"] == "", f"request {index} body is not empty")
        require(
            header_values(item, "Accept") == ["application/json"],
            f"request {index} Accept header is not exact",
        )
        require(
            header_values(item, "X-JWT-Token") == [config["log_token"]],
            f"request {index} token header is not exact",
        )
        require(
            header_values(item, "Content-Type") == [],
            f"request {index} sent Content-Type",
        )
        query_pairs = parse_qsl(item["query"], keep_blank_values=True)
        expected_page = index % 3
        require(
            query_pairs
            == [("page", str(expected_page)), ("size", str(page_size))],
            f"request {index} query pairs changed",
        )
        require(
            all(value != "" for _name, value in query_pairs),
            f"request {index} sent an empty optional value",
        )
        require(
            all(name != "sort" for name, _value in query_pairs),
            f"request {index} sent unset sort",
        )
        require(
            "?" in item["raw_target"]
            and not item["raw_target"].endswith("?"),
            f"request {index} query delimiter is malformed",
        )


def run_integration() -> None:
    config = runtime_config()
    with tempfile.TemporaryDirectory(prefix="vcf91-0167-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        output_path = temp / "result.json"
        config_path.write_text(
            json.dumps(config, separators=(",", ":")),
            encoding="utf-8",
        )

        server = subprocess.Popen(
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
            ready = wait_for_ready(ready_path, server)
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
                timeout=30,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
            require(
                result.returncode == 0,
                "PowerShell integration failed: "
                + (result.stderr or result.stdout).strip(),
            )
            require(output_path.is_file(), "PowerShell did not write a result")
            raw_output = output_path.read_text(encoding="utf-8")
            require(
                config["log_token"] not in raw_output
                and config["log_token"] not in result.stdout
                and config["log_token"] not in result.stderr,
                "log token leaked through output",
            )
            verify_output(json.loads(raw_output), config)
            verify_wire(read_log(log_path), config)
        finally:
            server.terminate()
            try:
                stdout, stderr = server.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                stdout, stderr = server.communicate(timeout=3)
            require(
                server.returncode in (-15, -9, 0),
                "mock terminated unexpectedly: " + (stderr or stdout).strip(),
            )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        run_integration()
    except (VerificationError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: complete stable agent-group inventory and exact wire contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
