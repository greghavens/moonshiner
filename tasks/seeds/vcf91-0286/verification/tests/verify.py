#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0286."""

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
MANIFEST_PATH = ROOT / "VcfOpsNetworksApp" / "VcfOpsNetworksApp.psd1"
MODULE_PATH = ROOT / "VcfOpsNetworksApp" / "VcfOpsNetworksApp.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_ni.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
SPEC_BLOB = "a275ed98b326e07f4dd5fcadfdd67b315fe49419"
BASE_PATH = "/api/ni"
SDK_MODULE = "VMware.Sdk.Vcf.Ops"
SDK_VERSION = "13.5.0.25380678"
AUTH_PREFIX = "NetworkInsight "

OPERATIONS = [
    ("create", "POST", "/auth/token", 70),
    (
        "getSavedApplicationsSummaries",
        "GET",
        "/groups/applications/fetch",
        15975,
    ),
    ("addApplicationWithTiers", "POST", "/groups/applications/full/", 16176),
    ("delete", "DELETE", "/auth/token", 103),
]
OPERATION_IDS = [item[0] for item in OPERATIONS]

USER_CREDENTIAL_SENT = ["username", "password", "domain"]
DOMAIN_LOCAL_SENT = ["domain_type"]
DOMAIN_LOCAL_OMITTED = {"value"}
SUMMARIES_OMITTED = {
    "modifiedAfter",
    "fetch_member_counts",
    "fetch_update_status",
}
APP_REQUEST_SENT = ["name", "tiers"]
APP_REQUEST_OMITTED = {"entity_id", "source_group_entity_id", "enable_intent"}
TIER_REQUEST_SENT = ["name", "group_membership_criteria"]
TIER_REQUEST_OMITTED = {"entity_id", "member_list", "source_group_entity_id"}
CRITERIA_SEARCH_SENT = ["membership_type", "search_membership_criteria"]
CRITERIA_IP_SENT = ["membership_type", "ip_address_membership_criteria"]

FORBIDDEN_SOURCE_TOKENS = (
    "vmware.com",
    "vmware.local",
    "start-process",
    "system.net.sockets",
    "tcpclient",
)
REQUIRED_SOURCE_TOKENS = ("vmware.sdk.vcf.ops",)

PUBLIC_PARAMETERS = [
    "Server",
    "Credential",
    "Name",
    "Tier",
    "Protocol",
    "Port",
    "DomainType",
    "DomainValue",
    "EnableIntent",
    "LastModifiedTimestamp",
    "PageSize",
]
PUBLIC_PARAMETER_TYPES = [
    "System.String",
    "System.Management.Automation.PSCredential",
    "System.String",
    "System.Collections.Hashtable[]",
    "System.String",
    "System.Int32",
    "System.String",
    "System.String",
    "System.Boolean",
    "System.Int64",
    "System.Int32",
]
PUBLIC_PARAMETER_DEFAULTS = [
    None,
    None,
    None,
    None,
    "'https'",
    "443",
    "'LOCAL'",
    None,
    None,
    None,
    "100",
]

