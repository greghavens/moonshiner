#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0307."""

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
MANIFEST_PATH = ROOT / "VcfAutomationDeployment" / "VcfAutomationDeployment.psd1"
MODULE_PATH = ROOT / "VcfAutomationDeployment" / "VcfAutomationDeployment.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

OPERATION_KEYS = [
    "requestCatalogItem",
    "getDeploymentById",
    "getRequestById",
    "actionRequest",
]
ROUTES = [
    ("POST", "/catalog/api/items/{id}/request"),
    ("GET", "/deployment/api/deployments/{deploymentId}"),
    ("GET", "/deployment/api/requests/{requestId}"),
    ("POST", "/deployment/api/requests/{requestId}"),
]
REFERENCE_PAGES = [
    (
        "requestCatalogItem",
        "POST /catalog/api/items/{id}/request",
        "https://developer.broadcom.com/xapis/vm-apps-org-catalog/latest"
        "/catalog/api/items/id/request/post/",
    ),
    (
        "getDeploymentById",
        "GET /deployment/api/deployments/{deploymentId}",
        "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest"
        "/deployment/api/deployments/deploymentId/get/",
    ),
    (
        "getRequestById",
        "GET /deployment/api/requests/{requestId}",
        "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest"
        "/deployment/api/requests/requestId/get/",
    ),
    (
        "actionRequest",
        "POST /deployment/api/requests/{requestId}",
        "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest"
        "/deployment/api/requests/requestId/post/",
    ),
]
CATALOG_ITEM_REQUEST_MEMBERS = [
    "bulkRequestCount",
    "deploymentName",
    "inputs",
    "projectId",
    "reason",
    "version",
]
DEPLOYMENT_QUERY_MEMBERS = [
    "deploymentId",
    "expandLastRequest",
    "expandResources",
    "expandProject",
    "expand",
    "deleted",
]
REQUEST_STATUS_ENUM = [
    "CREATED",
    "PENDING",
    "INITIALIZATION",
    "CHECKING_APPROVAL",
    "APPROVAL_PENDING",
    "USER_INTERACTION_PENDING",
    "INPROGRESS",
    "COMPLETION",
    "APPROVAL_REJECTED",
    "ABORTED",
    "SUCCESSFUL",
    "FAILED",
]
NON_TERMINAL = REQUEST_STATUS_ENUM[:8]
TERMINAL_SUCCESS = ["SUCCESSFUL"]
TERMINAL_FAILURE = ["APPROVAL_REJECTED", "ABORTED", "FAILED"]
OUTPUT_PROPERTIES = [
    "catalogItemId",
    "deploymentId",
    "deploymentName",
    "requestId",
    "requestStatus",
    "requestedBy",
    "totalTasks",
    "completedTasks",
    "pollCount",
    "outputs",
]
PARAMETERS = [
    ("ApiUri", "System.Uri", True, None, None, None),
    ("AccessToken", "System.Security.SecureString", True, None, None, None),
    ("CatalogItemId", "System.String", True, None, None, None),
    ("ProjectId", "System.String", True, None, None, None),
    ("DeploymentName", "System.String", True, None, None, None),
    ("Version", "System.String", False, None, None, None),
    ("Reason", "System.String", False, None, None, None),
    ("Inputs", "System.Collections.Hashtable", False, None, None, None),
    ("PollIntervalSeconds", "System.Int32", False, 0, 60, "2"),
    ("TimeoutSeconds", "System.Int32", False, 1, 900, "300"),
]
VALIDATIONS = {
    "relative-api-uri",
    "null-access-token",
    "blank-catalog-item",
    "blank-project",
    "blank-deployment-name",
    "poll-below-range",
    "poll-above-range",
    "timeout-below-range",
    "timeout-above-range",
}


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

    require(
        contract.get("contractFormat") == "reference-derived-projection-v1",
        "contract format changed",
    )
    source = contract.get("source", {})
    require(
        source.get("kind") == "vendor-reference-documentation",
        "contract source kind changed",
    )
    require(
        source.get("isPublishedSpecification") is False,
        "the contract must state plainly that it is not a published specification",
    )
    statement = source.get("statement", "")
    require(
        isinstance(statement, str)
        and "not from a published API specification" in statement
        and "vcf-api-specs" in statement,
        "the contract must say in prose that reference documentation is its source",
    )
    require(
        source.get("product") == "VCF Automation"
        and source.get("productRelease") == "VMware Cloud Foundation 9.1",
        "contract product identity changed",
    )
    require(
        source.get("retrievedOn") == "2026-08-05",
        "contract retrieval date changed",
    )
    require(
        "operationIdAvailability" in source
        and "do not publish an operationId" in source["operationIdAvailability"],
        "the contract must record that the reference pages publish no operationId",
    )

    operations = contract.get("operations", [])
    require(
        [item.get("operationKey") for item in operations] == OPERATION_KEYS,
        "contract operation keys changed",
    )
    require(
        [(item.get("method"), item.get("path")) for item in operations] == ROUTES,
        "contract routes changed",
    )
    catalog, deployment, poll, action = operations

    require(
        catalog.get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "CatalogItemRequest",
        },
        "catalog request body contract changed",
    )
    catalog_wire = catalog.get("focusedWireProfile", {})
    require(
        catalog_wire.get("boundMembers") == ["deploymentName", "projectId"],
        "bound catalog request members changed",
    )
    require(
        catalog_wire.get("conditionalMembers") == ["version", "reason", "inputs"],
        "conditional catalog request members changed",
    )
    require(
        catalog_wire.get("unsetMembers") == ["bulkRequestCount"]
        and catalog_wire.get("unsetBehavior") == "omit",
        "bulkRequestCount must be omitted",
    )

    require(
        [item.get("name") for item in deployment.get("parameters", [])]
        == DEPLOYMENT_QUERY_MEMBERS,
        "deployment parameter projection changed",
    )
    require(
        all(
            item.get("required") is False
            for item in deployment["parameters"]
            if item.get("in") == "query"
        ),
        "deployment query members must remain optional",
    )
    deployment_wire = deployment.get("focusedWireProfile", {})
    require(
        deployment_wire.get("boundQueryParameters") == ["expandLastRequest"],
        "bound deployment query members changed",
    )
    require(
        deployment_wire.get("unsetQueryParameters")
        == ["expandResources", "expandProject", "expand", "deleted"]
        and deployment_wire.get("unsetBehavior") == "omit",
        "unset deployment query members must be omitted",
    )

    require(poll.get("requestBody") is False, "the poll must be bodyless")
    require(
        poll.get("focusedWireProfile", {}).get("boundQueryParameters") == [],
        "the poll must carry no query string",
    )

    require(action.get("requestBody") is False, "the action must be bodyless")
    action_parameters = {item.get("name"): item for item in action.get("parameters", [])}
    require(
        action_parameters.get("action", {}).get("required") is True,
        "the action query parameter must remain required",
    )
    require(
        action_parameters.get("action", {}).get("schema", {}).get("enum")
        == ["cancel", "dismiss"],
        "the documented action values changed",
    )

    schemas = contract.get("schemas", {})
    require(
        sorted(schemas.get("CatalogItemRequest", {}).get("properties", {}))
        == CATALOG_ITEM_REQUEST_MEMBERS,
        "CatalogItemRequest projection changed",
    )
    require(
        schemas.get("CatalogItemRequest", {}).get("required") == [],
        "CatalogItemRequest members are documented as optional",
    )
    require(
        list(schemas.get("CatalogItemRequestResponseItemArray", {})
             .get("items", {}).get("properties", {}))
        == ["deploymentId", "deploymentName"],
        "the accepted-response projection changed",
    )
    require(
        schemas.get("Request", {}).get("statusEnum") == REQUEST_STATUS_ENUM,
        "Request status enumeration changed",
    )
    require(
        "lastRequest" in schemas.get("Deployment", {}).get("properties", [])
        and "inprogressRequests" in schemas["Deployment"]["properties"],
        "Deployment projection lost lastRequest or inprogressRequests",
    )

    profile = contract.get("asynchronousProfile", {})
    classification = profile.get("requestStatusClassification", {})
    require(
        classification.get("nonTerminal") == NON_TERMINAL
        and classification.get("terminalSuccess") == TERMINAL_SUCCESS
        and classification.get("terminalFailure") == TERMINAL_FAILURE,
        "asynchronous status classification changed",
    )
    require(
        profile.get("cancellation", {}).get("guard") == "cancelable",
        "the documented cancellation guard changed",
    )

    require(
        sources.get("isPublishedSpecification") is False,
        "official sources must state that no published specification was used",
    )
    require(
        sources.get("specificationRepositoryChecked", {}).get("result")
        == "no-vcf-automation-specification",
        "official sources must record the vcf-api-specs check",
    )
    require(sources.get("retrievedOn") == "2026-08-05", "source retrieval date changed")
    pages = sources.get("pages", [])
    require(
        all(
            isinstance(page.get("url"), str)
            and page["url"].startswith("https://developer.broadcom.com/")
            and page.get("dateFetched") == "2026-08-05"
            and isinstance(page.get("documents"), str)
            and page["documents"]
            for page in pages
        ),
        "every recorded page needs a developer.broadcom.com URL, what it documents "
        "and the date it was fetched",
    )
    documented = {
        page["operationKey"]: (page.get("operation"), page["url"])
        for page in pages
        if page.get("operationKey")
    }
    require(
        [(key, *documented.get(key, (None, None))) for key in OPERATION_KEYS]
        == REFERENCE_PAGES,
        "each contract operation must cite the exact reference page it came from",
    )
    derivation = sources.get("derivation", {})
    require(
        derivation.get("documentationPageUsedAsContractSource") is True
        and derivation.get("publishedSpecificationUsedAsContractSource") is False
        and derivation.get("operationKeysAreVendorOperationIds") is False,
        "the derivation record must not claim a specification or vendor operationIds",
    )


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_pwsh(arguments: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", *arguments],
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


def verify_manifest() -> None:
    command = (
        f"$d=Import-PowerShellDataFile -LiteralPath {ps_quote(str(MANIFEST_PATH))};"
        "if($d.RootModule -cne 'VcfAutomationDeployment.psm1'){exit 2};"
        "if($d.PowerShellVersion -cne '7.4'){exit 3};"
        "if(($d.FunctionsToExport -join ',') -cne 'New-VcfAutomationDeployment'){exit 4}"
    )
    result = run_pwsh(["-Command", command])
    require(
        result.returncode == 0,
        "protected manifest is invalid: "
        + (result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"),
    )


def verify_solution_shape() -> None:
    require(MODULE_PATH.is_file(), "create VcfAutomationDeployment.psm1")
    parse_command = (
        "$tokens=$null;$errors=$null;"
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{ps_quote(str(MODULE_PATH))},[ref]$tokens,[ref]$errors);"
        "if($errors.Count -ne 0){$errors | ForEach-Object {Write-Error $_};exit 9};"
        "$names=@($ast.FindAll({param($node) "
        "$node -is [Management.Automation.Language.CommandAst]},$true) | "
        "ForEach-Object {$_.GetCommandName()} | Where-Object {$_});"
        "$forbidden=@('Initialize-VcfCatalogItemRequest',"
        "'Invoke-VcfAutomationOperationBinding','Install-Module','Save-Module',"
        "'Start-Process','curl','curl.exe','wget','wget.exe');"
        "if(@($names | Where-Object {$forbidden -icontains $_}).Count -ne 0){exit 10};"
        "if(@($names | Where-Object {$_ -iin @('Invoke-RestMethod','Invoke-WebRequest')}).Count -eq 0){exit 11}"
    )
    parsed = run_pwsh(["-Command", parse_command])
    require(
        parsed.returncode == 0,
        "PowerShell module has syntax errors, shells out, installs dependencies, "
        "or does not issue HTTP directly: "
        + (parsed.stderr.strip() or parsed.stdout.strip() or f"exit {parsed.returncode}"),
    )
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and (
            path.suffix.casefold() in {".dll", ".nupkg", ".snupkg", ".zip"}
            or path.name.casefold().startswith("vmware.")
        )
    ]
    require(not vendored, "the seed must not vendor VMware or binary dependencies")


