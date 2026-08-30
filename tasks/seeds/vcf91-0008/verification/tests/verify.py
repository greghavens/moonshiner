#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0008."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "VcfFailureEvidence" / "VcfFailureEvidence.psd1"
MODULE_PATH = ROOT / "VcfFailureEvidence" / "VcfFailureEvidence.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
OPERATION_IDS = [
    "getTask",
    "getResourceWarnings",
    "startSupportBundle",
    "getSupportBundleStatus",
]
MODULE_NAME = "VMware.Sdk.Vcf.SddcManager"
MODULE_VERSION = "13.5.0.25380678"
LOG_PROPERTIES = [
    "vcLogs",
    "nsxLogs",
    "esxLogs",
    "hcxLogs",
    "wcpLogs",
    "sddcManagerLogs",
    "apiLogs",
    "systemDebugLogs",
    "vmScreenshots",
    "vraLogs",
    "vropsLogs",
    "vrliLogs",
    "vrslcmLogs",
    "automationLogs",
    "operationsLogs",
    "operationsForLogs",
    "lifecycleLogs",
    "vmsLogs",
]
OUTPUT_PROPERTIES = [
    "taskId",
    "taskName",
    "failedSubTask",
    "failedStage",
    "errorCode",
    "errorMessage",
    "remediationMessage",
    "referenceToken",
    "resourceId",
    "resourceType",
    "resourceName",
    "warningCode",
    "warningMessage",
    "supportBundleId",
    "supportBundleName",
    "supportBundleStatus",
    "logSelection",
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
    source = contract.get("source", {})
    require(
        contract.get("contractFormat") == "focused-openapi-projection-v1",
        "contract format changed",
    )
    require(source.get("repositoryCommitSha") == COMMIT, "contract commit changed")
    require(source.get("specPath") == SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(source.get("openapi") == "3.0.1", "OpenAPI version changed")
    require(source.get("apiVersion") == "9.1.0.0", "VCF API version changed")

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract operationIds changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations]
        == [
            ("GET", "/v1/tasks/{id}"),
            ("GET", "/v1/resource-warnings"),
            ("POST", "/v1/system/support-bundles"),
            ("GET", "/v1/system/support-bundles/{id}"),
        ],
        "contract routes changed",
    )
    get_task, get_warnings, start_bundle, get_status = operations
    require(get_task.get("requestBody") is False, "getTask must be bodyless")
    require(
        get_task.get("parameters")
        == [
            {
                "name": "id",
                "in": "path",
                "description": "Task id to retrieve",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        "getTask path parameter changed",
    )
    require(
        [item.get("name") for item in get_warnings.get("parameters", [])]
        == ["resourceType", "resourceIds", "resourceNames"],
        "warning query projection changed",
    )
    require(
        all(item.get("required") is False for item in get_warnings["parameters"]),
        "warning query members must remain optional",
    )
    warning_wire = get_warnings.get("focusedWireProfile", {})
    require(
        warning_wire.get("boundMembers") == ["resourceType", "resourceIds"],
        "focused warning members changed",
    )
    require(
        warning_wire.get("unsetMembers") == ["resourceNames"]
        and warning_wire.get("unsetBehavior") == "omit",
        "resourceNames must be omitted",
    )
    require(
        start_bundle.get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "SupportBundleSpec",
        },
        "support bundle body contract changed",
    )
    bundle_wire = start_bundle.get("focusedWireProfile", {})
    require(
        bundle_wire.get("supportBundleSpecMembers") == ["logs"],
        "focused support bundle members changed",
    )
    require(
        bundle_wire.get("unsetSupportBundleSpecMembers") == ["options", "scope"]
        and bundle_wire.get("unsetBehavior") == "omit",
        "unset support bundle fields must be omitted",
    )
    require(
        get_status.get("parameters", [])[0].get("name") == "id"
        and get_status["parameters"][0].get("required") is True,
        "support bundle status path parameter changed",
    )

    schemas = contract.get("schemas", {})
    require(
        schemas.get("Task", {}).get("required")
        == ["creationTimestamp", "id", "name", "status"],
        "Task required fields changed",
    )
    require(
        schemas.get("SubTask", {}).get("required")
        == ["creationTimestamp", "description", "name", "status"],
        "SubTask required fields changed",
    )
    require(
        schemas.get("Stage", {}).get("required")
        == ["creationTimestamp", "description", "name", "status", "type"],
        "Stage required fields changed",
    )
    require(
        schemas.get("Resource", {}).get("required") == ["resourceId", "type"],
        "Resource required fields changed",
    )
    require(
        schemas.get("AssociatedTask", {}).get("required") == ["taskId"],
        "AssociatedTask required fields changed",
    )
    require(
        schemas.get("ResourceWarning", {}).get("required")
        == ["occurredAtTimestamp"],
        "ResourceWarning required fields changed",
    )
    require(
        list(schemas.get("Logs", {}).get("properties", {})) == LOG_PROPERTIES,
        "Logs property projection changed",
    )
    require(
        list(schemas.get("SupportBundleSpec", {}).get("properties", {}))
        == ["options", "scope", "logs"],
        "SupportBundleSpec projection changed",
    )
    require(
        "referenceToken" in schemas.get("Error", {}).get("properties", {})
        and "referenceToken"
        in schemas.get("ResourceWarning", {}).get("properties", {}),
        "reference-token correlation fields changed",
    )

    require(sources.get("repositoryCommitSha") == COMMIT, "source commit changed")
    require(sources.get("specPath") == SPEC_PATH, "source path changed")
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(sources.get("operationIds") == OPERATION_IDS, "source operationIds changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
        "official source URL must be immutable",
    )
    require(
        [
            (
                item.get("operationId"),
                item.get("method"),
                item.get("path"),
                item.get("repositoryCommitSha"),
                item.get("specPath"),
                item.get("jsonPointer"),
            )
            for item in sources.get("operations", [])
        ]
        == [
            (
                "getTask",
                "GET",
                "/v1/tasks/{id}",
                COMMIT,
                SPEC_PATH,
                "/paths/~1v1~1tasks~1{id}/get/operationId",
            ),
            (
                "getResourceWarnings",
                "GET",
                "/v1/resource-warnings",
                COMMIT,
                SPEC_PATH,
                "/paths/~1v1~1resource-warnings/get/operationId",
            ),
            (
                "startSupportBundle",
                "POST",
                "/v1/system/support-bundles",
                COMMIT,
                SPEC_PATH,
                "/paths/~1v1~1system~1support-bundles/post/operationId",
            ),
            (
                "getSupportBundleStatus",
                "GET",
                "/v1/system/support-bundles/{id}",
                COMMIT,
                SPEC_PATH,
                "/paths/~1v1~1system~1support-bundles~1{id}/get/operationId",
            ),
        ],
        "each operation must repeat its exact pinned source",
    )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "a documentation page must not be the contract source",
    )


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_pwsh(command: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={
            **os.environ,
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        },
    )