# The whole scenario: three create/retry invocations, an exact first-page hit,
# and one injected probe failure, plus the token each invocation creates and
# deletes.
EXPECTED_WIRE = [
    "create",
    "getSavedApplicationsSummaries",
    "getSavedApplicationsSummaries",
    "addApplicationWithTiers",
    "delete",
    "create",
    "getSavedApplicationsSummaries",
    "getSavedApplicationsSummaries",
    "delete",
    "create",
    "getSavedApplicationsSummaries",
    "getSavedApplicationsSummaries",
    "addApplicationWithTiers",
    "delete",
    "create",
    "getSavedApplicationsSummaries",
    "delete",
    "create",
    "getSavedApplicationsSummaries",
    "delete",
]
EXPECTED_STATUSES = [
    200,
    200,
    200,
    201,
    204,
    200,
    200,
    200,
    204,
    200,
    200,
    200,
    201,
    204,
    200,
    200,
    204,
    200,
    500,
    204,
]
PAGE_SIZE = 3
EXISTING_APPLICATION_COUNT = 5


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
        source["title"] == "VCF Operations for networks API Reference",
        "API title changed",
    )
    require(source["apiVersion"] == "9.1.0.0", "API version changed")
    require(source["basePath"] == BASE_PATH, "specification base path changed")
    require(
        source["repository"] == "https://github.com/vmware/vcf-api-specs",
        "contract repository changed",
    )

    scheme = contract["securitySchemes"]["ApiKeyAuth"]
    require(
        scheme["type"] == "apiKey"
        and scheme["in"] == "header"
        and scheme["name"] == "Authorization"
        and scheme["valuePrefix"] == AUTH_PREFIX,
        "session security projection changed",
    )

    operations = contract["operations"]
    require(
        [(item["operationId"], item["method"], item["path"]) for item in operations]
        == [(op, method, path) for op, method, path, _ in OPERATIONS],
        "focused operation projection changed",
    )
    by_id = {item["operationId"]: item for item in operations}
    require(
        by_id["addApplicationWithTiers"]["path"].endswith("/full/"),
        "the specification spells the create route with a trailing slash",
    )
    require(
        by_id["create"]["security"] == [],
        "create is the only unauthenticated operation in the projection",
    )
    require(
        by_id["create"]["requestBody"]["schema"] == {"$ref": "UserCredential"},
        "create request projection changed",
    )
    require(
        by_id["addApplicationWithTiers"]["requestBody"]["schema"]
        == {"$ref": "AppWithTiersRequest"},
        "addApplicationWithTiers request projection changed",
    )
    require(
        [
            (item["name"], item["in"])
            for item in by_id["addApplicationWithTiers"]["parameters"]
        ]
        == [("If-Match", "header")],
        "addApplicationWithTiers parameter projection changed",
    )
    require(
        [
            (item["name"], item["in"])
            for item in by_id["getSavedApplicationsSummaries"]["parameters"]
        ]
        == [
            ("size", "query"),
            ("cursor", "query"),
            ("modifiedAfter", "query"),
            ("fetch_member_counts", "query"),
            ("fetch_update_status", "query"),
        ],
        "getSavedApplicationsSummaries parameter projection changed",
    )
    require(
        "409" not in by_id["addApplicationWithTiers"]["responses"]
        and "201" in by_id["addApplicationWithTiers"]["responses"],
        "the create route documents no conflict response; the projection must "
        "say so",
    )

    schemas = contract["schemas"]
    require(
        sorted(schemas["UserCredential"]["properties"])
        == sorted(USER_CREDENTIAL_SENT),
        "UserCredential projection changed",
    )
    require(
        sorted(schemas["Domain"]["properties"])
        == sorted(set(DOMAIN_LOCAL_SENT) | DOMAIN_LOCAL_OMITTED)
        and schemas["Domain"]["properties"]["domain_type"]["enum"]
        == ["LDAP", "LOCAL"],
        "Domain projection changed",
    )
    require(
        sorted(schemas["AppWithTiersRequest"]["properties"])
        == sorted(set(APP_REQUEST_SENT) | APP_REQUEST_OMITTED),
        "AppWithTiersRequest projection changed",
    )
    require(
        schemas["AppWithTiersRequest"]["properties"]["tiers"]["items"]
        == {"$ref": "TierRequest"},
        "AppWithTiersRequest tier projection changed",
    )
    require(
        sorted(schemas["TierRequest"]["properties"])
        == sorted(set(TIER_REQUEST_SENT) | TIER_REQUEST_OMITTED),
        "TierRequest projection changed",
    )
    require(
        sorted(schemas["GroupMembershipCriteria"]["properties"])
        == sorted(set(CRITERIA_SEARCH_SENT) | set(CRITERIA_IP_SENT)),
        "GroupMembershipCriteria projection changed",
    )
    require(
        schemas["GroupMembershipCriteria"]["properties"]["membership_type"]["enum"]
        == ["SearchMembershipCriteria", "IPAddressMembershipCriteria"],
        "membership_type enum projection changed",
    )
    require(
        sorted(schemas["SearchMembershipCriteria"]["properties"])
        == ["entity_type", "filter"]
        and schemas["SearchMembershipCriteria"]["properties"]["entity_type"]
        == {"$ref": "AllEntityType"},
        "SearchMembershipCriteria projection changed",
    )
    require(
        "VirtualMachine" in schemas["AllEntityType"]["enum"],
        "AllEntityType projection lost the VirtualMachine member",
    )
    require(
        list(schemas["IpAddressMembershipCriteria"]["properties"])
        == ["ip_addresses"],
        "IpAddressMembershipCriteria projection changed",
    )
    require(
        schemas["PagedApplicationListResponse"]["properties"]["results"]["items"]
        == {"$ref": "Application"}
        and sorted(schemas["PagedApplicationListResponse"]["properties"])
        == ["cursor", "results", "total_count"],
        "PagedApplicationListResponse projection changed",
    )
    require(
        list(schemas["Token"]["properties"]) == ["token", "expiry"],
        "Token projection changed",
    )
    require(
        sorted(schemas["BaseEntity"]["properties"])
        == ["entity_id", "entity_type", "name"],
        "BaseEntity projection changed",
    )

    workflow = contract["focusedWorkflow"]
    require(
        workflow["operationOrder"] == OPERATION_IDS,
        "focused operation order changed",
    )
    require(
        workflow["unauthenticatedOperations"] == ["create"],
        "unauthenticated operation projection changed",
    )
    require(
        workflow["authorizationHeader"]["name"] == "Authorization"
        and workflow["authorizationHeader"]["valuePrefix"] == AUTH_PREFIX,
        "authorization projection changed",
    )
    idempotency = workflow["idempotency"]
    require(
        idempotency["probeOperationId"] == "getSavedApplicationsSummaries"
        and idempotency["probeKey"] == "name"
        and idempotency["mutatingOperationId"] == "addApplicationWithTiers"
        and idempotency["skipMutationWhenProbeMatches"] is True
        and idempotency["conflictResponseDocumented"] is False,
        "idempotency contract changed",
    )
    require(
        workflow["userCredentialFieldsSent"] == USER_CREDENTIAL_SENT
        and workflow["domainFieldsSentForLocal"] == DOMAIN_LOCAL_SENT
        and set(workflow["domainFieldsOmittedForLocal"]) == DOMAIN_LOCAL_OMITTED
        and workflow["domainFieldsSentForLdap"] == ["domain_type", "value"],
        "credential omission contract changed",
    )
    require(
        workflow["summariesQueryParametersSentFirstPage"] == ["size"]
        and workflow["summariesQueryParametersSentLaterPages"] == ["size", "cursor"]
        and set(workflow["summariesQueryParametersOmitted"]) == SUMMARIES_OMITTED,
        "summaries paging contract changed",
    )
    require(
        workflow["appWithTiersFieldsSent"] == APP_REQUEST_SENT
        and set(workflow["appWithTiersFieldsOmittedWhenUnset"]) == APP_REQUEST_OMITTED
        and workflow["appWithTiersHeadersOmittedWhenUnset"] == ["If-Match"],
        "application omission contract changed",
    )
    require(
        workflow["tierRequestFieldsSent"] == TIER_REQUEST_SENT
        and set(workflow["tierRequestFieldsOmittedWhenUnset"]) == TIER_REQUEST_OMITTED
        and workflow["groupMembershipCriteriaFieldsSentForSearch"]
        == CRITERIA_SEARCH_SENT
        and workflow["groupMembershipCriteriaFieldsSentForIpAddress"]
        == CRITERIA_IP_SENT,
        "tier omission contract changed",
    )
    require(
        workflow["searchMembershipEntityType"] == "VirtualMachine",
        "search membership entity type changed",
    )
    require(workflow["unsetBehavior"] == "omit", "unset-field behaviour changed")
    require(
        "not evidence" in workflow["evidenceBoundary"]
        and "pinned OpenAPI YAML specification" in workflow["specificationBoundary"],
        "evidence or specification boundary is unclear",
    )

    require(
        sources["repositoryCommitSha"] == COMMIT
        and sources["specPath"] == SPEC_PATH
        and sources["specBlobSha"] == SPEC_BLOB
        and sources["license"] == "Apache-2.0"
        and sources["basePath"] == BASE_PATH,
        "official sources pin changed",
    )
    require(
        sources["operationIds"] == OPERATION_IDS,
        "official sources operationId list changed",
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
        "official sources operation records changed",
    )
    require(
        sources["specUrl"]
        == "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
        + COMMIT
        + "/"
        + SPEC_PATH,
        "official sources spec URL changed",
    )
    require(
        sources["derivation"]["documentationPageUsedAsContractSource"] is False,
        "the contract must come from the specification, not a documentation page",
    )
    projected = {item["name"] for item in sources["schemaProjections"]}
    require(
        projected == set(contract["schemas"]),
        "official sources and contract disagree about the projected schemas",
    )


