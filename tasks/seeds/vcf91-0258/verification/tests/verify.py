#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0258."""

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
MANIFEST_PATH = ROOT / "VcfOpsCollectionTriage" / "VcfOpsCollectionTriage.psd1"
MODULE_PATH = ROOT / "VcfOpsCollectionTriage" / "VcfOpsCollectionTriage.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_ops.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
SPEC_BLOB = "a56a00c504d4156aa765339c47d585d15db68768"
BASE_PATH = "/suite-api"
SDK_MODULE = "VMware.Sdk.Vcf.Ops"
SDK_VERSION = "13.5.0.25380678"
CAUSE_STAT_KEY = "System Attributes|last_collection_time_diff"

OPERATIONS = [
    ("acquireToken", "POST", "/api/auth/token/acquire", 5641),
    ("getCurrentVersionOfServer", "GET", "/api/versions/current", 22453),
    ("getMatchingResources", "POST", "/api/resources/query", 19113),
    ("queryAlert", "POST", "/api/alerts/query", 2634),
    ("getAlertContributingSymptoms", "GET", "/api/alerts/contributingsymptoms", 2416),
    ("querySymptoms", "POST", "/api/symptoms/query", 22268),
    ("releaseToken", "POST", "/api/auth/token/release", 5741),
]
OPERATION_IDS = [item[0] for item in OPERATIONS]

RESOURCE_QUERY_SENT = ["adapterKind", "name", "resourceKind"]
RESOURCE_QUERY_OMITTED = {
    "adapterInstanceId",
    "collectorId",
    "collectorName",
    "credentialId",
    "includeRelated",
    "maintenanceScheduleId",
    "parentId",
    "propertyConditions",
    "propertyName",
    "propertyValue",
    "recentlyAdded",
    "regex",
    "resourceHealth",
    "resourceId",
    "resourceState",
    "resourceStatus",
    "resourceTag",
    "statConditions",
    "statKey",
    "statKeyInclusive",
    "statKeyLowerBound",
    "statKeyUpperBound",
}
ALERT_QUERY_SENT = ["activeOnly", "resource-query"]
ALERT_QUERY_OMITTED = {
    "alertControlState",
    "alertCriticality",
    "alertDefinitionId",
    "alertId",
    "alertImpact",
    "alertName",
    "alertStatus",
    "alertTypeSubtype",
    "cancelTimeRange",
    "compositeOperator",
    "extractOwnerName",
    "groupId",
    "groupingCondition",
    "includeChildrenResources",
    "resourceKind",
    "startTimeRange",
    "updateTimeRange",
    "userId",
    "userName",
}
NESTED_RESOURCE_QUERY_OMITTED = (
    RESOURCE_QUERY_OMITTED | set(RESOURCE_QUERY_SENT)
) - {"resourceId"}
SYMPTOM_QUERY_SENT = ["includeAlarmInfo", "symptomId"]
SYMPTOM_QUERY_OMITTED = {
    "activeOnly",
    "alarmCriticality",
    "alarmType",
    "cancelTimeRange",
    "compositeOperator",
    "includeChildrenResources",
    "resource-query",
    "startTimeRange",
    "statKey",
    "symptomDefinitionId",
}