def verify_manifest_and_sdk() -> None:
    command = (
        f"$d=Import-PowerShellDataFile -LiteralPath {ps_quote(str(MANIFEST_PATH))};"
        "if($d.RootModule -cne 'VcfFailureEvidence.psm1'){exit 2};"
        "if($d.PowerShellVersion -cne '7.4'){exit 3};"
        "if(($d.FunctionsToExport -join ',') -cne 'Get-VcfFailureEvidence'){exit 4};"
        "$r=$d.RequiredModules[0];"
        f"if($r.ModuleName -cne '{MODULE_NAME}' -or "
        f"[string]$r.RequiredVersion -cne '{MODULE_VERSION}'){{exit 5}};"
        f"Import-Module '{MODULE_NAME}' -RequiredVersion '{MODULE_VERSION}' -Force -ErrorAction Stop;"
        "foreach($id in @('getTask','getResourceWarnings','startSupportBundle','getSupportBundleStatus')){"
        "$o=@(Get-VcfSddcManagerOperation -Name $id);"
        "if($o.Count -ne 1 -or $null -eq $o[0].CommandInfo){exit 6};"
        "$c=Get-Command $o[0].CommandInfo.Name -CommandType Cmdlet -ErrorAction Stop;"
        f"if($c.Source -cne '{MODULE_NAME}'){{exit 7}}"
        "};"
        "foreach($name in @('Initialize-VcfLogs','Initialize-VcfSupportBundleSpec')){"
        "$c=Get-Command $name -CommandType Cmdlet -ErrorAction Stop;"
        f"if($c.Source -cne '{MODULE_NAME}'){{exit 8}}"
        "}"
    )
    result = run_pwsh(command)
    require(
        result.returncode == 0,
        "protected manifest or genuine SDDC Manager SDK prerequisite is invalid: "
        + (result.stderr.strip() or result.stdout.strip()),
    )


