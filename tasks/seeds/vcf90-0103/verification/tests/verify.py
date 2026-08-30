#!/usr/bin/env python3
"""Deterministic protected verifier for vcf90-0103."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = (
    ROOT / "VcfInstallerTaskInventory" / "VcfInstallerTaskInventory.psd1"
)
MODULE_PATH = (
    ROOT / "VcfInstallerTaskInventory" / "VcfInstallerTaskInventory.psm1"
)
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

TAG = "9.0.0.0"
COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
OPERATION_IDS = ["getTasks"]
ROUTE = "/v1/tasks"
MODULE_NAME = "VMware.Sdk.Vcf.Installer"
MODULE_VERSION = "13.4.0.24798382"
QUERY_NAMES = [
    "limit",
    "taskStatus",
    "taskType",
    "resourceId",
    "resourceType",
    "completedAfter",
    "pageNumber",
    "pageSize",
    "orderDirection",
    "orderBy",
    "taskName",
    "doLiveRefresh",
]
UNSET_NAMES = [
    "limit",
    "taskStatus",
    "taskType",
    "resourceId",
    "resourceType",
    "completedAfter",
    "orderDirection",
    "orderBy",
    "taskName",
    "doLiveRefresh",
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

    require(source["tag"] == TAG, "contract tag changed")
    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["license"] == "Apache-2.0", "contract license changed")
    require(source["openapi"] == "3.0.1", "OpenAPI version changed")
    require(source["apiVersion"] == TAG, "API version changed")
    require(
        source["title"]
        == "VMware Cloud Foundation Installer API Reference Guide",
        "API title changed",
    )
    require(
        source["serverUrlInSpecification"] == "http://localhost:80",
        "specification server projection changed",
    )

    operations = contract["operations"]
    require(
        [item["operationId"] for item in operations] == OPERATION_IDS,
        "focused operationId set changed",
    )
    require(len(operations) == 1, "contract must name exactly one operation")
    operation = operations[0]
    require(
        (operation["method"], operation["path"], operation["requestBody"])
        == ("GET", ROUTE, False),
        "focused operation wire contract changed",
    )
    require(
        operation["powerCliCommand"] == "Invoke-VcfInstallerGetTasks",
        "PowerCLI operation binding changed",
    )
    parameters = operation["parameters"]
    require(
        [item["name"] for item in parameters] == QUERY_NAMES,
        "getTasks parameter list or order changed",
    )
    require(
        all(
            item["in"] == "query" and item["required"] is False
            for item in parameters
        ),
        "getTasks optional query projection changed",
    )
    by_name = {item["name"]: item for item in parameters}
    require(
        by_name["pageNumber"]["schema"]
        == {"type": "integer", "format": "int32"},
        "pageNumber schema changed",
    )
    require(
        by_name["pageSize"]["schema"]
        == {"type": "integer", "format": "int32"}
        and "Max page size allowed is 100"
        in by_name["pageSize"]["description"],
        "pageSize schema changed",
    )
    require(
        by_name["completedAfter"]["schema"]
        == {"type": "integer", "format": "int64"},
        "completedAfter schema changed",
    )
    require(
        by_name["doLiveRefresh"]["schema"]
        == {"type": "boolean", "default": False},
        "doLiveRefresh schema changed",
    )
    require(
        operation["responses"]["200"]
        == {
            "description": "Returns the list of tasks.",
            "contentType": "application/json",
            "schema": "PageOfTask",
        },
        "getTasks success response changed",
    )

    schemas = contract["schemas"]
    require(
        list(schemas["PageMetadata"]["properties"].keys())
        == ["pageNumber", "pageSize", "totalElements", "totalPages"],
        "PageMetadata projection changed",
    )
    require(
        schemas["PageOfTask"]
        == {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "readOnly": True,
                    "items": "Task",
                },
                "pageMetadata": {"schema": "PageMetadata"},
            },
        },
        "PageOfTask projection changed",
    )
    task = schemas["Task"]
    require(
        task["required"] == ["creationTimestamp", "id", "name", "status"],
        "Task required fields changed",
    )
    require(
        list(task["properties"].keys())
        == [
            "id",
            "name",
            "localizableDescriptionPack",
            "type",
            "status",
            "creationTimestamp",
            "completionTimestamp",
            "subTasks",
            "errors",
            "resources",
            "resolutionStatus",
            "isCancellable",
            "isRetryable",
        ],
        "Task property projection changed",
    )

    profile = contract["focusedCollectionProfile"]
    require(
        profile["operation"] == "tasks.list"
        and profile["firstPage"] == 0
        and profile["requestPageParameters"] == ["pageNumber", "pageSize"]
        and profile["requestPageParameterOrder"] == ["pageNumber", "pageSize"]
        and profile["unsetParameters"] == UNSET_NAMES
        and profile["unsetBehavior"] == "omit",
        "focused pagination profile changed",
    )
    require(
        profile["requiredTaskProperties"]
        == ["id", "name", "status", "creationTimestamp"]
        and profile["projectionPropertyOrder"]
        == ["Id", "Name", "Status", "CreationTimestamp"]
        and profile["ordering"]
        == {
            "keys": ["CreationTimestamp", "Id"],
            "timestampComparison": "represented instant ascending",
            "idComparison": "ordinal case-sensitive",
        },
        "focused task output profile changed",
    )
    require(
        "not represented as additional OpenAPI source material"
        in profile["profileBoundary"],
        "specification and scenario boundary is unclear",
    )

    require(sources["tag"] == TAG, "official source tag changed")
    require(sources["repositoryCommitSha"] == COMMIT, "official commit changed")
    require(sources["specPath"] == SPEC_PATH, "official spec path changed")
    require(sources["license"] == "Apache-2.0", "official license changed")
    require(sources["operationIds"] == OPERATION_IDS, "official operationIds changed")
    require(sources["excludedRevision"] == "9.1.0.0", "9.1 exclusion changed")
    require(
        COMMIT in sources["specUrl"]
        and sources["specUrl"].endswith(SPEC_PATH)
        and COMMIT in sources["rawSpecUrl"]
        and sources["rawSpecUrl"].endswith(SPEC_PATH),
        "official specification URLs are not immutable",
    )
    source_operation = sources["operations"]
    require(len(source_operation) == 1, "each operation must have one source record")
    require(
        {
            "operationId": source_operation[0]["operationId"],
            "method": source_operation[0]["method"],
            "path": source_operation[0]["path"],
            "openapiPointer": source_operation[0]["openapiPointer"],
            "repositoryCommitSha": source_operation[0]["repositoryCommitSha"],
            "specPath": source_operation[0]["specPath"],
        }
        == {
            "operationId": "getTasks",
            "method": "GET",
            "path": ROUTE,
            "openapiPointer": "#/paths/~1v1~1tasks/get",
            "repositoryCommitSha": COMMIT,
            "specPath": SPEC_PATH,
        },
        "getTasks source record changed",
    )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"] is False
        and sources["derivation"]["revision91UsedAsContractSource"] is False,
        "contract must come only from the pinned 9.0 JSON specification",
    )


def run_pwsh(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


def ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def verify_manifest_and_sdk() -> None:
    command = f"""