FORBIDDEN_SOURCE_TOKENS = (
    "invoke-restmethod",
    "invoke-webrequest",
    "httpclient",
    "httpwebrequest",
    "system.net.sockets",
    "tcpclient",
    "start-process",
    "curl ",
    "webclient",
)
REQUIRED_SOURCE_TOKENS = (
    "connect-vcfopsserver",
    "invoke-vcfopsgetmatchingresources",
    "invoke-vcfopsqueryalert",
    "invoke-vcfopsgetalertcontributingsymptoms",
    "invoke-vcfopsquerysymptoms",
    "disconnect-vcfopsserver",
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# specification projection
# --------------------------------------------------------------------------


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

    require(source["repositoryCommitSha"] == COMMIT, "contract commit changed")
    require(source["specPath"] == SPEC_PATH, "contract spec path changed")
    require(source["specBlobSha"] == SPEC_BLOB, "contract spec blob changed")
    require(source["license"] == "Apache-2.0", "contract license changed")
    require(source["openapi"] == "3.0.1", "OpenAPI version changed")
    require(
        source["title"] == "VMware Cloud Foundation Operations API",
        "API title changed",
    )
    require(source["apiVersion"] == "9.1.0.0", "API version changed")
    require(source["basePath"] == BASE_PATH, "specification base path changed")
    require(
        source["repository"] == "https://github.com/vmware/vcf-api-specs",
        "contract repository changed",
    )

    require(
        contract["securitySchemes"]["OpsToken"]["name"] == "Authorization"
        and contract["securitySchemes"]["OpsToken"]["valuePrefix"] == "OpsToken ",
        "session security projection changed",
    )

    operations = contract["operations"]
    require(
        [
            (item["operationId"], item["method"], item["path"])
            for item in operations
        ]
        == [(op, method, path) for op, method, path, _ in OPERATIONS],
        "focused operation projection changed",
    )
    by_id = {item["operationId"]: item for item in operations}

    require(
        by_id["acquireToken"]["requestBody"]["schema"]
        == {"$ref": "username-password"},
        "acquireToken request projection changed",
    )
    require(
        by_id["getMatchingResources"]["requestBody"]["schema"]
        == {"$ref": "resource-query"},
        "getMatchingResources request projection changed",
    )
    require(
        by_id["queryAlert"]["requestBody"]["schema"] == {"$ref": "alert-query"},
        "queryAlert request projection changed",
    )
    require(
        by_id["querySymptoms"]["requestBody"]["schema"]
        == {"$ref": "symptom-query"},
        "querySymptoms request projection changed",
    )
    require(
        [
            (item["name"], item["in"], item["required"])
            for item in by_id["getAlertContributingSymptoms"]["parameters"]
        ]
        == [("id", "query", True)],
        "getAlertContributingSymptoms parameter projection changed",
    )
    for op_id in ("getMatchingResources", "queryAlert", "querySymptoms"):
        require(
            [
                (item["name"], item["in"])
                for item in by_id[op_id]["parameters"]
            ]
            == [("page", "query"), ("pageSize", "query")],
            f"{op_id} paging parameter projection changed",
        )

    schemas = contract["schemas"]
    require(
        sorted(schemas["resource-query"]["properties"])
        == sorted(RESOURCE_QUERY_OMITTED | set(RESOURCE_QUERY_SENT)),
        "resource-query projection changed",
    )
    require(
        sorted(schemas["alert-query"]["properties"])
        == sorted(ALERT_QUERY_OMITTED | set(ALERT_QUERY_SENT)),
        "alert-query projection changed",
    )
    require(
        schemas["alert-query"]["properties"]["resource-query"]
        == {"$ref": "resource-query"},
        "alert-query must carry the hyphenated resource-query member",
    )
    require(
        sorted(schemas["symptom-query"]["properties"])
        == sorted(SYMPTOM_QUERY_OMITTED | set(SYMPTOM_QUERY_SENT)),
        "symptom-query projection changed",
    )
    require(
        schemas["resource-status-state"]["properties"]["resourceStatus"]["enum"]
        == [
            "NONE",
            "ERROR",
            "UNKNOWN",
            "DOWN",
            "DATA_RECEIVING",
            "OLD_DATA_RECEIVING",
            "NO_DATA_RECEIVING",
            "NO_PARENT_MONITORING",
            "COLLECTOR_DOWN",
        ],
        "resource collection status projection changed",
    )
    require(
        sorted(schemas["resource-status-state"]["properties"])
        == ["adapterInstanceId", "resourceState", "resourceStatus", "statusMessage"],
        "resource-status-state projection changed",
    )
    require(
        schemas["resources"]["properties"]["resourceList"]["items"]
        == {"$ref": "resource"}
        and schemas["alerts"]["properties"]["alerts"]["items"] == {"$ref": "alert"}
        and schemas["symptoms"]["properties"]["symptom"]["items"]
        == {"$ref": "symptom"},
        "collection response projection changed",
    )
    require(
        schemas["alert-contributing-symptoms"]["properties"][
            "contributingSymptoms"
        ]["items"]
        == {"$ref": "alert-contributing-symptom"}
        and schemas["alert-contributing-symptom"]["properties"][
            "contributingSymptoms"
        ]
        == {"$ref": "contributing-symptoms"}
        and schemas["contributing-symptoms"]["properties"]["contributingSymptoms"][
            "items"
        ]
        == {"$ref": "contributing-symptom"},
        "contributing-symptom projection changed",
    )
    require(
        sorted(schemas["symptom"]["properties"])
        == [
            "alarmInfo",
            "cancelTimeUTC",
            "extension",
            "faultDevices",
            "id",
            "kpi",
            "links",
            "message",
            "resourceId",
            "startTimeUTC",
            "statKey",
            "symptomCriticality",
            "symptomDefinitionId",
            "updateTimeUTC",
        ],
        "symptom projection changed",
    )

    workflow = contract["focusedWorkflow"]
    require(
        workflow["operationOrder"] == OPERATION_IDS,
        "focused operation order changed",
    )
    require(
        workflow["unauthenticatedOperations"] == ["acquireToken"],
        "unauthenticated operation projection changed",
    )
    require(
        workflow["resourceQueryFieldsSent"] == RESOURCE_QUERY_SENT
        and set(workflow["resourceQueryFieldsOmitted"]) == RESOURCE_QUERY_OMITTED,
        "resource-query omission contract changed",
    )
    require(
        workflow["alertQueryFieldsSent"] == ALERT_QUERY_SENT
        and set(workflow["alertQueryFieldsOmitted"]) == ALERT_QUERY_OMITTED
        and workflow["alertQueryNestedResourceQueryFieldsSent"] == ["resourceId"]
        and set(workflow["alertQueryNestedResourceQueryFieldsOmitted"])
        == NESTED_RESOURCE_QUERY_OMITTED,
        "alert-query omission contract changed",
    )
    require(
        workflow["symptomQueryFieldsSent"] == SYMPTOM_QUERY_SENT
        and set(workflow["symptomQueryFieldsOmitted"]) == SYMPTOM_QUERY_OMITTED,
        "symptom-query omission contract changed",
    )
    require(workflow["unsetBehavior"] == "omit", "unset-field behaviour changed")
    require(
        "not evidence" in workflow["evidenceBoundary"]
        and "pinned OpenAPI JSON specification"
        in workflow["specificationBoundary"],
        "evidence or specification boundary is unclear",
    )

    require(
        sources["repositoryCommitSha"] == COMMIT,
        "official source commit changed",
    )
    require(sources["specPath"] == SPEC_PATH, "official spec path changed")
    require(sources["specBlobSha"] == SPEC_BLOB, "official spec blob changed")
    require(sources["license"] == "Apache-2.0", "official license changed")
    require(sources["basePath"] == BASE_PATH, "official base path changed")
    require(
        sources["operationIds"] == OPERATION_IDS,
        "official operationIds changed",
    )
    require(
        COMMIT in sources["specUrl"] and sources["specUrl"].endswith(SPEC_PATH),
        "official specification URL is not immutable",
    )
    require(
        COMMIT in sources["commitUrl"],
        "official commit URL is not immutable",
    )
    require(
        [
            (
                item["operationId"],
                item["method"],
                item["path"],
                item["specLine"],
                item["repositoryCommitSha"],
                item["specPath"],
            )
            for item in sources["operations"]
        ]
        == [
            (op, method, path, line, COMMIT, SPEC_PATH)
            for op, method, path, line in OPERATIONS
        ],
        "each operation must carry its exact pinned source",
    )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"] is False,
        "a documentation page must not be the contract source",
    )