# --------------------------------------------------------------------------
# module shape
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
        + "if ($d.RootModule -cne 'VcfOpsNetworksApp.psm1') { exit 3 }; "
        + "if ($d.PowerShellVersion -cne '7.4') { exit 4 }; "
        + "if ($d.FunctionsToExport.Count -ne 1 -or "
        + "$d.FunctionsToExport[0] -cne 'New-VcfOpsNetworksApplication') "
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
        timeout=60,
        check=False,
        env=pwsh_env(),
    )
    require(
        result.returncode == 0,
        "protected PowerShell manifest is invalid: " + result.stderr,
    )

    module_literal = str(MODULE_PATH).replace("'", "''")
    ast_command = (
        "$tokens = $null; $parseErrors = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile('"
        + module_literal
        + "', [ref]$tokens, [ref]$parseErrors); "
        "if ($parseErrors.Count -ne 0) { exit 7 }; "
        "$functions = @($ast.FindAll({ param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -ceq 'New-VcfOpsNetworksApplication' }, $true)); "
        "if ($functions.Count -ne 1) { exit 8 }; "
        "$parameters = $functions[0].Body.ParamBlock.Parameters; "
        "[pscustomobject] @{ "
        "Names = @($parameters | ForEach-Object { $_.Name.VariablePath.UserPath }); "
        "Types = @($parameters | ForEach-Object { $_.StaticType.FullName }); "
        "Defaults = @($parameters | ForEach-Object { "
        "if ($null -eq $_.DefaultValue) { $null } "
        "else { $_.DefaultValue.Extent.Text } }) "
        "} | ConvertTo-Json -Depth 5 -Compress"
    )
    ast_result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ast_command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
        env=pwsh_env(),
    )
    require(
        ast_result.returncode == 0,
        "public function syntax or declaration changed: " + ast_result.stderr,
    )
    ast_shape = json.loads(ast_result.stdout)
    require(
        ast_shape["Names"] == PUBLIC_PARAMETERS
        and ast_shape["Types"] == PUBLIC_PARAMETER_TYPES
        and ast_shape["Defaults"] == PUBLIC_PARAMETER_DEFAULTS,
        "public function parameters, types or defaults changed",
    )

    source = MODULE_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    for token in FORBIDDEN_SOURCE_TOKENS:
        require(token not in folded, f"forbidden token found in module: {token}")
    for token in REQUIRED_SOURCE_TOKENS:
        require(
            token in folded,
            f"the module must keep the external prerequisite: {token}",
        )
    require(
        folded.count("function new-vcfopsnetworksapplication") == 1,
        "public function declaration changed",
    )

    vendored_suffixes = {".dll", ".nupkg", ".nuspec"}
    vendored_names = {
        "VMware.Sdk.Vcf.Ops.psm1",
        "VMware.Sdk.Vcf.Ops.psd1",
        "VMware.OpenAPI.psm1",
        "VMware.OpenAPI.psd1",
        "vcf-operations-for-networks-openapi.yaml",
        "vcf-operations-openapi.json",
    }
    skipped = {".git", ".sandbox-home", "_verification", "__pycache__"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or skipped.intersection(path.parts):
            continue
        require(
            path.suffix.casefold() not in vendored_suffixes
            and path.name not in vendored_names,
            "vendored VMware dependency, specification or binary found: "
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


def build_config() -> dict[str, Any]:
    start = 1_790_000_000_000 + secrets.randbelow(50_000_000)
    tag = secrets.token_hex(4)
    application_a_name = "payments-" + tag
    existing = [
        {
            "entity_id": "18230:561:%d" % (264351372 + index),
            "name": "estate-%s-%d" % (secrets.token_hex(3), index),
            "created_by": "seed@local",
            "tier_count": 2 + index,
            "member_count": 8 + index,
        }
        for index in range(EXISTING_APPLICATION_COUNT)
    ]
    # A case-only variant on page one proves that the existence match is exact
    # and case-sensitive; an unrelated exact name on that page proves that the
    # caller stops immediately when it does find a match.
    existing[0]["name"] = application_a_name.upper()
    early_existing_name = existing[1]["name"]
    return {
        "username": "svc-netapp-" + secrets.token_hex(4) + "@local",
        "password": secrets.token_hex(18),
        "domain_value": "corp" + secrets.token_hex(3) + ".example",
        "ignored_local_domain_value": "ignored-" + secrets.token_hex(3),
        "token_prefix": secrets.token_hex(10),
        "fail_summary_token_number": 5,
        "page_size": PAGE_SIZE,
        "start_time": start,
        "created_entity_prefix": "18230:561",
        "existing_applications": existing,
        "application_a_name": application_a_name,
        "application_b_name": "reporting-" + secrets.token_hex(4),
        "early_existing_name": early_existing_name,
        "failure_application_name": "failure-" + secrets.token_hex(4),
        "tier_a_one_name": "web-" + secrets.token_hex(3),
        "tier_a_one_filter": "security_groups.entity_id = '18230:82:%d'"
        % (604573173 + secrets.randbelow(1000)),
        "tier_a_two_name": "db-" + secrets.token_hex(3),
        "tier_a_two_addresses": [
            "10.0.0.1",
            "10.0.0.0/24",
            "10.0.0.1-10.0.0.200",
        ],
        "tier_b_one_name": "svc-" + secrets.token_hex(3),
        "tier_b_one_filter": "security_tags.name = 'tier-%s'" % secrets.token_hex(3),
        "tier_b_one_addresses": ["192.168.10.5", "192.168.11.0/24"],
        # The OpenAPI parameter is a bare int64. A signed value catches an
        # implementation that invents an unstated non-negative validation gate.
        "last_modified_timestamp": -(start + 900_000),
    }


def run_case() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    config = build_config()

    with tempfile.TemporaryDirectory(prefix="vcf91-0286-") as temp_name:
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
            invocation = subprocess.run(
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
                    str(result_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=300,
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
            for secret in (config["password"], config["token_prefix"]):
                require(
                    secret not in result_text
                    and secret not in invocation.stdout
                    and secret not in invocation.stderr,
                    "a credential or session token leaked outside the request",
                )
            result = json.loads(result_text)

            deadline = time.monotonic() + 3.0
            requests = read_jsonl(log_path)
            while len(requests) < len(EXPECTED_WIRE) and time.monotonic() < deadline:
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


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


def verify_result(result: dict[str, Any], config: dict[str, Any]) -> None:
    require(
        result["ValidationResults"]
        == {
            "BlankServer": True,
            "PaddedServer": True,
            "BlankName": True,
            "PaddedName": True,
            "EmptyTiers": True,
            "BlankTierName": True,
            "PaddedTierName": True,
            "RepeatedTierName": True,
            "MissingLdapValue": True,
            "UnknownTierKey": True,
            "MissingCriteria": True,
        },
        "an invalid argument was not rejected with ArgumentException before traffic",
    )
    require(result["FailureObserved"] is True, "the injected probe failure was hidden")

    first = result["First"]
    second = result["Second"]
    third = result["Third"]
    early = result["EarlyExisting"]

    for label, item in (
        ("First", first),
        ("Second", second),
        ("Third", third),
        ("EarlyExisting", early),
    ):
        require(
            sorted(item)
            == [
                "Created",
                "EntityId",
                "EntityType",
                "LastModifiedTime",
                "MemberCount",
                "Name",
                "PagesScanned",
                "Source",
                "TierCount",
                "TierNames",
            ],
            f"{label} result shape changed",
        )
        require(item["EntityType"] == "Application", f"{label} entity type is wrong")

    require(
        first["PagesScanned"] == 2
        and second["PagesScanned"] == 2
        and third["PagesScanned"] == 2,
        "a create or retry call did not page the probe",
    )

    require(
        first["Name"] == config["application_a_name"]
        and first["Created"] is True
        and first["Source"] == "created",
        "the first call must create the application",
    )
    require(
        first["EntityId"].startswith(config["created_entity_prefix"]),
        "the created application must carry the node's entity id",
    )
    require(
        first["TierCount"] == 2
        and first["MemberCount"] == 8
        and first["LastModifiedTime"] == config["start_time"] + 5_500
        and first["TierNames"]
        == [config["tier_a_one_name"], config["tier_a_two_name"]],
        "the created application must report its tiers in caller order",
    )

    require(
        second["Created"] is False and second["Source"] == "existing",
        "repeating the call must not create a second application",
    )
    require(
        second["Name"] == config["application_a_name"]
        and second["EntityId"] == first["EntityId"],
        "the repeated call must resolve to the application the first call made",
    )
    require(
        second["LastModifiedTime"] == first["LastModifiedTime"],
        "the repeated call must report the stored application, not a new one",
    )

    require(
        third["Name"] == config["application_b_name"]
        and third["Created"] is True
        and third["TierCount"] == 1
        and third["MemberCount"] == 4
        and third["LastModifiedTime"] == config["start_time"] + 6_500
        and third["TierNames"] == [config["tier_b_one_name"]],
        "a different application name must still be created",
    )
    require(
        early["Name"] == config["early_existing_name"]
        and early["Created"] is False
        and early["Source"] == "existing"
        and early["TierCount"] == 3
        and early["MemberCount"] == 9
        and early["LastModifiedTime"] == config["start_time"] + 1_500
        and early["PagesScanned"] == 1
        and early["TierNames"]
        == [config["tier_a_one_name"], config["tier_a_two_name"]],
        "an exact first-page match was not returned immediately from node data",
    )


# --------------------------------------------------------------------------
# wire
# --------------------------------------------------------------------------


def no_empty_members(raw: str, label: str) -> None:
    for pattern, description in (
        ("null", "null"),
        (':""', "an empty string"),
        (":[]", "an empty array"),
        (":{}", "an empty object"),
    ):
        require(
            pattern not in raw,
            f"{label}: unset optional fields must be omitted, not sent as "
            f"{description}",
        )


def verify_wire(
    requests: list[dict[str, Any]], config: dict[str, Any]
) -> None:
    require(
        [item["operationId"] for item in requests] == EXPECTED_WIRE,
        "the request sequence is not the contracted one: "
        + json.dumps([item["operationId"] for item in requests]),
    )
    require(
        [item["response_status"] for item in requests] == EXPECTED_STATUSES,
        "the response sequence changed: "
        + json.dumps([item["response_status"] for item in requests]),
    )
    require(
        sum(1 for item in requests if item["operationId"] == "addApplicationWithTiers")
        == 2,
        "the mutating call must run once per distinct application name",
    )

    page_size = str(config["page_size"])
    cursor = "Mw=="  # base64("3"), the only cursor a size-3 page can hand back

    for run, (offset, creates) in enumerate(
        ((0, True), (5, False), (9, True)), start=1
    ):
        token = "%s-%d" % (config["token_prefix"], run)
        authorization = AUTH_PREFIX + token

        create = requests[offset]
        require(
            create["path"] == BASE_PATH + "/auth/token"
            and create["query"] == ""
            and "authorization" not in create["headers"],
            f"run {run}: create target is not exact",
        )
        body = create["body"]
        require(
            list(body) == USER_CREDENTIAL_SENT,
            f"run {run}: UserCredential member set or order changed",
        )
        require(
            body["username"] == config["username"]
            and body["password"] == config["password"],
            f"run {run}: the caller credential was not sent",
        )
        domain = body["domain"]
        if run == 3:
            require(
                list(domain) == ["domain_type", "value"]
                and domain["domain_type"] == "LDAP"
                and domain["value"] == config["domain_value"],
                f"run {run}: an LDAP domain must carry its value",
            )
        else:
            require(
                list(domain) == DOMAIN_LOCAL_SENT
                and domain["domain_type"] == "LOCAL",
                f"run {run}: a LOCAL domain must omit the value member",
            )
            require(
                DOMAIN_LOCAL_OMITTED.isdisjoint(domain),
                f"run {run}: a LOCAL domain sent an unset member",
            )
        no_empty_members(create["body_raw"], f"run {run} create")

        probes = requests[offset + 1 : offset + 3]
        require(
            probes[0]["query_pairs"] == [["size", page_size]],
            f"run {run}: the first probe page must carry size only",
        )
        require(
            probes[1]["query_pairs"] == [["size", page_size], ["cursor", cursor]],
            f"run {run}: the second probe page must follow the node's cursor",
        )
        for index, probe in enumerate(probes):
            require(
                probe["path"] == BASE_PATH + "/groups/applications/fetch",
                f"run {run}: probe {index} target is not exact",
            )
            require(
                probe["body_bytes"] == 0,
                f"run {run}: probe {index} must not carry a body",
            )
            require(
                SUMMARIES_OMITTED.isdisjoint(
                    {name for name, _ in probe["query_pairs"]}
                ),
                f"run {run}: probe {index} sent an unrequested query parameter",
            )
            require(
                probe["headers"].get("authorization") == [authorization],
                f"run {run}: probe {index} must present this run's token once",
            )

        if creates:
            add = requests[offset + 3]
            require(
                add["path"] == BASE_PATH + "/groups/applications/full/"
                and add["raw_target"].endswith("/groups/applications/full/")
                and add["query"] == "",
                f"run {run}: the specification spells the create route "
                "/groups/applications/full/ with a trailing slash",
            )
            require(
                add["headers"].get("authorization") == [authorization],
                f"run {run}: the mutating call must present this run's token once",
            )
            require(
                any(
                    value.split(";")[0].strip() == "application/json"
                    for value in add["headers"].get("content-type", [])
                ),
                f"run {run}: the mutating call must send JSON",
            )
            body = add["body"]
            if run == 3:
                require(
                    list(body) == ["name", "enable_intent", "tiers"],
                    f"run {run}: AppWithTiersRequest member set or order changed",
                )
                require(
                    body["enable_intent"] is False,
                    f"run {run}: a bound EnableIntent must reach the wire",
                )
                require(
                    add["headers"].get("if-match")
                    == [str(config["last_modified_timestamp"])],
                    f"run {run}: a bound LastModifiedTimestamp must be sent as "
                    "the If-Match header",
                )
            else:
                require(
                    list(body) == APP_REQUEST_SENT,
                    f"run {run}: AppWithTiersRequest member set or order changed",
                )
                require(
                    APP_REQUEST_OMITTED.isdisjoint(body),
                    f"run {run}: an unset AppWithTiersRequest member was sent",
                )
                require(
                    "if-match" not in add["headers"],
                    f"run {run}: If-Match must be absent when no "
                    "lastModifiedTimestamp was supplied",
                )
            no_empty_members(add["body_raw"], f"run {run} addApplicationWithTiers")

            if run == 1:
                require(
                    body["name"] == config["application_a_name"],
                    "run 1: the created application name is wrong",
                )
                tiers = body["tiers"]
                require(len(tiers) == 2, "run 1: both tiers must be sent")
                for tier, expected_name in zip(
                    tiers, [config["tier_a_one_name"], config["tier_a_two_name"]]
                ):
                    require(
                        list(tier) == TIER_REQUEST_SENT,
                        "run 1: TierRequest member set or order changed",
                    )
                    require(
                        TIER_REQUEST_OMITTED.isdisjoint(tier),
                        "run 1: an unset TierRequest member was sent",
                    )
                    require(
                        tier["name"] == expected_name,
                        "run 1: tiers must keep the caller's order",
                    )
                search = tiers[0]["group_membership_criteria"]
                require(
                    len(search) == 1 and list(search[0]) == CRITERIA_SEARCH_SENT,
                    "run 1: a search tier must send only the search criteria",
                )
                require(
                    search[0]["membership_type"] == "SearchMembershipCriteria"
                    and search[0]["search_membership_criteria"]
                    == {
                        "entity_type": "VirtualMachine",
                        "filter": config["tier_a_one_filter"],
                    },
                    "run 1: search membership criteria shape changed",
                )
                addresses = tiers[1]["group_membership_criteria"]
                require(
                    len(addresses) == 1 and list(addresses[0]) == CRITERIA_IP_SENT,
                    "run 1: an address tier must send only the address criteria",
                )
                require(
                    addresses[0]["membership_type"] == "IPAddressMembershipCriteria"
                    and addresses[0]["ip_address_membership_criteria"]
                    == {"ip_addresses": config["tier_a_two_addresses"]},
                    "run 1: address membership criteria shape changed",
                )
            else:
                require(
                    body["name"] == config["application_b_name"],
                    "run 3: the created application name is wrong",
                )
                tiers = body["tiers"]
                require(len(tiers) == 1, "run 3: exactly one tier was requested")
                criteria = tiers[0]["group_membership_criteria"]
                require(
                    [list(item) for item in criteria]
                    == [CRITERIA_SEARCH_SENT, CRITERIA_IP_SENT],
                    "run 3: a tier holding both criteria must send the search "
                    "criteria first, each with only its own member",
                )
                require(
                    criteria[0]["search_membership_criteria"]["filter"]
                    == config["tier_b_one_filter"]
                    and criteria[1]["ip_address_membership_criteria"]["ip_addresses"]
                    == config["tier_b_one_addresses"],
                    "run 3: membership criteria values changed",
                )

        delete = requests[offset + (4 if creates else 3)]
        require(
            delete["path"] == BASE_PATH + "/auth/token"
            and delete["query"] == ""
            and delete["body_bytes"] == 0,
            f"run {run}: delete target is not exact",
        )
        require(
            delete["headers"].get("authorization") == [authorization],
            f"run {run}: delete must release this run's token",
        )

    run_two = requests[5:9]
    require(
        all(item["operationId"] != "addApplicationWithTiers" for item in run_two),
        "the repeated call must not reach the mutating operation at all",
    )

    # Run four finds an exact match on a page that still has a cursor. It must
    # stop at once, then release its own token.
    early = requests[14:17]
    early_authorization = AUTH_PREFIX + "%s-4" % config["token_prefix"]
    require(
        early[0]["path"] == BASE_PATH + "/auth/token"
        and "authorization" not in early[0]["headers"]
        and list(early[0]["body"]) == USER_CREDENTIAL_SENT
        and list(early[0]["body"]["domain"]) == DOMAIN_LOCAL_SENT,
        "run 4: token creation changed",
    )
    require(
        early[1]["query_pairs"] == [["size", page_size]]
        and early[1]["headers"].get("authorization") == [early_authorization],
        "run 4: an exact first-page match must stop before following the cursor",
    )
    require(
        early[2]["path"] == BASE_PATH + "/auth/token"
        and early[2]["headers"].get("authorization") == [early_authorization],
        "run 4: delete did not release the token",
    )

    # Run five receives a 500 from its first probe. The original error is
    # surfaced by the invoker, but delete must still run with that run's token.
    failed = requests[17:20]
    failed_authorization = AUTH_PREFIX + "%s-5" % config["token_prefix"]
    require(
        failed[0]["path"] == BASE_PATH + "/auth/token"
        and "authorization" not in failed[0]["headers"]
        and list(failed[0]["body"]) == USER_CREDENTIAL_SENT,
        "run 5: token creation changed",
    )
    require(
        failed[1]["query_pairs"] == [["size", page_size]]
        and failed[1]["response_status"] == 500
        and failed[1]["headers"].get("authorization") == [failed_authorization],
        "run 5: the injected probe failure changed",
    )
    require(
        failed[2]["path"] == BASE_PATH + "/auth/token"
        and failed[2]["headers"].get("authorization") == [failed_authorization]
        and failed[2]["response_status"] == 204,
        "run 5: delete did not run after the earlier failure",
    )


def main() -> int:
    try:
        verify_contract()
        verify_manifest_and_shape()
        result, requests, config = run_case()
        verify_result(result, config)
        verify_wire(requests, config)
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