def verify_solution_shape() -> None:
    require(MODULE_PATH.is_file(), "create VcfFailureEvidence.psm1")
    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    for required in (
        "vmware.sdk.vcf.sddcmanager",
        "get-vcfsddcmanageroperation",
        "gettask",
        "getresourcewarnings",
        "startsupportbundle",
        "getsupportbundlestatus",
        "initialize-vcflogs",
        "initialize-vcfsupportbundlespec",
    ):
        require(
            required in folded,
            f"implementation does not use required SDK surface: {required}",
        )
    for forbidden in (
        "invoke-webrequest",
        "invoke-restmethod",
        "system.net.http",
        "httpclient",
        "webclient",
        "tcpclient",
        "system.net.sockets",
        "start-process",
        "connect-vcfsddcmanagerserver",
        "disconnect-vcfsddcmanagerserver",
        "curl",
        "wget",
    ):
        require(forbidden not in folded, f"implementation bypasses or owns the SDK connection: {forbidden}")
    parse_command = (
        "$tokens=$null;$errors=$null;"
        f"[void][Management.Automation.Language.Parser]::ParseFile({ps_quote(str(MODULE_PATH))},[ref]$tokens,[ref]$errors);"
        "if($errors.Count -ne 0){$errors | ForEach-Object {Write-Error $_};exit 9}"
    )
    parsed = run_pwsh(parse_command)
    require(
        parsed.returncode == 0,
        "PowerShell module has syntax errors: "
        + (parsed.stderr.strip() or parsed.stdout.strip()),
    )
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and (
            path.suffix.casefold() in {".dll", ".nupkg", ".snupkg", ".zip"}
            or path.name.casefold().startswith("vmware.sdk.vcf")
        )
    ]
    require(not vendored, "the seed must not vendor VMware or binary dependencies")