# --------------------------------------------------------------------------
# implementation shape
# --------------------------------------------------------------------------


def pwsh_env() -> dict[str, str]:
    return {
        **os.environ,
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "POWERSHELL_UPDATECHECK": "Off",
    }


def verify_manifest_and_shape() -> None:
    command = (
        "$d = Import-PowerShellDataFile -Path '"
        + str(MANIFEST_PATH).replace("'", "''")
        + "'; "
        + "if ($d.RootModule -cne 'VcfOpsCollectionTriage.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne 'Get-VcfOpsCollectionDiagnosis') "
        + "{ exit 5 }; "
        + "$r = $d.RequiredModules[0]; "
        + "if ($r.ModuleName -cne '"
        + SDK_MODULE
        + "' -or [string]$r.RequiredVersion -cne '"
        + SDK_VERSION
        + "') { exit 6 }"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env=pwsh_env(),
    )
    require(
        result.returncode == 0,
        "protected PowerShell manifest is invalid: " + result.stderr,
    )

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    for token in FORBIDDEN_SOURCE_TOKENS:
        require(token not in folded, f"forbidden transport found: {token.strip()}")
    for token in REQUIRED_SOURCE_TOKENS:
        require(token in folded, f"required SDK cmdlet missing: {token}")
    require(
        folded.count("function get-vcfopscollectiondiagnosis") == 1,
        "public function declaration changed",
    )
    require(
        "/suite-api" not in folded and "/api/alerts" not in folded,
        "the module must let the SDK own routing, not hand-build routes",
    )

    vendored_suffixes = {".dll", ".nupkg", ".nuspec"}
    vendored_names = {
        "VMware.Sdk.Vcf.Ops.psm1",
        "VMware.Sdk.Vcf.Ops.psd1",
        "VMware.OpenAPI.psm1",
        "VMware.OpenAPI.psd1",
    }
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        require(
            path.suffix.casefold() not in vendored_suffixes
            and path.name not in vendored_names,
            f"vendored VMware dependency or binary found: "
            f"{path.relative_to(ROOT)}",
        )