$ErrorActionPreference = 'Stop'
$d = Import-PowerShellDataFile -Path {ps_quote(MANIFEST_PATH)}
if ($d.RootModule -cne 'VcfInstallerTaskInventory.psm1') {{ exit 3 }}
if ($d.PowerShellVersion -cne '7.4') {{ exit 4 }}
if ($d.FunctionsToExport.Count -ne 1 -or $d.FunctionsToExport[0] -cne 'Get-VcfInstallerTaskInventory') {{ exit 5 }}
if ($d.CmdletsToExport.Count -ne 0 -or $d.AliasesToExport.Count -ne 0 -or $d.VariablesToExport.Count -ne 0) {{ exit 6 }}
$r = $d.RequiredModules[0]
if ($r.ModuleName -cne '{MODULE_NAME}' -or $r.RequiredVersion.ToString() -cne '{MODULE_VERSION}') {{ exit 7 }}
if ([version]$PSVersionTable.PSVersion -lt [version]'7.4') {{ exit 8 }}
$installed = Get-Module -ListAvailable -Name '{MODULE_NAME}' | Where-Object {{ $_.Version.ToString() -ceq '{MODULE_VERSION}' }} | Select-Object -First 1
if ($null -eq $installed) {{ exit 9 }}
Import-Module '{MODULE_NAME}' -RequiredVersion '{MODULE_VERSION}' -Force
$discover = Get-Command Get-VcfInstallerOperation -ErrorAction Stop
$invoke = Get-Command Invoke-VcfInstallerGetTasks -ErrorAction Stop
if ($discover.CommandType -ne 'Cmdlet' -or $invoke.CommandType -ne 'Cmdlet') {{ exit 10 }}
if ($discover.Source -cne '{MODULE_NAME}' -or $invoke.Source -cne '{MODULE_NAME}') {{ exit 11 }}
"""
    result = run_pwsh(command)
    require(
        result.returncode == 0,
        "protected manifest or genuine PowerCLI prerequisite check failed: "
        + (result.stderr + result.stdout)[-700:],
    )

    require(MODULE_PATH.is_file(), "implementation module is missing")
    source = MODULE_PATH.read_text(encoding="utf-8")
    require(
        "Get-VcfInstallerOperation" in source
        and "Invoke-VcfInstallerGetTasks" in source,
        "implementation must resolve the genuine generated getTasks binding",
    )
    folded = source.casefold()
    forbidden = [
        "function get-vcfinstalleroperation",
        "function invoke-vcfinstallergettasks",
        "invoke-restmethod",
        "invoke-webrequest",
        "system.diagnostics.process",
        "start-process",
        "tcpclient",
        "system.net.sockets.socket",
        "curl.exe",
    ]
    require(
        not any(item in folded for item in forbidden),
        "implementation vendors, replaces, or bypasses the required integration",
    )
    require(
        not any(
            path.name.casefold().startswith("vmware.sdk.vcf")
            for path in ROOT.rglob("*")
        ),
        "the seed must not vendor VMware.Sdk.Vcf modules",
    )


def wait_for(path: Path, process: subprocess.Popen[str], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            return
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise VerificationError("mock exited before readiness: " + stderr[-700:])
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def header_values(record: dict[str, Any], name: str) -> list[str]:
    return [
        str(value)
        for key, value in record["headers"]
        if str(key).casefold() == name.casefold()
    ]


def verify_wire(log_path: Path) -> None:
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    expected_targets = [
        "/v1/tasks?pageNumber=0&pageSize=2",
        "/v1/tasks?pageNumber=1&pageSize=2",
        "/v1/tasks?pageNumber=2&pageSize=2",
    ] * 2
    expected_targets += [
        "/v1/tasks?pageNumber=0&pageSize=100",
        "/v1/tasks?pageNumber=0&pageSize=14",
        "/v1/tasks?pageNumber=0&pageSize=3",
        "/v1/tasks?pageNumber=0&pageSize=4",
        "/v1/tasks?pageNumber=1&pageSize=4",
        "/v1/tasks?pageNumber=0&pageSize=5",
        "/v1/tasks?pageNumber=0&pageSize=6",
        "/v1/tasks?pageNumber=0&pageSize=7",
        "/v1/tasks?pageNumber=0&pageSize=8",
        "/v1/tasks?pageNumber=0&pageSize=9",
        "/v1/tasks?pageNumber=0&pageSize=10",
        "/v1/tasks?pageNumber=0&pageSize=11",
        "/v1/tasks?pageNumber=0&pageSize=12",
        "/v1/tasks?pageNumber=1&pageSize=12",
        "/v1/tasks?pageNumber=0&pageSize=13",
        "/v1/tasks?pageNumber=1&pageSize=13",
        "/v1/tasks?pageNumber=0&pageSize=15",
        "/v1/tasks?pageNumber=0&pageSize=16",
        "/v1/tasks?pageNumber=0&pageSize=17",
        "/v1/tasks?pageNumber=0&pageSize=18",
        "/v1/tasks?pageNumber=1&pageSize=18",
        "/v1/tasks?pageNumber=0&pageSize=19",
        "/v1/tasks?pageNumber=0&pageSize=20",
        "/v1/tasks?pageNumber=0&pageSize=21",
        "/v1/tasks?pageNumber=0&pageSize=22",
        "/v1/tasks?pageNumber=0&pageSize=23",
        "/v1/tasks?pageNumber=0&pageSize=24",
        "/v1/tasks?pageNumber=0&pageSize=25",
        "/v1/tasks?pageNumber=0&pageSize=26",
    ]
    require(
        len(records) == len(expected_targets),
        f"expected exactly {len(expected_targets)} requests, got {len(records)}",
    )
    require(
        [record["method"] for record in records] == ["GET"] * len(records),
        "only getTasks GET requests are permitted",
    )
    require(
        [record["rawTarget"] for record in records] == expected_targets,
        "raw pagination targets or their order changed",
    )
    for index, record in enumerate(records):
        require(record["ordinal"] == index, "request log ordinal changed")
        require(record["bodyHex"] == "", "GET requests must have zero-byte bodies")
        require(
            header_values(record, "Accept") == ["application/json"],
            "each request must have exactly one application/json Accept header",
        )
        require(
            header_values(record, "Content-Type") == [],
            "bodyless GET must omit Content-Type",
        )
        require(
            header_values(record, "Transfer-Encoding") == [],
            "bodyless GET must not use transfer encoding",
        )
        require(
            header_values(record, "Content-Length") in ([], ["0"]),
            "bodyless GET must not have a positive Content-Length",
        )
        parsed = urlsplit(record["rawTarget"])
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        expected_query = parse_qsl(
            urlsplit(expected_targets[index]).query, keep_blank_values=True
        )
        require(pairs == expected_query, "query serialization is not exact")
        require(not any(value == "" for _, value in pairs), "empty query value sent")
        present = {name for name, _ in pairs}
        require(
            present.isdisjoint(UNSET_NAMES),
            "an unset getTasks optional parameter was serialized",
        )


def verify_output(output_path: Path, scenario_path: Path) -> None:
    output = load_json(output_path)
    expected = load_json(scenario_path)["expected"]
    require(output["ExportedFunctions"] == ["Get-VcfInstallerTaskInventory"], "module export surface changed")
    require(output["First"] == expected, "first inventory is incomplete or unstable")
    require(output["Second"] == expected, "second inventory changed with page composition")
    require(output["First"] == output["Second"], "identical membership must emit identically")
    require(
        output["DiscoveryCalls"] == 2,
        "Get-VcfInstallerOperation must be called exactly once per inventory",
    )
    success_cases = output["SuccessCases"]
    require(
        list(success_cases.keys())
        == ["DefaultPageSize", "CaseInsensitiveContentType"],
        "success validation case inventory changed",
    )
    for name, outcome in success_cases.items():
        require(outcome["Threw"] is False, f"{name} unexpectedly failed")
        require(outcome["Items"] == [], f"{name} emitted unexpected tasks")

    expected_failures = [
        "Redirect",
        "LatePageFailure",
        "DuplicateWithinPage",
        "MalformedTimestamp",
        "BlankRequiredTaskField",
        "NonIntegerMetadata",
        "InconsistentTotalPages",
        "WrongPageNumber",
        "ShortNonFinalPage",
        "ChangingTotals",
        "DuplicateAcrossPages",
        "UnexpectedStatus",
        "ElementsNotArray",
        "MetadataPageSizeMismatch",
        "IncompleteFinalPage",
        "InvalidJson",
        "MissingMetadataField",
        "NonStringTaskField",
        "UnexpectedContentType",
        "NegativeTotal",
        "TopLevelNotObject",
        "MetadataNotObject",
        "TaskNotObject",
    ]
    failure_cases = output["FailureCases"]
    require(
        list(failure_cases.keys()) == expected_failures,
        "failure validation case inventory changed",
    )
    for name, outcome in failure_cases.items():
        require(outcome["Threw"] is True, f"{name} was not rejected")
        require(
            outcome["SuccessCount"] == 0,
            f"{name} leaked success output before validation completed",
        )

    expected_invalid = [
        "RelativeUri",
        "WrongScheme",
        "Credentials",
        "Query",
        "Fragment",
        "NonRootPath",
        "HostlessOrigin",
        "PageSizeTooSmall",
        "PageSizeTooLarge",
    ]
    invalid_arguments = output["InvalidArguments"]
    require(
        list(invalid_arguments.keys()) == expected_invalid,
        "invalid argument case inventory changed",
    )
    for name, outcome in invalid_arguments.items():
        require(outcome["Threw"] is True, f"{name} was not rejected")
        require(
            outcome["SuccessCount"] == 0,
            f"{name} emitted success output",
        )

    require(
        output["ParameterContract"]
        == {
            "Names": ["BaseUri", "HttpClient", "PageSize"],
            "BaseUriType": "System.Uri",
            "PageSizeType": "System.Int32",
            "HttpClientType": "System.Net.Http.HttpClient",
            "BaseUriMandatory": True,
            "PageSizeMandatory": False,
            "HttpClientMandatory": False,
        },
        "Get-VcfInstallerTaskInventory parameter contract changed",
    )
    for item in output["First"]:
        require(
            list(item.keys()) == ["Id", "Name", "Status", "CreationTimestamp"],
            "task projection property order changed",
        )


def verify_integration() -> None:
    with tempfile.TemporaryDirectory(prefix="vcf90-0103-") as temporary:
        temp = Path(temporary)
        ready_path = temp / "ready.json"
        log_path = temp / "requests.jsonl"
        scenario_path = temp / "scenario.json"
        output_path = temp / "output.json"
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--ready",
                str(ready_path),
                "--log",
                str(log_path),
                "--scenario",
                str(scenario_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wait_for(ready_path, process)
            base_uri = load_json(ready_path)["baseUri"]
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-BaseUri",
                    base_uri,
                    "-OutputPath",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
            )
            require(
                result.returncode == 0,
                "PowerShell inventory scenario failed: "
                + (result.stderr + result.stdout)[-1000:],
            )
            require(output_path.is_file(), "PowerShell scenario produced no output artifact")
            verify_output(output_path, scenario_path)
            verify_wire(log_path)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_sdk()
        verify_integration()
    except (VerificationError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF Installer task inventory contract and wire behavior verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