def make_case(
    marker: str,
    resource_type: str,
    log_property: str,
    wrong_resource_type: str,
    index: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    task_id = str(uuid.uuid4())
    other_task_id = str(uuid.uuid4())
    resource_id = str(uuid.uuid4())
    wrong_resource_id = str(uuid.uuid4())
    bundle_id = str(uuid.uuid4())
    reference_token = "ref-" + secrets.token_urlsafe(20)
    decoy_reference = "decoy-" + secrets.token_urlsafe(18)
    task_name = f"Lifecycle workflow {marker}-{index}"
    failed_subtask = f"Apply component image {marker}-{index}"
    failed_stage = f"Validate package state {marker}-{index}"
    resource_prefix = {
        "VCENTER": "vc",
        "NSXT_MANAGER": "nsx",
        "ESXI": "esx",
        "SDDC_MANAGER": "sddc",
    }[resource_type]
    resource_name = f"{resource_prefix}-{marker}-{index}.example.test"
    error_code = f"LCM_{resource_type}_PACKAGE_REJECTED"
    error_message = f"Package verification failed for evidence {marker}-{index}"
    remediation = f"Replace the rejected package for evidence {marker}-{index}"
    warning_code = f"WARN_{resource_type}_PACKAGE_STATE"
    warning_message = f"Correlated package warning {marker}-{index}"
    bundle_name = f"sos-{marker}-{index}.tar.gz"
    base_time = f"2026-08-02T12:0{index}:00Z"

    target_resource = {
        "resourceId": resource_id,
        "fqdn": resource_name,
        "type": resource_type,
        "name": resource_name,
    }
    wrong_resource = {
        "resourceId": wrong_resource_id,
        "fqdn": f"decoy-{marker}-{index}.example.test",
        "type": wrong_resource_type,
        "name": f"decoy-{marker}-{index}",
    }
    error = {
        "errorCode": error_code,
        "errorType": "OPERATIONAL",
        "message": error_message,
        "remediationMessage": remediation,
        "referenceToken": reference_token,
    }
    task = {
        "id": task_id,
        "name": task_name,
        "type": "DOMAIN_UPGRADE",
        "status": " Failed ",
        "creationTimestamp": base_time,
        "completionTimestamp": f"2026-08-02T12:0{index}:30Z",
        "resources": [wrong_resource],
        "subTasks": [
            {
                "name": f"Inspect {wrong_resource_type}",
                "type": "DISCOVERY",
                "description": "A successful but misleading component event",
                "status": "SUCCESSFUL",
                "creationTimestamp": base_time,
                "resources": [wrong_resource],
                "stages": [
                    {
                        "name": "Discovery",
                        "type": "DISCOVERY",
                        "description": "Successful decoy stage",
                        "status": "SUCCESSFUL",
                        "creationTimestamp": base_time,
                    }
                ],
            },
            {
                "name": failed_subtask,
                "type": f"{resource_type}_UPGRADE",
                "description": "The failed leaf event",
                "status": " failed ",
                "creationTimestamp": base_time,
                "resources": [target_resource],
                "stages": [
                    {
                        "name": failed_stage,
                        "type": "PACKAGE_VALIDATION",
                        "description": "The failed evidence stage",
                        "status": "Failed",
                        "creationTimestamp": base_time,
                        "errors": [error],
                    },
                    {
                        "name": "Later cleanup",
                        "type": "CLEANUP",
                        "description": "A stage that was not applicable",
                        "status": "SKIPPED",
                        "creationTimestamp": base_time,
                    },
                ],
            },
        ],
    }
    if index % 2 == 0:
        failed_leaf = task["subTasks"][1]
        task["subTasks"][1] = {
            "name": f"Nested failed branch {marker}-{index}",
            "type": "COMPOSITE",
            "description": "A failed non-leaf with misleading evidence",
            "status": "FAILED",
            "creationTimestamp": base_time,
            "resources": [wrong_resource],
            "stages": [
                {
                    "name": "Parent summary",
                    "type": "SUMMARY",
                    "description": "A non-leaf stage is not the evidence stage",
                    "status": "FAILED",
                    "creationTimestamp": base_time,
                    "errors": [
                        {
                            "errorCode": "PARENT_DECOY",
                            "errorType": "OPERATIONAL",
                            "message": "Do not select the failed parent",
                            "remediationMessage": "Inspect the failed leaf",
                            "referenceToken": decoy_reference,
                        }
                    ],
                }
            ],
            "subTasks": [failed_leaf],
        }
    if index % 2 == 0:
        task["subTasks"].reverse()
    actual_warning = {
        "id": str(uuid.uuid4()),
        "warningCode": warning_code,
        "message": warning_message,
        "remediationMessage": remediation,
        "referenceToken": reference_token,
        "resourceId": resource_id,
        "resourceType": resource_type.lower(),
        "resourceName": resource_name,
        "warningType": "VALIDATION",
        "severity": "MAJOR",
        "occurredAtTimestamp": f"2026-08-02T12:0{index}:20Z",
        "associatedTask": {"taskId": task_id},
    }
    decoy_warning = {
        "id": str(uuid.uuid4()),
        "warningCode": f"DECOY_{resource_type}",
        "message": "An older warning for the same resource",
        "remediationMessage": "Ignore this unrelated warning",
        "referenceToken": decoy_reference,
        "resourceId": resource_id,
        "resourceType": resource_type,
        "resourceName": resource_name,
        "warningType": "OTHER",
        "severity": "MINOR",
        "occurredAtTimestamp": "2026-08-01T00:00:00Z",
        "associatedTask": {"taskId": other_task_id},
    }
    wrong_resource_warning = {
        **actual_warning,
        "id": str(uuid.uuid4()),
        "warningCode": f"WRONG_RESOURCE_{resource_type}",
        "message": "The token matches but the resource id does not",
        "resourceId": wrong_resource_id,
    }
    wrong_type_warning = {
        **actual_warning,
        "id": str(uuid.uuid4()),
        "warningCode": f"WRONG_TYPE_{resource_type}",
        "message": "The token matches but the resource type does not",
        "resourceType": wrong_resource_type,
    }
    wrong_task_warning = {
        **actual_warning,
        "id": str(uuid.uuid4()),
        "warningCode": f"WRONG_TASK_{resource_type}",
        "message": "The token matches but the associated task does not",
        "associatedTask": {"taskId": other_task_id},
    }
    started = {
        "status": "PENDING",
        "creationTimestamp": f"2026-08-02T12:0{index}:31Z",
        "description": "Targeted SOS collection",
        "bundleAvailable": "false",
        "id": bundle_id,
    }
    pending = {
        **started,
        "status": " PENDING " if index % 2 == 1 else " in progress ",
    }
    complete = {
        **started,
        "status": " completed with success ",
        "completionTimestamp": f"2026-08-02T12:0{index}:40Z",
        "bundleAvailable": "true",
        "bundleName": bundle_name,
        "size": "4096",
    }
    case = {
        "taskId": task_id,
        "resourceId": resource_id,
        "resourceType": resource_type,
        "bundleId": bundle_id,
        "logProperty": log_property,
        "task": task,
        "warningPage": {
            "elements": (
                [
                    decoy_warning,
                    wrong_resource_warning,
                    actual_warning,
                    wrong_type_warning,
                    wrong_task_warning,
                ]
                if index % 2 == 1
                else [
                    wrong_task_warning,
                    wrong_type_warning,
                    actual_warning,
                    wrong_resource_warning,
                    decoy_warning,
                ]
            ),
            "pageMetadata": {
                "pageNumber": 0,
                "pageSize": 5,
                "totalElements": 5,
                "totalPages": 1,
            },
        },
        "bundleStarted": started,
        "bundlePending": pending,
        "bundleComplete": complete,
    }
    expected = {
        "taskId": task_id,
        "taskName": task_name,
        "failedSubTask": failed_subtask,
        "failedStage": failed_stage,
        "errorCode": error_code,
        "errorMessage": error_message,
        "remediationMessage": remediation,
        "referenceToken": reference_token,
        "resourceId": resource_id,
        "resourceType": resource_type,
        "resourceName": resource_name,
        "warningCode": warning_code,
        "warningMessage": warning_message,
        "supportBundleId": bundle_id,
        "supportBundleName": bundle_name,
        "supportBundleStatus": "COMPLETED_WITH_SUCCESS",
        "logSelection": log_property,
    }
    return case, expected


def make_scenario() -> tuple[dict[str, Any], list[dict[str, str]]]:
    marker = secrets.token_hex(5)
    vcenter_case, vcenter_expected = make_case(
        marker, "VCENTER", "vcLogs", "NSXT_MANAGER", 1
    )
    nsx_case, nsx_expected = make_case(
        marker, "NSXT_MANAGER", "nsxLogs", "VCENTER", 2
    )
    esxi_case, esxi_expected = make_case(
        marker, "ESXI", "esxLogs", "SDDC_MANAGER", 3
    )
    sddc_case, sddc_expected = make_case(
        marker, "SDDC_MANAGER", "sddcManagerLogs", "ESXI", 4
    )
    return (
        {
            "accessToken": "access-" + secrets.token_urlsafe(28),
            "cases": [vcenter_case, nsx_case, esxi_case, sddc_case],
        },
        [vcenter_expected, nsx_expected, esxi_expected, sddc_expected],
    )


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=2)
            raise VerificationError(
                "loopback mock exited during startup: " + (stderr or stdout).strip()
            )
        if port_file.is_file():
            value = port_file.read_text(encoding="utf-8").strip()
            if value:
                return int(value)
        time.sleep(0.04)
    raise VerificationError("loopback mock did not publish its port")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_header(request: dict[str, Any], name: str) -> str:
    values = request.get("headerValues", {}).get(name.casefold(), [])
    require(len(values) == 1, f"{name} must occur exactly once")
    return values[0]