def make_case(
    name: str,
    marker: str,
    index: int,
    statuses: list[str],
    cancelable: bool | list[bool],
    poll_interval: int,
    timeout: int,
    optional: dict[str, Any] | None = None,
    *,
    expect_cancel: bool = False,
    cancel_response_status: int = 200,
    allow_unknown_status: bool = False,
    fault: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    optional = optional or {}
    catalog_item_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    deployment_id = "a" + str(uuid.uuid4())[1:]
    request_id = "b" + str(uuid.uuid4())[1:]
    decoy_request_id = str(uuid.uuid4())
    deployment_name = f"dep-{marker}-{index}"
    total_tasks = 4
    outputs = {
        "address": f"10.{index}.0.{index}",
        "resourceName": f"vm-{marker}-{index}",
    }

    expected_body: dict[str, Any] = {
        "deploymentName": deployment_name,
        "projectId": project_id,
    }
    for key in ("version", "reason", "inputs"):
        if key in optional:
            expected_body[key] = optional[key]

    scenario_case = {
        "name": name,
        "catalogItemId": catalog_item_id,
        "projectId": project_id,
        "deploymentId": deployment_id,
        "deploymentName": deployment_name,
        "requestId": request_id,
        "decoyRequestId": decoy_request_id,
        "requestName": f"Create deployment {deployment_name}",
        "requestedBy": f"svc-{marker}@vcf.test",
        "blueprintId": f"{uuid.uuid4()}:1",
        "orgId": str(uuid.uuid4()),
        "createdAt": f"2026-08-05T09:{index:02d}:00.000Z",
        "completedAt": f"2026-08-05T09:{index:02d}:40.000Z",
        "details": f"Provisioning {deployment_name}",
        "totalTasks": total_tasks,
        "outputs": outputs,
        "statusSequence": statuses,
        "cancelableSequence": (
            cancelable if isinstance(cancelable, list) else [cancelable]
        ),
        "expectCancel": expect_cancel,
        "cancelResponseStatus": cancel_response_status,
        "allowUnknownStatus": allow_unknown_status,
        "fault": fault,
        "expectedRequestBody": expected_body,
    }
    invocation_case = {
        "name": name,
        "catalogItemId": catalog_item_id,
        "projectId": project_id,
        "deploymentName": deployment_name,
        "pollIntervalSeconds": poll_interval,
        "timeoutSeconds": timeout,
        **optional,
    }
    return scenario_case, invocation_case


def make_scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    marker = secrets.token_hex(4)
    definitions: list[tuple[dict[str, Any], dict[str, Any]]] = [
        make_case(
            "normalized-success",
            marker,
            1,
            [
                " created ",
                "pending",
                " Initialization ",
                "checking_approval",
                "approval_pending",
                "user_interaction_pending",
                "inprogress",
                "completion",
                " successful ",
            ],
            True,
            0,
            30,
        ),
        make_case(
            "full-success",
            marker,
            2,
            ["PENDING", "CHECKING_APPROVAL", "INPROGRESS", "COMPLETION", "SUCCESSFUL"],
            True,
            0,
            30,
            {
                "version": "v2.0",
                "reason": f"change CHG-{marker}",
                "inputs": {
                    "cpuCount": 4,
                    "encrypted": True,
                    "hostname": f"web-{marker}",
                    "networks": ["app-tier", "db-tier"],
                    "tags": {"env": "prod", "owner": f"team-{marker}"},
                },
            },
        ),
        make_case(
            "empty-optionals-success",
            marker,
            3,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            {"version": "", "reason": "", "inputs": {}},
        ),
        make_case(
            "timeout-cancelable",
            marker,
            4,
            ["INPROGRESS"],
            True,
            1,
            2,
            expect_cancel=True,
        ),
        make_case(
            "timeout-not-cancelable",
            marker,
            5,
            ["INPROGRESS"],
            False,
            1,
            2,
        ),
        make_case(
            "timeout-recent-not-cancelable",
            marker,
            6,
            ["INPROGRESS"],
            [True, False],
            1,
            2,
        ),
        make_case(
            "timeout-cancel-fails",
            marker,
            7,
            ["INPROGRESS"],
            True,
            1,
            2,
            expect_cancel=True,
            cancel_response_status=503,
        ),
        make_case(
            "terminal-approval-rejected",
            marker,
            8,
            ["INPROGRESS", "APPROVAL_REJECTED"],
            True,
            0,
            30,
        ),
        make_case(
            "terminal-aborted",
            marker,
            9,
            ["INPROGRESS", "ABORTED"],
            True,
            0,
            30,
        ),
        make_case(
            "terminal-failed",
            marker,
            10,
            ["INPROGRESS", "FAILED"],
            True,
            0,
            30,
        ),
        make_case(
            "unknown-status",
            marker,
            11,
            ["NOT_A_CONTRACT_STATUS"],
            False,
            0,
            30,
            allow_unknown_status=True,
        ),
        make_case(
            "invalid-accepted-cardinality",
            marker,
            12,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            fault="accepted-cardinality",
        ),
        make_case(
            "invalid-accepted-blank",
            marker,
            13,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            fault="accepted-blank",
        ),
        make_case(
            "invalid-deployment-id",
            marker,
            14,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            fault="deployment-id-case",
        ),
        make_case(
            "invalid-poll-id",
            marker,
            15,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            fault="poll-id-case",
        ),
        make_case(
            "invalid-accepted-blank-name",
            marker,
            16,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            fault="accepted-blank-name",
        ),
        make_case(
            "invalid-last-request-id",
            marker,
            17,
            ["SUCCESSFUL"],
            False,
            0,
            30,
            fault="last-request-blank",
        ),
    ]
    scenario = {
        "accessToken": "eyJ" + secrets.token_urlsafe(32),
        "cases": [item[0] for item in definitions],
    }
    return scenario, [item[1] for item in definitions]


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


def media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().casefold()


def verify_runtime() -> None:
    scenario, invocation_cases = make_scenario()
    cases = scenario["cases"]
    token = scenario["accessToken"]

    with tempfile.TemporaryDirectory(prefix="vcf91-0307-") as temporary:
        temp = Path(temporary)
        port_file = temp / "port.txt"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        cases_file = temp / "cases.json"
        result_file = temp / "result.json"
        scenario_file.write_text(
            json.dumps(scenario, separators=(",", ":")), encoding="utf-8"
        )
        cases_file.write_text(
            json.dumps(invocation_cases, separators=(",", ":")), encoding="utf-8"
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
            invocation = run_pwsh(
                [
                    "-File",
                    str(INVOKER_PATH),
                    "-ModuleManifest",
                    str(MANIFEST_PATH),
                    "-ApiUri",
                    f"http://127.0.0.1:{port}/",
                    "-AccessToken",
                    token,
                    "-CasesFile",
                    str(cases_file),
                    "-OutputPath",
                    str(result_file),
                ],
                timeout=90,
            )
            require(
                invocation.returncode == 0,
                "PowerShell acceptance harness failed: "
                + (invocation.stderr.strip() or invocation.stdout.strip()),
            )
            require(result_file.is_file(), "PowerShell did not write its result")
            require(
                token not in invocation.stdout and token not in invocation.stderr,
                "the bearer token was written to a PowerShell output stream",
            )
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
        token not in json.dumps(result, separators=(",", ":")),
        "the bearer token leaked into the function's output",
    )
    require(
        result.get("exportedFunctions") == ["New-VcfAutomationDeployment"],
        "the module must export exactly New-VcfAutomationDeployment",
    )
    observed_parameters = [
        (
            item.get("name"),
            item.get("type"),
            item.get("mandatory"),
            item.get("rangeMin"),
            item.get("rangeMax"),
            item.get("default"),
        )
        for item in result.get("parameters", [])
    ]
    require(
        observed_parameters == PARAMETERS,
        "New-VcfAutomationDeployment parameter types, requirements, ranges, or "
        "defaults do not match the requested signature",
    )
    validations = result.get("validations", [])
    require(
        {item.get("name") for item in validations} == VALIDATIONS,
        "pre-request validation cases are missing",
    )
    require(
        all(
            item.get("rejected") is True and item.get("objectCount") == 0
            for item in validations
        ),
        "an invalid argument was accepted or wrote a partial object",
    )
    require(result.get("caseCount") == len(cases), "not every case ran")
    rows = result.get("results", [])
    by_name = {row.get("name"): row for row in rows}
    require(
        set(by_name) == {case["name"] for case in cases},
        "case results are missing or misnamed",
    )

    for index, case in enumerate(cases):
        row = by_name[case["name"]]
        name = case["name"]
        if name.endswith("-success"):
            require(
                row.get("outcome") == "success",
                f"{name}: expected success, got {row.get('errorType')} "
                f"{row.get('errorMessage')}",
            )
            require(row.get("objectCount") == 1, f"{name}: exactly one object expected")
            require(
                row.get("propertyOrder") == ",".join(OUTPUT_PROPERTIES),
                f"{name}: result property order changed",
            )
            values = row.get("values", {})
            require(
                values.get("catalogItemId") == case["catalogItemId"]
                and values.get("deploymentId") == case["deploymentId"]
                and values.get("deploymentName") == case["deploymentName"]
                and values.get("requestId") == case["requestId"],
                f"{name}: correlated identifiers were guessed or lost",
            )
            require(
                values.get("requestStatus") == "SUCCESSFUL",
                f"{name}: the request must be reported as SUCCESSFUL",
            )
            require(
                values.get("requestedBy") == case["requestedBy"],
                f"{name}: requestedBy was not carried through",
            )
            require(
                values.get("totalTasks") == case["totalTasks"]
                and values.get("completedTasks") == case["totalTasks"],
                f"{name}: task counters were not read from the terminal poll",
            )
            require(
                values.get("pollCount") == len(case["statusSequence"]),
                f"{name}: the request was not polled to a terminal status "
                f"(expected {len(case['statusSequence'])} polls, "
                f"reported {values.get('pollCount')})",
            )
            require(
                values.get("outputs") == case["outputs"],
                f"{name}: request outputs were not returned",
            )
        elif name.startswith("timeout-"):
            require(
                row.get("outcome") == "error"
                and row.get("errorType") == "System.TimeoutException",
                f"{name}: expected System.TimeoutException, got "
                f"{row.get('outcome')} {row.get('errorType')}",
            )
        elif (
            name.startswith("terminal-")
            or name == "unknown-status"
            or name.startswith("invalid-")
        ):
            require(
                row.get("outcome") == "error"
                and row.get("errorType") == "System.InvalidOperationException",
                f"{name}: expected System.InvalidOperationException, got "
                f"{row.get('outcome')} {row.get('errorType')}",
            )
        else:  # Defensive: the case list is fixed above.
            raise VerificationError(f"unexpected case {name}")

    require(requests, "the loopback mock recorded no requests")
    for index, request in enumerate(requests):
        require(
            request.get("operationKey") is not None,
            f"request {index} targeted {request.get('method')} "
            f"{request.get('rawTarget')}, which is outside the contract",
        )
        require(
            one_header(request, "authorization") == f"Bearer {token}",
            f"request {index} did not present the caller's bearer token exactly once",
        )
        require(
            media_type(one_header(request, "accept")) == "application/json",
            f"request {index} must accept application/json",
        )
        expected_status = 200
        if request.get("operationKey") == "actionRequest":
            segments = [part for part in request["path"].split("/") if part]
            request_id = segments[-1] if segments else ""
            owner = next(
                (case for case in cases if case["requestId"] == request_id), None
            )
            if owner is not None:
                expected_status = owner["cancelResponseStatus"]
        require(
            request.get("responseStatus") == expected_status,
            f"request {index} returned {request.get('responseStatus')}, expected "
            f"{expected_status}: {request.get('rawTarget')}",
        )

    grouped: dict[str, list[dict[str, Any]]] = {case["name"]: [] for case in cases}
    index_by_id: dict[str, str] = {}
    for case in cases:
        for identifier in (
            case["catalogItemId"],
            case["deploymentId"],
            case["requestId"],
        ):
            index_by_id[identifier] = case["name"]
    for request in requests:
        segments = [part for part in request["path"].split("/") if part]
        owners = {index_by_id[part] for part in segments if part in index_by_id}
        require(
            len(owners) == 1,
            f"request {request['rawTarget']} does not belong to exactly one case; "
            "a decoy identifier was used",
        )
        grouped[owners.pop()].append(request)

    for case in cases:
        name = case["name"]
        case_requests = grouped[name]
        verify_case_wire(case, case_requests)


def verify_case_wire(case: dict[str, Any], requests: list[dict[str, Any]]) -> None:
    name = case["name"]
    require(requests, f"{name}: the contract flow was not followed")

    catalog = requests[0]
    require(
        catalog.get("operationKey") == "requestCatalogItem"
        and catalog.get("method") == "POST",
        f"{name}: the flow must open with the catalog item request",
    )
    require(
        catalog.get("rawTarget") == f"/catalog/api/items/{case['catalogItemId']}/request",
        f"{name}: catalog request target changed",
    )
    require(catalog.get("rawQuery") == "", f"{name}: catalog request takes no query")
    require(
        media_type(one_header(catalog, "content-type")) == "application/json",
        f"{name}: catalog request media type must be application/json",
    )
    body = json.loads(catalog["body"])
    expected_body = case["expectedRequestBody"]
    require(
        sorted(body) == sorted(expected_body),
        f"{name}: catalog request body members are {sorted(body)}, expected "
        f"{sorted(expected_body)}",
    )
    require(
        body == expected_body,
        f"{name}: catalog request body values changed; nested inputs must be "
        "serialized faithfully rather than flattened",
    )
    for omitted in CATALOG_ITEM_REQUEST_MEMBERS:
        if omitted not in expected_body:
            require(
                omitted not in body,
                f"{name}: unset optional member {omitted} was serialized",
            )
    require(
        catalog.get("bodyLength") == len(catalog["body"].encode("utf-8")),
        f"{name}: catalog request Content-Length disagrees with the body",
    )

    if case["fault"] in {
        "accepted-cardinality",
        "accepted-blank",
        "accepted-blank-name",
    }:
        require(
            len(requests) == 1,
            f"{name}: an invalid accepted response must stop the flow immediately",
        )
        return

    require(len(requests) >= 2, f"{name}: the deployment was not read")
    deployment = requests[1]

    require(
        deployment.get("operationKey") == "getDeploymentById"
        and deployment.get("method") == "GET",
        f"{name}: the request identifier must come from the deployment read",
    )
    require(
        deployment.get("rawTarget")
        == f"/deployment/api/deployments/{case['deploymentId']}?expandLastRequest=true",
        f"{name}: deployment read target changed",
    )
    require(
        deployment.get("query") == {"expandLastRequest": ["true"]},
        f"{name}: the deployment read must bind only expandLastRequest",
    )
    for omitted in ("expandResources", "expandProject", "expand", "deleted"):
        require(
            omitted not in deployment.get("query", {}),
            f"{name}: unset optional query member {omitted} was sent",
        )
    require(
        deployment.get("bodyLength") == 0,
        f"{name}: the deployment read must be bodyless",
    )

    if case["fault"] in {"deployment-id-case", "last-request-blank"}:
        require(
            len(requests) == 2,
            f"{name}: invalid deployment correlation must stop before polling",
        )
        return

    require(len(requests) >= 3, f"{name}: the request was not polled")
    polls = requests[2:]
    cancels = [item for item in polls if item.get("operationKey") == "actionRequest"]
    polls = [item for item in polls if item.get("operationKey") == "getRequestById"]
    require(
        len(polls) + len(cancels) == len(requests) - 2,
        f"{name}: an unexpected operation was interleaved with polling",
    )
    for item in polls:
        require(
            item.get("rawTarget") == f"/deployment/api/requests/{case['requestId']}",
            f"{name}: every poll must target the lastRequest identifier with no query",
        )
        require(item.get("bodyLength") == 0, f"{name}: a poll must be bodyless")

    if name.endswith("-success"):
        require(
            len(polls) == len(case["statusSequence"]),
            f"{name}: expected {len(case['statusSequence'])} polls, saw {len(polls)}",
        )
        require(not cancels, f"{name}: a completed request must not be canceled")
        require(
            requests[-1].get("operationKey") == "getRequestById",
            f"{name}: the flow must end on the terminal poll",
        )
    elif name.startswith("terminal-") or name == "unknown-status":
        require(
            len(polls) == len(case["statusSequence"]),
            f"{name}: expected {len(case['statusSequence'])} polls, saw {len(polls)}",
        )
        require(not cancels, f"{name}: a failed request must not be canceled")
    elif case["fault"] == "poll-id-case":
        require(
            len(polls) == 1 and not cancels,
            f"{name}: a case-mismatched poll id must stop the flow immediately",
        )
    elif name.startswith("timeout-") and case["expectCancel"]:
        require(
            2 <= len(polls) <= 6,
            f"{name}: expected the request to be polled repeatedly, saw {len(polls)}",
        )
        require(
            len(cancels) == 1,
            f"{name}: a timed-out cancelable request must be canceled exactly once, "
            f"saw {len(cancels)}",
        )
        cancel = cancels[0]
        require(
            requests[-1] is cancel,
            f"{name}: the cancel must be the last request",
        )
        require(
            cancel.get("method") == "POST"
            and cancel.get("rawTarget")
            == f"/deployment/api/requests/{case['requestId']}?action=cancel",
            f"{name}: cancel target changed",
        )
        require(
            cancel.get("query") == {"action": ["cancel"]},
            f"{name}: the cancel must bind only action=cancel",
        )
        require(
            cancel.get("bodyLength") == 0 and cancel.get("body") == "",
            f"{name}: the cancel documents no request body; an empty JSON object "
            "must not be sent in its place",
        )
    elif name.startswith("timeout-"):
        require(
            2 <= len(polls) <= 6,
            f"{name}: expected the request to be polled repeatedly, saw {len(polls)}",
        )
        require(
            not cancels,
            f"{name}: a request reporting cancelable false must not be canceled",
        )


def main() -> int:
    try:
        verify_contract()
        verify_manifest()
        verify_solution_shape()
        verify_runtime()
    except (OSError, ValueError, subprocess.SubprocessError, VerificationError) as error:
        print(f"FAIL: {error}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