# --------------------------------------------------------------------------
# scenario
# --------------------------------------------------------------------------


def wait_for_ready(
    process: subprocess.Popen[str],
    ready_path: Path,
    timeout: float = 15.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_path.exists() and ready_path.stat().st_size:
            return load_json(ready_path)
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError("mock exited before ready: " + stdout + stderr)
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


def build_config(*, cause_symptom_criticality: str = "CRITICAL") -> dict[str, Any]:
    start = 1_790_000_000_000 + secrets.randbelow(50_000_000)
    return {
        "username": "svc-triage-" + secrets.token_hex(4),
        "password": "pw-" + secrets.token_urlsafe(18),
        "auth_source": "local",
        "token": str(uuid.uuid4()) + "::" + secrets.token_hex(8),
        "token_validity": start + 3_600_000,
        "token_expires_at": "Thursday, January 1, 2026 12:00:00 AM UTC",
        "release_name": "VCF Operations 9.1.0.0",
        "release_date": "Tuesday, March 3, 2026 at 12:00:00 PM UTC",
        "object_name": "wld01-cl-" + secrets.token_hex(3),
        "resource_kind": "ClusterComputeResource",
        "adapter_kind": "VMWARE",
        "page_size": 50 + secrets.randbelow(450),
        "resource_id": str(uuid.uuid4()),
        "healthy_adapter_instance_id": str(uuid.uuid4()),
        "failing_adapter_instance_id": str(uuid.uuid4()),
        "status_message": "collection halted; evidence " + secrets.token_hex(10),
        "decoy_alert_id": str(uuid.uuid4()),
        "decoy_alert_name": "Adapter instance collection has slowed down",
        "cause_alert_id": str(uuid.uuid4()),
        "cause_alert_name": "Object status changed",
        "cancelled_alert_id": str(uuid.uuid4()),
        "cancelled_alert_name": "Superseded availability alert",
        "decoy_symptom_id": str(uuid.uuid4()),
        "decoy_symptom_message": "adapter health badge red; " + secrets.token_hex(8),
        "cause_symptom_id": str(uuid.uuid4()),
        "cause_symptom_message": "collection age exceeded; " + secrets.token_hex(8),
        "cause_symptom_criticality": cause_symptom_criticality,
        "extra_symptom_id": str(uuid.uuid4()),
        "extra_symptom_message": "alarm count rising; " + secrets.token_hex(8),
        "start_time": start,
    }


def run_case(
    *, expect_inconclusive: bool = False
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    config = build_config(
        cause_symptom_criticality=(
            "WARNING" if expect_inconclusive else "CRITICAL"
        )
    )

    with tempfile.TemporaryDirectory(prefix="vcf91-0258-") as temp_name:
        temp = Path(temp_name)
        config_path = temp / "config.json"
        log_path = temp / "requests.jsonl"
        ready_path = temp / "ready.json"
        result_path = temp / "result.json"
        config_path.write_text(
            json.dumps(config, separators=(",", ":")), encoding="utf-8"
        )

        mock = subprocess.Popen(
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
            ready = wait_for_ready(mock, ready_path)
            require(ready["host"] == "127.0.0.1", "mock is not loopback-only")
            require(ready["basePath"] == BASE_PATH, "mock base path changed")
            invocation_command = [
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
                str(result_path),
            ]
            if expect_inconclusive:
                invocation_command.append("-ExpectInconclusive")
            invocation = subprocess.run(
                invocation_command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=180,
                check=False,
                env=pwsh_env(),
            )
            require(
                invocation.returncode == 0,
                "PowerShell scenario failed: "
                + invocation.stdout
                + invocation.stderr,
            )
            require(result_path.exists(), "PowerShell emitted no result")
            result_text = result_path.read_text(encoding="utf-8")
            for secret in (config["password"], config["token"]):
                require(
                    secret not in result_text
                    and secret not in invocation.stdout
                    and secret not in invocation.stderr,
                    "a credential or session token leaked outside the request",
                )
            result = json.loads(result_text)

            deadline = time.monotonic() + 3.0
            requests = read_jsonl(log_path)
            while len(requests) < len(OPERATIONS) and time.monotonic() < deadline:
                time.sleep(0.02)
                requests = read_jsonl(log_path)
            return result, requests, config
        finally:
            if mock.poll() is None:
                mock.terminate()
                try:
                    mock.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.wait(timeout=5)
            if mock.stdout is not None:
                mock.stdout.close()
            if mock.stderr is not None:
                mock.stderr.close()


def verify_result(result: dict[str, Any], config: dict[str, Any]) -> None:
    require(
        list(result.keys())
        == [
            "Status",
            "ObjectName",
            "ResourceId",
            "AdapterInstanceId",
            "ResourceStatus",
            "StatusMessage",
            "Cause",
            "AlertId",
            "AlertDefinitionName",
            "Evidence",
        ],
        "result property order or member set changed",
    )
    require(
        result["Status"] == "Diagnosed"
        and result["Cause"] == "AdapterInstanceNotCollecting",
        "verdict changed",
    )
    require(
        result["ObjectName"] == config["object_name"]
        and result["ResourceId"] == config["resource_id"]
        and result["AdapterInstanceId"] == config["failing_adapter_instance_id"],
        "diagnosis is not tied to the resolved object and adapter instance",
    )
    require(
        result["ResourceStatus"] == "NO_DATA_RECEIVING",
        "resource status must be reported using the specification value",
    )
    require(
        result["StatusMessage"] == config["status_message"],
        "the collection status message was not preserved",
    )
    require(
        result["AlertId"] == config["cause_alert_id"],
        "the triggering alert was not selected from symptom evidence",
    )
    require(
        result["AlertDefinitionName"] == config["cause_alert_name"],
        "the triggering alert name was not preserved",
    )

    evidence = result["Evidence"]
    require(
        isinstance(evidence, list) and len(evidence) == 2,
        "evidence must carry both contributing symptoms of the triggering alert",
    )
    expected = [
        {
            "SymptomId": config["cause_symptom_id"],
            "StatKey": CAUSE_STAT_KEY,
            "Criticality": "CRITICAL",
            "Message": config["cause_symptom_message"],
            "StartTimeUTC": config["start_time"] + 30_000,
        },
        {
            "SymptomId": config["extra_symptom_id"],
            "StatKey": "System Attributes|total_alarm_count",
            "Criticality": "WARNING",
            "Message": config["extra_symptom_message"],
            "StartTimeUTC": config["start_time"] + 45_000,
        },
    ]
    for index, item in enumerate(evidence):
        require(
            list(item.keys())
            == ["SymptomId", "StatKey", "Criticality", "Message", "StartTimeUTC"],
            f"evidence {index + 1} property order or member set changed",
        )
        require(
            item == expected[index],
            f"evidence {index + 1} was not preserved accurately",
        )
    require(
        config["decoy_symptom_id"] not in json.dumps(result),
        "evidence must exclude the unrelated alert's symptom",
    )


def verify_inconclusive_rejection(result: dict[str, Any]) -> None:
    require(
        result == {"Rejected": True},
        "inconclusive symptom evidence was not rejected without a verdict",
    )


def verify_wire(
    requests: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    require(
        len(requests) == len(OPERATIONS),
        f"expected {len(OPERATIONS)} requests, saw {len(requests)}",
    )
    require(
        [item["operationId"] for item in requests] == OPERATION_IDS,
        "operation order changed: "
        + ",".join(str(item["operationId"]) for item in requests),
    )
    require(
        [item["sequence"] for item in requests] == list(range(len(OPERATIONS))),
        "requests were not issued in a single ordered pass",
    )
    for index, request in enumerate(requests):
        require(
            request["response_status"] == 200,
            f"request {index + 1} was refused by the contract-pinned node",
        )
        require(
            request["method"] == OPERATIONS[index][1],
            f"request {index + 1} method changed",
        )
        require(
            request["path"] == BASE_PATH + OPERATIONS[index][2],
            f"request {index + 1} path changed",
        )

    authorization = "OpsToken " + config["token"]
    require(
        "authorization" not in requests[0]["headers"],
        "acquireToken must not present a session token",
    )
    for index, request in enumerate(requests[1:], start=1):
        require(
            request["headers"].get("authorization") == [authorization],
            f"request {index + 1} must present exactly one session token",
        )

    page_size = str(config["page_size"])

    acquire = requests[0]
    require(
        acquire["query"] == "" and acquire["raw_target"].endswith("/acquire"),
        "acquireToken target is not exact",
    )
    require(
        acquire["body_raw"]
        == json.dumps(
            {
                "username": config["username"],
                "password": config["password"],
                "authSource": config["auth_source"],
            },
            separators=(",", ":"),
        ),
        "acquireToken body shape changed",
    )

    require(requests[1]["query"] == "", "version probe must carry no query")

    resources = requests[2]
    require(
        resources["query_pairs"] == [["pageSize", page_size]],
        "getMatchingResources must page with the caller's pageSize only",
    )
    body = resources["body"]
    require(
        list(body.keys()) == RESOURCE_QUERY_SENT,
        "getMatchingResources body member set or order changed",
    )
    require(
        body
        == {
            "adapterKind": [config["adapter_kind"]],
            "name": [config["object_name"]],
            "resourceKind": [config["resource_kind"]],
        },
        "getMatchingResources body values changed",
    )
    require(
        RESOURCE_QUERY_OMITTED.isdisjoint(body),
        "getMatchingResources sent an unset resource-query field",
    )
    require(
        "null" not in resources["body_raw"] and "[]" not in resources["body_raw"],
        "unset resource-query fields must be omitted, not sent empty",
    )

    alerts = requests[3]
    require(
        alerts["query_pairs"] == [["pageSize", page_size]],
        "queryAlert must page with the caller's pageSize only",
    )
    body = alerts["body"]
    require(
        list(body.keys()) == ALERT_QUERY_SENT,
        "queryAlert body member set or order changed",
    )
    require(body["activeOnly"] is True, "queryAlert must request active alerts only")
    nested = body["resource-query"]
    require(
        list(nested.keys()) == ["resourceId"],
        "the nested resource-query member set or order changed",
    )
    require(
        nested["resourceId"] == [config["failing_adapter_instance_id"]],
        "alerts were not scoped to the adapter instance named by the stalled "
        "resource status",
    )
    require(
        ALERT_QUERY_OMITTED.isdisjoint(body),
        "queryAlert sent an unset alert-query field",
    )
    require(
        NESTED_RESOURCE_QUERY_OMITTED.isdisjoint(nested),
        "the nested resource-query sent an unset field",
    )
    require(
        "null" not in alerts["body_raw"] and "[]" not in alerts["body_raw"],
        "unset alert-query fields must be omitted, not sent empty",
    )
    require(
        "resourceQuery" not in alerts["body_raw"],
        "the alert-query member is spelled resource-query in the specification",
    )

    contributing = requests[4]
    require(
        contributing["body_bytes"] == 0,
        "getAlertContributingSymptoms must not carry a body",
    )
    require(
        contributing["query_pairs"]
        == [
            ["id", config["decoy_alert_id"]],
            ["id", config["cause_alert_id"]],
        ],
        "contributing symptoms must be requested for every active alert, in "
        "the order the alert query returned them",
    )

    symptoms = requests[5]
    require(
        symptoms["query_pairs"] == [["pageSize", page_size]],
        "querySymptoms must page with the caller's pageSize only",
    )
    body = symptoms["body"]
    require(
        list(body.keys()) == SYMPTOM_QUERY_SENT,
        "querySymptoms body member set or order changed",
    )
    require(
        body["includeAlarmInfo"] is True,
        "querySymptoms must request alarm information",
    )
    require(
        body["symptomId"]
        == [
            config["decoy_symptom_id"],
            config["extra_symptom_id"],
            config["cause_symptom_id"],
        ],
        "every contributed symptom must be resolved, in contribution order",
    )
    require(
        SYMPTOM_QUERY_OMITTED.isdisjoint(body),
        "querySymptoms sent an unset symptom-query field",
    )
    require(
        "null" not in symptoms["body_raw"],
        "unset symptom-query fields must be omitted, not sent empty",
    )

    release = requests[6]
    require(
        release["query"] == "" and release["body_bytes"] == 0,
        "releaseToken target is not exact",
    )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        result, requests, config = run_case()
        verify_result(result, config)
        verify_wire(requests, config)
        rejected, rejection_requests, rejection_config = run_case(
            expect_inconclusive=True
        )
        verify_inconclusive_rejection(rejected)
        verify_wire(rejection_requests, rejection_config)
    except (
        VerificationError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    print("all protected checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