def verify_runtime() -> None:
    scenario, expected_results = make_scenario()
    cases = scenario["cases"]
    with tempfile.TemporaryDirectory(prefix="vcf91-0008-") as temporary:
        temp = Path(temporary)
        port_file = temp / "port.txt"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        result_file = temp / "result.json"
        scenario_file.write_text(
            json.dumps(scenario, separators=(",", ":")), encoding="utf-8"
        )
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(request_log),
                str(CONTRACT_PATH),
                str(scenario_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(port_file, server)
            invocation = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-ModuleManifest",
                    str(MANIFEST_PATH),
                    "-BaseUri",
                    f"http://127.0.0.1:{port}/",
                    "-AccessToken",
                    scenario["accessToken"],
                    "-TaskIdOne",
                    cases[0]["taskId"],
                    "-TaskIdTwo",
                    cases[1]["taskId"],
                    "-TaskIdThree",
                    cases[2]["taskId"],
                    "-TaskIdFour",
                    cases[3]["taskId"],
                    "-OutputPath",
                    str(result_file),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=70,
                check=False,
                env={
                    **os.environ,
                    "POWERSHELL_TELEMETRY_OPTOUT": "1",
                    "POWERSHELL_UPDATECHECK": "Off",
                },
            )
            require(
                invocation.returncode == 0,
                "PowerShell acceptance cases failed: "
                + (invocation.stderr.strip() or invocation.stdout.strip()),
            )
            require(result_file.is_file(), "PowerShell did not write its result")
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

        result = load_json(result_file)
        requests = read_log(request_log)

    require(
        result.get("resultCount") == 4,
        "all supported incidents must return evidence",
    )
    require(
        result.get("preflightRejections") == 4,
        "null, blank, or out-of-range input was not rejected",
    )
    require(result.get("tokenUnchanged") is True, "the caller-owned token was mutated")
    require(
        isinstance(result.get("connectionType"), str)
        and "VcfSddcManager" in result["connectionType"],
        "caller-owned connection is not a genuine SDDC Manager SDK type",
    )
    output_results = result.get("results", [])
    require(len(output_results) == 4, "result count changed")
    for index, row in enumerate(output_results):
        require(
            row.get("propertyOrder") == ",".join(OUTPUT_PROPERTIES),
            f"result {index} property order changed",
        )
        projected = {name: row.get(name) for name in OUTPUT_PROPERTIES}
        require(
            projected == expected_results[index],
            f"result {index} was guessed or did not preserve correlated evidence",
        )
    require(
        scenario["accessToken"] not in json.dumps(result, separators=(",", ":")),
        "diagnostic output exposes the access token",
    )

    require(len(requests) == 20, "wire request count must be exactly twenty")
    expected_sequence = [
        "getTask",
        "getResourceWarnings",
        "startSupportBundle",
        "getSupportBundleStatus",
        "getSupportBundleStatus",
    ] * 4
    require(
        [request.get("operationId") for request in requests] == expected_sequence,
        "operation sequence changed or an extra route was contacted",
    )
    require(
        [request.get("responseStatus") for request in requests]
        == [200, 200, 202, 200, 200] * 4,
        "the mock rejected a request or polling sequence changed",
    )
    expected_targets: list[str] = []
    for case in cases:
        expected_targets.extend(
            [
                f"/v1/tasks/{case['taskId']}",
                "/v1/resource-warnings"
                f"?resourceType={case['resourceType']}"
                f"&resourceIds={case['resourceId']}",
                "/v1/system/support-bundles",
                f"/v1/system/support-bundles/{case['bundleId']}",
                f"/v1/system/support-bundles/{case['bundleId']}",
            ]
        )
    require(
        [request.get("rawTarget") for request in requests] == expected_targets,
        "exact raw targets changed",
    )

    for index, request in enumerate(requests):
        require(
            one_header(request, "authorization")
            == f"Bearer {scenario['accessToken']}",
            f"request {index} authorization changed",
        )
        require(
            "application/json" in one_header(request, "accept"),
            f"request {index} must accept JSON",
        )

    for case_index, case in enumerate(cases):
        offset = case_index * 5
        get_task, warnings, start, first_poll, final_poll = requests[offset : offset + 5]
        for label, request in (
            ("getTask", get_task),
            ("getResourceWarnings", warnings),
            ("first status poll", first_poll),
            ("final status poll", final_poll),
        ):
            require(request.get("method") == "GET", f"{label} method changed")
            require(request.get("bodyLength") == 0, f"{label} must be bodyless")
            require(request.get("body") == "", f"{label} body must be empty")
            require(
                "content-type" not in request.get("headerValues", {}),
                f"{label} must omit Content-Type",
            )
        require(get_task.get("query") == {}, "getTask query must be absent")
        require(
            warnings.get("query")
            == {
                "resourceType": [case["resourceType"]],
                "resourceIds": [case["resourceId"]],
            },
            "warning query omitted evidence or sent an unset optional field",
        )
        require(
            "resourceNames" not in warnings.get("query", {}),
            "unset resourceNames must be omitted",
        )
        require(first_poll.get("query") == {}, "first poll query must be absent")
        require(final_poll.get("query") == {}, "final poll query must be absent")

        require(start.get("method") == "POST", "startSupportBundle method changed")
        require(start.get("rawQuery") == "", "support bundle query must be absent")
        require(start.get("query") == {}, "support bundle query must be empty")
        content_type = one_header(start, "content-type")
        require(
            content_type.split(";", 1)[0].strip().casefold() == "application/json",
            "support bundle Content-Type must be application/json",
        )
        expected_body = json.dumps(
            {"logs": {case["logProperty"]: True}}, separators=(",", ":")
        )
        require(start.get("body") == expected_body, "support bundle body bytes changed")
        require(
            start.get("bodyLength") == len(expected_body.encode("utf-8")),
            "support bundle Content-Length/body length changed",
        )
        parsed_body = json.loads(start["body"])
        require(list(parsed_body) == ["logs"], "options or scope was sent unset")
        require(
            list(parsed_body["logs"]) == [case["logProperty"]]
            and parsed_body["logs"][case["logProperty"]] is True,
            "unset component log fields were serialized",
        )
        for omitted in [
            name for name in LOG_PROPERTIES if name != case["logProperty"]
        ]:
            require(
                omitted not in parsed_body["logs"],
                f"unset optional log field {omitted} was serialized",
            )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_sdk()
        verify_solution_shape()
        verify_runtime()
    except (OSError, ValueError, subprocess.SubprocessError, VerificationError) as error:
        print(f"FAIL: {error}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
