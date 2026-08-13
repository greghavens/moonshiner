#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0319.

Everything runs against a contract-pinned loopback service on 127.0.0.1.
No live VMware or Broadcom endpoint is contacted.
"""

from __future__ import annotations

import ast
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
PACKAGE_INIT = ROOT / "vcfa_change" / "__init__.py"
SOLUTION_PATH = ROOT / "vcfa_change" / "change.py"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
RUN_CASE_PATH = ROOT / ".protected" / "run_case.py"

REFERENCE_ROOT = "https://developer.broadcom.com/xapis/vm-apps-org-catalog/latest/"
FETCH_DATE = "2026-08-11"
OPERATION_KEYS = [
    "getCatalogItems",
    "requestCatalogItemInstances",
    "getDeploymentById",
    "submitDeploymentActionRequest",
]
ROUTES = [
    ("GET", "/catalog/api/items"),
    ("POST", "/catalog/api/items/{id}/request"),
    ("GET", "/deployment/api/deployments/{deploymentId}"),
    ("POST", "/deployment/api/deployments/{deploymentId}/requests"),
]
CATALOG_QUERY_MEMBERS = [
    "page",
    "size",
    "sort",
    "search",
    "projects",
    "types",
    "expandProjects",
    "expand",
    "$top",
    "$skip",
    "$orderby",
]
CATALOG_REQUEST_MEMBERS = [
    "bulkRequestCount",
    "deploymentName",
    "inputs",
    "projectId",
    "reason",
    "version",
]
ACTION_REQUEST_MEMBERS = ["actionId", "inputs", "reason"]
DEPLOYMENT_QUERY_MEMBERS = [
    "deploymentId",
    "expandResources",
    "expandLastRequest",
    "expandProject",
    "expand",
    "deleted",
]
RESULT_KEYS = [
    "outcome",
    "catalogItemId",
    "catalogItemName",
    "projectId",
    "deploymentId",
    "deploymentName",
    "deploymentStatus",
    "actionId",
    "actionRequestId",
    "steps",
    "failure",
]
STEP_KEYS = ["name", "operation", "status", "detail"]
FAILURE_KEYS = ["step", "operation", "httpStatus", "errorCode", "message"]
STEP_NAMES = [
    "resolve-catalog-item",
    "request-catalog-item",
    "read-deployment",
    "submit-deployment-action",
]
FUNCTION_PARAMETERS = [
    "base_url",
    "access_token",
    "project_id",
    "catalog_item_name",
    "deployment_name",
    "action_id",
    "inputs",
    "reason",
    "action_reason",
    "page_size",
    "timeout",
]
CONFLICT_CODES = [
    "DEPLOYMENT_HAS_ACTIVE_REQUEST",
    "ACTION_NOT_AVAILABLE_FOR_DEPLOYMENT",
    "DEPLOYMENT_ACTION_CONFLICT",
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Contract and sources
# --------------------------------------------------------------------------- #


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    require(
        contract.get("contractFormat") == "focused-reference-projection-v1",
        "contract format changed",
    )
    source = contract.get("source", {})
    require(source.get("kind") == "reference-documentation", "contract kind changed")
    require(
        source.get("isPublishedSpecification") is False,
        "the contract must state plainly that its source is reference documentation "
        "rather than a published specification",
    )
    require(
        "not a published API specification" in source.get("statement", ""),
        "contract source statement changed",
    )
    require(
        source.get("referenceRoot") == REFERENCE_ROOT, "contract reference root changed"
    )
    require(source.get("apiVersion") == "9.1", "contract API version changed")
    require(
        source.get("release") == "VMware Cloud Foundation 9.1",
        "contract release changed",
    )
    require(
        len(contract.get("referenceLimitations", [])) >= 3,
        "contract reference limitations changed",
    )

    envelope = contract.get("errorEnvelope", {})
    require(
        envelope.get("documentedByReference") is False,
        "the pinned error envelope must be marked as undocumented by the reference",
    )
    require(
        envelope.get("members") == ["statusCode", "errorCode", "message"],
        "pinned error envelope members changed",
    )

    rules = contract.get("wireRules", {})
    require(rules.get("unsetOptionalFields") == "omit", "unset field rule changed")
    require(
        "compact separators" in rules.get("jsonSerialization", ""),
        "JSON serialization rule changed",
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
    catalog, request_item, deployment, action = operations

    require(
        [item.get("name") for item in catalog.get("parameters", [])]
        == CATALOG_QUERY_MEMBERS,
        "catalog query projection changed",
    )
    require(
        all(item.get("required") is False for item in catalog["parameters"]),
        "catalog query members must remain optional",
    )
    catalog_wire = catalog.get("focusedWireProfile", {})
    require(
        catalog_wire.get("queryMemberOrder") == ["search", "projects", "size"],
        "focused catalog query order changed",
    )
    require(
        sorted(catalog_wire.get("unsetQueryMembers", []))
        == sorted(
            [name for name in CATALOG_QUERY_MEMBERS if name not in ("search", "projects", "size")]
        )
        and catalog_wire.get("unsetBehavior") == "omit",
        "unset catalog query members must be omitted",
    )

    body = request_item.get("requestBody", {})
    require(
        body.get("schema") == "CatalogItemRequest"
        and body.get("contentType") == "application/json",
        "catalog item request body contract changed",
    )
    require(
        body.get("memberOrder") == CATALOG_REQUEST_MEMBERS,
        "catalog item request member order changed",
    )
    require(
        all(member.get("required") is False for member in body.get("members", [])),
        "every catalog item request member is documented optional",
    )
    item_wire = request_item.get("focusedWireProfile", {})
    require(
        item_wire.get("boundBodyMembers")
        == ["deploymentName", "inputs", "projectId", "reason"],
        "focused catalog item request members changed",
    )
    require(
        item_wire.get("alwaysUnsetBodyMembers") == ["bulkRequestCount", "version"]
        and item_wire.get("unsetBehavior") == "omit",
        "bulkRequestCount and version must be omitted",
    )

    require(
        [item.get("name") for item in deployment.get("parameters", [])]
        == DEPLOYMENT_QUERY_MEMBERS,
        "deployment read parameter projection changed",
    )
    deprecated = {
        item["name"]
        for item in deployment["parameters"]
        if item.get("deprecated") is True
    }
    require(
        deprecated == {"expandResources", "expandLastRequest", "expandProject"},
        "deprecated deployment query members changed",
    )
    deployment_wire = deployment.get("focusedWireProfile", {})
    require(
        deployment_wire.get("queryMemberOrder") == ["expand"]
        and deployment_wire.get("boundQueryValues", {}).get("expand") == ["resources"],
        "focused deployment read query changed",
    )
    require(
        sorted(deployment_wire.get("unsetQueryMembers", []))
        == sorted(["expandResources", "expandLastRequest", "expandProject", "deleted"])
        and deployment_wire.get("unsetBehavior") == "omit",
        "unset deployment query members must be omitted",
    )

    action_body = action.get("requestBody", {})
    require(
        action_body.get("schema") == "ResourceActionRequest",
        "action request body schema changed",
    )
    require(
        action_body.get("memberOrder") == ACTION_REQUEST_MEMBERS,
        "action request member order changed",
    )
    action_wire = action.get("focusedWireProfile", {})
    require(
        action_wire.get("boundBodyMembers") == ["actionId", "reason"]
        and action_wire.get("alwaysUnsetBodyMembers") == ["inputs"]
        and action_wire.get("unsetBehavior") == "omit",
        "focused action request members changed",
    )
    require(
        409 in action.get("documentedStatusCodes", []),
        "the action operation must document a 409",
    )


def verify_sources() -> None:
    sources = load_json(SOURCES_PATH)
    require(
        sources.get("isPublishedSpecification") is False,
        "official sources must record a reference-documentation source",
    )
    absence = sources.get("specificationAbsence", {})
    require(
        absence.get("repository") == "https://github.com/vmware/vcf-api-specs",
        "the checked specification repository changed",
    )
    require(
        "automation" not in [
            name.lower() for name in absence.get("specificationDirectories", [])
        ],
        "the recorded specification listing must show no VCF Automation specification",
    )
    require(absence.get("fetchedDate") == FETCH_DATE, "absence check date changed")

    pages = sources.get("pages", [])
    require(len(pages) >= 10, "official sources must record every page fetched")
    seen: set[str] = set()
    for page in pages:
        url = page.get("pageUrl", "")
        require(
            url.startswith("https://developer.broadcom.com/xapis"),
            f"page url {url!r} is not a developer.broadcom.com xAPIs reference page",
        )
        require(url not in seen, f"page url {url!r} is recorded twice")
        seen.add(url)
        require(
            isinstance(page.get("documents"), str) and len(page["documents"]) > 20,
            f"page {url!r} does not record what it documents",
        )
        require(page.get("fetchedDate") == FETCH_DATE, f"page {url!r} fetch date changed")

    documented = {
        page.get("operationKey")
        for page in pages
        if page.get("role") == "operation"
    }
    require(
        documented == set(OPERATION_KEYS),
        "every contract operation must name the reference page that documents it",
    )
    contract_pages = {
        item["referencePage"] for item in load_json(CONTRACT_PATH)["operations"]
    }
    require(
        contract_pages <= seen,
        "a contract operation cites a page that official_sources.json does not record",
    )
    require(
        sources.get("derivation", {}).get("pinnedSpecificationUsed") is False,
        "derivation must record that no pinned specification was used",
    )


# --------------------------------------------------------------------------- #
# Static solution shape
# --------------------------------------------------------------------------- #


def verify_solution_shape() -> str:
    require(PACKAGE_INIT.is_file(), "the protected package initializer is missing")
    require(SOLUTION_PATH.is_file(), "vcfa_change/change.py was not created")
    text = SOLUTION_PATH.read_text(encoding="utf-8")
    require(text.strip() != "", "vcfa_change/change.py is empty")

    tree = ast.parse(text, filename=str(SOLUTION_PATH))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    roots.discard("vcfa_change")
    for root in sorted(roots):
        require(
            root in sys.stdlib_module_names,
            f"{root!r} is not a Python standard library module",
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            require(
                "://" not in value,
                "no string literal may contain a URL; build every request URL from "
                "the caller's base_url",
            )
            for banned in ("broadcom.com", "vmware.com"):
                require(
                    banned not in value,
                    f"no string literal may contain {banned!r}",
                )

    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    require(
        "apply_catalog_change" in names,
        "vcfa_change/change.py must define apply_catalog_change at module level",
    )
    public_functions = [name for name in names if not name.startswith("_")]
    require(
        public_functions == ["apply_catalog_change"],
        "apply_catalog_change must be the module's only public function",
    )
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "apply_catalog_change"
    )
    arguments = function.args
    require(
        not arguments.posonlyargs
        and not arguments.args
        and arguments.vararg is None
        and arguments.kwarg is None
        and [item.arg for item in arguments.kwonlyargs] == FUNCTION_PARAMETERS,
        "apply_catalog_change must have the documented keyword-only parameters",
    )
    require(
        arguments.kw_defaults[:6] == [None] * 6,
        "the first six apply_catalog_change parameters must be required",
    )
    try:
        optional_defaults = [
            ast.literal_eval(value) for value in arguments.kw_defaults[6:]
        ]
    except (ValueError, TypeError):
        raise VerificationError(
            "apply_catalog_change must use the documented literal defaults"
        ) from None
    require(
        optional_defaults == [None, None, None, 50, 30.0],
        "apply_catalog_change defaults changed",
    )
    return text


# --------------------------------------------------------------------------- #
# Scenario
# --------------------------------------------------------------------------- #


def catalog_item(item_id: str, name: str, project_ids: list[str], requestable: bool):
    return {
        "id": item_id,
        "name": name,
        "projectIds": project_ids,
        "isRequestable": requestable,
        "type": {"id": "com.vmw.blueprint", "name": "VMware Cloud Template"},
    }


def build_scenario() -> dict[str, Any]:
    project_id = str(uuid.uuid4())
    other_project_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    nonce = secrets.token_hex(6)
    cases: list[dict[str, Any]] = []

    specs = [
        {
            "key": "applied",
            "actionId": "Deployment.Update",
            "deploymentStatus": "CREATE_SUCCESSFUL",
            "pageSize": 50,
            "inputs": {
                "hostname": f"host-{secrets.token_hex(3)}",
                "cpuCount": 2,
                "adminEmail": f"ops-{secrets.token_hex(3)}@example.test",
            },
            "reason": f"Quarterly capacity change {secrets.token_hex(3)}",
            "actionReason": f"Resize approved under change {secrets.token_hex(3)}",
            "actionOutcome": {"status": 200},
        },
        {
            "key": "partial",
            "actionId": "Deployment.PowerOff",
            "deploymentStatus": "CREATE_INPROGRESS",
            "pageSize": 25,
            "inputs": None,
            "reason": None,
            "actionReason": None,
            "actionOutcome": {"status": 409},
        },
    ]

    for spec in specs:
        name = (
            f"web tier/edge & {secrets.token_hex(4)}"
            if spec["key"] == "applied"
            else f"web-tier-{secrets.token_hex(4)}"
        )
        target_id = str(uuid.uuid4())
        deployment_id = str(uuid.uuid4())
        requested_deployment_name = f"requested-{secrets.token_hex(4)}"
        deployment_name = f"created-{secrets.token_hex(4)}"
        page_size = spec["pageSize"]
        content = [
            catalog_item(str(uuid.uuid4()), f"{name}-legacy", [project_id], True),
            catalog_item(str(uuid.uuid4()), name, [other_project_id], True),
            catalog_item(str(uuid.uuid4()), name, [project_id], False),
            catalog_item(target_id, name, [project_id], True),
            catalog_item(str(uuid.uuid4()), f"{name}-clone", [project_id], True),
        ]
        case: dict[str, Any] = {
            "key": spec["key"],
            "catalogItemName": name,
            "catalogItemId": target_id,
            "requestedDeploymentName": requested_deployment_name,
            "deploymentName": deployment_name,
            "deploymentId": deployment_id,
            "deploymentStatus": spec["deploymentStatus"],
            "actionId": spec["actionId"],
            "pageSize": page_size,
            "catalogPage": {
                "content": content,
                "empty": False,
                "first": True,
                "last": True,
                "number": 0,
                "numberOfElements": len(content),
                "pageable": {
                    "pageNumber": 0,
                    "pageSize": page_size,
                    "offset": 0,
                    "paged": True,
                    "unpaged": False,
                },
                "size": page_size,
                "sort": {"sorted": False, "unsorted": True, "empty": True},
                "totalElements": len(content),
                "totalPages": 1,
            },
            "deployment": {
                "id": deployment_id,
                "name": deployment_name,
                "orgId": org_id,
                "projectId": project_id,
                "catalogItemId": target_id,
                "status": spec["deploymentStatus"],
                "createdAt": "2026-08-11T09:14:02.117Z",
                "createdBy": "svc-change@example.test",
                "resources": [
                    {
                        "id": str(uuid.uuid4()),
                        "name": "Cloud_vSphere_Machine_1",
                        "type": "Cloud.vSphere.Machine",
                    }
                ],
            },
        }
        if spec["inputs"] is not None:
            case["inputs"] = spec["inputs"]
        if spec["reason"] is not None:
            case["reason"] = spec["reason"]
        if spec["actionReason"] is not None:
            case["actionReason"] = spec["actionReason"]

        if spec["actionOutcome"]["status"] == 200:
            request_id = str(uuid.uuid4())
            case["actionOutcome"] = {"status": 200, "requestId": request_id}
            case["actionRequest"] = {
                "id": request_id,
                "name": "Update",
                "actionId": spec["actionId"],
                "deploymentId": deployment_id,
                "status": "PENDING",
                "requestedBy": "svc-change@example.test",
            }
        else:
            case["actionOutcome"] = {
                "status": 409,
                "errorCode": secrets.choice(CONFLICT_CODES),
                "message": (
                    f"Deployment {deployment_id} has request {nonce} in progress; "
                    f"the action {spec['actionId']} cannot be submitted."
                ),
            }
        cases.append(case)

    lookup_cases: list[dict[str, Any]] = []
    for key, match_count in (("duplicate", 2), ("missing", 0)):
        name = f"lookup {key} & {secrets.token_hex(4)}"
        target_id = str(uuid.uuid4())
        deployment_id = str(uuid.uuid4())
        requested_deployment_name = f"lookup-requested-{secrets.token_hex(4)}"
        deployment_name = f"lookup-created-{secrets.token_hex(4)}"
        request_id = str(uuid.uuid4())
        content = [
            catalog_item(str(uuid.uuid4()), f"{name}-legacy", [project_id], True),
            catalog_item(str(uuid.uuid4()), name, [other_project_id], True),
            catalog_item(str(uuid.uuid4()), name, [project_id], False),
        ]
        if match_count:
            content.append(catalog_item(target_id, name, [project_id], True))
        if match_count == 2:
            content.append(catalog_item(str(uuid.uuid4()), name, [project_id], True))
        lookup_cases.append(
            {
                "key": f"lookup-{key}",
                "catalogItemName": name,
                "catalogItemId": target_id,
                "requestedDeploymentName": requested_deployment_name,
                "deploymentName": deployment_name,
                "deploymentId": deployment_id,
                "deploymentStatus": "CREATE_SUCCESSFUL",
                "actionId": "Deployment.Update",
                "pageSize": 17,
                "catalogPage": {"content": content},
                "deployment": {
                    "id": deployment_id,
                    "name": deployment_name,
                    "status": "CREATE_SUCCESSFUL",
                },
                "actionOutcome": {"status": 200, "requestId": request_id},
                "actionRequest": {"id": request_id},
            }
        )

    response_cases: list[dict[str, Any]] = []
    for mode in (
        "catalog-http-error",
        "multiple-deployments",
        "different-deployment",
        "blank-status",
    ):
        name = f"response {mode} {secrets.token_hex(4)}"
        target_id = str(uuid.uuid4())
        deployment_id = str(uuid.uuid4())
        requested_deployment_name = f"response-requested-{secrets.token_hex(4)}"
        deployment_name = f"response-created-{secrets.token_hex(4)}"
        request_id = str(uuid.uuid4())
        deployment = {
            "id": deployment_id,
            "name": deployment_name,
            "status": "CREATE_SUCCESSFUL",
        }
        if mode == "different-deployment":
            deployment["id"] = str(uuid.uuid4())
        elif mode == "blank-status":
            deployment["status"] = " \t"
        response_cases.append(
            {
                "key": f"response-{mode}",
                "responseMode": mode,
                "catalogItemName": name,
                "catalogItemId": target_id,
                "requestedDeploymentName": requested_deployment_name,
                "deploymentName": deployment_name,
                "deploymentId": deployment_id,
                "extraDeploymentId": str(uuid.uuid4()),
                "deploymentStatus": "CREATE_SUCCESSFUL",
                "actionId": "Deployment.Update",
                "pageSize": 19,
                "catalogPage": {
                    "content": [catalog_item(target_id, name, [project_id], True)]
                },
                "deployment": deployment,
                "actionOutcome": {"status": 200, "requestId": request_id},
                "actionRequest": {"id": request_id},
            }
        )

    return {
        "accessToken": secrets.token_urlsafe(24),
        "projectId": project_id,
        "cases": cases,
        "lookupCases": lookup_cases,
        "responseCases": response_cases,
    }


def expected_result(case: dict[str, Any], project_id: str) -> dict[str, Any]:
    outcome = case["actionOutcome"]
    succeeded = outcome["status"] == 200
    steps = [
        {
            "name": "resolve-catalog-item",
            "operation": "getCatalogItems",
            "status": "succeeded",
            "detail": case["catalogItemId"],
        },
        {
            "name": "request-catalog-item",
            "operation": "requestCatalogItemInstances",
            "status": "succeeded",
            "detail": case["deploymentId"],
        },
        {
            "name": "read-deployment",
            "operation": "getDeploymentById",
            "status": "succeeded",
            "detail": case["deploymentStatus"],
        },
        {
            "name": "submit-deployment-action",
            "operation": "submitDeploymentActionRequest",
            "status": "succeeded" if succeeded else "failed",
            "detail": outcome["requestId"] if succeeded else outcome["errorCode"],
        },
    ]
    failure = (
        None
        if succeeded
        else {
            "step": "submit-deployment-action",
            "operation": "submitDeploymentActionRequest",
            "httpStatus": outcome["status"],
            "errorCode": outcome["errorCode"],
            "message": outcome["message"],
        }
    )
    return {
        "outcome": "applied" if succeeded else "partially_applied",
        "catalogItemId": case["catalogItemId"],
        "catalogItemName": case["catalogItemName"],
        "projectId": project_id,
        "deploymentId": case["deploymentId"],
        "deploymentName": case["deploymentName"],
        "deploymentStatus": case["deploymentStatus"],
        "actionId": case["actionId"],
        "actionRequestId": outcome["requestId"] if succeeded else None,
        "steps": steps,
        "failure": failure,
    }


# --------------------------------------------------------------------------- #
# Runtime
# --------------------------------------------------------------------------- #


def wait_for_service(port_file: Path, process: subprocess.Popen) -> int:
    deadline = time.monotonic() + 30.0
    port = 0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise VerificationError("the loopback service exited before it was ready")
        text = port_file.read_text(encoding="utf-8").strip() if port_file.exists() else ""
        if text.isdigit():
            port = int(text)
            break
        time.sleep(0.05)
    require(port > 0, "the loopback service did not publish a port")
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return port
        except OSError:
            time.sleep(0.05)
    raise VerificationError("the loopback service never accepted a connection")


def one_header(entry: dict[str, Any], name: str) -> str:
    values = entry.get("headerValues", {}).get(name, [])
    require(len(values) == 1, f"request must carry exactly one {name} header")
    return values[0]


def invoke_case(work: Path, label: str, call: dict[str, Any]) -> dict[str, Any]:
    args_path = work / f"call-{label}.json"
    out_path = work / f"result-{label}.json"
    args_path.write_text(json.dumps(call), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-B", str(RUN_CASE_PATH), str(args_path), str(out_path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    require(
        completed.returncode == 0,
        f"the case runner failed: {completed.stderr.strip()[:800]}",
    )
    require(out_path.is_file(), "the case runner produced no result")
    payload = load_json(out_path)
    return payload


def run_case(work: Path, index: int, call: dict[str, Any]) -> dict[str, Any]:
    payload = invoke_case(work, f"change-{index}", call)
    require(
        payload.get("ok") is True,
        "apply_catalog_change raised: " + str(payload.get("traceback", ""))[-900:],
    )
    return payload["result"]


def verify_input_validation(
    work: Path, base_url: str, scenario: dict[str, Any], log_file: Path
) -> None:
    case = scenario["cases"][0]
    valid = {
        "base_url": base_url,
        "access_token": scenario["accessToken"],
        "project_id": scenario["projectId"],
        "catalog_item_name": case["catalogItemName"],
        "deployment_name": case["requestedDeploymentName"],
        "action_id": case["actionId"],
    }
    invalid = [
        ("blank-base-url", "base_url", " \t"),
        ("blank-access-token", "access_token", ""),
        ("blank-project-id", "project_id", " \n"),
        ("blank-catalog-name", "catalog_item_name", ""),
        ("blank-deployment-name", "deployment_name", " \t"),
        ("blank-action-id", "action_id", ""),
        ("zero-page-size", "page_size", 0),
        ("negative-page-size", "page_size", -3),
        ("nonmapping-inputs", "inputs", ["not", "a", "mapping"]),
    ]
    for label, name, value in invalid:
        call = dict(valid)
        call[name] = value
        payload = invoke_case(work, f"invalid-{label}", call)
        require(
            payload.get("ok") is False and payload.get("errorType") == "ValueError",
            f"{label}: expected ValueError, got {payload}",
        )
    require(
        log_file.read_text(encoding="utf-8") == "",
        "invalid arguments must be rejected before any request",
    )


def verify_catalog_lookup_failures(
    work: Path, base_url: str, scenario: dict[str, Any]
) -> None:
    for index, case in enumerate(scenario["lookupCases"]):
        call = {
            "base_url": base_url,
            "access_token": scenario["accessToken"],
            "project_id": scenario["projectId"],
            "catalog_item_name": case["catalogItemName"],
            "deployment_name": case["requestedDeploymentName"],
            "action_id": case["actionId"],
            "page_size": case["pageSize"],
        }
        payload = invoke_case(work, f"lookup-{index}", call)
        require(
            payload.get("ok") is False and payload.get("errorType") == "LookupError",
            f"{case['key']}: expected LookupError, got {payload}",
        )


def verify_unusable_responses(
    work: Path, base_url: str, scenario: dict[str, Any]
) -> None:
    for index, case in enumerate(scenario["responseCases"]):
        call = {
            "base_url": base_url,
            "access_token": scenario["accessToken"],
            "project_id": scenario["projectId"],
            "catalog_item_name": case["catalogItemName"],
            "deployment_name": case["requestedDeploymentName"],
            "action_id": case["actionId"],
            "page_size": case["pageSize"],
        }
        payload = invoke_case(work, f"response-{index}", call)
        require(
            payload.get("ok") is False,
            f"{case['key']}: an unusable pre-action 200 must raise",
        )


def verify_runtime(solution_text: str) -> None:
    scenario = build_scenario()
    project_id = scenario["projectId"]

    all_cases = scenario["cases"] + scenario["lookupCases"] + scenario["responseCases"]
    for case in all_cases:
        for secret in (
            case["catalogItemId"],
            case["deploymentId"],
            case["catalogItemName"],
            case["requestedDeploymentName"],
            case["deploymentName"],
        ):
            require(
                secret not in solution_text,
                "the solution embeds a runtime identifier instead of reading it "
                "from the service",
            )
    require(
        scenario["accessToken"] not in solution_text,
        "the solution embeds the runtime access token",
    )

    with tempfile.TemporaryDirectory(prefix="vcfa-0319-") as raw_work:
        work = Path(raw_work)
        scenario_path = work / "scenario.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
        port_file = work / "port.txt"
        log_file = work / "requests.jsonl"

        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(log_file),
                str(CONTRACT_PATH),
                str(scenario_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_service(port_file, process)
            base_url = f"http://127.0.0.1:{port}"
            verify_input_validation(work, base_url, scenario, log_file)
            results = []
            for index, case in enumerate(scenario["cases"]):
                call: dict[str, Any] = {
                    "base_url": base_url,
                    "access_token": scenario["accessToken"],
                    "project_id": project_id,
                    "catalog_item_name": case["catalogItemName"],
                    "deployment_name": case["requestedDeploymentName"],
                    "action_id": case["actionId"],
                }
                if "inputs" in case:
                    call["inputs"] = case["inputs"]
                if "reason" in case:
                    call["reason"] = case["reason"]
                if "actionReason" in case:
                    call["action_reason"] = case["actionReason"]
                if case["pageSize"] != 50:
                    call["page_size"] = case["pageSize"]
                results.append(run_case(work, index, call))
            verify_catalog_lookup_failures(work, base_url, scenario)
            verify_unusable_responses(work, base_url, scenario)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()

        entries = [
            json.loads(line)
            for line in log_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    require(
        len(entries) == 19,
        f"expected nineteen contract requests, saw {len(entries)}",
    )
    require(
        all("operationkey" not in entry["headerValues"] for entry in entries),
        "operationKey must not be sent as a request header",
    )
    verify_results(scenario, results)
    verify_wire(scenario, entries[:8])
    verify_lookup_wire(scenario, entries[8:10])
    verify_response_wire(scenario, entries[10:])


def verify_results(scenario: dict[str, Any], results: list[Any]) -> None:
    project_id = scenario["projectId"]
    for case, result in zip(scenario["cases"], results):
        label = case["key"]
        require(isinstance(result, dict), f"{label}: the result must be a mapping")
        require(
            list(result) == RESULT_KEYS,
            f"{label}: result keys or their order changed: {list(result)}",
        )
        expected = expected_result(case, project_id)
        steps = result["steps"]
        require(
            isinstance(steps, list) and len(steps) == 4,
            f"{label}: every step must be reported once",
        )
        for index, step in enumerate(steps):
            require(
                list(step) == STEP_KEYS,
                f"{label}: step {index} keys or their order changed",
            )
            require(
                step["name"] == STEP_NAMES[index],
                f"{label}: step {index} name changed",
            )
        if result["failure"] is not None:
            require(
                list(result["failure"]) == FAILURE_KEYS,
                f"{label}: failure keys or their order changed",
            )
        for key in RESULT_KEYS:
            require(
                result[key] == expected[key],
                f"{label}: {key} is {result[key]!r}, expected {expected[key]!r}",
            )

    applied, partial = results
    require(
        applied["outcome"] == "applied" and partial["outcome"] == "partially_applied",
        "the two outcomes must be distinguished",
    )
    require(
        [step["status"] for step in partial["steps"]]
        == ["succeeded", "succeeded", "succeeded", "failed"],
        "the steps that were applied before the failing step must stay reported as "
        "succeeded",
    )


def verify_wire(scenario: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    project_id = scenario["projectId"]
    require(
        len(entries) == 8,
        f"expected exactly eight requests across both changes, saw {len(entries)}",
    )
    require(
        [entry["sequence"] for entry in entries] == list(range(1, 9)),
        "the request log is not contiguous",
    )

    expected_targets: list[str] = []
    expected_keys: list[str] = []
    expected_methods: list[str] = []
    expected_statuses: list[int] = []
    for case in scenario["cases"]:
        expected_targets.extend(
            [
                "/catalog/api/items?"
                + urlencode(
                    [
                        ("search", case["catalogItemName"]),
                        ("projects", project_id),
                        ("size", str(case["pageSize"])),
                    ]
                ),
                f"/catalog/api/items/{case['catalogItemId']}/request",
                f"/deployment/api/deployments/{case['deploymentId']}?expand=resources",
                f"/deployment/api/deployments/{case['deploymentId']}/requests",
            ]
        )
        expected_keys.extend(
            [
                "getCatalogItems",
                "requestCatalogItemInstances",
                "getDeploymentById",
                "submitDeploymentActionRequest",
            ]
        )
        expected_methods.extend(["GET", "POST", "GET", "POST"])
        expected_statuses.extend([200, 200, 200, case["actionOutcome"]["status"]])

    require(
        [entry["rawTarget"] for entry in entries] == expected_targets,
        "the exact request targets changed:\n"
        + "\n".join(
            f"  saw {entry['rawTarget']}" for entry in entries
        ),
    )
    require(
        [entry["operationKey"] for entry in entries] == expected_keys,
        "an operation outside the focused contract was called",
    )
    require(
        [entry["method"] for entry in entries] == expected_methods,
        "the request methods changed",
    )
    require(
        [entry["responseStatus"] for entry in entries] == expected_statuses,
        "the service rejected a request; the wire shape is wrong",
    )

    token = scenario["accessToken"]
    for index, entry in enumerate(entries):
        require(
            "operationkey" not in entry["headerValues"],
            f"request {index}: operationKey must not be sent as a header",
        )
        require(
            one_header(entry, "authorization") == f"Bearer {token}",
            f"request {index}: authorization header changed",
        )
        require(
            one_header(entry, "accept") == "application/json",
            f"request {index}: Accept must be exactly application/json",
        )

    for case_index, case in enumerate(scenario["cases"]):
        offset = case_index * 4
        search, create, read, action = entries[offset : offset + 4]
        label = case["key"]

        for name, entry in (("catalog search", search), ("deployment read", read)):
            require(
                entry["bodyLength"] == 0 and entry["body"] == "",
                f"{label}: the {name} must carry no body",
            )
            require(
                "content-type" not in entry["headerValues"],
                f"{label}: the {name} must omit Content-Type",
            )

        require(
            search["query"]
            == {
                "search": [case["catalogItemName"]],
                "projects": [project_id],
                "size": [str(case["pageSize"])],
            },
            f"{label}: the catalog query bound the wrong members",
        )
        for omitted in CATALOG_QUERY_MEMBERS:
            if omitted in ("search", "projects", "size"):
                continue
            require(
                omitted not in search["query"],
                f"{label}: unset catalog query member {omitted} was sent",
            )
        require(
            read["query"] == {"expand": ["resources"]},
            f"{label}: the deployment read query bound the wrong members",
        )
        for omitted in ("expandResources", "expandLastRequest", "expandProject", "deleted"):
            require(
                omitted not in read["query"],
                f"{label}: unset deployment query member {omitted} was sent",
            )

        for name, entry, members, values in (
            (
                "catalog item request",
                create,
                CATALOG_REQUEST_MEMBERS,
                {
                    "deploymentName": case["requestedDeploymentName"],
                    "inputs": case.get("inputs"),
                    "projectId": project_id,
                    "reason": case.get("reason"),
                },
            ),
            (
                "deployment action request",
                action,
                ACTION_REQUEST_MEMBERS,
                {
                    "actionId": case["actionId"],
                    "reason": case.get("actionReason"),
                },
            ),
        ):
            require(
                entry["rawQuery"] == "" and entry["query"] == {},
                f"{label}: the {name} must carry no query",
            )
            content_type = one_header(entry, "content-type")
            require(
                content_type == "application/json",
                f"{label}: the {name} Content-Type must be exactly application/json",
            )
            bound = {
                key: values[key]
                for key in members
                if key in values and values[key] is not None
            }
            expected_body = json.dumps(bound, separators=(",", ":"))
            require(
                entry["body"] == expected_body,
                f"{label}: the {name} body bytes changed\n"
                f"  expected {expected_body}\n"
                f"  saw      {entry['body']}",
            )
            require(
                entry["bodyLength"] == len(expected_body.encode("utf-8")),
                f"{label}: the {name} Content-Length disagrees with its body",
            )
            parsed = json.loads(entry["body"])
            require(
                list(parsed) == list(bound),
                f"{label}: the {name} member order changed",
            )
            for omitted in members:
                if omitted in bound:
                    continue
                require(
                    omitted not in parsed,
                    f"{label}: unset optional member {omitted} was serialized into "
                    f"the {name}",
                )
            if "inputs" in bound:
                require(
                    list(parsed["inputs"]) == list(bound["inputs"]),
                    f"{label}: the caller's inputs member order was not preserved",
                )


def verify_lookup_wire(
    scenario: dict[str, Any], entries: list[dict[str, Any]]
) -> None:
    require(len(entries) == 2, "catalog lookup checks made an unexpected request")
    for offset, (case, entry) in enumerate(zip(scenario["lookupCases"], entries)):
        expected_query = urlencode(
            [
                ("search", case["catalogItemName"]),
                ("projects", scenario["projectId"]),
                ("size", str(case["pageSize"])),
            ]
        )
        require(entry["sequence"] == 9 + offset, "lookup request order changed")
        require(
            entry["operationKey"] == "getCatalogItems"
            and entry["method"] == "GET"
            and entry["rawTarget"] == "/catalog/api/items?" + expected_query
            and entry["responseStatus"] == 200,
            f"{case['key']}: lookup request changed",
        )
        require(
            one_header(entry, "authorization")
            == f"Bearer {scenario['accessToken']}"
            and one_header(entry, "accept") == "application/json",
            f"{case['key']}: lookup headers changed",
        )
        require(
            entry["bodyLength"] == 0
            and entry["body"] == ""
            and "content-type" not in entry["headerValues"],
            f"{case['key']}: lookup must carry no body or Content-Type",
        )


def verify_response_wire(
    scenario: dict[str, Any], entries: list[dict[str, Any]]
) -> None:
    expected_by_mode = {
        "catalog-http-error": ["getCatalogItems"],
        "multiple-deployments": ["getCatalogItems", "requestCatalogItemInstances"],
        "different-deployment": [
            "getCatalogItems",
            "requestCatalogItemInstances",
            "getDeploymentById",
        ],
        "blank-status": [
            "getCatalogItems",
            "requestCatalogItemInstances",
            "getDeploymentById",
        ],
    }
    cursor = 0
    for case in scenario["responseCases"]:
        expected = expected_by_mode[case["responseMode"]]
        actual = entries[cursor : cursor + len(expected)]
        require(
            [entry["operationKey"] for entry in actual] == expected,
            f"{case['key']}: request sequence changed",
        )
        expected_statuses = (
            [401]
            if case["responseMode"] == "catalog-http-error"
            else [200] * len(expected)
        )
        require(
            [entry["responseStatus"] for entry in actual] == expected_statuses,
            f"{case['key']}: a request did not match the contract wire",
        )
        for entry in actual:
            require(
                one_header(entry, "authorization")
                == f"Bearer {scenario['accessToken']}"
                and one_header(entry, "accept") == "application/json",
                f"{case['key']}: request headers changed",
            )
        cursor += len(expected)
    require(cursor == len(entries), "an unusable response caused an extra request")
    require(
        [entry["sequence"] for entry in entries] == list(range(11, 20)),
        "unusable response request order changed",
    )


def main() -> int:
    try:
        verify_contract()
        verify_sources()
        solution_text = verify_solution_shape()
        verify_runtime(solution_text)
    except (
        OSError,
        ValueError,
        SyntaxError,
        subprocess.SubprocessError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}")
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
